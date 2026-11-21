from __future__ import annotations

import json
from collections.abc import Callable
from hashlib import sha256
from math import isclose
from pathlib import Path
from runpy import run_path
from statistics import fmean
from typing import Any, Protocol, TypedDict, cast

_TASK = Path(__file__).resolve().parents[2] / "tasks" / "dispatch-handoff"
_SEEDS = range(5801, 5813)
_BAYS = ("A", "B", "C", "D")
_IDS = dict(zip("PQRS", (1, 3, 5, 7), strict=True))
_ARMS = ("bound", "anonymous")
_BRIEF = (
    "Choose one dock bay for each shipment. Maximize utility among currently "
    "available bays. Each record lists the available bays for one shipment."
)


class _World(Protocol):
    def evidence(self) -> dict[str, Any]: ...

    def submit(self, role: str, payload: object) -> dict[str, bool]: ...


type _WorldFactory = Callable[[dict[str, str | int]], _World]
type _Grader = Callable[[dict[str, Any]], dict[str, Any]]


class _NativeRow(TypedDict):
    id: int
    utilities: dict[str, int]
    before: list[str]
    after: list[str]


class _Record(TypedDict):
    id: str
    old_version: str
    new_version: str
    before: str
    after: str
    question: str
    old_best: str
    best: str


class _Case(TypedDict):
    id: str
    cluster: str
    records: list[_Record]


def _settings(seed: int) -> dict[str, str | int]:
    return {
        "schema": "seatlab.scenario/v1",
        "seed": seed,
        "episodes": 8,
        "condition": "pooled",
    }


def _record(label: str, row: _NativeRow, revised: bool) -> _Record:
    current = row["after"]
    before = row["before"] if revised else current
    utility = row["utilities"]
    ranks = ", ".join(f"{bay}={utility[bay]}" for bay in _BAYS)
    return {
        "id": label,
        "old_version": "1",
        "new_version": "2" if revised else "1",
        "before": f"Available bays: {', '.join(before)}.",
        "after": f"Available bays: {', '.join(current)}.",
        "question": f"Utilities: {ranks}. Which available bay has the highest utility?",
        "old_best": max(before, key=utility.__getitem__),
        "best": max(current, key=utility.__getitem__),
    }


def _cases(world: _WorldFactory) -> list[_Case]:
    cases: list[_Case] = []
    for seed in _SEEDS:
        rows = cast(list[_NativeRow], world(_settings(seed)).evidence()["cases"])
        cluster = f"s{seed}"
        for revised in (*_IDS, "stable"):
            cases.append(
                {
                    "id": f"{cluster}-{revised.lower()}",
                    "cluster": cluster,
                    "records": [
                        _record(label, rows[row_id], label == revised)
                        for label, row_id in _IDS.items()
                    ],
                }
            )
    return cases


def _world() -> _WorldFactory:
    return run_path(str(_TASK / "environment" / "seatworld.py"))["World"]


