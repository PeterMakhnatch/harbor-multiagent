"""Verify the registered native team study and reduce its paired contrasts."""

import json
import math
import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from runpy import run_path
from types import ModuleType
from typing import Any, Literal, TypedDict, cast

if __package__:
    from .capsule import Capsule, _reject_constant, _unique_object, view_hash
    from .evidence import TrialEvidence, _by_role, _trial_id, read_trial_with_world
    from .export_capsules import reconstructed_capsules
    from .run_panel import _config_matches
else:
    from capsule import Capsule, _reject_constant, _unique_object, view_hash
    from evidence import TrialEvidence, _by_role, _trial_id, read_trial_with_world
    from export_capsules import reconstructed_capsules
    from run_panel import _config_matches

_ROLES = ("inventory", "dispatch")
_MODEL = "claude-sonnet-5-5"
_DECLARED_MODEL = f"anthropic/{_MODEL}"
_VERSION = "1.0.0"
_TELEMETRY = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "output_tokens",
    "duration_ms",
    "duration_api_ms",
    "estimated_cost_usd",
)
_BEFORE_GENERATION = {
    "invalid_seat",
    "waiting_view",
    "invalid_view",
    "route_unverified",
}
_MODEL_STAGES = _BEFORE_GENERATION | {
    "generation",
    "generation_timeout",
    "cli_launch_failed",
    "budget_exhausted",
    "refused",
    "rate_limited",
    "cli_or_model_unsupported",
    "cli_failed",
    "invalid_cli_result",
    "unfinished",
    "model_unattested",
    "session_unattested",
    "telemetry_invalid",
    "budget_estimate_exceeded",
    "invalid_or_refused_decisions",
    "submission_unacknowledged",
    "complete",
}
_WORLD_PATH = (
    Path(__file__).resolve().parent.parent
    / "tasks/dispatch-handoff/environment/seatworld.py"
)


class SeatReport(TypedDict):
    actor: Literal["script", "model"]
    native_agent_name: str | None
    requested_model: str | None
    served_model: str | None
    stage: str | None
    model_turns: int | None
    accepted_view_sha256: str | None
    telemetry: dict[str, int | float | None]


class Episode(TypedDict):
    id: int
    choice: str
    optimal: bool
    feasible: bool
    availability_changed: bool
    revision_1_correct: bool
    revision_2_correct: bool
    delivered_messages_sha256: str


class Metrics(TypedDict):
    episodes: int
    optimal: int
    feasible: int
    inventory_copy_fidelity: dict[str, int]
    delivered_messages_sha256: str
    rows: list[Episode]


class CellReport(TypedDict):
    cell_id: str
    seed: int
    condition: str
    pairing: str
    model_actors: int
    attempted: bool
    status: Literal["measured", "unmeasured", "invalid", "not_attempted"]
    error: str | None
    native_returncode: int | None
    trial_id: str | None
    fingerprint: str | None
    seats: dict[str, SeatReport]
    metrics: Metrics | None


def _driver() -> ModuleType:
    # The launch driver imports this report, so defer the reverse import.
    if __package__:
        from . import team_driver
    else:
        import team_driver

    return team_driver


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def _within_trial(path: Path, trial: Path) -> bool:
    return path.resolve() == path and path.is_relative_to(trial)


@lru_cache(maxsize=4)
def _registered_fingerprint(seed: int, episodes: int) -> str:
    # Use the pinned task generator, not any code stored with a trial result.
    world_type = run_path(str(_WORLD_PATH))["World"]
    world = world_type(
        {
            "schema": "seatlab.scenario/v1",
            "seed": seed,
            "episodes": episodes,
            "condition": "fresh",
        }
    )
    return view_hash(
        [
            {key: case[key] for key in ("id", "utilities", "before", "after")}
            for case in world.evidence()["cases"]
        ]
    )


