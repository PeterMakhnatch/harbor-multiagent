import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, override
from urllib.error import HTTPError

import harbor
from harbor.environments.docker.docker import _sanitize_docker_compose_project_name
from harbor.environments.docker.runtime import DOCKER_RUNTIME
from harbor.job import Job
from harbor.models.environment_type import EnvironmentType
from harbor.models.job.config import JobConfig, RetryConfig
from harbor.models.trial.config import EnvironmentConfig, Participant, TaskConfig
from harbor.models.trial.result import SeatResult
from harbor.publisher.packager import Packager
from harbor.trial.hooks import TrialEvent, TrialHookEvent

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
    if registry["schema"] != "harbor-deepseek-support-failure-cohort/v1":
        raise RuntimeError("The sealed study registry has an unexpected schema")
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
    receipt: dict[str, Any], stage: str, error: BaseException, *, stop: bool = False
) -> None:
    receipt["errors"][stage] = _error(error)
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
            details["gateway_left_audit_live"] = self.exchange.gateway.is_live(AUDIT)
            details["audit_worker_identity_absent_ns"] = (
                self.exchange.intervention or {}
            ).get("identity_absent_ns")
            details["worker_identity_absent_ns_by_call"] = {
                call_id: item["identity_absent_ns"]
                for call_id, item in self.exchange.worker_records.items()
            }
        evidence = record_event(self.journal, phase, **details)
        if event.event == TrialEvent.START:
            self.docker["compose_project"] = _sanitize_docker_compose_project_name(
                f"{event.trial_name}__env"
            )
            overlays = event.lock.extra_docker_compose or []
            if event.lock.task.digest != self.receipt["task_digest"] or [
                item.digest for item in overlays
            ] != [self.receipt["overlay_digest"]]:
                error = RuntimeError(
                    "The native trial lock does not match the input pins"
                )
                _record_error(self.receipt, "native_lock", error, stop=True)
                raise error
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


async def _create_job(directory: Path, condition: Condition, task: Path) -> Job:
    return await Job.create(
        JobConfig(
            job_name=condition,
            jobs_dir=directory / "native",
            n_attempts=1,
            n_concurrent_trials=1,
            quiet=True,
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
                    kwargs={"role": seat, "condition": condition},
                )
                for seat in ROLES
            ],
            tasks=[TaskConfig(path=task)],
        )
    )


async def _run_job(observer: _NativeObserver, condition: Condition, task: Path) -> None:
    receipt = observer.receipt
    job = await _create_job(observer.directory, condition, task)
    for event in TrialEvent:
        job.add_hook(event, observer.hook)
    started = record_event(observer.journal, "native_job_started")
    receipt["native_job_start_ns"] = started["monotonic_ns"]
    try:
        await job.run()
    except BaseException as error:
        ended = stamp()
        receipt["native_job_ended_ns"] = ended["monotonic_ns"]
        record_event(
            observer.journal, "native_job_raised", error=_error(error), **ended
        )
        raise
    else:
        ended = stamp()
        receipt["native_job_return_ns"] = ended["monotonic_ns"]
        receipt["native_job_ended_ns"] = ended["monotonic_ns"]
        record_event(observer.journal, "native_job_returned", **ended)


def _drain(observer: _NativeObserver) -> dict[str, dict[str, Any]]:
    receipt = observer.receipt
    before = record_event(observer.journal, "gateway_drain_started")
    try:
        records = observer.exchange.gateway.finish(timeout_sec=150)
        receipt["gateway_drain_completed"] = True
        return records
    except Exception as error:
        _record_error(receipt, "gateway_drain", error)
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