def _encode(cases: list[_Case]) -> bytes:
    payload = {
        "schema": "seatlab.handoff-input/v1",
        "brief": _BRIEF,
        "choices": _BAYS,
        "cases": cases,
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def packet_bytes() -> bytes:
    return _encode(_cases(_world()))


def _register(report: dict[str, Any], cases: list[_Case]) -> list[dict[str, Any]]:
    fingerprint = sha256(_encode(cases)).hexdigest()
    expected = {
        f"ho-{fingerprint[:12]}-{case['id']}-r{rep}": (case["id"], case["cluster"], rep)
        for case in cases
        for rep in (0, 1)
    }
    rows = report.get("outcomes")
    if (
        report.get("packet_sha256", fingerprint) != fingerprint
        or not isinstance(rows, list)
        or len(rows) != len(expected)
    ):
        raise ValueError("Default stock register does not match")
    seen: set[str] = set()
    for row in rows:
        key = row.get("block") if isinstance(row, dict) else None
        if (
            not isinstance(key, str)
            or key not in expected
            or key in seen
            or row.get("fingerprint") != fingerprint
            or type(row.get("rep")) is not int
            or expected[key] != (row.get("case"), row.get("cluster"), row.get("rep"))
        ):
            raise ValueError("Default stock row identity does not match")
        seen.add(key)
    return rows


def _draws(row: dict[str, Any]) -> dict[str, dict[str, str]]:
    outcome = row.get("outcome")
    if not isinstance(outcome, dict):
        raise ValueError("Invalid stock outcome")
    arms = outcome.get("arms")
    if not isinstance(arms, dict) or arms.keys() != set(_ARMS):
        raise ValueError("Invalid stock arms")
    draws: dict[str, dict[str, str]] = {}
    for name, arm in arms.items():
        if not isinstance(arm, dict) or arm.keys() != {
            "status",
            "lookup",
            "decision_stage",
            "choices",
        }:
            raise ValueError("Invalid stock arm")
        choices = arm["choices"]
        if arm["status"] != "complete":
            if choices is not None or arm["status"] not in (
                "no_note",
                "missing_select",
                "invalid_select",
                "missing_decide",
                "invalid_decide",
            ):
                raise ValueError("Invalid incomplete stock arm")
            continue
        stage = arm["decision_stage"]
        if (
            row.get("state") != "claimed"
            or outcome.get("note_status") != "valid"
            or arm["lookup"] not in tuple(_IDS)
            or stage not in (f"{name}-decide", "both-decide")
            or not isinstance(choices, dict)
            or choices.keys() != _IDS.keys()
            or any(bay not in _BAYS for bay in choices.values())
        ):
            raise ValueError("Invalid complete stock arm")
        draws[stage] = choices
    shared = "both-decide" in draws
    selected = [
        arms[name]["lookup"] for name in _ARMS if arms[name]["status"] == "complete"
    ]
    if shared != (len(selected) == 2 and selected[0] == selected[1]) or (
        shared
        and (
            len(draws) != 1 or arms["bound"]["choices"] != arms["anonymous"]["choices"]
        )
    ):
        raise ValueError("Invalid shared stock draw")
    return draws


def _grade(
    world: _WorldFactory, score: _Grader, seed: int, choices: dict[str, str]
) -> dict[str, bool]:
    native = world(_settings(seed))
    observed = {_IDS[label]: bay for label, bay in choices.items()}
    native.submit(
        "dispatch",
        {
            "rows": [
                {"id": row, "bay": observed[row] if row % 2 else "A"}
                for row in range(8)
            ]
        },
    )
    scored = score(native.evidence())["rows"]
    return {label: scored[row]["optimal"] for label, row in _IDS.items()}


def _primary(pairs: dict[str, list[int]]) -> dict[str, int | float | None]:
    covered = sum(bool(values) for values in pairs.values())
    return {
        "scheduled_pairs": len(_SEEDS) * len(_IDS) * 2,
        "complete_pairs": sum(map(len, pairs.values())),
        "clusters_with_pairs": covered,
        "enrolled_clusters": len(pairs),
        "paired_complete_delta": (
            fmean(fmean(values) for values in pairs.values())
            if covered == len(pairs)
            else None
        ),
    }


def _verify_primary(
    report: dict[str, Any], primary: dict[str, int | float | None]
) -> None:
    if "primary" not in report:
        return
    claimed = report["primary"]
    if not isinstance(claimed, dict):
        raise ValueError("Invalid registered stock primary")
    for key, value in primary.items():
        actual = claimed.get(key)
        if key not in claimed or not (
            (type(actual) is int and actual == value)
            or (
                type(actual) is float
                and isclose(actual, value, rel_tol=0, abs_tol=1e-12)
            )
            if isinstance(value, float)
            else type(actual) is type(value) and actual == value
        ):
            raise ValueError("Registered stock primary does not match native score")


def stock(report: dict[str, Any]) -> dict[str, Any]:
    world = _world()
    cases = _cases(world)
    rows = _register(report, cases)
    score = run_path(str(_TASK / "tests" / "score.py"))["score"]
    by_case = {case["id"]: case for case in cases}
    pairs: dict[str, list[int]] = {f"s{seed}": [] for seed in _SEEDS}
    arm_counts = {
        name: dict(choices=0, correct=0, changed=0, changed_correct=0) for name in _ARMS
    }
    worlds: set[int] = set()
    dossier_count = shared_count = 0
    for row in rows:
        case = by_case[row["case"]]
        cluster = case["cluster"]
        seed = int(cluster[1:])
        graded: dict[str, dict[str, bool]] = {}
        for stage, choices in _draws(row).items():
            results = _grade(world, score, seed, choices)
            if any(
                results[item["id"]] != (choices[item["id"]] == item["best"])
                for item in case["records"]
            ):
                raise ValueError("Packet reward does not match native score")
            worlds.add(seed)
            dossier_count += 1
            shared_count += stage == "both-decide"
            names = (
                _ARMS if stage == "both-decide" else (stage.removesuffix("-decide"),)
            )
            for name in names:
                graded[name] = results
                arm_counts[name]["choices"] += len(results)
                arm_counts[name]["correct"] += sum(results.values())
        for item in case["records"]:
            if item["old_version"] != item["new_version"]:
                for name, results in graded.items():
                    arm_counts[name]["changed"] += 1
                    arm_counts[name]["changed_correct"] += results[item["id"]]
                if graded.keys() == set(_ARMS):
                    pairs[cluster].append(
                        int(graded["bound"][item["id"]])
                        - int(graded["anonymous"][item["id"]])
                    )
    primary = _primary(pairs)
    _verify_primary(report, primary)
    return {
        "mismatches": 0,
        "registered_worlds": len(_SEEDS),
        "registered_native_records": len(_SEEDS) * len(_IDS),
        "observed_worlds": len(worlds),
        "observed_distinct_native_records": len(worlds) * len(_IDS),
        "observed_dossiers": dossier_count,
        "observed_native_choices": dossier_count * len(_IDS),
        "reference_even_id_fillers": dossier_count * (8 - len(_IDS)),
        "shared_dossiers": shared_count,
        "arms": arm_counts,
        "primary": primary,
    }
