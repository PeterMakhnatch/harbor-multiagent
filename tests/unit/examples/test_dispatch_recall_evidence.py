import copy
import json
import re
import sys
from collections.abc import Callable, Iterator
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
SOURCE = (
    ROOT
    / "examples"
    / "tasks"
    / "dispatch-recall"
    / "environment"
    / "recall_evidence.py"
)
NAME = "dispatch_recall_evidence_under_test"
SEED = 918273645
INITIAL, OTHER = "Z4", "Q7"
BAYS = [OTHER, INITIAL]
TRANSITIONS = ("stay", "move")
TIMINGS = ("immediate", "deferred", "withheld")
TURNS = ["stage", "report", "commit", "commit", "close", "close", "done"]
ACCEPTED = [0, 1, 2, 2, 3, 3, 4]
OPEN = [None, None, None, "commit", None, "close", None]


@pytest.fixture(scope="module")
def evidence() -> Iterator[ModuleType]:
    spec = spec_from_file_location(NAME, SOURCE)
    assert spec is not None and spec.loader is not None
    module = sys.modules[NAME] = module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(NAME, None)


def states(
    transition: str,
    timing: str,
    stage: str = INITIAL,
    report: str | None = OTHER,
    commit: str = OTHER,
) -> list[dict[str, Any]]:
    actual = OTHER if transition == "move" else INITIAL
    eligible = {"commit": timing == "immediate", "close": timing != "withheld"}
    actions: list[dict[str, Any]] = []
    opened: dict[str, dict[str, object]] = {}
    cuts: list[dict[str, Any]] = []

    def snap() -> None:
        committed = len(actions) >= 3
        cuts.append(
            {
                "schema": "harbor.dispatch-recall.cut/v1",
                "seed": SEED,
                "transition": transition,
                "timing": timing,
                "bays": list(BAYS),
                "initial_bay": INITIAL,
                "current_bay": actual if actions else INITIAL,
                "actions": copy.deepcopy(actions),
                "windows": {
                    name: dict(opened[name]) if name in opened else None
                    for name in ("commit", "close")
                },
                "reward": int(commit == actual) if committed else None,
            }
        )

    def view(window: str) -> None:
        shown = report if eligible[window] and report is not None else "--"
        opened[window] = {"notice": shown, "redeemed": False}

    snap()
    actions.append({"role": "loader", "op": "stage", "bay": stage})
    snap()
    actions.append({"role": "depot", "op": "report", "bay": report})
    snap()
    view("commit")
    snap()
    actions.append({"role": "loader", "op": "commit", "bay": commit})
    opened["commit"]["redeemed"] = True
    snap()
    view("close")
    snap()
    actions.append({"role": "loader", "op": "close"})
    opened["close"]["redeemed"] = True
    snap()
    return cuts


@pytest.mark.parametrize("transition", TRANSITIONS)
@pytest.mark.parametrize("timing", TIMINGS)
def test_every_accepted_prefix_and_open_window_diagnoses(
    evidence: ModuleType, transition: str, timing: str
) -> None:
    rows = [evidence.diagnose(cut) for cut in states(transition, timing)]
    assert [row["progress"]["turn"] for row in rows] == TURNS
    assert [row["progress"]["accepted"] for row in rows] == ACCEPTED
    assert [row["progress"]["open_window"] for row in rows] == OPEN
    assert {row["schema"] for row in rows} == {"harbor.dispatch-recall.diagnosis/v1"}
    assert all(
        row["condition"] == {"transition": transition, "timing": timing} for row in rows
    )
    assert [row["sender"]["opportunity"] for row in rows[:2]] == [
        "not_reached",
        "available",
    ]
    assert [row["delivery"]["precommit"]["window"] for row in rows] == [
        "not_reached",
        "not_reached",
        "unrequested",
        "requested",
        "redeemed",
        "redeemed",
        "redeemed",
    ]
    assert [row["delivery"]["postcommit"]["window"] for row in rows] == [
        "not_reached",
        "not_reached",
        "not_reached",
        "not_reached",
        "unrequested",
        "requested",
        "redeemed",
    ]
    assert [row["loader"]["closed"] for row in rows] == [False] * 6 + [True]


