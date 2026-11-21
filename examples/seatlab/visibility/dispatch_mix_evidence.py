import importlib
import re
import sqlite3
import stat
from collections import Counter
from contextlib import closing
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any, TypeGuard

from .. import evidence, run_panel, team_driver, team_report
from ..capsule import Capsule, view_hash
from ..export_capsules import reconstructed_capsules
from . import dispatch_mix_plan as plan
from . import spend

_AGENT = "seatlab-mixed-model"
_VERSION = "1.0.0"
_TOKENS = ("n_input_tokens", "n_cache_tokens", "n_output_tokens")
_DIGESTS = ("view_sha256", "prompt_sha256", "decision_sha256")
_STAGES = team_report._MODEL_STAGES | {
    "balance_below_minimum",
    "balance_unverified",
    "budget_guard",
    "direct_failed",
    "direct_pending",
    "direct_unavailable",
    "invalid_model_decisions",
    "key_already_bound",
    "key_unavailable",
    "payload_invalid",
    "payload_too_large",
    "provider_response_invalid",
    "provider_timeout",
    "provider_unavailable",
    "served_model_unverified",
    "usage_unverified",
}
_NO_EVIDENCE = (OSError, ValueError, KeyError, TypeError, IndexError, RuntimeError)
_CELLS = {cell["cell_id"]: cell for cell in plan.grid()}


@cache
def _native_pairs() -> ModuleType:
    return importlib.import_module(f"{__package__}.native_pairs")


def _present(path: Path, *, directory: bool = False) -> bool:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return False
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(details.st_mode) or (not directory and details.st_nlink != 1):
        raise ValueError("aliased or invalid native path")
    return True


def _attempted(root: Path, trial: Path, returncode: int | None) -> bool:
    directory = trial.parent.parent
    for path in (root, directory.parent, directory):
        if not _present(path, directory=True):
            return returncode is not None
    markers = [
        _present(directory / name)
        for name in ("attempt.json", "native.log", "outcome.json")
    ]
    trials = _present(trial.parent, directory=True)
    if trials and any(entry != trial for entry in trial.parent.iterdir()):
        raise ValueError("unexpected native trial path")
    return trials or any(markers) or returncode is not None


def _dossier(trial: Path) -> dict[str, Any] | None:
    directory = trial
    for name in ("artifacts", "logs", "world"):
        directory /= name
        if not _present(directory, directory=True):
            return None
    source = directory / "handoff.json"
    return team_report._read_object(source) if _present(source) else None


def _native_identity(
    native: dict[str, Any], expected: dict[str, Any], trial: Path, cell: plan.Cell
) -> str:
    team_report._check_config(native.get("config"), expected)
    directory = trial.parent.parent
    task = directory / "dispatch-handoff"
    _present(task, directory=True)
    if (
        not run_panel._config_matches(native.get("schema_version", 1), 1)
        or native.get("agent_result") is not None
        or native.get("step_results") is not None
        or expected.get("trials_dir") != str(trial.parent)
        or expected["task"].get("path") != str(task)
        or expected.get("trial_name") != trial.name
        or native.get("trial_name") != trial.name
        or native.get("trial_uri") != trial.as_uri()
        or native.get("task_id") != {"path": str(task)}
    ):
        raise ValueError("foreign native trial")
    participants = evidence._by_role(expected["participants"], "participants")
    for role, participant in participants.items():
        if (
            participant.get("model_name") != plan.model_for(cell, role)
            or participant.get("name") not in (None, _AGENT)
            or participant["kwargs"].get("run_id") != cell["cell_id"]
        ):
            raise ValueError("wrong native participant assignment")
    return evidence._trial_id(native)


def _original_reward(native: dict[str, Any]) -> int | float | None:
    reward_valid = native.get("reward_valid")
    if reward_valid is not None and type(reward_valid) is not bool:
        raise ValueError("invalid native reward status")
    verifier = native.get("verifier_result")
    if verifier is None:
        if reward_valid is True:
            raise ValueError("valid native reward without verifier")
        return None
    if not isinstance(verifier, dict):
        raise ValueError("invalid native verifier result")
    rewards = verifier.get("rewards")
    if rewards in (None, {}) and reward_valid is not True:
        return None
    if not isinstance(rewards, dict) or rewards.keys() != {"reward"}:
        raise ValueError("invalid native rewards")
    reward: object = rewards["reward"]
    if (type(reward) is not int and type(reward) is not float) or not 0 <= reward <= 1:
        raise ValueError("invalid native reward")
    return reward if reward_valid is True else None


