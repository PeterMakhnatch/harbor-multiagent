import asyncio
import base64
import importlib.util
import json
import os
import shlex
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import ModuleType
from typing import Any, override
from uuid import UUID
from weakref import WeakValueDictionary

import httpx

from harbor.agents.base import BaseAgent
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext, ModelUsage

MODEL = "deepseek-flash"
MODEL_SLUG = f"deepseek/{MODEL}"
BASE_URL = "https://api.deepseek.com"
BALANCE_FLOOR = Decimal("1.50")
MAX_MESSAGE_BYTES = 30_720
MAX_INPUT_TOKENS = 32_768
MAX_OUTPUT_TOKENS = 512
MAX_SEAT_CALLS = 10
MAX_TRIAL_CALLS = 40
MAX_HTTP_BYTES = 262_144
PEAK_NANODOLLARS = (300, 6, 1200)
OFF_PEAK_NANODOLLARS = (150, 3, 600)
RESERVE_NANODOLLARS = (
    MAX_INPUT_TOKENS * PEAK_NANODOLLARS[0] + MAX_OUTPUT_TOKENS * PEAK_NANODOLLARS[2]
)


def _load_protocol() -> ModuleType:
    path = Path(__file__).resolve().parent / "environment/world/agentsnet_protocol.py"
    spec = importlib.util.spec_from_file_location("agentsnet_protocol", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot import the AgentsNet protocol at {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


protocol = _load_protocol()


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON number: {value}")


def _decode_object(data: str | bytes | bytearray) -> dict[str, Any]:
    return _object(json.loads(data, parse_constant=_reject_constant))


def _json_body(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8", errors="backslashreplace")


def _record(logs_dir: Path, event: str, **fields: Any) -> None:
    with (logs_dir / "deepseek.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps({"event": event, **fields}, allow_nan=False) + "\n")


def _usd(nanodollars: int) -> float:
    return nanodollars / 1_000_000_000


def _rates(started: datetime) -> tuple[int, int, int]:
    peak = started.weekday() < 5 and (1 <= started.hour < 4 or 6 <= started.hour < 10)
    return PEAK_NANODOLLARS if peak else OFF_PEAK_NANODOLLARS


@dataclass
class _Admission:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    started_calls: int = 0
    stopped: str | None = None

    def require_open(self) -> None:
        if self.stopped is not None:
            raise NonZeroAgentExitCodeError(self.stopped)


_admissions: WeakValueDictionary[UUID, _Admission] = WeakValueDictionary()


def _trial_admission(context_id: UUID) -> _Admission:
    admission = _admissions.get(context_id)
    if admission is None:
        admission = _Admission()
        _admissions[context_id] = admission
    return admission


def _token_count(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    raise ValueError("DeepSeek usage is missing or invalid")


@dataclass
class _Usage:
    input_tokens: int
    cache_tokens: int
    output_tokens: int
    cost_nanodollars: int

    @classmethod
    def from_response(cls, payload: dict[str, Any], started: datetime) -> "_Usage":
        usage = _object(payload.get("usage"))
        input_tokens = _token_count(usage.get("prompt_tokens"))
        cache_tokens = _token_count(usage.get("prompt_cache_hit_tokens"))
        miss_tokens = _token_count(usage.get("prompt_cache_miss_tokens"))
        output_tokens = _token_count(usage.get("completion_tokens"))
        if (
            cache_tokens + miss_tokens != input_tokens
            or input_tokens > MAX_INPUT_TOKENS
            or output_tokens > MAX_OUTPUT_TOKENS
        ):
            raise ValueError(
                "DeepSeek usage exceeds the admitted bounds or is inconsistent"
            )
        uncached_rate, cached_rate, output_rate = _rates(started)
        return cls(
            input_tokens,
            cache_tokens,
            output_tokens,
            miss_tokens * uncached_rate
            + cache_tokens * cached_rate
            + output_tokens * output_rate,
        )


@dataclass
class _Account:
    started: int = 0
    observed: int = 0
    input_tokens: int = 0
    cache_tokens: int = 0
    output_tokens: int = 0
    cost_nanodollars: int = 0

    def add(self, usage: _Usage) -> None:
        self.observed += 1
        self.input_tokens += usage.input_tokens
        self.cache_tokens += usage.cache_tokens
        self.output_tokens += usage.output_tokens
        self.cost_nanodollars += usage.cost_nanodollars

    def publish(self, context: AgentContext) -> None:
        if self.observed:
            context.n_input_tokens = self.input_tokens
            context.n_cache_tokens = self.cache_tokens
            context.n_output_tokens = self.output_tokens
            context.cost_usd = _usd(self.cost_nanodollars)
            context.model_usage = {
                MODEL: ModelUsage(
                    n_input_tokens=self.input_tokens,
                    n_cache_tokens=self.cache_tokens,
                    n_output_tokens=self.output_tokens,
                    cost_usd=context.cost_usd,
                )
            }
        context.metadata = {
            "started_calls": self.started,
            "observed_usage_calls": self.observed,
            "unknown_usage_calls": self.started - self.observed,
            "unknown_usage_reserve_usd": _usd(
                (self.started - self.observed) * RESERVE_NANODOLLARS
            ),
        }


@dataclass
class _Capture:
    status: int | None = None
    body: bytearray = field(default_factory=bytearray)

    async def read(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        body: bytes | None,
        timeout_sec: int,
    ) -> None:
        headers = {"Content-Type": "application/json"} if body is not None else None
        async with asyncio.timeout(timeout_sec):
            async with client.stream(
                method, path, content=body, headers=headers
            ) as response:
                self.status = response.status_code
                async for chunk in response.aiter_bytes():
                    remaining = MAX_HTTP_BYTES - len(self.body)
                    if len(chunk) > remaining:
                        self.body.extend(chunk[:remaining])
                        raise ValueError(
                            "DeepSeek HTTP response exceeded the byte limit"
                        )
                    self.body.extend(chunk)

    def raw(self, key: str) -> bytes:
        return bytes(self.body).replace(key.encode(), b"[REDACTED]")


async def _balance(client: httpx.AsyncClient) -> Decimal:
    captured = _Capture()
    await captured.read(client, "GET", "/user/balance", body=None, timeout_sec=10)
    if captured.status != 200:
        raise ValueError("The DeepSeek balance refresh did not return HTTP 200")
    data = _decode_object(captured.body)
    entries = data.get("balance_infos")
    if data.get("is_available") is not True or not isinstance(entries, list):
        raise ValueError("The DeepSeek account balance is unavailable")
    usd = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("currency") == "USD"
    ]
    if len(usd) != 1:
        raise ValueError("The DeepSeek balance refresh did not include one USD balance")
    total = Decimal(str(usd[0].get("total_balance")))
    if not total.is_finite() or total < BALANCE_FLOOR:
        raise ValueError("The DeepSeek USD balance is below the admission floor")
    return total


def _completion(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("DeepSeek did not return one completion choice")
    choice = _object(choices[0])
    text = _object(choice.get("message")).get("content")
    if not isinstance(text, str) or not isinstance(choice.get("finish_reason"), str):
        raise ValueError("DeepSeek did not return a complete text reply")
    return text


async def _request(
    client: httpx.AsyncClient,
    key: str,
    body: bytes,
    started: datetime,
    logs_dir: Path,
    call: dict[str, Any],
) -> tuple[str, _Usage]:
    captured = _Capture()
    response = None
    usage = None
    failure = None
    raw = None
    try:
        await captured.read(
            client, "POST", "/chat/completions", body=body, timeout_sec=45
        )
        if captured.status is None or not 200 <= captured.status < 300:
            raise ValueError("DeepSeek completion did not return HTTP success")
        raw = captured.raw(key)
        response = _decode_object(raw)
        text = _completion(response)
        usage = _Usage.from_response(response, started)
        return text, usage
    except asyncio.CancelledError:
        failure = "CancelledError"
        raise
    except (httpx.HTTPError, TimeoutError, ValueError, RecursionError) as exc:
        failure = type(exc).__name__
        if type(exc) is ValueError:
            failure = str(exc)
        raise NonZeroAgentExitCodeError(
            "DeepSeek request has unknown usage; stopped admission"
        ) from None
    finally:
        if raw is None:
            raw = captured.raw(key)
        try:
            raw_text, raw_base64 = raw.decode("utf-8"), None
        except UnicodeDecodeError:
            raw_text, raw_base64 = None, base64.b64encode(raw).decode("ascii")
        _record(
            logs_dir,
            "call_finished",
            **call,
            finished_at=datetime.now(UTC).isoformat(),
            status_code=captured.status,
            response_body=raw_text,
            response_body_base64=raw_base64,
            response_model=response.get("model") if response else None,
            usage=response.get("usage") if response else None,
            cost_usd=_usd(usage.cost_nanodollars) if usage else None,
            unknown_usage_reserve_usd=0 if usage else _usd(RESERVE_NANODOLLARS),
            error=failure,
        )


@dataclass
class _SeatModel:
    client: httpx.AsyncClient
    key: str = field(repr=False)
    admission: _Admission
    logs_dir: Path
    account: _Account = field(default_factory=_Account)
    history: list[dict[str, str]] = field(default_factory=list)

    async def query(self, prompt: str, round_number: int, attempt: int) -> str:
        self.history.append({"role": "user", "content": prompt})
        payload = {
            "model": MODEL,
            "messages": self.history,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "stream": False,
            "thinking": {"type": "disabled"},
        }
        message_bytes = len(_json_body(self.history))
        async with self.admission.lock:
            self.admission.require_open()
            if (
                message_bytes > MAX_MESSAGE_BYTES
                or self.account.started >= MAX_SEAT_CALLS
                or self.admission.started_calls >= MAX_TRIAL_CALLS
            ):
                raise NonZeroAgentExitCodeError(
                    "AgentsNet request exceeds admission bounds"
                )
            try:
                balance = await _balance(self.client)
            except (
                httpx.HTTPError,
                TimeoutError,
                ValueError,
                InvalidOperation,
                RecursionError,
            ) as exc:
                _record(
                    self.logs_dir,
                    "admission_refused",
                    at=datetime.now(UTC).isoformat(),
                    round=round_number,
                    attempt=attempt,
                    error=type(exc).__name__,
                )
                raise NonZeroAgentExitCodeError(
                    "DeepSeek balance did not satisfy the admission floor"
                ) from None
            self.admission.require_open()
            started = datetime.now(UTC)
            call = {
                "seat_call": self.account.started + 1,
                "trial_call": self.admission.started_calls + 1,
                "round": round_number,
                "attempt": attempt,
                "started_at": started.isoformat(),
                "reserve_usd": _usd(RESERVE_NANODOLLARS),
                "rates_nanodollars_per_token": _rates(started),
            }
            body = _json_body(payload)
            _record(
                self.logs_dir,
                "call_started",
                **call,
                balance_checked_at=started.isoformat(),
                balance_usd=str(balance),
                message_bytes=message_bytes,
                request=payload,
            )
            self.account.started += 1
            self.admission.started_calls += 1
            try:
                reply, usage = await _request(
                    self.client, self.key, body, started, self.logs_dir, call
                )
            except BaseException:
                self.admission.stopped = "A prior DeepSeek request has unknown usage"
                raise
            self.account.add(usage)
        self.history.append({"role": "assistant", "content": reply})
        return reply

    async def decide[T](
        self,
        observation: dict[str, Any],
        parse: Callable[[str], T | None],
        retry: str,
    ) -> T | None:
        round_number = observation["round"]
        prompt = protocol.user_prompt(observation)
        for attempt, text in enumerate((prompt, retry), start=1):
            result = parse(await self.query(text, round_number, attempt))
            _record(
                self.logs_dir,
                "parse_result",
                round=round_number,
                attempt=attempt,
                valid=result is not None,
            )
            if result is not None:
                return result
        return None


async def _rpc(
    environment: SeatEnvironment, operation: str, data: dict[str, Any] | None = None
) -> dict[str, Any]:
    args = ["python3", "/app/world_client.py", operation]
    if data is not None:
        args.append(_json_body(data).decode("utf-8"))
    result = await environment.exec(shlex.join(args), timeout_sec=10)
    if result.return_code != 0 or result.stdout is None:
        raise NonZeroAgentExitCodeError(f"World RPC {operation} failed")
    return _decode_object(result.stdout)


async def _wait_turn(
    environment: SeatEnvironment, admission: _Admission, round_number: int
) -> dict[str, Any]:
    try:
        async with asyncio.timeout(450):
            for _ in range(225):
                admission.require_open()
                observation = await _rpc(environment, "observe")
                if observation["seat"] != environment.service:
                    raise NonZeroAgentExitCodeError(
                        "World observation is for another seat"
                    )
                if (
                    observation["round"] == round_number
                    and not observation["submitted"]
                ):
                    phase = "final" if round_number == 5 else "messages"
                    if observation["phase"] == phase:
                        return observation
                    break
                if (
                    observation["round"] != round_number - 1
                    or not observation["submitted"]
                ):
                    break
                await asyncio.sleep(2)
    except TimeoutError:
        raise NonZeroAgentExitCodeError("World observation wait expired") from None
    raise NonZeroAgentExitCodeError("World did not advance to the expected turn")


class AgentsNetAgent(BaseAgent):
    @staticmethod
    @override
    def name() -> str:
        return "agentsnet-coloring-deepseek"

    @override
    def version(self) -> str:
        return "1.0.0"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        if (
            not isinstance(environment, SeatEnvironment)
            or self.model_name != MODEL_SLUG
        ):
            raise ValueError(f"AgentsNetAgent requires a native seat and {MODEL_SLUG}")

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        if not isinstance(environment, SeatEnvironment) or self.context_id is None:
            raise ValueError("AgentsNetAgent requires a native seat trial")
        key = os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise NonZeroAgentExitCodeError("DEEPSEEK_API_KEY is missing on the host")
        admission = _trial_admission(self.context_id)
        async with httpx.AsyncClient(
            base_url=BASE_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Accept-Encoding": "identity",
            },
            transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False),
            trust_env=False,
            timeout=httpx.Timeout(45.0, connect=10.0),
            follow_redirects=False,
        ) as client:
            model = _SeatModel(client, key, admission, self.logs_dir)
            try:
                await self._play(instruction, environment, model)
            except NonZeroAgentExitCodeError as exc:
                if admission.stopped is None:
                    admission.stopped = str(exc)
                raise
            finally:
                model.account.publish(context)

    async def _play(
        self, instruction: str, environment: SeatEnvironment, model: _SeatModel
    ) -> None:
        for round_number in range(1, 6):
            observation = await _wait_turn(environment, model.admission, round_number)
            if round_number == 1:
                role_line = (
                    f"You are {observation['name']} (seat {environment.service})."
                )
                if instruction.split("\n", 1)[0] != role_line:
                    raise NonZeroAgentExitCodeError(
                        "Seat instruction does not match the world"
                    )
                model.history.append(
                    {"role": "system", "content": protocol.system_prompt(observation)}
                )
            if observation["phase"] == "messages":
                messages = await model.decide(
                    observation, protocol.parse_messages, protocol.JSON_RETRY
                )
                await _rpc(
                    environment,
                    "messages",
                    {
                        "round": round_number,
                        "messages": {} if messages is None else messages,
                    },
                )
            else:
                answer = await model.decide(
                    observation, protocol.parse_answer, protocol.ANSWER_RETRY
                )
                await _rpc(
                    environment, "answer", {"round": round_number, "answer": answer}
                )
