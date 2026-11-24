"""Native Dispatch Handoff participants: arrival script and one tool-free model turn."""

import asyncio
import http.client
import json
import os
import re
import shlex
import time
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any, override
from uuid import UUID

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext, ModelUsage

BAYS = ("A", "B", "C", "D")
_DELIVERIES = {
    "silent": [],
    "stale": [1],
    "fresh": [2],
    "reordered": [2, 1],
    "pooled": [],
}
_CHAT = "/api/v1/chat/completions"
_FREE_SUFFIX = ":free"
_MODEL_SLUG = re.compile(r"[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*:free")
_PROVIDER_SLUG = re.compile(r"[a-z0-9][a-z0-9-]*(?:/[a-z0-9][a-z0-9-]*)*")
_HOSTED_TIMEOUT_SEC = 55
_VIEW_TIMEOUT_SEC = 115
_WORLD_LIMIT = 128 * 1024
_ROUTER_LIMIT = 2 * 1024 * 1024
_SEAT_HTTP = f"""\
import http.client, sys
from contextlib import closing
method, path = sys.argv[1:3]
if method == 'POST':
    body = sys.argv[3].encode('utf-8')
    headers = {{'Content-Type': 'application/json', 'X-SeatLab-View-SHA256': sys.argv[4]}}
else:
    body = None
    headers = {{}}
with closing(http.client.HTTPConnection('world', 8080, timeout=3)) as connection:
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    data = response.read({_WORLD_LIMIT + 1})
    if len(data) > {_WORLD_LIMIT}:
        sys.exit(1)
    sys.stdout.write(str(response.status) + '\\n' + data.decode('utf-8'))
"""
_RULES = {
    "inventory": (
        "You are the inventory clerk. Send both revision reports for every case, "
        "with the availability you observed at each revision. "
        'Return {"rows":[{"id":0,"reports":[{"revision":1,"available":["A","B"]},'
        '{"revision":2,"available":["B","C"]}]}]} with one row per input ID.'
    ),
    "dispatch": (
        "You are the dispatcher. Choose the available dock bay with the highest utility "
        "for each case. Use pooled_available if present. Otherwise use the bulletin with "
        "the highest revision number you received; arrival order can differ from revision order. "
        "If no availability information is visible, choose the bay with the highest utility. "
        'Return {"rows":[{"id":0,"bay":"A"}]} with one row per input ID.'
    ),
}


class _Unmeasured(RuntimeError):
    """The seat could not submit a valid decision under the declared policy."""


class _OwnerUnavailable(_Unmeasured):
    pass


def _unique_object(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate field")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Non-JSON constant")


def _json_object(raw: str | bytes, source: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=Decimal,
        )
    except (InvalidOperation, ValueError, RecursionError):
        raise _Unmeasured(f"Invalid JSON from {source}") from None
    if not isinstance(value, dict):
        raise _Unmeasured(f"Expected a JSON object from {source}")
    return value


def _keys(value: object, *keys: str) -> bool:
    return isinstance(value, dict) and value.keys() == set(keys)


def _bay_pair(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(bay in BAYS for bay in value)
        and value[0] != value[1]
    )


def _revisions(reports: object) -> list[int] | None:
    if not isinstance(reports, list):
        return None
    revisions: list[int] = []
    for report in reports:
        if not isinstance(report, dict) or not _keys(report, "revision", "available"):
            return None
        revision = report.get("revision")
        if type(revision) is not int or not _bay_pair(report.get("available")):
            return None
        revisions.append(revision)
    return revisions


def _checked_view(view: dict[str, Any], role: str) -> list[dict[str, Any]]:
    condition, cases = view.get("condition"), view.get("cases")
    if (
        not _keys(view, "role", "condition", "cases")
        or view.get("role") != role
        or not isinstance(condition, str)
        or condition not in _DELIVERIES
        or not isinstance(cases, list)
        or not 1 <= len(cases) <= 256
    ):
        raise _Unmeasured("Malformed scoped world view")
    for case_id, case in enumerate(cases):
        if not isinstance(case, dict):
            raise _Unmeasured("Malformed world case ID")
        episode = case.get("id")
        if type(episode) is not int or episode != case_id:
            raise _Unmeasured("Malformed world case ID")
        if role == "inventory":
            valid = (
                condition != "pooled"
                and _keys(case, "id", "reports")
                and _revisions(case.get("reports")) == [1, 2]
            )
        else:
            utilities = case.get("utilities")
            available = case.get("pooled_available")
            valid = (
                _keys(case, "id", "utilities", "messages", "pooled_available")
                and isinstance(utilities, dict)
                and _keys(utilities, *BAYS)
                and all(type(rank) is int for rank in utilities.values())
                and set(utilities.values()) == {1, 2, 3, 4}
                and _revisions(case.get("messages")) == _DELIVERIES[condition]
                and (
                    _bay_pair(available) if condition == "pooled" else available is None
                )
            )
        if not valid:
            raise _Unmeasured("Malformed scoped world case")
    return cases