def _gateway_usage(
    directory: Path, records: dict[str, dict[str, Any]], receipt: dict[str, Any]
) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    expected = set(_CALLS)
    exceeded = not set(records) <= expected
    auth_failure = False
    for call_id in _CALLS:
        root = directory / "gateway" / call_id
        record = records.get(call_id)
        request = (root / "request.json").is_file()
        if record is None and not request:
            continue
        record = record if isinstance(record, dict) else {}
        cost, rate, over_limit = _call_cost(record)
        exceeded = exceeded or over_limit
        status = record.get("http_status")
        if status is None and not receipt["gateway_drain_completed"]:
            try:
                event = _worker_events(root / "events.jsonl").get(
                    "provider_http_response", {}
                )
                status = event.get("http_status")
            except (OSError, ValueError, RuntimeError) as error:
                _record_error(receipt, f"gateway_events:{call_id}", error)
        if record.get("stop_new_admission") is True:
            reason = (
                record.get("admission_stop_reason")
                or "Gateway rejected further admissions"
            )
            _record_error(
                receipt, f"gateway_admission:{call_id}", RuntimeError(reason), stop=True
            )
        auth_failure = auth_failure or status in (401, 402, 403)
        rows[call_id] = {
            "request_path": str((root / "request.json").relative_to(directory))
            if request
            else None,
            "result_path": str((root / "result.json").relative_to(directory))
            if (root / "result.json").is_file()
            else None,
            "http_status": status,
            "usage": record.get("usage"),
            "response_id": record.get("response_id"),
            "error": record.get("error"),
            "balance": record.get("balance"),
            "stop_new_admission": record.get("stop_new_admission"),
            "admission_stop_reason": record.get("admission_stop_reason"),
            "price_rate": rate,
            "calculated_cost_usd": cost,
            "unknown_cost_reserve_usd": _USD_RESERVE_PER_CALL if cost is None else None,
            "upstream_seconds": _elapsed(
                record.get("started_ns"), record.get("completed_ns")
            ),
        }
    known = sum(
        row["calculated_cost_usd"]
        for row in rows.values()
        if row["calculated_cost_usd"] is not None
    )
    complete = receipt["gateway_drain_completed"] and all(
        row["calculated_cost_usd"] is not None for row in rows.values()
    )
    if exceeded or known > len(_CALLS) * _USD_RESERVE_PER_CALL:
        _record_error(
            receipt,
            "budget",
            RuntimeError("Provider evidence exceeds the fixed cost reserve"),
            stop=True,
        )
    if auth_failure:
        _record_error(
            receipt,
            "provider_auth_or_balance",
            RuntimeError("DeepSeek returned HTTP 401, 402 or 403"),
            stop=True,
        )
    return {
        "by_call": rows,
        "known_cost_usd": known
        if complete
        or any(row["calculated_cost_usd"] is not None for row in rows.values())
        else None,
        "actual_cost_usd": known if complete else None,
        "actual_cost_complete": complete,
        "unknown_cost_reserve_usd": (
            sum(
                row["unknown_cost_reserve_usd"]
                for row in rows.values()
                if row["unknown_cost_reserve_usd"] is not None
            )
            if receipt["gateway_drain_completed"]
            else len(_CALLS) * _USD_RESERVE_PER_CALL
        ),
    }


def _capture_native(
    directory: Path, condition: Condition, receipt: dict[str, Any]
) -> None:
    job_dir = directory / "native" / condition
    job_result = job_dir / "result.json"
    if job_result.is_file():
        receipt["native_job_result_path"] = str(job_result.relative_to(directory))
    paths = sorted(job_dir.glob("*/result.json"))
    receipt["native_result_paths"] = [
        str(path.relative_to(directory)) for path in paths
    ]
    if len(paths) != 1:
        raise RuntimeError(f"Expected 1 native trial result; found {len(paths)}")
    path = paths[0]
    receipt["native_result_path"] = str(path.relative_to(directory))
    result = json.loads(path.read_bytes())
    lock_path = path.with_name("lock.json")
    receipt["native_lock_path"] = (
        str(lock_path.relative_to(directory)) if lock_path.is_file() else None
    )
    seats = result.get("seat_results") or []
    verifier = result.get("verifier") or {}
    play = result.get("agent_execution")
    receipt.update(
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
        all_seats_frozen_before_verifier=_freeze_before_verifier(
            seats, play=play, verifier=verifier
        ),
    )