def test_mid_observation_is_open_window_without_commit(evidence: ModuleType) -> None:
    row = evidence.diagnose(states("move", "immediate")[3])
    assert row["progress"] == {"accepted": 2, "turn": "commit", "open_window": "commit"}
    assert row["delivery"]["precommit"] == {
        "eligible": True,
        "window": "requested",
        "content": "report",
        "shows_actual": True,
    }
    assert row["loader"]["committed"] is None
    assert row["loader"]["outcome"] == "missing"
    assert row["loader"]["agrees_with_precommit_notice"] is None
    assert row["reward"] is None


def test_timing_omission_is_not_an_incorrect_report(evidence: ModuleType) -> None:
    row = evidence.diagnose(states("move", "deferred")[-1])
    assert row["sender"] == {
        "opportunity": "available",
        "action": "report",
        "value": "other",
        "correctness": "correct",
    }
    assert row["delivery"]["precommit"]["content"] == "blank_by_timing"
    assert row["delivery"]["precommit"]["eligible"] is False
    assert row["delivery"]["precommit"]["shows_actual"] is None
    assert row["delivery"]["postcommit"]["content"] == "report"
    assert row["delivery"]["postcommit"]["shows_actual"] is True
    assert row["loader"]["agrees_with_precommit_notice"] is None


def test_incorrect_report_is_delivered_not_omitted(evidence: ModuleType) -> None:
    row = evidence.diagnose(states("move", "immediate", report=INITIAL)[-1])
    assert row["sender"]["correctness"] == "incorrect"
    assert row["sender"]["value"] == "initial"
    assert row["delivery"]["precommit"]["content"] == "report"
    assert row["delivery"]["precommit"]["shows_actual"] is False
    assert row["loader"]["agrees_with_precommit_notice"] is False


@pytest.mark.parametrize("timing", TIMINGS)
def test_silence_is_elected_not_withheld(evidence: ModuleType, timing: str) -> None:
    row = evidence.diagnose(states("stay", timing, report=None, commit=INITIAL)[-1])
    assert row["sender"] == {
        "opportunity": "available",
        "action": "silence",
        "value": None,
        "correctness": "no_value",
    }
    assert {
        row["delivery"][label]["content"] for label in ("precommit", "postcommit")
    } == {"blank_after_silence"}


def test_withheld_report_blanks_both_windows(evidence: ModuleType) -> None:
    row = evidence.diagnose(states("move", "withheld")[-1])
    assert row["sender"]["correctness"] == "correct"
    for label in ("precommit", "postcommit"):
        assert row["delivery"][label]["eligible"] is False
        assert row["delivery"][label]["content"] == "blank_by_timing"


@pytest.mark.parametrize(
    ("transition", "stage", "commit", "plan", "outcome", "reward"),
    [
        ("move", INITIAL, OTHER, "changed", "correct", 1),
        ("move", INITIAL, INITIAL, "kept", "stale", 0),
        ("stay", INITIAL, INITIAL, "kept", "correct", 1),
        ("stay", INITIAL, OTHER, "changed", "switched", 0),
        ("stay", OTHER, INITIAL, "changed", "correct", 1),
    ],
)
def test_physical_outcome_separates_stale_from_switched(
    evidence: ModuleType,
    transition: str,
    stage: str,
    commit: str,
    plan: str,
    outcome: str,
    reward: int,
) -> None:
    row = evidence.diagnose(
        states(transition, "immediate", stage=stage, commit=commit)[-1]
    )
    assert row["loader"]["plan"] == plan
    assert row["loader"]["outcome"] == outcome
    assert row["reward"] == reward
    assert row["loader"]["planned"] == ("initial" if stage == INITIAL else "other")


def test_missing_commit_differs_from_wrong_commit(evidence: ModuleType) -> None:
    missing = evidence.diagnose(states("move", "immediate", commit=INITIAL)[2])
    wrong = evidence.diagnose(states("move", "immediate", commit=INITIAL)[-1])
    assert (missing["loader"]["outcome"], missing["reward"]) == ("missing", None)
    assert (wrong["loader"]["outcome"], wrong["reward"]) == ("stale", 0)


