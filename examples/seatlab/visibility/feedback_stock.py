import argparse
import json
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from runpy import run_path
from typing import Any, Never, Protocol, TypedDict, cast

_ASSETS = Path(__file__).resolve().parents[2] / "tasks/dispatch-handoff"
_BAYS = ("A", "B", "C", "D")
_DESKS = ("X", "Y")
_REGIMES = ("stable", "switch")
_STATES = ("unclaimed", "unbound", "claimed")
_EPISODES = 20
_NATIVE_IDS = tuple(range(1, _EPISODES, 2))
_SLOTS = frozenset(
    (arm, step)
    for step in range(len(_NATIVE_IDS))
    for arm in (("warm",) if step < 2 else ("lifetime", "recent"))
)
_SELECT_FAILURES = ("missing_select", "invalid_select")
_REVIEW_FAILURES = ("missing_review", "invalid_review")
_DECIDE_FAILURES = ("missing_decide", "invalid_decide")
_ROW_FIELDS = frozenset(
    "arm step native_id desk current_desk exact copied bay optimal feasible "
    "source_sensitive status".split()
)
_MAX_REPORT_BYTES = 16 * 1024 * 1024
_MAX_SEEDS = 64


class _InvalidReport(ValueError):
    pass


class _Case(TypedDict):
    id: int
    before: list[str]
    after: list[str]
    utilities: dict[str, int]


class _Dossier(TypedDict):
    cases: list[_Case]


class _World(Protocol):
    def submit(self, role: str, payload: object) -> dict[str, bool]: ...

    def evidence(self) -> _Dossier: ...


class _ScoreRow(TypedDict):
    id: int
    optimal: bool
    feasible: bool


class _Score(TypedDict):
    rows: list[_ScoreRow]


type _Truth = tuple[tuple[str, ...], tuple[str, ...], tuple[int, ...]]


@dataclass(frozen=True, slots=True)
class _Shipment:
    truth: _Truth
    source_sensitive: bool
    grades: dict[str, tuple[bool, bool]]


@dataclass(frozen=True, slots=True)
class _Block:
    seed: int
    regime: str
    state: str
    rows: list[object]


def _controls(seeds: set[int]) -> dict[int, dict[int, _Shipment]]:
    if not seeds:
        return {}
    native = run_path(str(_ASSETS / "environment/seatworld.py"))
    scorer = run_path(str(_ASSETS / "tests/score.py"))
    world_type = cast(Callable[[object], _World], native["World"])
    score = cast(Callable[[_Dossier], _Score], scorer["score"])
    worlds = {}
    for seed in sorted(seeds):
        grades: dict[int, dict[str, tuple[bool, bool]]] = {
            native_id: {} for native_id in _NATIVE_IDS
        }
        cases: list[_Case] = []
        for bay in _BAYS:
            world = world_type(
                {
                    "schema": "seatlab.scenario/v1",
                    "seed": seed,
                    "episodes": _EPISODES,
                    "condition": "pooled",
                }
            )
            accepted = world.submit(
                "dispatch",
                {"rows": [{"id": i, "bay": bay} for i in range(_EPISODES)]},
            )
            assert accepted == {"accepted": True}
            evidence = world.evidence()
            if bay == _BAYS[0]:
                cases = evidence["cases"]
            for row in score(evidence)["rows"]:
                if row["id"] in grades:
                    grades[row["id"]][bay] = row["optimal"], row["feasible"]

        shipments = {}
        for native_id in _NATIVE_IDS:
            case = cases[native_id]
            assert case["id"] == native_id and grades[native_id].keys() == set(_BAYS)
            utilities = case["utilities"]
            before_best = max(case["before"], key=utilities.__getitem__)
            shipments[native_id] = _Shipment(
                (
                    tuple(case["before"]),
                    tuple(case["after"]),
                    tuple(utilities[bay] for bay in _BAYS),
                ),
                not grades[native_id][before_best][0],
                grades[native_id],
            )
        worlds[seed] = shipments
    return worlds


def _blocks(report: object) -> list[_Block]:
    if (
        not isinstance(report, dict)
        or report.get("schema") != "seatlab.feedback-report/v1"
    ):
        raise _InvalidReport("expected a seatlab.feedback-report/v1 object")
    entries = report.get("blocks")
    if not isinstance(entries, list) or len(entries) > _MAX_SEEDS * 4:
        raise _InvalidReport("invalid block register")
    blocks: list[_Block] = []
    keys: set[str] = set()
    seeds: set[int] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise _InvalidReport("expected a block object")
        seed, regime, rep, state = (
            entry.get(key) for key in ("seed", "regime", "rep", "state")
        )
        if (
            type(seed) is not int
            or not 1 <= seed <= 2_147_483_647
            or regime not in _REGIMES
            or type(rep) is not int
            or rep not in (0, 1)
            or state not in _STATES
        ):
            raise _InvalidReport("invalid block identity or state")
        key = f"hz-s{seed}-{regime}-r{rep}"
        if entry.get("block") != key or key in keys:
            raise _InvalidReport("incorrect or duplicate block key")
        keys.add(key)
        seeds.add(seed)
        if len(seeds) > _MAX_SEEDS:
            raise _InvalidReport("too many distinct seeds")
        if "rows" not in entry:
            raise _InvalidReport("missing block rows")
        rows = entry["rows"]
        if rows is None and state != "claimed":
            rows = []
        if not isinstance(rows, list) or (
            len(rows) != len(_SLOTS) and (state == "claimed" or rows)
        ):
            raise _InvalidReport("incorrect block row count")
        blocks.append(_Block(seed, regime, state, rows))
    return blocks


