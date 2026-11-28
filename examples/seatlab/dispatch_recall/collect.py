from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from harbor.models.job.lock import TrialLock
from harbor.models.trial.paths import TrialPaths
from harbor.models.trial.result import TrialResult

from ...native_study import native, record_bytes
from ..exhibit_capture import read_record
from ..exhibit_pairs import DIR_FLAGS, open_folder
from . import participant
from .design import check_plan
from .native import Session, diagnose
from .prepare import CONFIG, LOCK, RECEIPT, ROLES, records

SCHEMA = "seatlab.dispatch-recall.collected/v1"
VERDICT = "harbor.dispatch-recall.verdict/v1"
PRIVATE_CUT = "private-recall-cut.json"
TRIALS = "trials"
STATUSES = ("no_trial", "unfinished", "no_retained_cut", "verified", "rejected")
EVIDENCE = ("free_team", "scripted_control")
VERIFIER_FILES = {
    "cut": PRIVATE_CUT,
    "reward": "reward.json",
    "verdict": "verdict.json",
    "diagnostics": "diagnostics.json",
}
HASH_KEYS = {
    "cut": "private_cut",
    "reward": "reward",
    "verdict": "verdict",
    "diagnostics": "diagnostics",
}
METHOD_KEYS = (
    "team_agents",
    "native_digest",
    "task_tree_digest",
    "source_digest",
    "host_harness_digest",
)
RECEIPT_DIGEST = re.compile(r"[0-9a-f]{64}")
INFRASTRUCTURE = {
    "schema": VERDICT,
    "disposition": "infrastructure_error",
    "reward": None,
    "closed": None,
}
NATIVE_SCALAR = {
    "committed": "physical_commit_reward",
    "missing_commit": "grade_zero_without_commit_physical_reward_undefined",
}
LIMITS = (
    (
        "Consistency of local unsigned records with one prepared slot; not proof "
        "of provider or model identity, of what a seat read or attended to, or of "
        "participant independence."
    ),
    "A scripted control is inspectable here but is never free-team evidence.",
    (
        "no_trial, unfinished and no_retained_cut carry no physical outcome; "
        "their cause is the recorded status and exceptions only."
    ),
)
READ_FAILURES = frozenset(
    {
        "invalid artifact file",
        "artifact changed",
        "invalid artifact record",
        "duplicate JSON key",
        "nonfinite JSON number",
    }
)
UNSET = dict.fromkeys(
    (
        "exceptions",
        "reward_valid",
        "disposition",
        "native_reward",
        "native_reward_meaning",
        "physical",
        "closed",
        "owner_requests",
        "reason",
    )
)

Found = tuple[dict[str, Any], bytes] | None
Inspection = tuple[str, dict[str, Any], dict[str, Any] | None]


