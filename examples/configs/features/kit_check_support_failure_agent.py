import asyncio
import errno
import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic_ns, time_ns
from typing import Any, Literal, override

from harbor.agents.base import BaseAgent
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from deepseek_stream_transport import DeepSeekGateway
from kit_check_codex_agent import _action, _observe, _prompt, _setup_seat, _submit
from kit_check_deepseek_agent import _count

Condition = Literal["healthy", "worker-timeout", "worker-sigkill"]
CONDITIONS = ("healthy", "worker-timeout", "worker-sigkill")
MODEL = "deepseek-flash"
PROVIDER = "deepseek-host"
CALL_IDS = {"left": "left-report", "right": "right-report", "builder": "builder-choice"}
AUDIT = "left-audit"
FaultCallId = Literal["left-report", "left-audit"]
FAULT_CALL_IDS = (CALL_IDS["left"], AUDIT)
_TRANSPORT = Path(__file__).with_name("deepseek_stream_transport.py").resolve()
_CLIENT_WAIT_SEC = 100
_GATE_WAIT_SEC = 110
_POLL_SEC = 0.002
_MAX_WORKER_EVENTS_BYTES = 65_536
_USAGE_FIELDS = {
    "n_input_tokens": "prompt_tokens",
    "n_cache_tokens": "prompt_cache_hit_tokens",
    "n_output_tokens": "completion_tokens",
}


def stamp() -> dict[str, int]:
    return {"monotonic_ns": monotonic_ns(), "wall_time_ns": time_ns()}


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def record_event(path: Path, event: str, **fields: Any) -> dict[str, Any]:
    row = {"event": event, **stamp(), **fields}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(_json(row) + "\n")
    return row