def test_diagnosis_is_json_and_omits_seed_bays_and_tickets(
    evidence: ModuleType,
) -> None:
    for cut in states("move", "immediate"):
        before = copy.deepcopy(cut)
        text = json.dumps(evidence.diagnose(cut), allow_nan=False)
        assert cut == before
        for secret in (*BAYS, str(SEED)):
            assert secret not in text
        assert re.search(r"[0-9a-f]{32}", text) is None
    row = evidence.diagnose(states("move", "immediate")[-1])
    assert set(row) == {
        "schema",
        "condition",
        "progress",
        "sender",
        "delivery",
        "loader",
        "reward",
        "caveat",
    }


Mutate = Callable[[dict[str, Any]], None]


def push(action: dict[str, Any]) -> Mutate:
    return lambda cut: cut["actions"].append(action)


def put(key: str, value: object) -> Mutate:
    return lambda cut: cut.__setitem__(key, value)


def window(name: str, field: str, value: object) -> Mutate:
    return lambda cut: cut["windows"][name].__setitem__(field, value)


REPORT = {"role": "depot", "op": "report", "bay": OTHER}
COMMIT = {"role": "loader", "op": "commit", "bay": OTHER}
CLOSE = {"role": "loader", "op": "close"}
OPENED = {"notice": OTHER, "redeemed": False}
REJECTED: list[tuple[str, int, str, Mutate, str]] = [
    ("repeated_report", 3, "immediate", push(REPORT), "out of order or repeated"),
    ("commit_before_report", 1, "immediate", push(COMMIT), "out of order"),
    ("close_before_commit", 3, "immediate", push(CLOSE), "out of order"),
    (
        "stage_twice",
        1,
        "immediate",
        push({"role": "loader", "op": "stage", "bay": INITIAL}),
        "out of order",
    ),
    (
        "depot_commits",
        3,
        "immediate",
        push({"role": "depot", "op": "commit", "bay": OTHER}),
        "out of order",
    ),
    ("fifth_action", 6, "immediate", push(CLOSE), "more accepted actions"),
    (
        "commit_keeps_ticket",
        3,
        "immediate",
        push(COMMIT | {"ticket": "a" * 32}),
        "keys differ",
    ),
    ("close_with_bay", 5, "immediate", push(CLOSE | {"bay": OTHER}), "keys differ"),
    (
        "illegal_bay",
        3,
        "immediate",
        push(COMMIT | {"bay": "AA"}),
        "commit bay",
    ),
    ("commit_silence", 3, "immediate", push(COMMIT | {"bay": None}), "commit bay"),
    (
        "commit_without_window",
        4,
        "immediate",
        lambda cut: cut["windows"].__setitem__("commit", None),
        "without its window",
    ),
    (
        "redeemed_without_commit",
        3,
        "immediate",
        window("commit", "redeemed", True),
        "redemption contradicts",
    ),
    (
        "commit_not_redeemed",
        4,
        "immediate",
        window("commit", "redeemed", False),
        "redemption contradicts",
    ),
    ("redeemed_as_int", 4, "immediate", window("commit", "redeemed", 1), "bool"),
    (
        "window_before_report",
        1,
        "immediate",
        lambda cut: cut["windows"].__setitem__("commit", dict(OPENED)),
        "before its turn",
    ),
    (
        "close_window_before_commit",
        3,
        "immediate",
        lambda cut: cut["windows"].__setitem__("close", dict(OPENED)),
        "before its turn",
    ),
    (
        "window_keeps_ticket",
        3,
        "immediate",
        window("commit", "ticket", "a" * 32),
        "window keys",
    ),
    ("immediate_blank", 3, "immediate", window("commit", "notice", "--"), "notice"),
    ("deferred_early", 3, "deferred", window("commit", "notice", OTHER), "notice"),
    ("withheld_shown", 5, "withheld", window("close", "notice", OTHER), "notice"),
    ("notice_swapped", 3, "immediate", window("commit", "notice", INITIAL), "notice"),
    ("reward_flipped", 6, "immediate", put("reward", 0), "reward contradicts"),
    ("reward_bool", 6, "immediate", put("reward", True), "int 0 or 1"),
    ("reward_before_commit", 3, "immediate", put("reward", 0), "before a commit"),
    ("reward_missing", 4, "immediate", put("reward", None), "int 0 or 1"),
    ("move_not_moved", 6, "immediate", put("current_bay", INITIAL), "transition"),
    ("move_before_stage", 0, "immediate", put("current_bay", OTHER), "transition"),
    ("unknown_current", 2, "immediate", put("current_bay", "AA"), "current bay"),
    ("unknown_initial", 2, "immediate", put("initial_bay", "AA"), "initial bay"),
    ("unknown_transition", 2, "immediate", put("transition", "swap"), "transition"),
    ("unknown_timing", 2, "immediate", put("timing", "late"), "timing"),
    ("schema_drift", 0, "immediate", put("schema", "v2"), "schema"),
    ("seed_bool", 0, "immediate", put("seed", True), "seed"),
    ("seed_negative", 0, "immediate", put("seed", -1), "seed"),
    ("seed_unbounded", 0, "immediate", put("seed", 2**63), "seed"),
    ("seed_text", 0, "immediate", put("seed", str(SEED)), "seed"),
    ("extra_key", 0, "immediate", put("arm", "move"), "keys differ"),
    ("three_bays", 0, "immediate", put("bays", [*BAYS, "K1"]), "2 names"),
    ("one_bay", 0, "immediate", put("bays", [INITIAL]), "2 names"),
    ("bays_tuple", 0, "immediate", put("bays", tuple(BAYS)), "2 names"),
    ("wide_bay", 0, "immediate", put("bays", ["Q77", INITIAL]), "2 uppercase"),
    ("narrow_bay", 0, "immediate", put("bays", ["Q", INITIAL]), "2 uppercase"),
    ("blank_bay", 0, "immediate", put("bays", ["--", INITIAL]), "2 uppercase"),
    ("lower_bay", 0, "immediate", put("bays", ["q7", INITIAL]), "2 uppercase"),
    ("unicode_bay", 0, "immediate", put("bays", ["Ä1", INITIAL]), "2 uppercase"),
    ("fullwidth_bay", 0, "immediate", put("bays", ["Ｑ7", INITIAL]), "2 uppercase"),
    ("control_bay", 0, "immediate", put("bays", ["\x01A", INITIAL]), "2 uppercase"),
    ("bay_not_text", 0, "immediate", put("bays", [17, INITIAL]), "2 uppercase"),
    ("same_bays", 0, "immediate", put("bays", [INITIAL, INITIAL]), "distinct"),
    ("unsorted_bays", 0, "immediate", put("bays", [INITIAL, OTHER]), "sorted"),
    ("actions_text", 0, "immediate", put("actions", "stage"), "actions"),
    (
        "actions_tuple",
        2,
        "immediate",
        lambda cut: cut.__setitem__("actions", tuple(cut["actions"])),
        "actions must be a list",
    ),
    (
        "unbounded_actions",
        6,
        "immediate",
        lambda cut: cut["actions"].extend([dict(CLOSE)] * 1000),
        "more accepted actions",
    ),
    (
        "action_not_dict",
        1,
        "immediate",
        lambda cut: cut["actions"].append(["depot", "report", OTHER]),
        "must be a dict",
    ),
    ("windows_list", 0, "immediate", put("windows", []), "windows"),
    (
        "windows_extra",
        0,
        "immediate",
        lambda cut: cut["windows"].__setitem__("stage", None),
        "exactly commit and close",
    ),
]


@pytest.mark.parametrize(
    ("index", "timing", "mutate", "match"),
    [case[1:] for case in REJECTED],
    ids=[case[0] for case in REJECTED],
)
def test_impossible_or_repeated_records_are_rejected(
    evidence: ModuleType, index: int, timing: str, mutate: Mutate, match: str
) -> None:
    cut = states("move", timing)[index]
    evidence.diagnose(copy.deepcopy(cut))
    mutate(cut)
    with pytest.raises(ValueError, match=match):
        evidence.diagnose(cut)


def test_non_dict_cut_is_rejected(evidence: ModuleType) -> None:
    with pytest.raises(ValueError, match="cut must be a dict"):
        evidence.diagnose([("schema", "harbor.dispatch-recall.cut/v1")])