def _check_config(actual: object, expected: dict[str, Any]) -> None:
    if not isinstance(actual, dict):
        raise ValueError("missing native config")
    config = cast(dict[str, object], actual)
    # Harbor adds defaults on serialization. Match the registered fields and
    # reject overrides that could change the participants, task, or verifier.
    expected_participants = _by_role(expected["participants"], "plan.participants")
    actual_participants = _by_role(config.get("participants"), "config.participants")
    if not _config_matches(
        {key: value for key, value in config.items() if key != "participants"},
        {key: value for key, value in expected.items() if key != "participants"},
    ):
        raise ValueError("native config differs from the registered config")
    for role, participant in actual_participants.items():
        target = expected_participants[role]
        if not _config_matches(participant, target) or not _config_matches(
            target["kwargs"], participant["kwargs"]
        ):
            raise ValueError("native participant differs from the registered actor")
        for key, default in {
            "base_url": None,
            "model_api_key_name": None,
            "model_api_format": None,
            "skip_model_parsing": False,
            "resume_trajectory": False,
            "load_trajectory": None,
            "env": {},
            "mcp_servers": [],
            "skills": [],
            "extra_allowed_hosts": [],
        }.items():
            if key not in target and not _config_matches(
                participant.get(key, default), default
            ):
                raise ValueError("unexpected native participant settings")
    defaults: dict[str, dict[str, object]] = {
        "environment": {
            "import_path": None,
            "mounts": None,
            "extra_docker_compose": [],
            "env": {},
            "kwargs": {},
            "extra_allowed_hosts": [],
        },
        "verifier": {
            "import_path": None,
            "env": {},
            "kwargs": {},
            "extra_allowed_hosts": [],
        },
        "task": {"git_commit_id": None, "ref": None, "source": None},
    }
    for section, fields in defaults.items():
        value = config.get(section)
        if not isinstance(value, dict):
            raise ValueError("missing native config section")
        options = cast(dict[str, object], value)
        for key, value in fields.items():
            if key not in expected[section] and not _config_matches(
                options.get(key, value), value
            ):
                raise ValueError(
                    "unexpected native task, environment, or verifier settings"
                )
    for key, default in {
        "source_trial": None,
        "user_agent": None,
        "install_only": False,
        "extra_instruction_paths": [],
        "extra_instructions": [],
    }.items():
        if key not in expected and not _config_matches(
            config.get(key, default), default
        ):
            raise ValueError("unexpected native trial settings")


def _empty_seat(is_model: bool) -> SeatReport:
    return {
        "actor": "model" if is_model else "script",
        "native_agent_name": None,
        "requested_model": None,
        "served_model": None,
        "stage": None,
        "model_turns": None,
        "accepted_view_sha256": None,
        "telemetry": dict.fromkeys(_TELEMETRY),
    }


def _telemetry(value: object) -> dict[str, int | float | None]:
    if not isinstance(value, dict) or value.keys() != set(_TELEMETRY):
        raise ValueError("invalid model telemetry fields")
    counts = cast(dict[str, object], value)
    metrics: dict[str, int | float | None] = {}
    for key in _TELEMETRY:
        count = counts[key]
        if count is None:
            metrics[key] = None
        elif key == "estimated_cost_usd":
            if not (type(count) is float or type(count) is int):
                raise ValueError("invalid model estimate")
            if not 0 <= count < math.inf:
                raise ValueError("invalid model estimate")
            metrics[key] = count
        else:
            if type(count) is not int or not 0 <= count < 2**53:
                raise ValueError("invalid model telemetry count")
            metrics[key] = count
    return metrics


def _check_context_usage(
    context: dict[str, Any],
    telemetry: dict[str, int | float | None],
    *,
    model_attested: bool = False,
) -> None:
    components = [
        value for key in _TELEMETRY[:3] if isinstance(value := telemetry[key], int)
    ]
    fields: dict[str, int | float | None] = {
        "n_input_tokens": sum(components) if len(components) == 3 else None,
        "n_cache_tokens": telemetry["cache_read_input_tokens"],
        "n_output_tokens": telemetry["output_tokens"],
        "cost_usd": telemetry["estimated_cost_usd"],
    }

    def matches(record: dict[str, Any]) -> bool:
        for key, value in fields.items():
            actual = record.get(key)
            if key == "cost_usd" and value is not None:
                if type(actual) not in (int, float) or actual != value:
                    return False
            elif not _config_matches(actual, value):
                return False
        return True

    if not matches(context) or context.get("rollout_details") is not None:
        raise ValueError("native usage differs from the seat attestation")
    usage = context.get("model_usage")
    if model_attested and all(
        fields[key] is not None
        for key in ("n_input_tokens", "n_cache_tokens", "n_output_tokens")
    ):
        if (
            not isinstance(usage, dict)
            or usage.keys() != {_DECLARED_MODEL}
            or not isinstance(item := usage[_DECLARED_MODEL], dict)
            or item.keys() != fields.keys()
            or not matches(item)
        ):
            raise ValueError("native per-model usage differs from the seat attestation")
    elif usage is not None:
        raise ValueError("unexpected native per-model usage")