def _arrival(role: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
    if role == "inventory":
        return {"rows": cases}
    rows = []
    for case in cases:
        messages = case["messages"]
        eligible = case["pooled_available"] or (
            messages[-1]["available"] if messages else BAYS
        )
        rows.append(
            {"id": case["id"], "bay": max(eligible, key=case["utilities"].__getitem__)}
        )
    return {"rows": rows}


def _checked_submission(value: dict[str, Any], role: str, episodes: int) -> None:
    field = "reports" if role == "inventory" else "bay"
    rows = value.get("rows")
    if not _keys(value, "rows") or not isinstance(rows, list) or len(rows) != episodes:
        raise _Unmeasured("Expected one decision for each case")
    ids: set[int] = set()
    for row in rows:
        if not isinstance(row, dict) or not _keys(row, "id", field):
            raise _Unmeasured("Invalid decision fields")
        case_id = row["id"]
        if type(case_id) is not int or not 0 <= case_id < episodes or case_id in ids:
            raise _Unmeasured("Invalid decision case ID")
        ids.add(case_id)
        if role == "inventory":
            revisions = _revisions(row[field])
            valid = revisions in ([1, 2], [2, 1])
        else:
            valid = row[field] in BAYS
        if not valid:
            raise _Unmeasured("Invalid decision value")


async def _world_request(
    environment: BaseEnvironment,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    view_sha256: str | None = None,
) -> tuple[int, str]:
    args = ["python", "-c", _SEAT_HTTP, "GET" if payload is None else "POST", path]
    if payload is not None:
        if view_sha256 is None:
            raise ValueError("World submission requires a view digest")
        args.extend((json.dumps(payload, separators=(",", ":")), view_sha256))
    result = await environment.exec(command=shlex.join(args), timeout_sec=6)
    status, sep, body = (result.stdout or "").partition("\n")
    if result.return_code != 0 or not sep or not re.fullmatch(r"[1-5][0-9]{2}", status):
        raise _Unmeasured("Scoped world request failed")
    return int(status), body


async def _view(environment: SeatEnvironment) -> dict[str, Any]:
    try:
        async with asyncio.timeout(_VIEW_TIMEOUT_SEC):
            while True:
                status, body = await _world_request(environment, "/view")
                if status == 200:
                    return _json_object(body, "world view")
                if status != 409 or environment.service != "dispatch":
                    raise _Unmeasured(f"World view rejected (HTTP {status})")
                if _json_object(body, "world view") != {
                    "error": "waiting for inventory"
                }:
                    raise _Unmeasured("Malformed pending world view")
                await asyncio.sleep(1)
    except TimeoutError:
        raise _Unmeasured("Timed out waiting for the scoped world view") from None


@dataclass(frozen=True)
class _Route:
    model: str
    slug: str
    provider: str


def _is_zero(value: object) -> bool:
    if type(value) not in (str, int, float, Decimal):
        return False
    try:
        return Decimal(str(value)).is_zero()
    except InvalidOperation:
        return False


def _free_pricing(value: object) -> bool:
    if not isinstance(value, dict) or not {"prompt", "completion"} <= value.keys():
        return False
    return all(
        (price == [] if field == "overrides" else _is_zero(price))
        for field, price in value.items()
    )


def _openrouter(
    path: str, *, payload: dict[str, Any] | None = None, key: str | None = None
) -> dict[str, Any]:
    # Direct HTTPS: no environment proxy, redirects, model fallback, or retry.
    headers = {"Accept": "application/json"}
    body = None
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers.update(
            {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "X-OpenRouter-Metadata": "enabled",
            }
        )
    timeout = _HOSTED_TIMEOUT_SEC if payload is not None else 10
    deadline = time.monotonic() + timeout
    try:
        with closing(
            http.client.HTTPSConnection("openrouter.ai", timeout=timeout)
        ) as connection:
            connection.request("GET" if body is None else "POST", path, body, headers)
            socket = connection.sock
            response = connection.getresponse()
            if response.status != 200:
                raise _Unmeasured(f"OpenRouter request failed (HTTP {response.status})")
            data = bytearray()
            while len(data) <= _ROUTER_LIMIT:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _Unmeasured("OpenRouter response timed out")
                if socket is not None:
                    socket.settimeout(remaining)
                part = response.read1(min(65536, _ROUTER_LIMIT + 1 - len(data)))
                if not part:
                    break
                data.extend(part)
    except (OSError, http.client.HTTPException, ValueError):
        raise _Unmeasured("OpenRouter transport failed") from None
    if len(data) > _ROUTER_LIMIT:
        raise _Unmeasured("OpenRouter response exceeded its size limit")
    return _json_object(bytes(data), "OpenRouter")


def _verify_route(
    model: str, slug: str, catalog: dict[str, Any], endpoints: dict[str, Any]
) -> _Route:
    entries = catalog.get("data")
    if not isinstance(entries, list):
        raise _Unmeasured("OpenRouter model catalog is malformed")
    models = [
        row for row in entries if isinstance(row, dict) and row.get("id") == model
    ]
    if len(models) != 1:
        raise _Unmeasured("Requested free model is not in the OpenRouter catalog")
    entry = models[0]
    architecture = entry.get("architecture")
    paths = entry.get("endpoints")
    if (
        not isinstance(architecture, dict)
        or not all(
            isinstance(architecture.get(field), list) and "text" in architecture[field]
            for field in ("input_modalities", "output_modalities")
        )
        or not isinstance(paths, list)
        or _CHAT not in paths
        or not _free_pricing(entry.get("pricing"))
    ):
        raise _Unmeasured(
            "Requested model is not a verified zero-price text chat model"
        )
    endpoint_data = endpoints.get("data")
    if not isinstance(endpoint_data, dict) or endpoint_data.get("id") != model:
        raise _Unmeasured("OpenRouter free endpoint catalog does not match the model")
    rows = endpoint_data.get("endpoints")
    if not isinstance(rows, list):
        raise _Unmeasured("OpenRouter endpoint list is malformed")
    matches = [
        row
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("tag"), str)
        and (row["tag"] == slug or row["tag"].startswith(f"{slug}/"))
    ]
    if len(matches) != 1 or matches[0]["tag"] != slug:
        raise _Unmeasured("The declared provider slug does not pin one model endpoint")
    match = matches[0]
    provider = match.get("provider_name")
    params = match.get("supported_parameters")
    if (
        match.get("model_id") != model
        or not _free_pricing(match.get("pricing"))
        or not isinstance(provider, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._()/+-]{0,79}", provider)
        or not isinstance(params, list)
        or "max_tokens" not in params
    ):
        raise _Unmeasured("The declared provider endpoint is not verified free")
    return _Route(model, slug, provider)