def _error(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


@dataclass
class CaseExchange:
    directory: Path
    condition: Condition
    fault_call_id: FaultCallId
    gateway: DeepSeekGateway
    audit_ended: asyncio.Event = field(default_factory=asyncio.Event)
    intervention: dict[str, Any] | None = None
    worker_attempts: dict[str, dict[str, Any]] = field(default_factory=dict)
    worker_records: dict[str, dict[str, Any]] = field(default_factory=dict)
    worker_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    gate: dict[str, Any] | None = None


_CASE: CaseExchange | None = None


def prepare_case(
    *,
    directory: Path,
    condition: Condition,
    fault_call_id: FaultCallId,
    gateway: DeepSeekGateway,
) -> CaseExchange:
    global _CASE
    if _CASE is not None:
        raise RuntimeError("A Kit Check support failure trial is already prepared")
    if fault_call_id not in FAULT_CALL_IDS:
        raise ValueError("Invalid Kit Check fault call")
    _CASE = CaseExchange(
        directory=directory,
        condition=condition,
        fault_call_id=fault_call_id,
        gateway=gateway,
    )
    return _CASE


def clear_case(exchange: CaseExchange) -> None:
    global _CASE
    if _CASE is not exchange:
        raise RuntimeError("Cannot clear a different Kit Check exchange")
    _CASE = None


def _process_identity(pid: int, record: dict[str, Any]) -> None:
    argv = [
        "/bin/ps",
        "-o",
        "pid=",
        "-o",
        "ppid=",
        "-o",
        "pgid=",
        "-o",
        "lstart=",
        "-p",
        str(pid),
    ]
    result = subprocess.run(
        argv,
        env={"LC_ALL": "C", "TZ": "UTC"},
        capture_output=True,
        text=True,
        timeout=2,
    )
    record["startup_ps"] = {
        "argv": argv,
        "return_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        **stamp(),
    }
    result.check_returncode()
    observed_pid, ppid, pgid, started = result.stdout.strip().split(maxsplit=3)
    if int(observed_pid) != pid or int(ppid) != os.getpid() or int(pgid) != pid:
        raise RuntimeError("Worker does not have the expected parent and process group")
    record.update(ppid=int(ppid), pgid=int(pgid), start_identity=started)


def _worker_events(path: Path) -> dict[str, dict[str, Any]]:
    try:
        with path.open("rb") as stream:
            data = stream.read(_MAX_WORKER_EVENTS_BYTES + 1)
    except FileNotFoundError:
        return {}
    if len(data) > _MAX_WORKER_EVENTS_BYTES:
        raise RuntimeError("Worker event file exceeds its size bound")
    return {row["event"]: row for row in map(json.loads, data.split(b"\n")[:-1])}


def _payload(prompt: str, *, audit: bool) -> dict[str, Any]:
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.6,
        "thinking": {"type": "disabled"},
        "max_tokens": 4096 if audit else 1024,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if not audit:
        payload["response_format"] = {"type": "json_object"}
    return payload


def _audit_prompt(observation: dict[str, Any], report: dict[str, Any]) -> str:
    return (
        "Audit the left review that was already submitted in Kit Check. For each of the 24 "
        "cases in order, name both observed kits and both submitted kits, then give a short "
        "explanation of whether the submitted pair matches the observation. Include 2 complete "
        "sentences for each case. After the 24 checks, summarize any discrepancies. Do not "
        "propose replacement reports or builder choices. Return only the audit, without tools.\n"
        f"Observation: {_json(observation)}\nSubmitted report: {_json(report)}"
    )


class _Worker:
    def __init__(self, exchange: CaseExchange, call_id: str) -> None:
        self.exchange = exchange
        self.call_id = call_id
        self.directory = exchange.directory / "worker" / call_id
        self.events_path = self.directory / "events.jsonl"
        self.result_path = self.directory / "result.json"
        self.mechanism: str | None = None
        self.intervention_signal: dict[str, int] | None = None
        self.signal_gateway_live_checked: dict[str, int] | None = None
        self.terminal_observed: dict[str, int] | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self.record: dict[str, Any] = {
            "call_id": call_id,
            "pid": None,
            "ppid": None,
            "pgid": None,
            "start_identity": None,
            "startup_ps": None,
            "absence_probe": None,
            "spawned_ns": None,
            "spawned_wall_time_ns": None,
            "reaped_ns": None,
            "reaped_wall_time_ns": None,
            "identity_absent_ns": None,
            "identity_absent_wall_time_ns": None,
            "exit_code": None,
            "cleanup_signal_ns": None,
            "cleanup_signal_wall_time_ns": None,
            "cleanup_signal_target": None,
            "cleanup_error": None,
            "error": None,
        }

    def _spawn(self, prompt: str) -> None:
        inject_timeout = (
            self.call_id == self.exchange.fault_call_id
            and self.exchange.condition == "worker-timeout"
        )
        config = {
            "gateway_url": self.exchange.gateway.url,
            "gateway_token": self.exchange.gateway.token,
            "call_id": self.call_id,
            "payload": _payload(prompt, audit=self.call_id == AUDIT),
            "result_path": str(self.result_path),
            "events_path": str(self.events_path),
            "mode": "timeout" if inject_timeout else "normal",
            "timeout_after_delta_sec": 0.05,
        }
        config_path = self.directory / "config.json"
        with os.fdopen(
            os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
        ) as setup:
            setup.write(_json(config) + "\n")
        with (
            (self.directory / "stdout.txt").open("xb") as stdout,
            (self.directory / "stderr.txt").open("xb") as stderr,
        ):
            self.process = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    str(_TRANSPORT),
                    "worker",
                    "--config",
                    str(config_path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                env={"PATH": os.defpath, "LANG": "C.UTF-8"},
                start_new_session=True,
            )
        started = stamp()
        self.record.update(
            pid=self.process.pid,
            spawned_ns=started["monotonic_ns"],
            spawned_wall_time_ns=started["wall_time_ns"],
        )
        self.exchange.worker_records[self.call_id] = self.record
        try:
            _process_identity(self.process.pid, self.record)
        except (subprocess.CalledProcessError, ProcessLookupError):
            if self.process.poll() is None:
                raise
            self.record["error"] = "Worker terminated before its identity could be read"
        write_json(self.directory / "process.json", self.record)

    def _watch_fault_call(self) -> None:
        assert self.process is not None and self.call_id == self.exchange.fault_call_id
        if self.intervention_signal is not None or self.terminal_observed is not None:
            return
        if "first_model_delta" not in _worker_events(self.events_path):
            return
        if not self.exchange.gateway.is_live(self.call_id):
            self.terminal_observed = stamp()
            return
        if (
            self.exchange.condition != "worker-sigkill"
            or self.process.poll() is not None
        ):
            return
        pid = self.process.pid
        if self.record["pgid"] != pid:
            raise RuntimeError("Refusing to signal an unverified worker group")
        if not self.exchange.gateway.is_live(self.call_id):
            self.terminal_observed = stamp()
            return
        live_checked = stamp()
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        self.signal_gateway_live_checked = live_checked
        self.intervention_signal = stamp()
        self.mechanism = "sigkill"

    async def _wait(self) -> int:
        assert self.process is not None
        async with asyncio.timeout(_CLIENT_WAIT_SEC):
            while (code := self.process.poll()) is None:
                if self.call_id == self.exchange.fault_call_id:
                    self._watch_fault_call()
                await asyncio.sleep(_POLL_SEC)
        return code

    def _cleanup(self) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            return
        try:
            if os.getpgid(process.pid) == process.pid:
                os.killpg(process.pid, signal.SIGKILL)
                target = "process_group"
            else:
                process.kill()
                target = "pid"
        except ProcessLookupError:
            pass
        else:
            sent = stamp()
            self.record.update(
                cleanup_signal_ns=sent["monotonic_ns"],
                cleanup_signal_wall_time_ns=sent["wall_time_ns"],
                cleanup_signal_target=target,
            )
        process.wait(timeout=3)

    def _record_reap(self) -> None:
        if self.process is None or self.process.returncode is None:
            return
        reaped = stamp()
        self.record.update(
            exit_code=self.process.returncode,
            reaped_ns=reaped["monotonic_ns"],
            reaped_wall_time_ns=reaped["wall_time_ns"],
        )
        probe: dict[str, str | int | None] = {
            "method": "kill(pid, 0)",
            "pid": self.process.pid,
        }
        try:
            os.kill(self.process.pid, 0)
        except ProcessLookupError:
            absent = stamp()
            probe.update(outcome="ESRCH", errno=errno.ESRCH, **absent)
            self.record.update(
                identity_absent_ns=absent["monotonic_ns"],
                identity_absent_wall_time_ns=absent["wall_time_ns"],
            )
        except PermissionError:
            probe.update(outcome="EPERM", errno=errno.EPERM, **stamp())
            self.record["cleanup_error"] = (
                "Cannot confirm absence of the reaped worker PID"
            )
        else:
            probe.update(outcome="present", errno=None, **stamp())
            self.record["cleanup_error"] = "The reaped worker PID is present"
        self.record["absence_probe"] = probe

    def _record_intervention(self) -> None:
        events = _worker_events(self.events_path)
        fired = events.get("timeout_fired")
        if (
            self.exchange.condition == "worker-timeout"
            and fired
            and self.record["exit_code"] == 124
        ):
            self.mechanism = "worker_deadline"
        completed = events.get("response_complete")
        gateway_events = _worker_events(
            self.exchange.directory / "gateway" / self.call_id / "events.jsonl"
        )
        gateway_terminal = gateway_events.get("terminal_delta")
        terminal = gateway_terminal or self.terminal_observed or completed
        fault = self.intervention_signal or fired
        terminal_before = terminal is not None and (
            fault is None or terminal["monotonic_ns"] <= fault["monotonic_ns"]
        )
        error = self.record["error"] or self.record["cleanup_error"]
        if error is None and self.exchange.condition != "healthy":
            expected_code = 124 if self.exchange.condition == "worker-timeout" else -9
            if self.mechanism is None or self.record["exit_code"] != expected_code:
                error = (
                    "The scheduled injection did not produce the expected worker exit"
                )
            elif terminal_before:
                error = "The stream was terminal before the intervention"
        elif error is None and self.record["exit_code"] != 0:
            error = "The healthy target worker did not exit successfully"
        evidence = {
            "condition": self.exchange.condition,
            "target_seat": "left",
            "call_id": self.call_id,
            "mechanism": self.mechanism,
            "signal_gateway_live": True
            if self.signal_gateway_live_checked is not None
            else None,
            "signal_number": int(signal.SIGKILL)
            if self.intervention_signal is not None
            else None,
            "stream_terminal_before_intervention": self.exchange.condition != "healthy"
            and terminal_before,
            "terminal_observation_source": (
                "gateway_terminal_delta"
                if gateway_terminal is not None
                else "gateway_is_live"
                if self.terminal_observed is not None
                else "worker_response_complete"
                if completed is not None
                else None
            ),
            "error": error,
            **{
                key: self.record[key]
                for key in (
                    "pid",
                    "ppid",
                    "pgid",
                    "start_identity",
                    "exit_code",
                    "reaped_ns",
                    "reaped_wall_time_ns",
                    "identity_absent_ns",
                    "identity_absent_wall_time_ns",
                    "cleanup_signal_ns",
                )
            },
        }
        for name, event in (
            ("first_model_delta", events.get("first_model_delta")),
            ("signal", self.intervention_signal),
            ("signal_gateway_live_checked", self.signal_gateway_live_checked),
            ("deadline_armed", events.get("deadline_armed")),
            ("timeout_fired", fired),
            ("terminal_observed", terminal),
        ):
            evidence[f"{name}_ns"] = event["monotonic_ns"] if event else None
            evidence[f"{name}_wall_time_ns"] = event["wall_time_ns"] if event else None
        write_json(self.exchange.directory / "intervention.json", evidence)
        self.exchange.intervention = evidence

    async def run(self, prompt: str) -> dict[str, Any]:
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=False)
        self.exchange.worker_attempts[self.call_id] = self.record
        code = None
        result = None
        try:
            self._spawn(prompt)
            code = await self._wait()
        except BaseException as error:
            self.record["error"] = _error(error)
            try:
                self._cleanup()
            except Exception as cleanup_error:
                self.record["cleanup_error"] = _error(cleanup_error)
            raise
        finally:
            self._record_reap()
            write_json(self.directory / "process.json", self.record)
            if self.call_id == self.exchange.fault_call_id:
                self._record_intervention()
            if self.result_path.is_file():
                try:
                    parsed = json.loads(self.result_path.read_bytes())
                    if (
                        isinstance(parsed, dict)
                        and parsed.get("call_id") == self.call_id
                    ):
                        self.exchange.worker_results[self.call_id] = parsed
                        result = parsed
                except (OSError, ValueError):
                    if code == 0:
                        raise
        if code != 0:
            raise NonZeroAgentExitCodeError(
                f"DeepSeek host worker {self.call_id} exited {code}"
            )
        if (
            not isinstance(result, dict)
            or result.get("call_id") != self.call_id
            or result.get("request_model") != MODEL
            or result.get("stream_done") is not True
            or result.get("error") is not None
            or not isinstance(result.get("content"), str)
        ):
            raise RuntimeError(
                "DeepSeek host worker did not record a complete response"
            )
        return result