def _is_case_list(value: object) -> TypeGuard[list[dict[str, object]]]:
    return isinstance(value, list) and all(
        isinstance(case, dict) and all(isinstance(key, str) for key in case)
        for case in value
    )


def _view_cases(scope: Capsule) -> list[dict[str, object]]:
    view = scope["view"]
    if not isinstance(view, dict):
        raise ValueError("invalid capsule view")
    cases: object = view["cases"]
    if not _is_case_list(cases):
        raise ValueError("invalid capsule cases")
    return cases


def _check_world(world: dict[str, Any], cell: plan.Cell) -> None:
    expected = {
        "schema": "seatlab.scenario/v1",
        "seed": cell["seed"],
        "episodes": plan.EPISODES,
        "condition": cell["condition"],
    }
    if (
        world.get("schema") != "seatlab.world/v1"
        or not run_panel._config_matches(world.get("settings"), expected)
        or type(world.get("complete")) is not bool
    ):
        raise ValueError("foreign native dossier")


def _score(
    trial: Path,
    cell: plan.Cell,
    native: dict[str, Any],
    world: dict[str, Any] | None,
    records: dict[str, dict[str, Any]],
    row: dict[str, Any],
) -> dict[str, Capsule] | None:
    reward = _original_reward(native)
    if world is None:
        return None
    if not world["complete"]:
        if reward is not None:
            raise ValueError("valid native reward with unfinished dossier")
        return None
    if reward is None:
        evidence._score(world)
        return None
    registered = team_report._registered_fingerprint(cell["seed"], plan.EPISODES)
    if any(records.get(role, {}).get("agent_result") is None for role in plan.ROLES):
        evidence._require_success(native, "trial")
        score = evidence._score(world)
        fingerprint = view_hash(
            [
                {key: case[key] for key in ("id", "utilities", "before", "after")}
                for case in world["cases"]
            ]
        )
    else:
        receipt, checked_world = evidence.read_trial_with_world(trial)
        if receipt["trial_id"] != row["trial_id"] or checked_world != world:
            raise ValueError("native evidence changed during inspection")
        score, fingerprint = receipt["score"], receipt["fingerprint"]
    if (
        score["episodes"] != plan.EPISODES
        or fingerprint != registered
        or reward != score["optimal"] / plan.EPISODES
    ):
        raise ValueError("native dossier does not support this trial reward")
    row.update(native_score=reward, fingerprint=fingerprint)
    row["actions"] = [
        {**grade, "choice": case["chosen"]}
        for grade, case in zip(score["rows"], world["cases"], strict=True)
    ]
    scopes = dict[str, Capsule](reconstructed_capsules(world))
    row["inventory_exact"] = sum(
        actual["reports"] == observed["reports"]
        for actual, observed in zip(
            world["cases"], _view_cases(scopes["inventory"]), strict=True
        )
    )
    return scopes