def _model_request(route: _Route, role: str, view: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": route.model,
        "provider": {
            "only": [route.slug],
            "order": [route.slug],
            "allow_fallbacks": False,
            "require_parameters": True,
            "max_price": {"prompt": "0", "completion": "0", "request": "0"},
        },
        "stream": False,
        "max_tokens": 2048 + 80 * len(view["cases"]),
        "messages": [
            {
                "role": "system",
                "content": _RULES[role]
                + " Output only a JSON object. Do not include explanations.",
            },
            {"role": "user", "content": json.dumps(view, separators=(",", ":"))},
        ],
    }


def _owner_directory(raw: str, logs: Path, environment: SeatEnvironment) -> Path:
    directory = Path(raw)
    if not directory.is_absolute() or directory.resolve(strict=True) != directory:
        raise ValueError("Invalid owner directory")

    roots = {logs}
    if logs.name == "agent" and logs.parent.parent.name == "seats":
        roots.add(logs.absolute().parents[3])
    project = getattr(environment, "project", None)
    for scope in (environment, project):
        trial = getattr(getattr(scope, "trial_paths", None), "trial_dir", None)
        if isinstance(trial, Path):
            roots.add(trial)
            if scope is project:
                roots.add(trial.parent)
        source = getattr(scope, "environment_dir", None)
        if isinstance(source, Path):
            roots.add(source)
            if source.name == "environment":
                roots.add(source.parent)
        for mount in getattr(scope, "_host_mounts", ()):
            if mount["type"] == "bind":
                roots.add(Path(mount["source"]))

    # Compare filesystem identities too: different spellings can name the same
    # directory on a case-insensitive host.
    pin = directory.stat()
    ancestors = {
        (info.st_dev, info.st_ino) for info in (p.stat() for p in directory.parents)
    }
    for path in roots:
        root = path.resolve()
        for part in (root, *root.parents):
            try:
                info = part.stat()
            except FileNotFoundError:
                continue
            if os.path.samestat(info, pin) or (
                part == root and (info.st_dev, info.st_ino) in ancestors
            ):
                raise ValueError("Owner directory overlaps a native input or output")
    return directory