def _row(value: object, seen: set[tuple[str, int]], claimed: bool) -> dict[str, Any]:
    if not isinstance(value, dict) or value.keys() != _ROW_FIELDS:
        raise _InvalidReport("unexpected decision row fields")
    arm, step, native_id = value["arm"], value["step"], value["native_id"]
    if (
        not isinstance(arm, str)
        or type(step) is not int
        or (arm, step) not in _SLOTS
        or (arm, step) in seen
    ):
        raise _InvalidReport("unexpected or duplicate decision slot")
    seen.add((arm, step))
    if type(native_id) is not int or native_id != _NATIVE_IDS[step]:
        raise _InvalidReport("unexpected native shipment ID")
    if (
        type(value["source_sensitive"]) is not bool
        or value["current_desk"] not in _DESKS
    ):
        raise _InvalidReport("invalid shipment metadata")
    status = value["status"]
    if status not in (
        "complete",
        "blocked_history",
        *_SELECT_FAILURES,
        *_REVIEW_FAILURES,
        *_DECIDE_FAILURES,
    ):
        raise _InvalidReport("invalid row status")
    if arm == "warm" and status in _SELECT_FAILURES:
        raise _InvalidReport("a warmup has no selector")

    choice_observed = status == "complete"
    review_observed = choice_observed or status in _DECIDE_FAILURES
    desk_known = review_observed or status in _REVIEW_FAILURES
    if not claimed and (review_observed or (desk_known and arm != "warm")):
        raise _InvalidReport("observed action without a claim")
    if value["desk"] not in (_DESKS if desk_known else (None,)):
        raise _InvalidReport("desk does not match the row status")
    if value["bay"] not in (_BAYS if choice_observed else (None,)):
        raise _InvalidReport("choice does not match the row status")
    for fields, observed in (
        (("exact", "copied"), review_observed),
        (("optimal", "feasible"), choice_observed),
    ):
        expected = bool if observed else type(None)
        if any(type(value[field]) is not expected for field in fields):
            raise _InvalidReport("decision evidence does not match the row status")
    return value


def audit(report: object) -> dict[str, Any]:
    blocks = _blocks(report)
    worlds = _controls({block.seed for block in blocks})
    truths = {
        shipment.truth for world in worlds.values() for shipment in world.values()
    }
    fields = Counter(optimal=0, feasible=0, source_sensitive=0)
    coverage = {
        regime: Counter(
            blocks=0,
            claimed_blocks=0,
            unbound_blocks=0,
            unclaimed_blocks=0,
            expected_rows=0,
            reported_rows=0,
            actual_decisions=0,
        )
        for regime in _REGIMES
    }
    for block in blocks:
        counts = coverage[block.regime]
        counts["blocks"] += 1
        counts[f"{block.state}_blocks"] += 1
        counts["expected_rows"] += len(_SLOTS)
        counts["reported_rows"] += len(block.rows)
        seen: set[tuple[str, int]] = set()
        for value in block.rows:
            row = _row(value, seen, claimed=block.state == "claimed")
            shipment = worlds[block.seed][row["native_id"]]
            fields["source_sensitive"] += (
                row["source_sensitive"] is not shipment.source_sensitive
            )
            bay = row["bay"]
            if bay is not None:
                counts["actual_decisions"] += 1
                optimal, feasible = shipment.grades[bay]
                fields["optimal"] += row["optimal"] is not optimal
                fields["feasible"] += row["feasible"] is not feasible
    return {
        "schema": "seatlab.feedback-stock/v1",
        "distinct_native_worlds": len(worlds),
        "registered_shipments": len(worlds) * len(_NATIVE_IDS),
        "distinct_shipment_truths": len(truths),
        "coverage": coverage,
        "actual_decisions": sum(
            counts["actual_decisions"] for counts in coverage.values()
        ),
        "mismatches": sum(fields.values()),
        "mismatches_by_field": fields,
    }


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise _InvalidReport("duplicate JSON object key")
    return result


def _reject_constant(_: str) -> Never:
    raise _InvalidReport("non-finite JSON number")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check public dispatch scores using stock controls."
    )
    parser.add_argument("report", type=Path, metavar="PUBLIC_REPORT_JSON")
    args = parser.parse_args(argv)
    try:
        with args.report.open("rb") as source:
            data = source.read(_MAX_REPORT_BYTES + 1)
        if len(data) > _MAX_REPORT_BYTES:
            raise _InvalidReport("public report is too large")
        report = json.loads(
            data, object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
        result = audit(report)
    except _InvalidReport as error:
        print(json.dumps({"error": str(error)}))
        return 2
    except (OSError, ValueError, RecursionError):
        print(json.dumps({"error": "cannot read or check the public report"}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 1 if result["mismatches"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
