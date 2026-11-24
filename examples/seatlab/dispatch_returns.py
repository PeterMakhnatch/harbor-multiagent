"""Reduce native Dispatch Handoff returns to aggregate outcome consistency."""

import re
from collections.abc import Mapping, Sequence
from typing import Any, cast
from uuid import UUID

if __package__:
    from .capsule import view_hash
else:
    from capsule import view_hash

_STATUSES = ("measured", "invalid", "unmeasured", "not_attempted")
_PAIRINGS = ("SS", "MS", "SM", "MM")
_ACTORS = {"S": "script", "M": "model"}
_ROLES = ("inventory", "dispatch")
_CATEGORIES = (
    "qualified_different_latest",
    "qualified_different_last",
    "qualified_different_neither",
    "qualified_same_best",
    "qualified_same_other",
    "unqualified_reports",
)
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _digest(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _integer(record: dict[str, Any], field: str, expected: int) -> None:
    value = record.get(field)
    if type(value) is not int or value != expected:
        raise ValueError(f"invalid {field}")


def _grid_by_id(grid: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    if not grid or len(grid) > 1024:
        raise ValueError("invalid grid size")
    registered: dict[str, Mapping[str, Any]] = {}
    combinations: set[tuple[int, str, str]] = set()
    for cell in grid:
        cell_id = cell.get("cell_id")
        seed, condition, pairing = (
            cell.get("seed"),
            cell.get("condition"),
            cell.get("pairing"),
        )
        if not isinstance(cell_id, str) or not cell_id or cell_id in registered:
            raise ValueError("invalid grid cell_id")
        if type(seed) is not int:
            raise ValueError("invalid grid seed")
        if condition not in ("fresh", "reordered"):
            raise ValueError("invalid grid condition")
        if pairing not in _PAIRINGS:
            raise ValueError("invalid grid pairing")
        combination = (seed, condition, pairing)
        if combination in combinations:
            raise ValueError("duplicate grid cell")
        combinations.add(combination)
        registered[cell_id] = cell
    return registered


def _check_trial_id(
    cell: dict[str, Any], seen: dict[str, bool], *, required: bool, attempted: bool
) -> None:
    trial_id = cell.get("trial_id")
    if trial_id is None and not required:
        return
    if not attempted or not isinstance(trial_id, str) or len(trial_id) != 36:
        raise ValueError("invalid trial_id")
    try:
        parsed = UUID(trial_id)
    except ValueError:
        raise ValueError("invalid trial_id") from None
    if not parsed.int or str(parsed) != trial_id:
        raise ValueError("invalid trial_id")
    invalid = cell["status"] == "invalid"
    if trial_id in seen and not (invalid and seen[trial_id]):
        raise ValueError("duplicate trial_id")
    seen[trial_id] = invalid


def _observed_cells(
    study: dict[str, Any], registered: dict[str, Mapping[str, Any]]
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    cells = study.get("cells")
    if not isinstance(cells, list) or len(cells) != len(registered):
        raise ValueError("invalid cells total")
    coverage = dict.fromkeys(_STATUSES, 0)
    coverage["registered"] = len(registered)
    observed: dict[str, dict[str, Any]] = {}
    seen: dict[str, bool] = {}
    attempted_total = 0
    for cell in cells:
        if not isinstance(cell, dict) or "cell_id" not in cell:
            raise ValueError("invalid cell")
        cell_id = cell.get("cell_id")
        if (
            not isinstance(cell_id, str)
            or cell_id not in registered
            or cell_id in observed
        ):
            raise ValueError("invalid cell_id")
        expected = registered[cell_id]
        _integer(cell, "seed", expected["seed"])
        for field in ("condition", "pairing"):
            if cell.get(field) != expected[field]:
                raise ValueError(f"invalid {field}")
        seats = cell.get("seats")
        if not isinstance(seats, dict) or seats.keys() != set(_ROLES):
            raise ValueError("invalid seats")
        for role, actor in zip(_ROLES, expected["pairing"], strict=True):
            if (
                not isinstance(seats[role], dict)
                or seats[role].get("actor") != _ACTORS[actor]
            ):
                raise ValueError("invalid seats actor")
        status, attempted = cell.get("status"), cell.get("attempted")
        if status not in _STATUSES:
            raise ValueError("invalid status")
        if type(attempted) is not bool or (
            status != "invalid" and attempted != (status != "not_attempted")
        ):
            raise ValueError("invalid attempted")
        if "metrics" not in cell or (
            not isinstance(cell["metrics"], dict)
            if status == "measured"
            else cell["metrics"] is not None
        ):
            raise ValueError("invalid metrics")
        _check_trial_id(cell, seen, required=status == "measured", attempted=attempted)
        observed[cell_id] = cell
        coverage[status] += 1
        attempted_total += attempted
    counts = study.get("counts")
    if not isinstance(counts, dict) or "registered" not in counts:
        raise ValueError("invalid counts")
    for field, count in coverage.items():
        _integer(counts, field, count)
    _integer(counts, "attempted", attempted_total)
    if study.get("complete") is not (coverage["measured"] == len(registered)):
        raise ValueError("invalid complete")
    return observed, coverage


def _latent_evidence(
    world_type: type, seed: int, episodes: int
) -> tuple[list[dict[str, Any]], str]:
    world = world_type(
        {
            "schema": "seatlab.scenario/v1",
            "seed": seed,
            "episodes": episodes,
            "condition": "fresh",
        }
    )
    cases = world.evidence()["cases"]
    if len(cases) != episodes:
        raise ValueError("invalid evidence episodes")
    latent = [
        {field: case[field] for field in ("id", "utilities", "before", "after")}
        for case in cases
    ]
    return latent, view_hash(latent)


def choice_bucket(choice: str, latest_best: str, last_best: str) -> str:
    if latest_best == last_best:
        return (
            "qualified_same_best" if choice == latest_best else "qualified_same_other"
        )
    if choice == latest_best:
        return "qualified_different_latest"
    if choice == last_best:
        return "qualified_different_last"
    return "qualified_different_neither"


def _expected_messages(case: dict[str, Any], reordered: bool) -> list[dict[str, Any]]:
    messages = [{"revision": 2, "available": case["after"]}]
    if reordered:
        messages.append({"revision": 1, "available": case["before"]})
    return messages


def _outcome(
    row: dict[str, Any],
    case: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    reordered: bool,
) -> str | None:
    before, after, utilities = case["before"], case["after"], case["utilities"]
    choice = row.get("choice")
    if not isinstance(choice, str) or choice not in utilities:
        raise ValueError("invalid choice")
    latest = max(after, key=utilities.__getitem__)
    for field, expected in (
        ("availability_changed", before != after),
        ("feasible", choice in after),
        ("optimal", choice == latest),
    ):
        if row.get(field) is not expected:
            raise ValueError(f"invalid {field}")
    first, second = row.get("revision_1_correct"), row.get("revision_2_correct")
    if type(first) is not bool:
        raise ValueError("invalid revision_1_correct")
    if type(second) is not bool:
        raise ValueError("invalid revision_2_correct")
    qualified = second and (first or not reordered)
    delivered = row.get("delivered_messages_sha256")
    if not _digest(delivered) or (delivered == view_hash(messages)) != qualified:
        raise ValueError("invalid delivered_messages_sha256")
    if not reordered:
        return None
    if not qualified:
        return "unqualified_reports"
    last = max(before, key=utilities.__getitem__)
    return choice_bucket(choice, latest, last)


def _measure(
    metrics: dict[str, Any],
    cases: list[dict[str, Any]],
    *,
    reordered: bool,
    pairing: str,
) -> dict[str, int]:
    _integer(metrics, "episodes", len(cases))
    rows = metrics.get("rows")
    if not isinstance(rows, list) or len(rows) != len(cases):
        raise ValueError("invalid rows total")
    counts = dict.fromkeys(_CATEGORIES, 0)
    expected_delivery = []
    optimal = feasible = first = second = both = 0
    for record, case in zip(rows, cases, strict=True):
        if not isinstance(record, dict) or "id" not in record:
            raise ValueError("invalid row")
        row = cast(dict[str, Any], record)
        _integer(row, "id", case["id"])
        messages = _expected_messages(case, reordered)
        expected_delivery.append({"id": case["id"], "messages": messages})
        outcome = _outcome(row, case, messages, reordered=reordered)
        if pairing[0] == "S" and not (
            row["revision_1_correct"] and row["revision_2_correct"]
        ):
            raise ValueError("invalid scripted inventory reports")
        if pairing[1] == "S" and (
            (
                reordered
                and outcome
                in {
                    "qualified_different_latest",
                    "qualified_different_neither",
                    "qualified_same_other",
                }
            )
            or (not reordered and row["revision_2_correct"] and not row["optimal"])
        ):
            raise ValueError("invalid scripted dispatch choice")
        if outcome is not None:
            counts[outcome] += 1
        optimal += row["optimal"]
        feasible += row["feasible"]
        first += row["revision_1_correct"]
        second += row["revision_2_correct"]
        both += row["revision_1_correct"] and row["revision_2_correct"]
    delivery = metrics.get("delivered_messages_sha256")
    all_correct = (both if reordered else second) == len(cases)
    if (
        not _digest(delivery)
        or (view_hash(expected_delivery) == delivery) != all_correct
    ):
        raise ValueError("invalid team delivered_messages_sha256")
    _integer(metrics, "optimal", optimal)
    _integer(metrics, "feasible", feasible)
    fidelity = metrics.get("inventory_copy_fidelity")
    if not isinstance(fidelity, dict) or "revision_reports" not in fidelity:
        raise ValueError("invalid inventory_copy_fidelity")
    _integer(fidelity, "revision_1_correct", first)
    _integer(fidelity, "revision_2_correct", second)
    _integer(fidelity, "both_correct", both)
    _integer(fidelity, "revision_reports", 2 * len(cases))
    return counts


def reduce_returns(
    study: dict[str, Any],
    study_pin: str,
    grid: Sequence[Mapping[str, Any]],
    episodes: int,
    world_type: type,
) -> dict[str, Any]:
    """Classify reordered outcomes from a native report whose bytes were verified."""
    if (
        not isinstance(study, dict)
        or study.get("schema") != "seatlab.native-team-report/v1"
    ):
        raise ValueError("invalid schema")
    if not _digest(study_pin):
        raise ValueError("invalid study_sha256")
    if type(episodes) is not int or not 1 <= episodes <= 256:
        raise ValueError("invalid episodes")
    _integer(study, "episodes_per_cell", episodes)
    registered = _grid_by_id(grid)
    observed, coverage = _observed_cells(study, registered)
    evidence: dict[int, tuple[list[dict[str, Any]], str]] = {}
    teams = []
    for cell_id, expected in registered.items():
        cell = observed[cell_id]
        seed, pairing = expected["seed"], expected["pairing"]
        reordered = expected["condition"] == "reordered"
        status = cell["status"]
        counts = None
        if status == "measured":
            if seed not in evidence:
                evidence[seed] = _latent_evidence(world_type, seed, episodes)
            cases, fingerprint = evidence[seed]
            if cell.get("fingerprint") != fingerprint:
                raise ValueError("invalid fingerprint")
            counts = _measure(
                cell["metrics"], cases, reordered=reordered, pairing=pairing
            )
        if reordered:
            teams.append(
                {"seed": seed, "pairing": pairing, "status": status, "counts": counts}
            )
    return {
        "schema": "seatlab.dispatch-return-diagnostic/v1",
        "study_sha256": study_pin,
        "coverage": coverage,
        "teams": teams,
    }