def _captured_chat(
    request: dict[str, Any],
    key: str,
    raw_dir: str,
    trial: UUID | None,
    seat: str,
    logs: Path,
    environment: SeatEnvironment,
) -> tuple[dict[str, Any], str]:
    try:
        # Frozen arrival and default hosted adapters need only this module.
        if __package__:
            from .workbench.generation_details import generation_id
            from .workbench.owner_journal import OwnerJournal
        else:
            from workbench.generation_details import generation_id
            from workbench.owner_journal import OwnerJournal

        if type(trial) is not UUID:
            raise ValueError("Missing native trial UUID")
        directory = _owner_directory(raw_dir, logs, environment)
        journal = OwnerJournal.bind(directory)
    except (ImportError, LookupError, OSError, RuntimeError, TypeError, ValueError):
        raise _OwnerUnavailable("Hosted owner capture is unavailable") from None

    status = "no_generation_id"
    try:
        result = _openrouter(_CHAT, payload=request, key=key)
        returned_id = result.get("id")
        if generation_id(returned_id):
            try:
                journal.append(str(trial), seat, returned_id)
                status = "recorded"
            except (OSError, RuntimeError, ValueError):
                status = "write_failed"
    finally:
        try:
            journal.close()
        except (OSError, RuntimeError, ValueError):
            status = "write_failed"
    return result, status


def _count(value: object) -> int | None:
    if type(value) is int and value >= 0:
        return value
    return None


def _decision(
    result: dict[str, Any], route: _Route, context: AgentContext
) -> dict[str, Any]:
    served = result.get("model")
    if result.get("error") is not None or served not in (
        route.model,
        route.model.removesuffix(_FREE_SUFFIX),
    ):
        raise _Unmeasured("OpenRouter served an error or an undeclared model")
    metadata = result.get("openrouter_metadata")
    endpoints = metadata.get("endpoints") if isinstance(metadata, dict) else None
    available = endpoints.get("available") if isinstance(endpoints, dict) else None
    if (
        not isinstance(metadata, dict)
        or metadata.get("requested") != route.model
        or type(metadata.get("attempt")) is not int
        or metadata["attempt"] != 1
        or metadata.get("is_byok") is not False
        or not isinstance(available, list)
        or [
            endpoint.get("provider")
            for endpoint in available
            if isinstance(endpoint, dict) and endpoint.get("selected") is True
        ]
        != [route.provider]
        or result.get("provider", route.provider) != route.provider
    ):
        raise _Unmeasured("OpenRouter did not attest the declared provider route")
    if context.metadata is not None:
        context.metadata.update({"served_model": served, "provider": route.provider})
    usage = result.get("usage")
    if usage is not None and not isinstance(usage, dict):
        raise _Unmeasured("OpenRouter usage record was malformed")
    if isinstance(usage, dict):
        cost = usage.get("cost")
        if cost is not None and (
            type(cost) not in (int, float, Decimal) or not _is_zero(cost)
        ):
            raise _Unmeasured("OpenRouter reported a nonzero or invalid charge")
        tools = usage.get("server_tool_use_details")
        if tools is not None and (
            not isinstance(tools, dict)
            or any(not _is_zero(count) for count in tools.values())
        ):
            raise _Unmeasured("OpenRouter reported server tool use")
        if usage.get("is_byok", False) is not False:
            raise _Unmeasured("OpenRouter reported a BYOK request")
        context.cost_usd = 0.0 if cost is not None else None
        context.n_input_tokens = _count(usage.get("prompt_tokens"))
        context.n_output_tokens = _count(usage.get("completion_tokens"))
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict):
            cache = _count(details.get("cached_tokens"))
            if cache is not None and (
                context.n_input_tokens is None or cache <= context.n_input_tokens
            ):
                context.n_cache_tokens = cache
        if (
            context.n_input_tokens is not None
            and context.n_output_tokens is not None
            and context.n_cache_tokens is not None
        ):
            context.model_usage = {
                f"openrouter/{served}": ModelUsage(
                    n_input_tokens=context.n_input_tokens,
                    n_output_tokens=context.n_output_tokens,
                    n_cache_tokens=context.n_cache_tokens,
                    cost_usd=context.cost_usd,
                )
            }
    choices = result.get("choices")
    if (
        not isinstance(choices, list)
        or len(choices) != 1
        or not isinstance(choices[0], dict)
    ):
        raise _Unmeasured("OpenRouter did not return one decision")
    choice = choices[0]
    message = choice.get("message")
    if (
        choice.get("finish_reason") != "stop"
        or choice.get("error") is not None
        or not isinstance(message, dict)
        or message.get("role") != "assistant"
        or message.get("tool_calls") not in (None, [])
        or message.get("function_call") is not None
        or message.get("refusal") is not None
        or not isinstance(message.get("content"), str)
    ):
        raise _Unmeasured("OpenRouter decision was interrupted or was not plain text")
    return _json_object(message["content"], "model decision")


