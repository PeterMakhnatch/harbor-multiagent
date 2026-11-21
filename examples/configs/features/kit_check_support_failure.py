import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tomllib
import traceback
import urllib.request
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, override
from urllib.error import HTTPError

import harbor
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.environments.docker.docker import _sanitize_docker_compose_project_name
from harbor.environments.docker.runtime import DOCKER_RUNTIME
from harbor.job import Job
from harbor.models.environment_type import EnvironmentType
from harbor.models.job.config import JobConfig, RetryConfig
from harbor.models.trial.config import EnvironmentConfig, Participant, TaskConfig
from harbor.models.trial.result import SeatResult
from harbor.publisher.packager import Packager
from harbor.trial.errors import AgentTimeoutError
from harbor.trial.hooks import TrialEvent, TrialHookEvent
from harbor.verifier.verifier import RewardFileNotFoundError

from deepseek_stream_transport import DeepSeekGateway
from kit_check_controls import HERE, ROLES
from kit_check_deepseek_agent import _calculated_cost, _count
from kit_check_support_failure_agent import (
    AUDIT,
    CALL_IDS,
    CONDITIONS,
    MODEL,
    PROVIDER,
    CaseExchange,
    Condition,
    FaultCallId,
    SupportFailureKitAgent,
    _worker_events,
    clear_case,
    prepare_case,
    record_event,
    stamp,
    write_json,
)
from kit_check_subscription import _duration, _freeze_before_verifier

_SOURCE = HERE.parents[2]
_SOURCE_BASE = "9c2278ebea6452639fe422d013c26c2333465763"
_TRANSPORT_BASE = "301862ae77fd7f801b7f1e12c7643cb53e98dcf9"
_RUNTIME_COMMIT = "bfbfe127e1e40b4dbddf503b8ca3f4914b0f40be"
_TASK_DIGEST = "sha256:8144f8e522e83870ae4a5ebd5cad749bafb8d9fa4873ef523ea148caa34e2e5b"
_OVERLAY = HERE / "kit-check-visible.override.yaml"
_FILES = (
    _OVERLAY.name,
    "codex_text_turn.py",
    "deepseek_stream_transport.py",
    "kit_check_codex_agent.py",
    "kit_check_controls.py",
    "kit_check_deepseek_agent.py",
    "kit_check_subscription.py",
    "kit_check_support_failure.py",
    "kit_check_support_failure_agent.py",
)
_BALANCE_URL = "https://api.deepseek.com/user/balance"
_MIN_BALANCE_USD = 1.50
_MAX_PROMPT_BYTES = 8192
_INPUT_RESERVE = 9216
_OUTPUT_RESERVE = 4096
_USD_RESERVE_PER_CALL = (_INPUT_RESERVE * 0.30 + _OUTPUT_RESERVE * 1.20) / 1e6
_CALLS = (*CALL_IDS.values(), AUDIT)
_SCHEDULED_TRIALS = 24
_COHORT_CAP_USD = 0.80
_EXPECTED_SERVICES = {"main", *ROLES}
_FAULT_CALL_ID: FaultCallId = "left-report"
_NATIVE_PLAY_TIMEOUT_SEC = 30.0
_TASK_PLAY_TIMEOUT_SEC = 600.0
_NATIVE_PLAY_TIMEOUT_FIELD = "agent_timeout_multiplier"
_NATIVE_PLAY_MULTIPLIER = _NATIVE_PLAY_TIMEOUT_SEC / _TASK_PLAY_TIMEOUT_SEC
_API_TIMEOUT_SEC = 90.0
_PREVIOUS_LANE_COST_USD = "0.022710012"
_CUMULATIVE_CAP_USD = "0.85"
_VERIFIER_LOG_LIMIT = 65_536


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    @override
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _balance(key: str) -> dict[str, Any]:
    if not key or any(char.isspace() for char in key):
        raise RuntimeError("The host DeepSeek API key is missing or malformed")
    request = urllib.request.Request(
        _BALANCE_URL, headers={"Authorization": f"Bearer {key}"}
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    checked = stamp()
    try:
        with opener.open(request, timeout=20) as response:
            body = response.read(4097)
            if len(body) > 4096:
                raise RuntimeError(
                    "The DeepSeek balance response exceeds the size bound"
                )
            value = json.loads(body)
    except HTTPError as error:
        error.close()
        raise RuntimeError(f"DeepSeek balance returned HTTP {error.code}") from None
    entries = value.get("balance_infos") if isinstance(value, dict) else None
    if not isinstance(entries, list):
        raise RuntimeError("The DeepSeek balance response has no balance details")
    usd = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("currency") == "USD"
    ]
    if len(usd) != 1:
        raise RuntimeError("The DeepSeek balance response must contain one USD balance")
    total = float(usd[0]["total_balance"])
    if not math.isfinite(total):
        raise RuntimeError("The DeepSeek USD balance is not finite")
    return {
        "usd": total,
        "is_available": value.get("is_available"),
        "minimum_usd": _MIN_BALANCE_USD,
        "started": checked,
        "finished": stamp(),
    }


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        check=True,
        timeout=15,
    ).stdout


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _sealed_case(
    directory: Path, condition: Condition, receipt: dict[str, Any]
) -> dict[str, Any]:
    root_text = os.environ.get("KIT_CHECK_STUDY_ROOT")
    if not root_text:
        raise RuntimeError("KIT_CHECK_STUDY_ROOT must identify the sealed study")
    root = Path(root_text).resolve(strict=True)
    registry_path = root / "cohort.json"
    data = registry_path.read_bytes()
    seal = (root / "cohort.sha256").read_text(encoding="ascii").split()
    digest = _sha256(data)
    if seal != [digest.removeprefix("sha256:"), "cohort.json"]:
        raise RuntimeError("The study registry does not match its seal")
    registry = json.loads(data)
    if registry["schema"] != "harbor-deepseek-prehandoff-cohort/v1":
        raise RuntimeError("The sealed study registry has an unexpected schema")
    required = {
        "fault_call_id": _FAULT_CALL_ID,
        "native_play_timeout_sec": _NATIVE_PLAY_TIMEOUT_SEC,
        "max_trials": _SCHEDULED_TRIALS,
        "max_calls_per_trial": len(_CALLS),
        "max_prompt_bytes": _MAX_PROMPT_BYTES,
        "reserved_input_tokens": _INPUT_RESERVE,
        "max_output_tokens": _OUTPUT_RESERVE,
        "peak_rates": {"miss": 0.30, "hit": 0.006, "out": 1.20},
        "worst_case_usd": 0.73728,
        "hard_ceiling_usd": _COHORT_CAP_USD,
        "request_balance_floor_usd": _MIN_BALANCE_USD,
        "max_retries": 0,
        "previous_lane_known_cost_usd": _PREVIOUS_LANE_COST_USD,
        "cumulative_hard_ceiling_usd": _CUMULATIVE_CAP_USD,
    }
    if any(registry.get(field) != value for field, value in required.items()):
        raise RuntimeError(
            "The sealed study has unexpected fault, deadline or budget inputs"
        )
    if Decimal(_PREVIOUS_LANE_COST_USD) + Decimal(
        str(registry["worst_case_usd"])
    ) >= Decimal(_CUMULATIVE_CAP_USD):
        raise RuntimeError("The fixed cumulative reserve exceeds the admission cap")
    if len(registry["cases"]) != _SCHEDULED_TRIALS:
        raise RuntimeError("The sealed trial count does not match the cost reserve")
    case_id = directory.name
    case = registry["cases"].get(case_id)
    if (
        case is None
        or case["condition"] != condition
        or case["output"] != f"cases/{case_id}"
        or directory != root / case["output"]
        or os.environ.get("KIT_CHECK_CASE_ID") != case_id
        or os.environ.get("KIT_CHECK_CASE_CONDITION") != condition
        or os.environ.get("KIT_CHECK_CASE_OUTPUT") != str(root / case["output"])
    ):
        raise RuntimeError("The condition and output do not match a sealed trial")
    receipt.update(
        case_id=case_id, cohort_path=str(registry_path), cohort_digest=digest
    )
    return registry