def _seat_report(role: str, record: dict[str, Any], is_model: bool) -> SeatReport:
    report = _empty_seat(is_model)
    name = "seatlab-first-party" if is_model else "seatlab-dispatch-handoff"
    info = record.get("agent_info")
    expected_model = {"name": _MODEL, "provider": "anthropic"} if is_model else None
    if (
        record.get("under_test") is not True
        or not isinstance(info, dict)
        or (info.get("name"), info.get("version"), info.get("model_info"))
        != (name, _VERSION, expected_model)
    ):
        raise ValueError("native actor identity differs from the registration")
    report["native_agent_name"] = name
    context = record.get("agent_result")
    if context is None:
        return report
    if not isinstance(context, dict):
        raise ValueError("invalid native seat context")
    metadata = context.get("metadata")
    if metadata is None:
        if any(value is not None for value in context.values()):
            raise ValueError("native seat usage has no attestation")
        return report
    if not isinstance(metadata, dict) or (
        metadata.get("role") != role
        and not (
            is_model
            and metadata.get("stage") == "invalid_seat"
            and metadata.get("role") is None
        )
    ):
        raise ValueError("invalid role attestation")
    if not is_model:
        if (
            not _config_matches(
                metadata,
                {
                    "schema": "seatlab.seat/v1",
                    "role": role,
                    "policy": "arrival",
                    "decision_source": "script",
                    "model_turns": 0,
                },
            )
            or len(metadata) != 5
        ):
            raise ValueError("script seat does not attest zero model turns")
        names = ("n_input_tokens", "n_cache_tokens", "n_output_tokens", "cost_usd")
        zeros: dict[str, int | float | None] = dict.fromkeys(_TELEMETRY)
        zeros.update({key: 0 for key in _TELEMETRY[:4]})
        zeros["estimated_cost_usd"] = 0.0
        if record.get("exception_info") is not None:
            if (
                context.get("model_usage") is not None
                or context.get("rollout_details") is not None
                or any(context.get(key) is not None for key in names)
            ):
                _check_context_usage(context, zeros)
            return report
        _check_context_usage(context, zeros)
        report.update(model_turns=0, stage="complete", telemetry=zeros)
        return report

    if (
        metadata.keys()
        != {
            "schema",
            "role",
            "model_turns",
            "requested_model",
            "served_model",
            "stage",
            "view_sha256",
            "telemetry",
        }
        or metadata["schema"] != "seatlab.native-model-seat/v1"
    ):
        raise ValueError("invalid model seat attestation schema")
    stage = metadata["stage"]
    turns = metadata["model_turns"]
    served = metadata["served_model"]
    digest = metadata["view_sha256"]
    if (
        not isinstance(stage, str)
        or stage not in _MODEL_STAGES
        or type(turns) is not int
        or metadata["requested_model"] != _MODEL
        or served not in (None, _MODEL)
        or (turns == 0 and served is not None)
    ):
        raise ValueError("invalid native model identity, stage, or turn count")
    expected_turns = 0 if stage in _BEFORE_GENERATION else 1
    if turns != expected_turns and (stage, turns) != ("generation", 0):
        raise ValueError("invalid native model turn count")
    if stage == "complete":
        if (
            served != _MODEL
            or not isinstance(digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
        ):
            raise ValueError("model completion lacks a model or view attestation")
    elif digest is not None:
        raise ValueError("incomplete model seat claims a view acknowledgment")
    telemetry = _telemetry(metadata["telemetry"])
    if turns == 0 and any(value is not None for value in telemetry.values()):
        raise ValueError("seat reports usage before model generation")
    _check_context_usage(context, telemetry, model_attested=served == _MODEL)
    report.update(
        requested_model=_MODEL,
        served_model=served,
        stage=stage,
        model_turns=None if stage == "generation" and turns == 0 else turns,
        telemetry=telemetry,
    )
    # Do not publish the agent's claim as an accepted digest until the native
    # world dossier separately corroborates it.
    return report


def _measure(
    receipt: TrialEvidence,
    world: dict[str, Any],
    capsules: dict[str, Capsule],
    seats: dict[str, SeatReport],
) -> Metrics:
    inventory = cast(dict[str, Any], capsules["inventory"]["view"])["cases"]
    dispatch = cast(dict[str, Any], capsules["dispatch"]["view"])["cases"]
    rows: list[Episode] = []
    delivered = []
    for case, source, listener, grade in zip(
        world["cases"], inventory, dispatch, receipt["score"]["rows"], strict=True
    ):
        if not case["id"] == source["id"] == listener["id"] == grade["id"]:
            raise ValueError("native episode IDs do not align")
        first, second = (
            actual == observed
            for actual, observed in zip(case["reports"], source["reports"], strict=True)
        )
        messages = listener["messages"]
        if seats["inventory"]["actor"] == "script" and not (first and second):
            raise ValueError("inventory decisions differ from the registered script")
        if seats["dispatch"]["actor"] == "script" and case["chosen"] != max(
            messages[-1]["available"], key=listener["utilities"].__getitem__
        ):
            raise ValueError("dispatch decisions differ from the registered script")
        delivered.append({"id": case["id"], "messages": messages})
        rows.append(
            {
                "id": case["id"],
                "choice": case["chosen"],
                "optimal": grade["optimal"],
                "feasible": grade["feasible"],
                "availability_changed": grade["changed"],
                "revision_1_correct": first,
                "revision_2_correct": second,
                "delivered_messages_sha256": view_hash(messages),
            }
        )
    return {
        "episodes": receipt["score"]["episodes"],
        "optimal": receipt["score"]["optimal"],
        "feasible": receipt["score"]["feasible"],
        "inventory_copy_fidelity": {
            "revision_1_correct": sum(row["revision_1_correct"] for row in rows),
            "revision_2_correct": sum(row["revision_2_correct"] for row in rows),
            "both_correct": sum(
                row["revision_1_correct"] and row["revision_2_correct"] for row in rows
            ),
            "revision_reports": 2 * len(rows),
        },
        "delivered_messages_sha256": view_hash(delivered),
        "rows": rows,
    }


def inspect_cell(
    root: Path, plan: Mapping[str, Any], cell: Mapping[str, Any]
) -> CellReport:
    """Verify one registered trial without publishing raw inputs or exceptions."""
    driver = _driver()
    root = root.resolve()
    pairing = cell["pairing"]
    seats = {
        role: _empty_seat(actor == "M")
        for role, actor in zip(_ROLES, pairing, strict=True)
    }
    row: CellReport = {
        "cell_id": cell["cell_id"],
        "seed": cell["seed"],
        "condition": cell["condition"],
        "pairing": pairing,
        "model_actors": pairing.count("M"),
        "attempted": False,
        "status": "not_attempted",
        "error": None,
        "native_returncode": None,
        "trial_id": None,
        "fingerprint": None,
        "seats": seats,
        "metrics": None,
    }
    directory = root / "cells" / row["cell_id"]
    row["attempted"] = (directory / "attempt.json").exists()
    try:
        trial = driver._checked_cell(root, plan, cell)
        outcome = directory / "outcome.json"
        if outcome.exists():
            row["native_returncode"] = driver._outcome(outcome)["returncode"]
    except (OSError, ValueError, KeyError):
        row.update(status="invalid", error="registered_cell_mismatch")
        return row
    if trial is None:
        return row
    row.update(attempted=True, status="unmeasured")
    result_path = trial / "result.json"
    dossier = trial / "artifacts/logs/world/handoff.json"
    try:
        if not all(_within_trial(path, trial) for path in (result_path, dossier)):
            raise ValueError("native outputs must remain inside the registered trial")
        native = _read_object(result_path)
        row["trial_id"] = _trial_id(native)
        version = native.get("schema_version", 1)
        if (
            type(version) is not int
            or version != 1
            or any(
                native.get(key) is not None for key in ("step_results", "agent_result")
            )
        ):
            raise ValueError("expected a native seat trial result")
        expected = driver._trial_config(root, cell)
        _check_config(native.get("config"), expected)
        if (
            native.get("trial_name") != expected["trial_name"]
            or ("trial_uri" in native and native["trial_uri"] != trial.as_uri())
            or (
                "task_id" in native
                and native["task_id"] != {"path": expected["task"]["path"]}
            )
        ):
            raise ValueError("native result belongs to a different trial")
        records = _by_role(native.get("seat_results"), "seat_results")
    except FileNotFoundError:
        row["error"] = "native_result_missing"
        return row
    except (OSError, ValueError, KeyError, RecursionError, RuntimeError):
        row.update(status="invalid", error="native_trial_identity_invalid")
        return row

    invalid_seat = False
    for role, record in records.items():
        try:
            seats[role] = _seat_report(role, record, seats[role]["actor"] == "model")
        except ValueError:
            invalid_seat = True
    if invalid_seat:
        row.update(status="invalid", error="native_seat_attestation_invalid")
        return row
    failed_model = next(
        (
            seat["stage"]
            for seat in seats.values()
            if seat["actor"] == "model" and seat["stage"] not in (None, "complete")
        ),
        None,
    )
    if failed_model is not None:
        row.update(status="invalid", error=f"model_{failed_model}")
        return row
    verifier = native.get("verifier_result")
    if (
        native.get("reward_valid") is not True
        or verifier is None
        or (isinstance(verifier, dict) and verifier.get("rewards") is None)
    ):
        row["error"] = "native_grade_missing"
        return row
    failure = "native_measurement_invalid"
    try:
        receipt, world = read_trial_with_world(trial)
        failure = "native_world_mismatch"
        if (
            receipt["trial_id"] != row["trial_id"]
            or receipt["seed"] != cell["seed"]
            or receipt["condition"] != cell["condition"]
            or receipt["score"]["episodes"] != plan["episodes"]
            or receipt["fingerprint"]
            != _registered_fingerprint(cell["seed"], plan["episodes"])
        ):
            raise ValueError("native world differs from the registered scenario")
        row["fingerprint"] = receipt["fingerprint"]
        failure = "native_observation_receipt_invalid"
        capsules = reconstructed_capsules(world)
        if capsules.keys() != seats.keys() or any(
            capsule["basis"] != "bound" for capsule in capsules.values()
        ):
            raise ValueError("native world lacks the two accepted whole-view digests")
        for role, seat in seats.items():
            digest = capsules[role]["acknowledged_sha256"]
            if seat["stage"] != "complete" or (
                seat["actor"] == "model"
                and records[role]["agent_result"]["metadata"]["view_sha256"] != digest
            ):
                raise ValueError("native completion or accepted digest is unconfirmed")
            estimate = seat["telemetry"]["estimated_cost_usd"]
            if (
                seat["actor"] == "model"
                and estimate is not None
                and estimate > plan["per_turn_budget_usd"]
            ):
                failure = "native_model_budget_violation"
                raise ValueError("model estimate exceeds the registered bound")
        failure = "native_actor_decisions_invalid"
        row["metrics"] = _measure(receipt, world, capsules, seats)
        for role, seat in seats.items():
            seat["accepted_view_sha256"] = capsules[role]["acknowledged_sha256"]
    except FileNotFoundError:
        row["error"] = "native_dossier_missing"
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        row.update(status="invalid", error=failure)
    else:
        row["status"] = "measured"
    return row


def _paired(
    base: CellReport, treatment: CellReport, *, listener: bool
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "seed": base["seed"],
        "condition": base["condition"],
        "contrast": f"{base['pairing']}_to_{treatment['pairing']}",
        "baseline_cell_id": base["cell_id"],
        "treatment_cell_id": treatment["cell_id"],
        "status": "unavailable",
        "delivered_messages_equal": None,
        "messages_changed_episode_ids": None,
        "differences": None,
    }
    baseline, changed = base["metrics"], treatment["metrics"]
    if baseline is None or changed is None:
        return result
    if (base["seed"], base["condition"]) != (
        treatment["seed"],
        treatment["condition"],
    ) or base["fingerprint"] != treatment["fingerprint"]:
        raise ValueError("paired measurements do not share a world and condition")
    pairs = list(zip(baseline["rows"], changed["rows"], strict=True))
    if any(before["id"] != after["id"] for before, after in pairs):
        raise ValueError("paired measurements do not share episode IDs")
    messages_changed = [
        before["id"]
        for before, after in pairs
        if before["delivered_messages_sha256"] != after["delivered_messages_sha256"]
    ]
    equal = (
        baseline["delivered_messages_sha256"] == changed["delivered_messages_sha256"]
    )
    if equal != (not messages_changed):
        raise ValueError("delivered message digests do not agree")
    result.update(
        delivered_messages_equal=equal, messages_changed_episode_ids=messages_changed
    )
    if listener and not equal:
        result["status"] = "excluded_messages_changed"
        return result
    result["status"] = "compared"
    result["differences"] = {
        "episodes": len(pairs),
        "choices_changed": sum(a["choice"] != b["choice"] for a, b in pairs),
        "optimal_delta": changed["optimal"] - baseline["optimal"],
        "feasible_delta": changed["feasible"] - baseline["feasible"],
        "became_optimal": sum(not a["optimal"] and b["optimal"] for a, b in pairs),
        "ceased_to_be_optimal": sum(
            a["optimal"] and not b["optimal"] for a, b in pairs
        ),
        "became_feasible": sum(not a["feasible"] and b["feasible"] for a, b in pairs),
        "ceased_to_be_feasible": sum(
            a["feasible"] and not b["feasible"] for a, b in pairs
        ),
    }
    return result


def _usage(cells: list[CellReport]) -> dict[str, int | float | None]:
    attempted = [
        seat
        for cell in cells
        if cell["attempted"]
        for seat in cell["seats"].values()
        if seat["actor"] == "model"
    ]
    called = [seat for seat in attempted if seat["model_turns"] == 1]
    known = [
        estimate
        for seat in called
        if (estimate := seat["telemetry"]["estimated_cost_usd"]) is not None
    ]
    return {
        "confirmed_model_turns_in_measured_cells": sum(
            cell["model_actors"] for cell in cells if cell["status"] == "measured"
        ),
        "model_turns_recorded": len(called),
        "attempted_model_seats_with_unknown_turn_count": sum(
            seat["model_turns"] is None for seat in attempted
        ),
        "model_calls_with_known_cli_estimate": len(known),
        "model_calls_with_unknown_cli_estimate": len(called) - len(known),
        "known_cli_estimate_usd": math.fsum(known) if known else None,
    }


def collect(root: Path | str) -> dict[str, Any]:
    """Read the frozen grid and return only verified, path-free study results."""
    root = Path(root).resolve()
    driver = _driver()
    plan = driver._load_plan(root)
    registered = {cell["cell_id"] for cell in plan["cells"]}
    cells_dir = root / "cells"
    if cells_dir.is_symlink() or (
        cells_dir.exists()
        and any(
            entry.name not in registered or not entry.is_dir() or entry.is_symlink()
            for entry in cells_dir.iterdir()
        )
    ):
        raise ValueError("unexpected native cell directory")
    cells = [inspect_cell(root, plan, cell) for cell in plan["cells"]]
    native_ids: dict[str, list[CellReport]] = {}
    for cell in cells:
        if (trial_id := cell["trial_id"]) is not None:
            native_ids.setdefault(trial_id, []).append(cell)
    for matches in native_ids.values():
        if len(matches) > 1:
            for cell in matches:
                cell.update(
                    status="invalid",
                    error="duplicate_native_trial_id",
                    metrics=None,
                    fingerprint=None,
                    seats={
                        role: _empty_seat(seat["actor"] == "model")
                        for role, seat in cell["seats"].items()
                    },
                )

    by_world: dict[tuple[int, str], dict[str, CellReport]] = {}
    for cell in cells:
        by_world.setdefault((cell["seed"], cell["condition"]), {})[cell["pairing"]] = (
            cell
        )
    measured = sum(cell["status"] == "measured" for cell in cells)
    return {
        "schema": "seatlab.native-team-report/v1",
        "complete": measured == len(cells),
        "model": plan["model"],
        "registered_plan_sha256": driver._attempt(plan, plan["cells"][0])[
            "plan_sha256"
        ],
        "observation_binding": "accepted_whole_view_digest_not_proof_of_understanding",
        "episodes_per_cell": plan["episodes"],
        "max_model_turns": plan["max_model_turns"],
        "per_turn_budget_usd": plan["per_turn_budget_usd"],
        "max_model_budget_usd": plan["max_model_budget_usd"],
        "cost_basis": "provider_cli_estimate_not_an_invoice",
        "counts": {
            "registered": len(cells),
            "attempted": sum(cell["attempted"] for cell in cells),
            "measured": measured,
            "unmeasured": sum(cell["status"] == "unmeasured" for cell in cells),
            "invalid": sum(cell["status"] == "invalid" for cell in cells),
            "not_attempted": sum(cell["status"] == "not_attempted" for cell in cells),
            "by_model_actors": {
                str(number): {
                    "registered": sum(cell["model_actors"] == number for cell in cells),
                    "attempted": sum(
                        cell["model_actors"] == number and cell["attempted"]
                        for cell in cells
                    ),
                    "measured": sum(
                        cell["model_actors"] == number and cell["status"] == "measured"
                        for cell in cells
                    ),
                }
                for number in range(3)
            },
        },
        "usage": _usage(cells),
        "listener_comparisons": [
            _paired(group[base], group[treatment], listener=True)
            for group in by_world.values()
            for base, treatment in (("SS", "SM"), ("MS", "MM"))
        ],
        "speaker_comparisons": [
            _paired(group[base], group[treatment], listener=False)
            for group in by_world.values()
            for base, treatment in (("SS", "MS"), ("SM", "MM"))
        ],
        "cells": cells,
    }