class SeatAgent(BaseAgent):
    """A host adapter with access to only the native seat's view and submit endpoints."""

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        policy: str,
        **kwargs: Any,
    ) -> None:
        if policy not in ("arrival", "hosted"):
            raise ValueError("Unknown SeatAgent policy")
        if kwargs.get("extra_env"):
            raise ValueError("SeatAgent accepts no participant environment variables")
        self._policy = policy
        self._requested_model = None
        self._provider_slug = None
        if policy == "hosted":
            model = os.environ.get("SEATLAB_FREE_MODEL", "")
            provider = os.environ.get("SEATLAB_FREE_PROVIDER", "")
            if not _MODEL_SLUG.fullmatch(model) or not _PROVIDER_SLUG.fullmatch(
                provider
            ):
                raise ValueError(
                    "Hosted requires SEATLAB_FREE_MODEL and SEATLAB_FREE_PROVIDER"
                )
            self._requested_model = model
            self._provider_slug = provider
        declared = (
            f"openrouter/{self._requested_model}" if self._requested_model else None
        )
        if model_name not in (None, declared) or kwargs.pop(
            "skip_model_parsing", False
        ):
            raise ValueError("SeatAgent cannot override the declared model identity")
        super().__init__(logs_dir=logs_dir, model_name=declared, **kwargs)

    @staticmethod
    @override
    def name() -> str:
        return "seatlab-dispatch-handoff"

    @override
    def version(self) -> str:
        return "1.0.0"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        if (
            not isinstance(environment, SeatEnvironment)
            or environment.service not in _RULES
        ):
            raise _Unmeasured(
                "SeatAgent requires the native inventory or dispatch seat"
            )
        role = environment.service
        context.metadata = {
            "schema": "seatlab.seat/v1",
            "policy": self._policy,
            "role": role,
            "decision_source": "script" if self._policy == "arrival" else "model",
            "model_turns": 0,
        }
        owners_dir = (
            os.environ.get("SEATLAB_OPENROUTER_OWNERS_DIR")
            if self._policy == "hosted"
            else None
        )
        trial = self.context_id if owners_dir is not None else None
        view = await _view(environment)
        cases = _checked_view(view, role)
        # Match the capsule encoding without adding a dependency to the native adapter.
        view_bytes = json.dumps(
            view,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            ensure_ascii=True,
        ).encode("utf-8")
        view_sha256 = sha256(view_bytes).hexdigest()
        if self._policy == "arrival":
            context.n_input_tokens = context.n_cache_tokens = (
                context.n_output_tokens
            ) = 0
            context.cost_usd = 0.0
            submission = _arrival(role, cases)
        else:
            model, slug = self._requested_model, self._provider_slug
            key = os.environ.get("OPENROUTER_API_KEY")
            if model is None or slug is None or not key:
                raise _Unmeasured("Hosted SeatAgent requires a host OPENROUTER_API_KEY")
            context.metadata["requested_model"] = model
            catalog = await asyncio.to_thread(_openrouter, "/api/v1/models")
            endpoints = await asyncio.to_thread(
                _openrouter, f"/api/v1/models/{model}/endpoints"
            )
            route = _verify_route(model, slug, catalog, endpoints)
            request = _model_request(route, role, view)
            context.metadata["model_turns"] = 1
            if owners_dir is None:
                result = await asyncio.to_thread(
                    _openrouter, _CHAT, payload=request, key=key
                )
            else:
                try:
                    result, capture = await asyncio.to_thread(
                        _captured_chat,
                        request,
                        key,
                        owners_dir,
                        trial,
                        role,
                        self.logs_dir,
                        environment,
                    )
                except _OwnerUnavailable:
                    context.metadata["model_turns"] = 0
                    raise
                context.metadata["owner_capture"] = capture
            submission = _decision(result, route, context)
        _checked_submission(submission, role, len(cases))
        status, body = await _world_request(
            environment, "/submit", submission, view_sha256=view_sha256
        )
        if status != 200:
            raise _Unmeasured(f"World decision rejected (HTTP {status})")
        acknowledgment = _json_object(body, "world submission")
        if (
            not _keys(acknowledgment, "accepted")
            or acknowledgment["accepted"] is not True
        ):
            raise _Unmeasured("World did not acknowledge the decision")