def _audit_timing(
    audit: dict[str, Any], receipt: dict[str, Any]
) -> dict[str, Any] | None:
    intervention = receipt.get("intervention")
    if intervention is None:
        return None
    fault = intervention["signal_ns"] or intervention["timeout_fired_ns"]
    fault_wall = (
        intervention["signal_wall_time_ns"]
        or intervention["timeout_fired_wall_time_ns"]
    )
    first = audit.get("first_delta_ns")
    terminal = audit.get("terminal_ns")
    active = None
    if fault is not None and first is not None and terminal is not None:
        active = first <= fault < terminal
    ended = (receipt.get("native_agent_execution") or {}).get("finished_at")
    before_play_end = None
    if fault_wall is not None and ended is not None:
        before_play_end = fault_wall < round(
            datetime.fromisoformat(ended).timestamp() * 1e9
        )
    return {
        "intervention_ns": fault,
        "intervention_wall_time_ns": fault_wall,
        "gateway_first_delta_ns": first,
        "gateway_terminal_ns": terminal,
        "gateway_active_at_intervention": active,
        "intervention_before_native_play_end": before_play_end,
    }


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
    directory: Path, condition: Condition, task: Path, key: str, receipt: dict[str, Any]
) -> None:
    gateway = DeepSeekGateway(
        directory / "gateway",
        key,
        max_calls=len(_CALLS),
        max_prompt_bytes=_MAX_PROMPT_BYTES,
        max_output_tokens=_OUTPUT_RESERVE,
        api_timeout_sec=90.0,
    )
    exchange = prepare_case(directory=directory, condition=condition, gateway=gateway)
    observer = _NativeObserver(directory, exchange, receipt)
    records: dict[str, dict[str, Any]] = {}
    stage = "gateway_start"
    try:
        gateway.start()
        stage = "native_job"
        await _run_job(observer, condition, task)
    except Exception as error:
        _record_error(receipt, stage, error)
    finally:
        try:
            if receipt.get("native_job_ended_ns") is not None:
                try:
                    observer.after_job()
                    write_json(directory / "docker_teardown.json", observer.docker)
                    receipt["docker_teardown_path"] = "docker_teardown.json"
                except Exception as error:
                    _record_error(receipt, "docker_teardown_observation", error)
                records = _drain(observer)
        finally:
            try:
                gateway.close()
            except Exception as error:
                _record_error(receipt, "gateway_close", error, stop=True)
            clear_case(exchange)
        receipt["worker_processes"] = exchange.worker_records
        receipt["worker_usage"] = {
            call_id: exchange.worker_results.get(call_id, {}).get("usage")
            for call_id in _CALLS
        }
        receipt["builder_gate"] = exchange.gate
        receipt["intervention"] = exchange.intervention
        receipt["intervention_path"] = (
            "intervention.json" if exchange.intervention is not None else None
        )
        receipt["gateway_usage"] = _gateway_usage(directory, records, receipt)
        try:
            _capture_native(directory, condition, receipt)
        except Exception as error:
            _record_error(receipt, "native_result", error)
        receipt["audit_timing"] = _audit_timing(records.get(AUDIT) or {}, receipt)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one native DeepSeek support failure trial."
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
        "schema": "kit-check-support-failure/v1",
        "condition": args.condition,
        "model": f"{PROVIDER}/{MODEL}",
        "task_path": str(task),
        "source_base_commit": _SOURCE_BASE,
        "overlay_path": str(_OVERLAY),
        "lifecycle_path": journal.name,
        "planned_trial_reserve_usd": len(_CALLS) * _USD_RESERVE_PER_CALL,
        "planned_cohort_reserve_usd": cohort_reserve,
        "cohort_cap_usd": _COHORT_CAP_USD,
        "native_job_return_ns": None,
        "native_result_path": None,
        "native_reward_valid": None,
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
        asyncio.run(_run(directory, args.condition, task, key, receipt))
    except (Exception, KeyboardInterrupt, asyncio.CancelledError) as error:
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