def _served(model: str, value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if model == plan.CLAUDE:
        return value if value == plan.CLAUDE.split("/", 1)[1] else None
    if model == plan.DIRECT and value.isascii():
        canonical = value.lower()
        return canonical if canonical in _native_pairs()._SERVED_ENGINES else None
    return None


def _seat_context(
    record: dict[str, Any], role: str, seat: dict[str, Any]
) -> dict[str, Any] | None:
    provider, short = seat["model"].split("/", 1)
    expected = {
        "name": _AGENT,
        "version": _VERSION,
        "model_info": {"name": short, "provider": provider},
    }
    if record.get("under_test") is not True:
        raise ValueError("unexpected native seat participation")
    identity = record.get("agent_info")
    if identity is not None and identity != expected:
        raise ValueError("wrong native seat assignment")
    context = record.get("agent_result")
    if context is None:
        return None
    if not isinstance(context, dict) or identity is None:
        raise ValueError("invalid native seat result")
    for field in _TOKENS:
        seat[field] = evidence._token_count(context, role, field)
    total, hit, _ = (seat[field] for field in _TOKENS)
    if total is not None and hit is not None and hit > total:
        raise ValueError("invalid native cache token count")
    return context


def _seat_metadata(
    context: dict[str, Any], role: str, cell: plan.Cell, seat: dict[str, Any]
) -> dict[str, Any] | None:
    metadata = context.get("metadata")
    if metadata is None:
        return None
    pairs = _native_pairs()
    expected = {
        "schema": pairs._SEAT_SCHEMA,
        "role": role,
        "run_id": cell["cell_id"],
        "requested_model": seat["model"].split("/", 1)[1],
    }
    optional = {"telemetry"} if seat["model"] == plan.CLAUDE else set()
    if not isinstance(metadata, dict) or not metadata.keys() <= (
        expected.keys()
        | optional
        | set(_DIGESTS)
        | {"served_model", "stage", "model_turns"}
    ):
        raise ValueError("invalid native invocation fields")
    stage, turns = metadata.get("stage"), metadata.get("model_turns")
    if stage is not None and (not isinstance(stage, str) or stage not in _STAGES):
        raise ValueError("unknown native invocation stage")
    if turns is not None and (type(turns) is not int or turns not in (0, 1)):
        raise ValueError("invalid native invocation turn count")
    for key, value in expected.items():
        actual = metadata.get(key)
        if actual is not None and actual != value:
            raise ValueError("wrong native invocation assignment or scope")
    identity_complete = all(metadata.get(key) is not None for key in expected)
    if identity_complete:
        seat["stage"] = stage
    served = _served(seat["model"], metadata.get("served_model"))
    if metadata.get("served_model") is not None and served is None:
        raise ValueError("foreign native served identity")
    for key in _DIGESTS:
        value = metadata.get(key)
        if value is not None and (
            not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
        ):
            raise ValueError("invalid native invocation digest")
    telemetry = metadata.get("telemetry")
    if telemetry is not None:
        metrics = team_report._telemetry(telemetry)
        estimate = metrics["estimated_cost_usd"]
        cost = context.get("cost_usd")
        if (
            estimate is not None
            and cost is not None
            and (type(cost) not in (int, float) or cost != estimate)
        ):
            raise ValueError("native Claude estimate differs from CLI telemetry")
        if identity_complete:
            seat["cli_estimated_usd"] = estimate
    if stage == "complete" and turns == 0:
        raise ValueError("completed native invocation has no model turn")
    if identity_complete and stage == "complete" and turns == 1 and served is not None:
        return metadata
    return None


def _bind_seat(
    metadata: dict[str, Any],
    role: str,
    seat: dict[str, Any],
    scope: Capsule,
    world: dict[str, Any],
) -> bool:
    pairs = _native_pairs()
    field, dossier_field = (
        ("bay", "chosen") if role == "dispatch" else ("reports", "reports")
    )
    decisions = [
        {"id": case["id"], field: case[dossier_field]} for case in world["cases"]
    ]
    digests = {
        "prompt_sha256": pairs._prompt_hash(pairs._prompt(role, _view_cases(scope))),
        "view_sha256": scope["sha256"],
        "decision_sha256": pairs._decision_hash(role, decisions),
    }
    for field, value in digests.items():
        if metadata.get(field) not in (None, value):
            raise ValueError("native invocation differs from the bound decision")
    if scope["basis"] != "bound" or any(
        metadata.get(field) is None for field in digests
    ):
        return False
    seat.update(digests, served_model=_served(seat["model"], metadata["served_model"]))
    return True


def _inspect(
    trial: Path, cell: plan.Cell, expected: dict[str, Any], row: dict[str, Any]
) -> bool:
    if not _present(trial, directory=True):
        return False
    world = _dossier(trial)
    if world is not None:
        _check_world(world, cell)
    result = trial / "result.json"
    if not _present(result):
        return False
    native = team_report._read_object(result)
    row["trial_id"] = _native_identity(native, expected, trial, cell)
    entries = native.get("seat_results")
    records = evidence._by_role(entries, "seat_results") if entries is not None else {}
    scopes = _score(trial, cell, native, world, records, row)
    if not records:
        return False
    complete = True
    for role, record in records.items():
        seat = row["seats"][role]
        context = _seat_context(record, role, seat)
        metadata = (
            _seat_metadata(context, role, cell, seat) if context is not None else None
        )
        bound = (
            metadata is not None
            and scopes is not None
            and world is not None
            and _bind_seat(metadata, role, seat, scopes[role], world)
        )
        complete = bound and complete
    return complete


def inspect_cell(
    root: Path,
    cell: plan.Cell,
    expected_config: dict[str, Any],
    native_returncode: int | None,
) -> dict[str, Any]:
    registered = _CELLS.get(cell["cell_id"])
    if (
        registered is None
        or not run_panel._config_matches(cell, registered)
        or cell.keys() != registered.keys()
    ):
        raise ValueError("unregistered native mixed cell")
    row: dict[str, Any] = {
        **registered,
        "status": "missing",
        "trial_id": None,
        "native_score": None,
        "fingerprint": None,
        "inventory_exact": None,
        "actions": None,
        "seats": {
            role: {
                "model": plan.model_for(registered, role),
                **dict.fromkeys(("served_model", "stage", *_DIGESTS, *_TOKENS)),
                **(
                    {"cli_estimated_usd": None}
                    if plan.model_for(registered, role) == plan.CLAUDE
                    else {}
                ),
            }
            for role in plan.ROLES
        },
    }
    try:
        root = team_driver._root(root)
        trial = team_driver._trial_dir(root, registered)
        if _attempted(root, trial, native_returncode):
            complete = _inspect(trial, registered, expected_config, row)
            row["status"] = (
                "valid"
                if complete
                and type(native_returncode) is int
                and native_returncode == 0
                else "incomplete"
            )
    except _NO_EVIDENCE:
        row["status"] = "invalid_evidence"
    return row


def _ledger_calls(root: Path) -> list[sqlite3.Row]:
    root = root.expanduser()
    if root.is_symlink():
        raise ValueError("aliased direct ledger root")
    ledger = spend._location(root.resolve())
    if not _present(ledger):
        raise FileNotFoundError
    database = sqlite3.connect(f"{ledger.as_uri()}?mode=ro", uri=True, timeout=5)
    calls: list[sqlite3.Row] = []
    with closing(database) as db:
        db.row_factory = sqlite3.Row
        for call in db.execute(
            "SELECT run, role, served_model, hit, miss, output, cost_low, cost_high "
            "FROM calls WHERE run GLOB 'mx-*' ORDER BY run, role LIMIT 4097"
        ):
            if not isinstance(call, sqlite3.Row):
                raise ValueError("invalid direct ledger row")
            calls.append(call)
    keys = {(call["run"], call["role"]) for call in calls}
    if len(calls) > 4096 or len(keys) != len(calls):
        raise ValueError("invalid direct ledger scope")
    return calls


def _ledger_cost(call: sqlite3.Row) -> tuple[int, int, bool]:
    low: object = call["cost_low"]
    high: object = call["cost_high"]
    hit: object = call["hit"]
    miss: object = call["miss"]
    output: object = call["output"]
    if high is None and all(
        value is None for value in (low, hit, miss, output, call["served_model"])
    ):
        return 0, spend._HOLD, False
    if (
        type(low) is not int
        or type(high) is not int
        or type(hit) is not int
        or type(miss) is not int
        or type(output) is not int
        or min(low, high, hit, miss, output) < 0
        or low != hit * 3 + miss * 150 + output * 600
        or high not in (low, 2 * low)
        or high > spend._HOLD
    ):
        raise ValueError("invalid direct ledger costs")
    return low, high, True


def _matched(row: dict[str, Any], role: str, call: sqlite3.Row) -> bool:
    if row["status"] not in ("valid", "incomplete"):
        return False
    seat = row["seats"][role]
    served = _served(plan.DIRECT, call["served_model"])
    usage = (call["hit"] + call["miss"], call["hit"], call["output"])
    if not (
        seat["model"] == plan.DIRECT
        and seat["stage"] == "complete"
        and served is not None
        and seat["served_model"] == served
    ):
        return False
    return all(
        type(seat[field]) is int and seat[field] == count
        for field, count in zip(_TOKENS, usage, strict=True)
    )


def reconcile(rows: list[dict[str, Any]], spend_dir: Path | None) -> dict[str, Any]:
    counts = Counter(row["cell_id"] for row in rows)
    by_id = {row["cell_id"]: row for row in rows if row["cell_id"] in _CELLS}
    pending = {key for key, row in by_id.items() if row["status"] != "missing"}
    report = {
        "ledger_status": "missing",
        "matched": 0,
        "unexpected": 0,
        "unresolved": len(pending),
        "known_usd_low": None,
        "upper_usd_including_holds": None,
    }
    if spend_dir is None:
        return report
    try:
        calls = _ledger_calls(spend_dir)
        costs = [_ledger_cost(call) for call in calls]
    except FileNotFoundError:
        return report
    except (OSError, ValueError, RuntimeError, sqlite3.Error):
        report["ledger_status"] = "invalid"
        return report
    matched = unexpected = unresolved = total_low = total_high = 0
    for call, (low, high, settled) in zip(calls, costs, strict=True):
        total_low += low
        total_high += high
        key, role = call["run"], call["role"]
        if (
            key not in by_id
            or role not in plan.ROLES
            or plan.model_for(_CELLS[key], role) != plan.DIRECT
        ):
            unexpected += 1
            continue
        pending.discard(key)
        if settled and counts[key] == 1 and _matched(by_id[key], role, call):
            matched += 1
        else:
            unresolved += 1
    return {
        "ledger_status": "available",
        "matched": matched,
        "unexpected": unexpected,
        "unresolved": unresolved + len(pending),
        "known_usd_low": spend._usd(total_low),
        "upper_usd_including_holds": spend._usd(total_high),
    }