def _pins(task: Path, registry: dict[str, Any], receipt: dict[str, Any]) -> None:
    runtime_root = Path(registry["runtime_root"])
    runtime_python = Path(registry["python"])
    receipt.update(runtime_path=str(runtime_root), runtime_python=str(runtime_python))
    if (
        not runtime_root.is_absolute()
        or not runtime_python.is_absolute()
        or registry["runtime_commit"] != _RUNTIME_COMMIT
    ):
        raise RuntimeError(
            "The sealed study does not identify the pinned native runtime"
        )
    executable = Path(sys.executable)
    if (
        executable.parent.resolve() != runtime_python.parent.resolve()
        or executable.name != runtime_python.name
    ):
        raise RuntimeError("The runner is not using the pinned native Python")
    if not harbor.__file__ or not Path(harbor.__file__).resolve().is_relative_to(
        runtime_root.resolve() / "src"
    ):
        raise RuntimeError(
            "The imported Harbor package is not from the pinned native checkout"
        )
    runtime_commit = _git(runtime_root, "rev-parse", "HEAD").decode("ascii").strip()
    receipt["runtime_commit"] = runtime_commit
    if runtime_commit != _RUNTIME_COMMIT or "freeze" not in SeatResult.model_fields:
        raise RuntimeError(
            "The native Harbor runtime or seat freeze schema is not pinned"
        )
    _git(runtime_root, "diff", "--quiet", "HEAD", "--")
    source_commit = _git(_SOURCE, "rev-parse", "HEAD").decode("ascii").strip()
    receipt["source_commit"] = source_commit
    if (
        source_commit != registry["source_commit"]
        or source_commit != os.environ.get("KIT_CHECK_SOURCE_COMMIT")
        or Path(registry["source_root"]).resolve() != _SOURCE
    ):
        raise RuntimeError(
            "The study source does not match the launcher's sealed commit"
        )
    _git(_SOURCE, "merge-base", "--is-ancestor", _SOURCE_BASE, source_commit)
    if source_commit == _SOURCE_BASE:
        raise RuntimeError("The study source has not been committed")
    source_files = [(HERE / name).relative_to(_SOURCE).as_posix() for name in _FILES]
    _git(_SOURCE, "ls-files", "--error-unmatch", "--", *source_files)
    _git(_SOURCE, "diff", "--quiet", "HEAD", "--", *source_files)
    base_paths = [
        path
        for path in source_files
        if Path(path).name
        not in {
            "deepseek_stream_transport.py",
            "kit_check_support_failure.py",
            "kit_check_support_failure_agent.py",
        }
    ]
    _git(_SOURCE, "diff", "--quiet", _SOURCE_BASE, "HEAD", "--", *base_paths)
    transport = (HERE / "deepseek_stream_transport.py").relative_to(_SOURCE)
    _git(_SOURCE, "diff", "--quiet", _TRANSPORT_BASE, "HEAD", "--", str(transport))
    overlay_digest = _sha256(_OVERLAY.read_bytes())
    original_overlay = _git(
        _SOURCE, "show", f"{_SOURCE_BASE}:{_OVERLAY.relative_to(_SOURCE)}"
    )
    receipt["overlay_digest"] = overlay_digest
    if (
        overlay_digest != registry["overlay_digest"]
        or Path(registry["overlay_path"]).resolve() != _OVERLAY
        or overlay_digest != _sha256(original_overlay)
    ):
        raise RuntimeError("The original visible Compose overlay has changed")
    digest, _ = Packager.compute_content_hash(task)
    receipt["task_digest"] = f"sha256:{digest}"
    if (
        receipt["task_digest"] != _TASK_DIGEST
        or receipt["task_digest"] != registry["task_digest"]
        or task != Path(registry["task_path"]).resolve()
    ):
        raise RuntimeError("The Kit Check task does not match the pinned content")
    task_timeout = tomllib.loads((task / "task.toml").read_text(encoding="utf-8"))[
        "agent"
    ]["timeout_sec"]
    if task_timeout != _TASK_PLAY_TIMEOUT_SEC:
        raise RuntimeError("Unexpected input to the native shared play deadline")
    receipt["native_task_agent_timeout_sec"] = task_timeout


def _elapsed(start: int | None, finish: int | None) -> float | None:
    return (
        (finish - start) / 1e9
        if start is not None and finish is not None and finish >= start
        else None
    )


def _gate(cli_started: dict[str, int]) -> dict[str, Any]:
    queued = os.environ.get("KIT_CHECK_GATE_ENQUEUED_MONOTONIC_NS")
    admitted = os.environ.get("KIT_CHECK_GATE_ADMITTED_MONOTONIC_NS")
    enqueued_ns = int(queued) if queued is not None else None
    admitted_ns = int(admitted) if admitted is not None else None
    if (enqueued_ns is None) != (admitted_ns is None):
        raise RuntimeError(
            "Docker gate evidence requires both queue and admission timestamps"
        )
    if (
        enqueued_ns is not None
        and admitted_ns is not None
        and not 0 < enqueued_ns <= admitted_ns <= cli_started["monotonic_ns"]
    ):
        raise RuntimeError("Docker gate timestamps are not in admission order")
    return {
        "enqueued_ns": enqueued_ns,
        "admitted_ns": admitted_ns,
        "cli_started_ns": cli_started["monotonic_ns"],
        "gate_wait_seconds": _elapsed(enqueued_ns, admitted_ns),
        "after_admission_to_cli_seconds": _elapsed(
            admitted_ns, cli_started["monotonic_ns"]
        ),
        "evidence": "launcher_timestamps"
        if admitted_ns is not None
        else "cli_start_only",
    }