class Rejected(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Rejected(message)


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _same(left: Any, right: Any, message: str) -> None:
    require(_text(left) == _text(right), message)


def _slot(plan: dict[str, Any], job_id: str) -> dict[str, Any]:
    check_plan(plan)
    require(type(job_id) is str, "job id must be text")
    matches = [job for job in plan["jobs"] if job["job_id"] == job_id]
    require(len(matches) == 1, "the plan holds no such assignment")
    return matches[0]


def _optional(folder: int, name: str) -> Found:
    try:
        return read_record(folder, name)
    except FileNotFoundError:
        return None


def _rebuild(
    plan: dict[str, Any],
    job_id: str,
    prepared: Path,
    config: dict[str, Any],
    stored: dict[str, bytes],
) -> None:
    try:
        rebuilt = records(
            plan,
            job_id,
            Path(config["task"]["path"]),
            {"participants": config["participants"]},
            prepared,
            live_endpoints=False,
        )
    except (ValueError, OSError, TypeError, KeyError) as error:
        raise ValueError(f"prepared binding differs: {type(error).__name__}") from None
    for name, data in stored.items():
        require(
            record_bytes(rebuilt[name]) == data,
            f"prepared {name} differs from its rebuild",
        )


def _provenance(receipt: dict[str, Any], expected: str) -> dict[str, Any]:
    return {
        "plan_sha256": receipt["plan_sha256"],
        "receipt_sha256": expected,
        "prepared_config": receipt["config_sha256"],
        "prepared_lock": receipt["lock_sha256"],
        "roster": receipt["roster_sha256"],
        "team_agents": receipt["team"]["agents"],
        "task_name": receipt["task_name"],
        "native_digest": receipt["native_digest"],
        "task_checksum": receipt["task_checksum"],
        "task_tree_digest": receipt["task_tree_digest"],
        "role_instruction_digests": receipt["role_instruction_digests"],
        "source_digest": receipt["source_digest"],
        "host_harness_digest": receipt["host_harness_digest"],
        "native_trial_id": None,
        "trial": {},
    }


def prepared_slot(
    plan: dict[str, Any], job_id: str, folder: int, prepared: Path
) -> tuple[dict[str, dict[str, Any]], str]:
    config, config_bytes = read_record(folder, CONFIG)
    lock, lock_bytes = read_record(folder, LOCK)
    receipt, receipt_bytes = read_record(folder, RECEIPT)
    stored = {CONFIG: config_bytes, LOCK: lock_bytes, RECEIPT: receipt_bytes}
    _rebuild(plan, job_id, prepared, config, stored)
    trials = Path(config["trials_dir"])
    require(
        trials.name == TRIALS and config["trial_name"] == job_id,
        "configured trial path differs from the slot",
    )
    held, opened = os.stat(trials.parent, follow_symlinks=False), os.fstat(folder)
    require(
        (held.st_dev, held.st_ino) == (opened.st_dev, opened.st_ino),
        "prepared directory differs from the configured trials parent",
    )
    digest = hashlib.sha256(receipt_bytes).hexdigest()
    return {CONFIG: config, LOCK: lock, RECEIPT: receipt}, digest


def _evidence(team: dict[str, Any]) -> str:
    kind, scripted, eligible = (
        team["kind"],
        team["scripted"],
        team["free_team_eligible"],
    )
    require(
        type(scripted) is bool
        and type(eligible) is bool
        and scripted == (kind == "scripted_control")
        and eligible == (kind == "host_adapter")
        and scripted != eligible,
        "prepared team kind and flags disagree",
    )
    return EVIDENCE[0] if eligible else EVIDENCE[1]


def _bind(
    plan: dict[str, Any], job_id: str, folder: int, prepared: Path, expected: str
) -> dict[str, Any]:
    slot, digest = prepared_slot(plan, job_id, folder, prepared)
    require(digest == expected, "prepared receipt differs from the expected receipt")
    receipt = slot[RECEIPT]
    return {
        "receipt": receipt,
        "config": native().TrialConfig.model_validate(slot[CONFIG]),
        "lock": slot[LOCK],
        "evidence": _evidence(receipt["team"]),
        "provenance": _provenance(receipt, expected),
    }


def _archived(trial: int, bound: dict[str, Any], hashes: dict[str, str]) -> None:
    config = bound["config"]
    paths = TrialPaths(Path(config.trials_dir) / config.trial_name)
    stored = _optional(trial, paths.config_path.name)
    if stored is not None:
        expected = config.model_dump(mode="json", exclude={"agent"})
        _same(stored[0], expected, "archived config differs")
        hashes["config"] = _sha(stored[1])
    locked = _optional(trial, paths.lock_path.name)
    if locked is not None:
        require(
            TrialLock.model_validate(locked[0])
            == TrialLock.model_validate(bound["lock"]),
            "archived lock differs",
        )
        hashes["lock"] = _sha(locked[1])


def _seats(result: TrialResult, agents: dict[str, Any], graded: bool) -> None:
    seats = result.seat_results or []
    require(
        len(seats) == len(ROLES)
        and {seat.seat for seat in seats} == set(ROLES)
        and all(seat.under_test for seat in seats)
        and result.step_results is None
        and result.agent_result is None,
        "native seat roster differs",
    )
    for seat in seats:
        info, declared = seat.agent_info, agents[seat.seat]
        if info is None:
            require(not graded, "a graded seat has no native agent record")
            continue
        adapter = declared.get("import_path") == participant.IMPORT_PATH
        require(
            ("name" not in declared or info.name == declared["name"])
            and (not adapter or info.name == participant.RecallParticipant.name())
            and info.model_info is None
            and "model_name" not in declared,
            "native seat agent differs from the prepared roster",
        )


def _result(raw: dict[str, Any], bound: dict[str, Any], graded: bool) -> TrialResult:
    config, receipt = bound["config"], bound["receipt"]
    result = TrialResult.model_validate(raw)
    dumped = result.model_dump(mode="json")
    _same(dumped, raw, "noncanonical native result")
    expected = config.model_dump(mode="json")
    _same(dumped["config"], expected, "native result config differs")
    trial = Path(config.trials_dir) / config.trial_name
    mode = bound["lock"]["verifier"].get("environment_mode")
    require(
        result.trial_name == config.trial_name
        and result.trial_uri == trial.as_uri()
        and result.task_name == receipt["task_name"]
        and result.task_checksum == receipt["task_checksum"]
        and result.task_id == config.task.get_task_id()
        and result.source == config.task.source
        and dumped["verifier_environment_mode"] == mode,
        "native result identity differs",
    )
    start, finish = result.started_at, result.finished_at
    require(
        start is not None and finish is not None and start <= finish,
        "native result is not finished",
    )
    _seats(result, receipt["team"]["agents"], graded)
    return result


def _exceptions(result: TrialResult) -> list[dict[str, str]]:
    found = []
    if result.exception_info is not None:
        found.append({"where": "trial", "type": result.exception_info.exception_type})
    for seat in result.seat_results or []:
        if seat.exception_info is not None:
            found.append(
                {"where": seat.seat, "type": seat.exception_info.exception_type}
            )
    return found


def _verdict(diagnosis: dict[str, Any]) -> dict[str, Any]:
    reward = diagnosis["reward"]
    if reward is None:
        return {
            "schema": VERDICT,
            "disposition": "missing_commit",
            "reward": 0,
            "closed": False,
        }
    return {
        "schema": VERDICT,
        "disposition": "committed",
        "reward": reward,
        "closed": diagnosis["loader"]["closed"],
    }


def _physical(reward: int | None) -> str:
    if reward is None:
        return "uncommitted"
    return "correct" if reward == 1 else "wrong"


def _layout(cut: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    try:
        diagnosis = diagnose(cut)
    except (ValueError, TypeError, KeyError):
        raise Rejected("retained cut fails the task diagnosis") from None
    session = job["session"]
    require(
        cut["seed"] == session["seed"]
        and diagnosis["condition"]
        == {"transition": session["transition"], "timing": session["timing"]},
        "cut seed or condition differs from the assignment",
    )
    seeded = Session(session["seed"], session["transition"], session["timing"])
    layout = seeded.snapshot()
    require(
        list(cut["bays"]) == layout["bays"]
        and cut["initial_bay"] == layout["initial_bay"],
        "cut layout differs from the seeded layout",
    )
    return diagnosis


def _graded(
    files: dict[str, Found],
    job: dict[str, Any],
    result: TrialResult,
    hashes: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    present = {name: found for name, found in files.items() if found is not None}
    require(set(present) == set(VERIFIER_FILES), "retained cut lacks verifier records")
    cut = present["cut"][0]
    diagnosis = _layout(cut, job)
    verdict = _verdict(diagnosis)
    _same(
        present["diagnostics"][0], diagnosis, "native diagnostics differ from the cut"
    )
    _same(present["verdict"][0], verdict, "native verdict differs from the cut")
    reward = {"reward": verdict["reward"]}
    _same(present["reward"][0], reward, "native reward file differs from the cut")
    rewards = None if result.verifier_result is None else result.verifier_result.rewards
    require(
        result.verifier is not None
        and rewards is not None
        and set(rewards) == {"reward"}
        and rewards["reward"] == verdict["reward"],
        "native result reward differs from the verifier records",
    )
    for name, (_, data) in present.items():
        hashes[HASH_KEYS[name]] = _sha(data)
    return cut, {
        "disposition": verdict["disposition"],
        "native_reward": verdict["reward"],
        "native_reward_meaning": NATIVE_SCALAR[verdict["disposition"]],
        "physical": {
            "status": _physical(diagnosis["reward"]),
            "reward": diagnosis["reward"],
        },
        "closed": verdict["closed"],
        "diagnosis": diagnosis,
    }


def _ungraded(
    files: dict[str, Found], result: TrialResult, hashes: dict[str, str]
) -> dict[str, Any]:
    require(
        files["reward"] is None and files["diagnostics"] is None,
        "verifier records exist without a retained cut",
    )
    rewards = None if result.verifier_result is None else result.verifier_result.rewards
    require(not rewards, "native result claims a reward without a retained cut")
    disposition = None
    verdict = files["verdict"]
    if verdict is not None:
        _same(verdict[0], INFRASTRUCTURE, "verdict without a cut is not an error")
        disposition = INFRASTRUCTURE["disposition"]
        hashes["verdict"] = _sha(verdict[1])
    return {"disposition": disposition, "native_reward": None, "closed": None}


def _verifier_files(trial: int, name: str) -> dict[str, Found]:
    try:
        folder = open_folder(trial, (name,))
    except FileNotFoundError:
        return dict.fromkeys(VERIFIER_FILES)
    try:
        return {key: _optional(folder, leaf) for key, leaf in VERIFIER_FILES.items()}
    finally:
        os.close(folder)


def _owner(
    cut: dict[str, Any], result: TrialResult, bound: dict[str, Any]
) -> dict[str, Any]:
    if bound["evidence"] != EVIDENCE[0]:
        return {"status": "not_applicable"}
    from .owner_evidence import owner_evidence

    task = native().Task(Path(bound["config"].task.path))
    instructions = {role: task.seat_instruction(role) for role in ROLES}
    metadata = {
        seat.seat: None if seat.agent_result is None else seat.agent_result.metadata
        for seat in result.seat_results or []
    }
    try:
        return owner_evidence(cut, instructions, metadata)
    except (ValueError, TypeError, KeyError):
        return {
            "status": "inconsistent",
            "reason": "recorded owner requests contradict the native cut",
        }


def _inspect(trial: int, job: dict[str, Any], bound: dict[str, Any]) -> Inspection:
    provenance = bound["provenance"]
    hashes = provenance["trial"]
    _archived(trial, bound, hashes)
    config = bound["config"]
    paths = TrialPaths(Path(config.trials_dir) / config.trial_name)
    stored = _optional(trial, paths.result_path.name)
    if stored is None:
        return "unfinished", {}, None
    require(
        "config" in hashes and "lock" in hashes, "finished trial lacks config or lock"
    )
    files = _verifier_files(trial, paths.verifier_dir.name)
    result = _result(stored[0], bound, files["cut"] is not None)
    hashes["result"] = _sha(stored[1])
    provenance["native_trial_id"] = str(result.id)
    facts = {"exceptions": _exceptions(result), "reward_valid": result.reward_valid}
    if files["cut"] is None:
        return "no_retained_cut", facts | _ungraded(files, result, hashes), None
    cut, observed = _graded(files, job, result, hashes)
    owner = {"owner_requests": _owner(cut, result, bound)}
    return "verified", facts | observed | owner, cut


def _reason(error: BaseException) -> str:
    if isinstance(error, Rejected):
        return str(error)
    if isinstance(error, ValidationError):
        return "native record failed model validation"
    if isinstance(error, json.JSONDecodeError):
        return "native record is not valid JSON"
    if isinstance(error, ValueError) and str(error) in READ_FAILURES:
        return "native record: " + str(error)
    return "native record raised " + type(error).__name__


def _trial(folder: int, job: dict[str, Any], bound: dict[str, Any]) -> Inspection:
    try:
        trial = open_folder(folder, (TRIALS, job["job_id"]))
    except FileNotFoundError:
        return "no_trial", {}, None
    except OSError as error:
        return "rejected", {"reason": _reason(error)}, None
    try:
        return _inspect(trial, job, bound)
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
        bound["provenance"]["trial"].clear()
        bound["provenance"]["native_trial_id"] = None
        return "rejected", {"reason": _reason(error)}, None
    finally:
        os.close(trial)


def _record(
    job: dict[str, Any], status: str, evidence: str, observed: dict[str, Any]
) -> dict[str, Any]:
    session = job["session"]
    return {
        "job_id": job["job_id"],
        "position": job["position"],
        "condition": {"transition": session["transition"], "timing": session["timing"]},
        "status": status,
        "evidence": evidence,
        **UNSET,
        **observed,
        "limits": list(LIMITS),
    }


def collect(
    plan: dict[str, Any], job_id: str, prepared: Path, expected_receipt: str
) -> dict[str, Any]:
    require(
        type(expected_receipt) is str
        and RECEIPT_DIGEST.fullmatch(expected_receipt) is not None,
        "expected receipt must be a bare sha256 hex digest",
    )
    require(isinstance(prepared, Path), "prepared must be a path")
    job = _slot(plan, job_id)
    prepared = prepared.expanduser().absolute()
    folder = os.open(prepared, DIR_FLAGS)
    try:
        bound = _bind(plan, job_id, folder, prepared, expected_receipt)
        status, observed, cut = _trial(folder, job, bound)
    finally:
        os.close(folder)
    assert status in STATUSES and (cut is not None) == (status == "verified")
    evidence = bound["evidence"]
    return {
        "schema": SCHEMA,
        "job_id": job_id,
        "status": status,
        "evidence": evidence,
        "record": _record(job, status, evidence, observed),
        "provenance": bound["provenance"],
        "cut": cut,
    }


def outcomes(
    plan: dict[str, Any], collected: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    check_plan(plan)
    require(
        type(collected) is list and len(collected) <= len(plan["jobs"]),
        "collected must be a list bounded by the plan",
    )
    digest = hashlib.sha256(record_bytes(plan)).hexdigest()
    planned = {job["job_id"] for job in plan["jobs"]}
    cuts: dict[str, dict[str, Any]] = {}
    methods: set[str] = set()
    seen: set[str] = set()
    for item in collected:
        require(type(item) is dict and item.get("schema") == SCHEMA, "not a collection")
        job_id = item["job_id"]
        require(job_id in planned and job_id not in seen, "unplanned or repeated job")
        seen.add(job_id)
        provenance = item["provenance"]
        require(provenance["plan_sha256"] == digest, "collected job binds another plan")
        require(item["status"] in STATUSES[:4], f"rejected native archive for {job_id}")
        require(
            item["evidence"] == EVIDENCE[0], "scripted control is not team evidence"
        )
        methods.add(_text({key: provenance[key] for key in METHOD_KEYS}))
        require(len(methods) == 1, "collected jobs mix team methods or task sources")
        if item["status"] == "verified":
            cuts[job_id] = item["cut"]
    return cuts