class SupportFailureKitAgent(BaseAgent):
    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        role: str,
        condition: Condition,
        fault_call_id: FaultCallId,
        **kwargs: Any,
    ) -> None:
        if (
            role not in CALL_IDS
            or condition not in CONDITIONS
            or fault_call_id not in FAULT_CALL_IDS
        ):
            raise ValueError("Invalid Kit Check support failure seat options")
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        if (self._parsed_model_provider, self._parsed_model_name) != (PROVIDER, MODEL):
            raise ValueError(
                f"Kit Check support failure seats require {PROVIDER}/{MODEL}"
            )
        if (
            _CASE is None
            or _CASE.condition != condition
            or _CASE.fault_call_id != fault_call_id
        ):
            raise RuntimeError(
                "No matching Kit Check support failure trial is prepared"
            )
        self._exchange = _CASE
        self._role = role
        self._directory = _CASE.directory / "seat" / role

    @staticmethod
    @override
    def _parse_model_name_for_connection(
        model_name: str,
        *,
        skip_model_parsing: bool = False,
    ) -> tuple[str | None, str]:
        provider, separator, model = model_name.partition("/")
        if not skip_model_parsing and separator and provider == PROVIDER:
            return provider, model
        return BaseAgent._parse_model_name_for_connection(
            model_name,
            skip_model_parsing=skip_model_parsing,
        )

    @staticmethod
    @override
    def name() -> str:
        return "kit-check-deepseek-support-failure"

    @override
    def version(self) -> str:
        return "1"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        await _setup_seat(environment)

    def _event(self, event: str, **fields: Any) -> dict[str, Any]:
        return record_event(
            self._directory / "events.jsonl", event, seat=self._role, **fields
        )

    async def _builder_gate(self) -> None:
        start = self._event("builder_gate_start")
        error = None
        try:
            async with asyncio.timeout(_GATE_WAIT_SEC):
                await self._exchange.audit_ended.wait()
            audit = self._exchange.worker_records.get(AUDIT)
            if audit is None or audit["reaped_ns"] is None:
                raise RuntimeError("The left audit worker was not reaped")
        except BaseException as failure:
            error = _error(failure)
            raise
        finally:
            finish = self._event("builder_gate_end", error=error)
            self._exchange.gate = {
                "started_ns": start["monotonic_ns"],
                "finished_ns": finish["monotonic_ns"],
                "seconds": (finish["monotonic_ns"] - start["monotonic_ns"]) / 1e9,
                "error": error,
            }

    async def _audit(self, observation: dict[str, Any], action: dict[str, Any]) -> None:
        self._event("audit_start", call_id=AUDIT)
        try:
            await _Worker(self._exchange, AUDIT).run(_audit_prompt(observation, action))
        finally:
            self._event("audit_end", call_id=AUDIT)
            self._exchange.audit_ended.set()

    def _usage(self, context: AgentContext) -> None:
        calls = [CALL_IDS[self._role]]
        if self._role == "left":
            calls.append(AUDIT)
        calls = [
            call_id for call_id in calls if call_id in self._exchange.worker_records
        ]
        by_call = {
            key: self._exchange.worker_results.get(key, {}).get("usage")
            for key in calls
        }
        complete = True
        for field_name, usage_name in _USAGE_FIELDS.items():
            counts = [
                _count(usage if isinstance(usage, dict) else {}, usage_name)
                for usage in by_call.values()
            ]
            complete = complete and all(value is not None for value in counts)
            known = [value for value in counts if value is not None]
            setattr(context, field_name, sum(known) if known else None)
        context.metadata = {
            **(context.metadata or {}),
            "model_auth": "deepseek-api-host",
            "worker_usage_by_call": by_call,
            "worker_usage_complete": complete,
            "worker_usage_totals_kind": "complete" if complete else "known_only",
        }

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        self._directory.mkdir(parents=True, exist_ok=False)
        call_id = CALL_IDS[self._role]
        self._event("seat_start", primary_call_id=call_id)
        error = None
        error_type = None
        phase = "observation"
        try:
            self._event("observation_start")
            observation = await _observe(environment, self._role)
            write_json(self._directory / "observation.json", observation)
            self._event("observation_received")
            phase = "primary_worker"
            response = await _Worker(self._exchange, call_id).run(
                _prompt(instruction, self._role, observation)
            )
            phase = "decision"
            if response.get("finish_reason") != "stop":
                raise RuntimeError("DeepSeek did not finish the Kit Check decision")
            action = _action(response["content"], self._role)
            (self._directory / "action.json").write_text(
                _json(action), encoding="utf-8"
            )
            if self._role == "builder":
                phase = "builder_audit_gate"
                await self._builder_gate()
            phase = "post"
            self._event("post_attempt", primary_call_id=call_id)
            await _submit(environment, self._role, action)
            self._event("post_accepted", primary_call_id=call_id, http_status=201)
            if self._role == "left":
                phase = "audit"
                await self._audit(observation, action)
        except BaseException as failure:
            error = _error(failure)
            error_type = type(failure).__name__
            raise
        finally:
            self._usage(context)
            self._event("seat_end", phase=phase, error=error, error_type=error_type)