def _error(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def _record_error(
    receipt: dict[str, Any],
    stage: str,
    error: BaseException | str,
    *,
    stop: bool = False,
) -> None:
    receipt["errors"][stage] = error if isinstance(error, str) else _error(error)
    if stop:
        receipt["admission_stop_reasons"].append(stage)


def _container_snapshot(project: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "started": stamp(),
        "finished": None,
        "containers": None,
        "argv": None,
        "return_code": None,
        "stdout": None,
        "stderr": None,
        "error": None,
    }
    try:
        command = DOCKER_RUNTIME.engine_cmd(
            "ps",
            "--all",
            "--no-trunc",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            '{{.ID}}\t{{.Label "com.docker.compose.service"}}\t{{.State}}',
        )
        result["argv"] = command
        current = subprocess.run(command, capture_output=True, text=True, timeout=8)
        result.update(
            return_code=current.returncode, stdout=current.stdout, stderr=current.stderr
        )
        current.check_returncode()
        rows = current.stdout.splitlines()
        if len(rows) > 16:
            raise RuntimeError("The trial Compose project returned too many containers")
        containers = []
        for row in rows:
            container_id, service, state = row.split("\t")
            if (
                re.fullmatch(r"[a-f0-9]{64}", container_id) is None
                or not service
                or not state
            ):
                raise RuntimeError("Unexpected Docker container details")
            containers.append({"id": container_id, "service": service, "state": state})
        result["containers"] = containers
    except Exception as error:
        result["error"] = _error(error)
    finally:
        result["finished"] = stamp()
    return result


class _NativeObserver:
    def __init__(
        self, directory: Path, exchange: CaseExchange, receipt: dict[str, Any]
    ) -> None:
        self.directory = directory
        self.journal = directory / "lifecycle.jsonl"
        self.exchange = exchange
        self.receipt = receipt
        self.docker: dict[str, Any] = {
            "compose_project": None,
            "during": None,
            "after": None,
            "observed_container_ids": [],
            "absent_container_ids": None,
            "all_observed_containers_absent": None,
            "all_expected_services_running": None,
        }
        receipt["docker_teardown"] = self.docker

    async def hook(self, event: TrialHookEvent) -> None:
        phase = {
            TrialEvent.START: "native_trial_start",
            TrialEvent.END: "native_trial_end",
            TrialEvent.CANCEL: "native_trial_cancel",
        }.get(event.event, f"native_{event.event.value.replace('-', '_')}")
        details: dict[str, Any] = {
            "trial_id": str(event.trial_id),
            "trial_name": event.trial_name,
            "native_timestamp": event.timestamp.isoformat(),
        }
        if event.event == TrialEvent.VERIFICATION_START:
            fault_call_id = self.exchange.fault_call_id
            details["gateway_fault_call_live"] = self.exchange.gateway.is_live(
                fault_call_id
            )
            target = self.exchange.worker_records.get(fault_call_id) or {}
            details["fault_worker_identity_absent_ns"] = target.get(
                "identity_absent_ns"
            )
            details["fault_call_id"] = fault_call_id
            details["worker_identity_absent_ns_by_call"] = {
                call_id: item["identity_absent_ns"]
                for call_id, item in self.exchange.worker_records.items()
            }
        evidence = record_event(self.journal, phase, **details)
        if event.event == TrialEvent.START:
            self._validate_trial_input(event)
            self.docker["compose_project"] = _sanitize_docker_compose_project_name(
                f"{event.trial_name}__env"
            )
            self.receipt["native_agent_timeout_multiplier"] = (
                event.config.agent_timeout_multiplier
            )
        elif event.event == TrialEvent.AGENT_START:
            project = self.docker["compose_project"]
            assert project is not None
            current = _container_snapshot(project)
            self.docker["during"] = current
            containers = current["containers"]
            if containers is not None:
                self.docker["observed_container_ids"] = [
                    item["id"] for item in containers
                ]
                self.docker["all_expected_services_running"] = (
                    len(containers) == len(_EXPECTED_SERVICES)
                    and {
                        item["service"]
                        for item in containers
                        if item["state"] == "running"
                    }
                    == _EXPECTED_SERVICES
                )
        elif event.event == TrialEvent.AGENT_END:
            self.receipt["native_play_end_hook_ns"] = evidence["monotonic_ns"]
        elif event.event == TrialEvent.VERIFICATION_START:
            self.receipt["provider_observer_at_verification"] = evidence

    def _validate_trial_input(self, event: TrialHookEvent) -> None:
        overlays = event.lock.extra_docker_compose or []
        expected_kwargs = {
            seat: {
                "role": seat,
                "condition": self.exchange.condition,
                "fault_call_id": self.exchange.fault_call_id,
            }
            for seat in ROLES
        }
        if (
            event.lock.task.digest != self.receipt["task_digest"]
            or [item.digest for item in overlays] != [self.receipt["overlay_digest"]]
            or event.config.agent_timeout_multiplier != _NATIVE_PLAY_MULTIPLIER
            or event.lock.agent_timeout_multiplier != _NATIVE_PLAY_MULTIPLIER
            or event.config.timeout_multiplier != 1.0
            or event.config.agent.override_timeout_sec is not None
            or event.config.agent.max_timeout_sec is not None
            or event.config.override_error_policy
            or event.lock.override_error_policy
            or event.config.verifier.disable
            or {item.seat: item.kwargs for item in event.config.participants}
            != expected_kwargs
            or {item.seat: item.kwargs for item in event.lock.participants}
            != expected_kwargs
        ):
            error = RuntimeError("The native trial does not match the sealed inputs")
            _record_error(self.receipt, "native_lock", error, stop=True)
            raise error

    def after_job(self) -> None:
        project = self.docker["compose_project"]
        if project is None:
            return
        after = _container_snapshot(project)
        self.docker["after"] = after
        observed = self.docker["observed_container_ids"]
        if not observed or after["containers"] is None:
            return
        remaining = {item["id"] for item in after["containers"]}
        missing = [
            container_id for container_id in observed if container_id not in remaining
        ]
        self.docker["absent_container_ids"] = missing
        self.docker["all_observed_containers_absent"] = len(missing) == len(observed)


async def _create_job(
    directory: Path, condition: Condition, task: Path, *, fault_call_id: FaultCallId
) -> Job:
    return await Job.create(
        JobConfig(
            job_name=condition,
            jobs_dir=directory / "native",
            n_attempts=1,
            n_concurrent_trials=1,
            quiet=True,
            agent_timeout_multiplier=_NATIVE_PLAY_MULTIPLIER,
            retry=RetryConfig(max_retries=0),
            environment=EnvironmentConfig(
                type=EnvironmentType.DOCKER,
                delete=True,
                extra_docker_compose=[_OVERLAY],
            ),
            participants=[
                Participant(
                    seat=seat,
                    model_name=f"{PROVIDER}/{MODEL}",
                    import_path=SupportFailureKitAgent.import_path(),
                    kwargs={
                        "role": seat,
                        "condition": condition,
                        "fault_call_id": fault_call_id,
                    },
                )
                for seat in ROLES
            ],
            tasks=[TaskConfig(path=task)],
        )
    )


async def _run_job(
    observer: _NativeObserver, condition: Condition, task: Path
) -> Exception | None:
    receipt = observer.receipt
    job = await _create_job(
        observer.directory,
        condition,
        task,
        fault_call_id=observer.exchange.fault_call_id,
    )
    for event in TrialEvent:
        job.add_hook(event, observer.hook)
    started = record_event(observer.journal, "native_job_started")
    receipt["native_job_start_ns"] = started["monotonic_ns"]
    try:
        await job.run()
    except BaseException as error:
        ended = stamp()
        details = {
            "type": type(error).__name__,
            "message": str(error),
            "traceback": "".join(traceback.format_exception(error)),
        }
        receipt["native_job_ended_ns"] = ended["monotonic_ns"]
        receipt["native_job_run_error"] = details
        record_event(
            observer.journal,
            "native_job_raised",
            error=_error(error),
            **details,
            **ended,
        )
        if isinstance(error, Exception):
            return error
        raise
    else:
        ended = stamp()
        receipt["native_job_return_ns"] = ended["monotonic_ns"]
        receipt["native_job_ended_ns"] = ended["monotonic_ns"]
        record_event(observer.journal, "native_job_returned", **ended)
        return None


def _drain(observer: _NativeObserver) -> dict[str, dict[str, Any]]:
    receipt = observer.receipt
    before = record_event(observer.journal, "gateway_drain_started")
    try:
        records = observer.exchange.gateway.finish(timeout_sec=150)
        receipt["gateway_drain_completed"] = True
        return records
    except Exception as error:
        _record_error(receipt, "gateway_drain", error, stop=True)
        records = {}
        for call_id in _CALLS:
            path = observer.directory / "gateway" / call_id / "result.json"
            if path.is_file():
                try:
                    records[call_id] = json.loads(path.read_bytes())
                except (OSError, ValueError) as read_error:
                    _record_error(receipt, f"gateway_result:{call_id}", read_error)
        return records
    finally:
        after = record_event(
            observer.journal,
            "gateway_drain_finished",
            completed=receipt["gateway_drain_completed"],
        )
        receipt["gateway_drain"] = {
            "started_ns": before["monotonic_ns"],
            "finished_ns": after["monotonic_ns"],
            "seconds": _elapsed(before["monotonic_ns"], after["monotonic_ns"]),
        }


def _call_cost(record: dict[str, Any]) -> tuple[float | None, str | None, bool]:
    usage = record.get("usage")
    if not isinstance(usage, dict):
        return None, None, False
    prompt = _count(usage, "prompt_tokens")
    cache = _count(usage, "prompt_cache_hit_tokens")
    output = _count(usage, "completion_tokens")
    if prompt is None or output is None:
        return None, None, False
    exceeds_reserve = prompt > _INPUT_RESERVE or output > _OUTPUT_RESERVE
    if cache is None or cache > prompt:
        return None, None, exceeds_reserve
    start_wall_ns = record.get("started_wall_time_ns")
    start_ns = record.get("started_ns")
    end_ns = record.get("completed_ns")
    if (
        type(start_wall_ns) is not int
        or start_wall_ns <= 0
        or type(start_ns) is not int
        or type(end_ns) is not int
        or end_ns < start_ns
    ):
        return None, None, exceeds_reserve
    started = datetime.fromtimestamp(start_wall_ns / 1e9, UTC)
    finished = started + timedelta(seconds=(end_ns - start_ns) / 1e9)
    cost, rate = _calculated_cost(prompt, cache, output, started)
    _, finish_rate = _calculated_cost(prompt, cache, output, finished)
    if rate != finish_rate:
        return None, "rate_boundary", exceeds_reserve
    return cost, rate, exceeds_reserve


def _artifact_path(path: Path, directory: Path) -> str | None:
    return str(path.relative_to(directory)) if path.is_file() else None


def _gateway_call(
    directory: Path,
    call_id: str,
    record: dict[str, Any] | None,
    receipt: dict[str, Any],
) -> tuple[dict[str, Any] | None, bool]:
    root = directory / "gateway" / call_id
    request = _artifact_path(root / "request.json", directory)
    result = _artifact_path(root / "result.json", directory)
    events_path = root / "events.jsonl"
    drained = receipt["gateway_drain_completed"]
    if record is None and request is None and not events_path.is_file():
        if drained or call_id not in receipt["worker_processes"]:
            return None, False
    events = _worker_events(events_path)
    complete_record = isinstance(record, dict) and record.get("call_id") == call_id
    record = record or {}
    started_ns = record.get("started_ns") or events.get("started", {}).get(
        "monotonic_ns"
    )
    started = True if started_ns is not None else None
    if started is None and drained and complete_record:
        started = False
    cost, rate, exceeds_reserve = _call_cost(record)
    status = record.get("http_status") or events.get("provider_http_response", {}).get(
        "http_status"
    )
    error = record.get("error")
    if record.get("stop_new_admission") is True:
        reason = record.get("admission_stop_reason") or "Gateway rejected admission"
        _record_error(
            receipt, f"gateway_admission:{call_id}", RuntimeError(reason), stop=True
        )
    if status in (401, 402, 403):
        _record_error(
            receipt,
            f"provider_auth_or_balance:{call_id}",
            RuntimeError(f"DeepSeek returned HTTP {status}"),
            stop=True,
        )
    elif error and not (
        started is False
        and error.get("kind") == "downstream_disconnected_before_request"
    ):
        _record_error(
            receipt, f"provider:{call_id}", RuntimeError(str(error)), stop=True
        )
    if drained and not complete_record:
        _record_error(
            receipt,
            f"gateway_result:{call_id}",
            RuntimeError("Gateway evidence has no matching final record"),
            stop=True,
        )
    return {
        "request_path": request,
        "result_path": result,
        "events_path": _artifact_path(events_path, directory),
        "upstream_started": started,
        "upstream_started_ns": started_ns,
        "http_status": status,
        "usage": record.get("usage"),
        "response_id": record.get("response_id"),
        "error": error,
        "balance": record.get("balance"),
        "stop_new_admission": record.get("stop_new_admission"),
        "admission_stop_reason": record.get("admission_stop_reason"),
        "price_rate": rate,
        "calculated_cost_usd": cost,
        "unknown_cost_reserve_usd": _USD_RESERVE_PER_CALL
        if started is not False and cost is None
        else None,
        "upstream_seconds": _elapsed(started_ns, record.get("completed_ns")),
    }, exceeds_reserve


def _gateway_usage(
    directory: Path, records: dict[str, dict[str, Any]], receipt: dict[str, Any]
) -> dict[str, Any]:
    rows = {}
    exceeded = not set(records) <= set(_CALLS)
    for call_id in _CALLS:
        row, over_limit = _gateway_call(
            directory, call_id, records.get(call_id), receipt
        )
        if row is not None:
            rows[call_id] = row
        exceeded |= over_limit
    costs = [
        row["calculated_cost_usd"]
        for row in rows.values()
        if row["calculated_cost_usd"] is not None
    ]
    known = sum(costs)
    reserve = sum(row["unknown_cost_reserve_usd"] or 0 for row in rows.values())
    complete = receipt["gateway_drain_completed"] and reserve == 0
    if exceeded or known + reserve > len(_CALLS) * _USD_RESERVE_PER_CALL + 1e-12:
        _record_error(
            receipt,
            "budget",
            RuntimeError("Provider evidence exceeds the fixed cost reserve"),
            stop=True,
        )
    return {
        "by_call": rows,
        "upstream_started_call_ids": [
            call_id for call_id, row in rows.items() if row["upstream_started"] is True
        ],
        "known_cost_usd": known if complete or costs else None,
        "actual_cost_usd": known if complete else None,
        "actual_cost_complete": complete,
        "unknown_cost_reserve_usd": reserve,
    }


def _seat_evidence(directory: Path, receipt: dict[str, Any]) -> None:
    stages = {}
    for role in ROLES:
        path = directory / "seat" / role / "events.jsonl"
        events = _worker_events(path)
        stages[role] = {
            "events_path": _artifact_path(path, directory),
            "seat_end": events.get("seat_end"),
            "observation_received": events.get("observation_received"),
            "post_attempt": events.get("post_attempt"),
            "post_accepted": events.get("post_accepted"),
        }
    receipt["seat_stages"] = stages
    left = stages["left"]
    receipt["left_report_post_state"] = (
        "accepted"
        if left["post_accepted"] is not None
        else "attempted"
        if left["post_attempt"] is not None
        else "not_attempted"
        if left["seat_end"] is not None
        else "unconfirmed"
    )


def _verifier_evidence(
    directory: Path, trial_dir: Path, receipt: dict[str, Any]
) -> None:
    output_paths = {}
    rejection = None
    for stream in ("stdout", "stderr"):
        path = trial_dir / "verifier" / f"test-{stream}.txt"
        output_paths[stream] = _artifact_path(path, directory)
        if output_paths[stream] is None:
            continue
        with path.open("rb") as log:
            data = log.read(_VERIFIER_LOG_LIMIT + 1)
        if len(data) > _VERIFIER_LOG_LIMIT:
            raise RuntimeError("The native verifier output exceeds its size bound")
        for line in data.decode("utf-8", errors="replace").splitlines():
            if line.startswith("No kit-check reward: "):
                rejection = line
                break
    receipt["native_verifier_output_paths"] = output_paths
    receipt["native_verifier_rejection"] = rejection
    receipt["native_verifier_rejection_kind"] = _verifier_rejection_kind(rejection)


def _verifier_rejection_kind(line: str | None) -> str | None:
    if line is None:
        return None
    if line == (
        "No kit-check reward: [Errno 2] No such file or directory: "
        "'/logs/world/kit-check.json'"
    ):
        return "missing_world_snapshot"
    match = re.fullmatch(
        r"No kit-check reward: seed \d+: (invalid left review|invalid right review|"
        r"invalid or missing choice)",
        line,
    )
    if match is None:
        return "other_rejection"
    return {
        "invalid left review": "invalid_left_review",
        "invalid right review": "invalid_right_review",
        "invalid or missing choice": "missing_or_invalid_builder_choice",
    }[match[1]]


def _verifier_route(receipt: dict[str, Any]) -> str:
    if receipt["native_verifier_result"] is not None:
        return "verifier_result"
    if receipt["native_verifier"].get("started_at") is not None:
        if receipt["left_report_post_state"] == "not_attempted" and receipt[
            "native_verifier_rejection_kind"
        ] in {"missing_world_snapshot", "invalid_left_review"}:
            return "verifier_missing_handoff"
        return "verifier_error"
    error = receipt["native_trial_error"]
    if receipt["native_agent_execution"] and error is not None:
        for seat in receipt["native_seat_results"]:
            detail = seat.get("exception_info")
            if detail and all(
                detail[field] == error[field]
                for field in ("exception_type", "exception_message")
            ):
                return "recovery_only"
    return "no_verifier"


def _capture_trial(directory: Path, path: Path, receipt: dict[str, Any]) -> None:
    result = json.loads(path.read_bytes())
    seats = result.get("seat_results") or []
    verifier = result.get("verifier") or {}
    play = result.get("agent_execution")
    receipt.update(
        native_result_path=str(path.relative_to(directory)),
        native_config_path=_artifact_path(path.with_name("config.json"), directory),
        native_lock_path=_artifact_path(path.with_name("lock.json"), directory),
        native_trial_log_path=_artifact_path(path.with_name("trial.log"), directory),
        native_trial_id=result["id"],
        native_trial_error=result.get("exception_info"),
        native_reward_valid=result["reward_valid"],
        native_reward=((result.get("verifier_result") or {}).get("rewards") or {}).get(
            "reward"
        ),
        native_verifier_result=result.get("verifier_result"),
        native_verifier=verifier,
        native_agent_execution=play,
        native_seat_results=seats,
        native_shared_deadline_reached=any(
            (seat.get("exception_info") or {}).get("exception_type")
            == AgentTimeoutError.__name__
            for seat in seats
        ),
        all_seats_frozen_before_verifier=_freeze_before_verifier(
            seats, play=play, verifier=verifier
        )
        if verifier.get("started_at") is not None
        else None,
    )
    _verifier_evidence(directory, path.parent, receipt)
    receipt["native_verifier_route"] = _verifier_route(receipt)
    receipt["native_valid_reward_without_left_handoff"] = (
        receipt["left_report_post_state"] == "not_attempted"
        and receipt["native_reward_valid"]
    )
    receipt["native_embedded_agent_timeout_multiplier"] = result["config"].get(
        _NATIVE_PLAY_TIMEOUT_FIELD
    )


def _capture_native(
    directory: Path, condition: Condition, receipt: dict[str, Any]
) -> None:
    job_dir = directory / "native" / condition
    job_result = job_dir / "result.json"
    receipt["native_job_result_path"] = _artifact_path(job_result, directory)
    receipt["native_job_config_path"] = _artifact_path(
        job_dir / "config.json", directory
    )
    receipt["native_job_lock_path"] = _artifact_path(job_dir / "lock.json", directory)
    paths = sorted(job_dir.glob("*/result.json"))
    receipt["native_result_paths"] = [
        str(path.relative_to(directory)) for path in paths
    ]
    if len(paths) != 1:
        raise RuntimeError(f"Expected 1 native trial result; found {len(paths)}")
    _capture_trial(directory, paths[0], receipt)
    if receipt["native_job_result_path"] is None:
        raise RuntimeError("The native job did not persist its result")
    receipt["native_job_result_finalized"] = (
        json.loads(job_result.read_bytes()).get("finished_at") is not None
    )
    if (
        receipt["native_job_return_ns"] is not None
        and not receipt["native_job_result_finalized"]
    ):
        raise RuntimeError("The returned native job result is not finalized")
    _validate_persisted_deadline(directory, receipt)


def _validate_persisted_deadline(directory: Path, receipt: dict[str, Any]) -> None:
    job_config, job_lock, trial_config, trial_lock = (
        json.loads((directory / receipt[field]).read_bytes())
        for field in (
            "native_job_config_path",
            "native_job_lock_path",
            "native_config_path",
            "native_lock_path",
        )
    )
    (locked_trial,) = job_lock["trials"]
    rows = (job_config, locked_trial, trial_config, trial_lock)
    if (
        any(
            row.get(_NATIVE_PLAY_TIMEOUT_FIELD) != _NATIVE_PLAY_MULTIPLIER
            for row in rows
        )
        or receipt["native_embedded_agent_timeout_multiplier"]
        != _NATIVE_PLAY_MULTIPLIER
    ):
        raise RuntimeError("The serialized native shared deadline differs from the pin")


def _fault_worker_exit(receipt: dict[str, Any]) -> str | None:
    intervention = receipt.get("intervention") or {}
    expected = {
        "worker-timeout": (124, "worker_deadline", "timeout_fired_ns"),
        "worker-sigkill": (-9, "sigkill", "signal_ns"),
    }.get(receipt["condition"])
    if expected is None:
        return None
    code, mechanism, event = expected
    if (
        intervention.get("call_id") == receipt["fault_call_id"]
        and intervention.get("exit_code") == code
        and intervention.get("mechanism") == mechanism
        and intervention.get(event) is not None
    ):
        return f"DeepSeek host worker {receipt['fault_call_id']} exited {code}"
    return None


def _native_error_expected(kind: str, message: str, receipt: dict[str, Any]) -> bool:
    fault = _fault_worker_exit(receipt)
    if kind == NonZeroAgentExitCodeError.__name__:
        return fault is not None and message in (fault, f"Seat 'left': {fault}")
    if kind == AgentTimeoutError.__name__:
        deadline = f"Agent execution timed out after {_NATIVE_PLAY_TIMEOUT_SEC} seconds"
        return receipt["native_shared_deadline_reached"] and (
            message == deadline
            or message in tuple(f"Seat '{role}': {deadline}" for role in ROLES)
        )
    if kind == RewardFileNotFoundError.__name__:
        return _original_verifier_rejected(receipt)
    if kind != "RuntimeError":
        return False
    if message in (
        "Codex returned an invalid Kit Check action",
        "DeepSeek did not finish the Kit Check decision",
    ):
        return any(
            (stage["seat_end"] or {}).get("error") == f"RuntimeError: {message}"
            and (stage["seat_end"] or {}).get("phase") == "decision"
            for stage in receipt["seat_stages"].values()
        )
    return (
        fault is not None
        and receipt["left_report_post_state"] == "not_attempted"
        and message == "Kit Check builder timed out waiting for both reviews"
        and (receipt["seat_stages"]["builder"]["seat_end"] or {}).get("error")
        == f"RuntimeError: {message}"
    )


def _original_verifier_rejected(receipt: dict[str, Any]) -> bool:
    kind = receipt.get("native_verifier_rejection_kind")
    if receipt.get("native_verifier_result") is not None or receipt.get(
        "native_reward_valid"
    ):
        return False
    if kind == "missing_world_snapshot":
        stages = receipt["seat_stages"]
        return bool(stages) and not all(
            stage["post_accepted"] for stage in stages.values()
        )
    return kind in {
        "invalid_left_review",
        "invalid_right_review",
        "missing_or_invalid_builder_choice",
    }


def _raised_missing_handoff(error: Exception, receipt: dict[str, Any]) -> bool:
    if (
        receipt.get("native_result_path") is None
        or receipt.get("left_report_post_state") != "not_attempted"
        or _fault_worker_exit(receipt) is None
    ):
        return False
    pending: list[BaseException] = [error]
    for _ in range(16):
        if not pending:
            return True
        current = pending.pop()
        if isinstance(current, BaseExceptionGroup):
            if len(current.exceptions) + len(pending) > 16:
                return False
            pending.extend(current.exceptions)
        elif not _native_error_expected(type(current).__name__, str(current), receipt):
            return False
    return False


def _native_stops(receipt: dict[str, Any], job_error: Exception | None) -> None:
    if job_error is not None:
        expected = _raised_missing_handoff(job_error, receipt)
        receipt["native_job_run_error_expected"] = expected
        if not expected:
            _record_error(receipt, "native_job_run", job_error, stop=True)
    sources = {"trial": receipt.get("native_trial_error")}
    for seat in receipt.get("native_seat_results") or []:
        role = seat["seat"]
        sources[role] = seat.get("exception_info")
        sources[f"freeze:{role}"] = (seat.get("freeze") or {}).get("exception_info")
    for phase, info in sources.items():
        if info is not None and not _native_error_expected(
            info["exception_type"], info["exception_message"], receipt
        ):
            detail = f"{info['exception_type']}: {info['exception_message']}"
            _record_error(receipt, f"native_error:{phase}", detail, stop=True)
    if receipt["native_verifier_route"] in {
        "verifier_error",
        "verifier_missing_handoff",
    } and not _original_verifier_rejected(receipt):
        error = RuntimeError("The verifier failed without a recognized task rejection")
        _record_error(receipt, "native_verifier", error, stop=True)
    play = receipt.get("native_agent_execution")
    if play is not None:
        if not receipt["cleanup_boundary"]["all_eligible_freezes_succeeded"]:
            _record_error(
                receipt,
                "native_freeze",
                "Native play ended without every eligible seat freeze",
                stop=True,
            )
        trial_error = sources["trial"] or {}
        if (
            receipt["native_verifier_route"] == "no_verifier"
            and not receipt["native_job_run_error_expected"]
            and (trial_error.get("policy") or {}).get("should_grade", True)
        ):
            _record_error(
                receipt,
                "native_verifier",
                "Native play ended without its policy-required verifier",
                stop=True,
            )


def _fault_call_timing(
    provider: dict[str, Any], receipt: dict[str, Any]
) -> dict[str, Any] | None:
    intervention = receipt.get("intervention")
    if intervention is None:
        return None
    fault = intervention["signal_ns"] or intervention["timeout_fired_ns"]
    fault_wall = (
        intervention["signal_wall_time_ns"]
        or intervention["timeout_fired_wall_time_ns"]
    )
    first = provider.get("first_delta_ns")
    terminal = provider.get("terminal_ns")
    active = None
    if fault is not None and first is not None and terminal is not None:
        active = first <= fault < terminal
    ended = (receipt.get("native_agent_execution") or {}).get("finished_at")
    before_play_end = None
    end_wall = _wall_time_ns(ended)
    if fault_wall is not None and end_wall is not None:
        before_play_end = fault_wall < end_wall
    return {
        "intervention_ns": fault,
        "intervention_wall_time_ns": fault_wall,
        "gateway_first_delta_ns": first,
        "gateway_terminal_ns": terminal,
        "gateway_active_at_intervention": active,
        "intervention_before_native_play_end": before_play_end,
    }


def _wall_time_ns(value: str | None) -> int | None:
    return round(datetime.fromisoformat(value).timestamp() * 1e9) if value else None


def _cleanup_boundary(receipt: dict[str, Any]) -> dict[str, Any]:
    seats = {seat["seat"]: seat for seat in receipt.get("native_seat_results") or []}
    freezes = [seat.get("freeze") or {} for seat in seats.values()]
    freeze_times = [
        _wall_time_ns(item["finished_at"])
        for item in freezes
        if item.get("finished_at") is not None
    ]
    verify = receipt.get("provider_observer_at_verification")
    verifier_started = (receipt.get("native_verifier") or {}).get("started_at")
    play = receipt.get("native_agent_execution") or {}
    play_end = _wall_time_ns(play.get("finished_at"))
    earliest_stop = min(freeze_times, default=None)
    if verifier_started is not None:
        kind = "verifier_start"
    elif earliest_stop is None:
        kind = "none"
    elif receipt["native_verifier_route"] == "recovery_only":
        kind = "native_recovery_freeze"
    else:
        kind = "native_freeze_without_verifier"
    owners = {call_id: role for role, call_id in CALL_IDS.items()}
    owners[AUDIT] = "left"
    rows = {}
    for call_id, worker in receipt["worker_processes"].items():
        role = owners[call_id]
        stage = receipt.get("seat_stages", {}).get(role) or {}
        adapter_end = stage.get("seat_end") or {}
        seat_end = (seats.get(role, {}).get("agent_execution") or {}).get("finished_at")
        native_seat_end = _wall_time_ns(seat_end)
        reaped = worker["reaped_ns"]
        absent = worker["identity_absent_ns"]
        adapter_end_ns = adapter_end.get("monotonic_ns")
        adapter_end_wall = adapter_end.get("wall_time_ns")
        ordered = (
            reaped is not None
            and absent is not None
            and adapter_end_ns is not None
            and reaped <= absent <= adapter_end_ns
        )
        if kind == "verifier_start":
            proven = bool(
                ordered
                and verify
                and adapter_end_ns is not None
                and adapter_end_ns <= verify["monotonic_ns"]
                and verify["worker_identity_absent_ns_by_call"].get(call_id) == absent
            )
        elif kind == "none":
            proven = None
        else:
            proven = (
                ordered
                and adapter_end_wall is not None
                and native_seat_end is not None
                and play_end is not None
                and earliest_stop is not None
                and adapter_end_wall <= native_seat_end <= play_end <= earliest_stop
            )
        rows[call_id] = {
            "role": role,
            "worker_reaped_ns": reaped,
            "worker_identity_absent_ns": absent,
            "adapter_seat_end": adapter_end or None,
            "native_seat_finished_at": seat_end,
            "absence_before_boundary": proven,
        }
    return {
        "kind": kind,
        "verifier_started_at": verifier_started,
        "earliest_native_freeze_finished_wall_ns": earliest_stop,
        "all_eligible_freezes_recorded": len(freeze_times) == len(ROLES),
        "all_eligible_freezes_succeeded": set(seats) == set(ROLES)
        and all(
            item.get("finished_at") is not None and item.get("exception_info") is None
            for item in freezes
        ),
        "all_spawned_workers_absent_before_boundary": all(
            row["absence_before_boundary"] is True for row in rows.values()
        )
        if kind != "none"
        else None,
        "by_call": rows,
    }


def _capture_docker(observer: _NativeObserver) -> None:
    receipt = observer.receipt
    try:
        observer.after_job()
        write_json(observer.directory / "docker_teardown.json", observer.docker)
        receipt["docker_teardown_path"] = "docker_teardown.json"
        after = observer.docker["after"]
        if (
            observer.docker["all_expected_services_running"] is not True
            or observer.docker["all_observed_containers_absent"] is not True
            or after is None
            or after["containers"] != []
        ):
            raise RuntimeError("The trial Compose project cleanup is not confirmed")
    except Exception as error:
        _record_error(receipt, "docker_teardown_observation", error, stop=True)


def _finalize_case(
    observer: _NativeObserver,
    records: dict[str, dict[str, Any]],
    job_error: Exception | None,
) -> None:
    exchange = observer.exchange
    receipt = observer.receipt
    directory = observer.directory
    receipt.update(
        worker_attempts=exchange.worker_attempts,
        worker_processes=exchange.worker_records,
        worker_usage={
            call_id: exchange.worker_results.get(call_id, {}).get("usage")
            for call_id in exchange.worker_records
        },
        builder_gate=exchange.gate,
        intervention=exchange.intervention,
        intervention_path=_artifact_path(directory / "intervention.json", directory),
    )
    try:
        _seat_evidence(directory, receipt)
    except Exception as error:
        _record_error(receipt, "seat_evidence", error, stop=True)
    try:
        _capture_native(directory, exchange.condition, receipt)
    except Exception as error:
        _record_error(receipt, "native_result", error, stop=True)
    try:
        receipt["gateway_usage"] = _gateway_usage(directory, records, receipt)
    except Exception as error:
        _record_error(receipt, "gateway_usage", error, stop=True)
    receipt["fault_call_timing"] = _fault_call_timing(
        records.get(exchange.fault_call_id) or {}, receipt
    )
    receipt["cleanup_boundary"] = _cleanup_boundary(receipt)
    for call_id, worker in exchange.worker_attempts.items():
        if worker["cleanup_error"] or (
            worker["pid"] is not None and worker["identity_absent_ns"] is None
        ):
            error = RuntimeError(
                worker["cleanup_error"] or "Worker reap is unconfirmed"
            )
            _record_error(receipt, f"worker_cleanup:{call_id}", error, stop=True)
    _native_stops(receipt, job_error)


def _timings(receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        "outer_gate_seconds": (receipt.get("docker_gate") or {}).get(
            "gate_wait_seconds"
        ),
        "builder_gate_seconds": (receipt.get("builder_gate") or {}).get("seconds"),
        "native_job_seconds": _elapsed(
            receipt.get("native_job_start_ns"), receipt.get("native_job_ended_ns")
        ),
        "native_play_seconds": _duration(receipt.get("native_agent_execution")),
        "native_verifier_seconds": _duration(receipt.get("native_verifier")),
        "seat_execution_seconds": {
            seat["seat"]: _duration(seat.get("agent_execution"))
            for seat in (receipt.get("native_seat_results") or [])
        },
        "worker_seconds": {
            call_id: _elapsed(record["spawned_ns"], record["reaped_ns"])
            for call_id, record in (receipt.get("worker_processes") or {}).items()
        },
        "gateway_upstream_seconds": {
            call_id: row["upstream_seconds"]
            for call_id, row in (receipt.get("gateway_usage") or {})
            .get("by_call", {})
            .items()
        },
        "gateway_drain_seconds": (receipt.get("gateway_drain") or {}).get("seconds"),
        "docker_snapshot_seconds": {
            phase: _elapsed(
                item["started"]["monotonic_ns"], item["finished"]["monotonic_ns"]
            )
            for phase in ("during", "after")
            if (item := (receipt.get("docker_teardown") or {}).get(phase)) is not None
        },
    }


async def _run(
    directory: Path,
    condition: Condition,
    task: Path,
    key: str,
    receipt: dict[str, Any],
    *,
    fault_call_id: FaultCallId,
) -> None:
    gateway = DeepSeekGateway(
        directory / "gateway",
        key,
        max_calls=len(_CALLS),
        max_prompt_bytes=_MAX_PROMPT_BYTES,
        max_output_tokens=_OUTPUT_RESERVE,
        api_timeout_sec=_API_TIMEOUT_SEC,
    )
    exchange = prepare_case(
        directory=directory,
        condition=condition,
        fault_call_id=fault_call_id,
        gateway=gateway,
    )
    observer = _NativeObserver(directory, exchange, receipt)
    records: dict[str, dict[str, Any]] = {}
    job_error = None
    stage = "gateway_start"
    try:
        gateway.start()
        stage = "native_job_setup"
        job_error = await _run_job(observer, condition, task)
    except Exception as error:
        _record_error(receipt, stage, error, stop=True)
    finally:
        try:
            if receipt.get("native_job_ended_ns") is not None:
                _capture_docker(observer)
            if stage != "gateway_start":
                records = _drain(observer)
        finally:
            try:
                gateway.close()
            except Exception as error:
                _record_error(receipt, "gateway_close", error, stop=True)
            clear_case(exchange)
            _finalize_case(observer, records, job_error)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one native DeepSeek pre-handoff failure trial."
    )
    parser.add_argument("condition", choices=CONDITIONS)
    parser.add_argument("output", type=Path)
    parser.add_argument("--task", required=True, type=Path)
    parser.add_argument("--model", required=True, choices=(MODEL,))
    args = parser.parse_args()
    key = os.environ.pop("DEEPSEEK_API_KEY", "")
    os.environ["HARBOR_TELEMETRY"] = "0"
    cli_started = stamp()
    if args.output.is_symlink():
        parser.error("The output path already exists as a symbolic link")
    directory = args.output.resolve()
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    except OSError as error:
        parser.error(f"Cannot create a fresh output directory: {error}")
    task = args.task.resolve()
    journal = directory / "lifecycle.jsonl"
    record_event(journal, "cli_started", **cli_started)
    cohort_reserve = _SCHEDULED_TRIALS * len(_CALLS) * _USD_RESERVE_PER_CALL
    receipt: dict[str, Any] = {
        "schema": "kit-check-prehandoff-failure/v1",
        "condition": args.condition,
        "model": f"{PROVIDER}/{MODEL}",
        "task_path": str(task),
        "source_base_commit": _SOURCE_BASE,
        "overlay_path": str(_OVERLAY),
        "lifecycle_path": journal.name,
        "planned_trial_reserve_usd": len(_CALLS) * _USD_RESERVE_PER_CALL,
        "planned_cohort_reserve_usd": cohort_reserve,
        "cohort_cap_usd": _COHORT_CAP_USD,
        "previous_lane_known_cost_usd": _PREVIOUS_LANE_COST_USD,
        "cumulative_hard_ceiling_usd": _CUMULATIVE_CAP_USD,
        "fault_call_id": _FAULT_CALL_ID,
        "native_play_timeout_sec": _NATIVE_PLAY_TIMEOUT_SEC,
        "native_play_timeout_field": _NATIVE_PLAY_TIMEOUT_FIELD,
        "provider_api_timeout_sec": _API_TIMEOUT_SEC,
        "native_job_return_ns": None,
        "native_job_run_error": None,
        "native_job_run_error_expected": None,
        "native_job_result_finalized": None,
        "native_result_path": None,
        "native_reward_valid": None,
        "native_verifier_route": "no_trial_result",
        "native_shared_deadline_reached": None,
        "left_report_post_state": "unconfirmed",
        "seat_stages": {},
        "intervention_path": None,
        "provider_observer_at_verification": None,
        "gateway_drain_completed": False,
        "errors": {},
        "admission_stop_reasons": [],
    }
    stage = "admission"
    try:
        receipt["docker_gate"] = _gate(cli_started)
        if cohort_reserve > _COHORT_CAP_USD:
            raise RuntimeError("The fixed cohort would exceed its cost cap")
        stage = "cohort_registry"
        registry = _sealed_case(directory, args.condition, receipt)
        stage = "source_pins"
        _pins(task, registry, receipt)
        stage = "balance"
        receipt["balance"] = _balance(key)
        if (
            receipt["balance"]["usd"] < _MIN_BALANCE_USD
            or receipt["balance"]["is_available"] is not True
        ):
            raise RuntimeError(
                "DeepSeek balance is below the admission floor or is not available"
            )
        stage = "study_runner"
        asyncio.run(
            _run(
                directory,
                args.condition,
                task,
                key,
                receipt,
                fault_call_id=_FAULT_CALL_ID,
            )
        )
    except (
        Exception,
        KeyboardInterrupt,
        asyncio.CancelledError,
        BaseExceptionGroup,
    ) as error:
        _record_error(receipt, stage, error, stop=True)
    finally:
        receipt["native_job_returned"] = receipt["native_job_return_ns"] is not None
        receipt["timings"] = _timings(receipt)
        receipt["stop_new_admission"] = bool(receipt["admission_stop_reasons"])
        receipt["evidence_captured"] = True
        receipt["cli_exit_code"] = 2 if receipt["stop_new_admission"] else 0
        output = directory / "receipt.json"
        write_json(output, receipt)
        print(output, flush=True)
    return receipt["cli_exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
