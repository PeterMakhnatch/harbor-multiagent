import base64
import json
from pathlib import Path

import pytest

from examples.seatlab.decision_boundary import temporal
from examples.seatlab.decision_boundary.__main__ import main
from examples.seatlab.decision_boundary.core import BoundaryError, World

SCORES = {"hidden-stay": {"a": 1, "b": 0}, "hidden-move": {"a": 0, "b": 1}}
TIMELY = {
    "early_commit": {"hidden-stay": b"SECRET-S", "hidden-move": b"SECRET-M"},
    "early_close": {"hidden-stay": b"SECRET-S|S", "hidden-move": b"SECRET-M|M"},
    "late_commit": {"hidden-stay": b"--", "hidden-move": b"--"},
    "late_close": {"hidden-stay": b"--|S", "hidden-move": b"--|M"},
}
WITHHELD = {
    **TIMELY,
    "late_close": {"hidden-stay": b"--|-", "hidden-move": b"--|-"},
}


def _worlds(observations: dict[str, bytes], scores=SCORES) -> list[World]:
    return [
        World(name, 1, observation, dict(scores[name]))
        for name, observation in observations.items()
    ]


def _arms(table, scores=SCORES) -> dict[str, list[World]]:
    return {arm: _worlds(table[arm], scores) for arm in temporal.ARMS}


def _files(tmp_path: Path, table, scores=SCORES, order=temporal.ARMS) -> list[str]:
    paths = []
    for arm in order:
        rows = [
            {
                "name": name,
                "mass": 1,
                "observation_b64": base64.b64encode(observation).decode("ascii"),
                "scores": scores[name],
            }
            for name, observation in table[arm].items()
        ]
        path = tmp_path / f"{arm}.json"
        path.write_text(
            json.dumps(
                {"schema": "seatlab.decision-worlds/v1", "scale": 1, "worlds": rows}
            )
        )
        paths.append(str(path))
    return paths


def test_timely_notice_matches_the_timing_only_pattern():
    report = temporal.qualify(_arms(TIMELY), 1)
    assert report["timing_only_pattern"] is True
    assert report["commit_relation"] == "refinement"
    assert report["commit_gain"] == "1/2"
    assert report["close_relation"] == "equivalent"
    assert report["close_gain"] == "0"
    assert report["early_hindsight_gain"] == "0"
    assert report["late_hindsight_gain"] == "1/2"
    assert report["early_hindsight_relation"] == "equivalent"
    assert report["late_hindsight_relation"] == "refinement"
    assert {arm: report["arms"][arm]["observed"] for arm in temporal.ARMS} == {
        "early_commit": "1",
        "early_close": "1",
        "late_commit": "1/2",
        "late_close": "1",
    }
    text = json.dumps(report)
    for private in ("hidden-", "SECRET", "U0VDUkVU", '"a"', '"b"'):
        assert private not in text
    assert "not proof" in report["pattern_scope"]


def test_withheld_notice_is_a_real_negative():
    report = temporal.qualify(_arms(WITHHELD), 1)
    assert report["timing_only_pattern"] is False
    assert report["commit_gain"] == "1/2"
    assert report["close_relation"] == "refinement"
    assert report["close_gain"] == "1/2"
    assert report["late_hindsight_gain"] == "0"


def test_uninformative_refinement_is_not_a_timing_pattern():
    scores = {
        "x": {"a": 1, "b": 0},
        "y": {"a": 1, "b": 0},
        "z": {"a": 0, "b": 1},
    }
    table = {
        "early_commit": {"x": b"P", "y": b"Q", "z": b"Q"},
        "early_close": {"x": b"P|x", "y": b"Q|y", "z": b"Q|z"},
        "late_commit": {"x": b"-", "y": b"-", "z": b"-"},
        "late_close": {"x": b"-|x", "y": b"-|y", "z": b"-|z"},
    }
    report = temporal.qualify(_arms(table, scores), 1)
    assert report["commit_relation"] == "refinement"
    assert report["commit_gain"] == "0"
    assert report["close_relation"] == "equivalent"
    assert report["late_hindsight_gain"] == "1/3"
    assert report["timing_only_pattern"] is False


def test_partial_early_cue_with_later_completion_is_not_a_timing_pattern():
    names = ("w1", "w2", "w3", "w4")
    scores = {name: {action: int(action == name) for action in names} for name in names}
    table = {
        "early_commit": {"w1": b"L", "w2": b"L", "w3": b"R", "w4": b"R"},
        "early_close": {"w1": b"L|1", "w2": b"L|2", "w3": b"R|3", "w4": b"R|4"},
        "late_commit": {name: b"-" for name in names},
        "late_close": {name: b"-|" + name[1:].encode() for name in names},
    }
    report = temporal.qualify(_arms(table, scores), 1)
    assert report["commit_relation"] == "refinement"
    assert report["commit_gain"] == "1/4"
    assert report["close_relation"] == "equivalent"
    assert report["close_gain"] == "0"
    assert report["late_hindsight_gain"] == "3/4"
    assert report["early_hindsight_relation"] == "refinement"
    assert report["early_hindsight_gain"] == "1/2"
    assert report["timing_only_pattern"] is False


def test_swapped_timing_order_reverses_the_pattern():
    arms = _arms(TIMELY)
    swapped = {
        "early_commit": arms["late_commit"],
        "early_close": arms["late_close"],
        "late_commit": arms["early_commit"],
        "late_close": arms["early_close"],
    }
    report = temporal.qualify(swapped, 1)
    assert report["commit_relation"] == "coarsening"
    assert report["commit_gain"] == "-1/2"
    assert report["timing_only_pattern"] is False


def test_swapped_boundaries_fail_the_prefix_contract():
    arms = _arms(TIMELY)
    arms["early_commit"], arms["early_close"] = (
        arms["early_close"],
        arms["early_commit"],
    )
    with pytest.raises(BoundaryError, match="prefix"):
        temporal.qualify(arms, 1)


def test_non_prefix_history_is_rejected():
    table = {**TIMELY, "late_close": {"hidden-stay": b"-S", "hidden-move": b"-M"}}
    with pytest.raises(BoundaryError, match="late full history"):
        temporal.qualify(_arms(table), 1)


def test_history_that_erases_decision_information_is_rejected():
    table = {
        **TIMELY,
        "late_commit": {"hidden-stay": b"-", "hidden-move": b"--"},
        "late_close": {"hidden-stay": b"--|", "hidden-move": b"--|"},
    }
    with pytest.raises(BoundaryError, match="erases"):
        temporal.qualify(_arms(table), 1)


def test_changed_payoffs_are_rejected():
    arms = _arms(TIMELY)
    changed = {"hidden-stay": {"a": 0, "b": 1}, "hidden-move": {"a": 1, "b": 0}}
    arms["late_close"] = _worlds(TIMELY["late_close"], changed)
    with pytest.raises(BoundaryError, match="score"):
        temporal.qualify(arms, 1)


def test_cli_expectations_and_exit_codes(tmp_path: Path, capsys):
    files = _files(tmp_path, TIMELY)
    args = ["temporal", "--files", *files, "--expect-timing-only"]
    assert main([*args, "yes"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["qualification"]["timing_only_pattern"] is True
    assert report["expectation_met"] is True and report["model_calls"] == 0
    assert main([*args, "no"]) == 1
    assert json.loads(capsys.readouterr().out)["expectation_met"] is False
    assert main(["temporal", "--files", *files]) == 0
    assert json.loads(capsys.readouterr().out)["expectation_met"] is None
    swapped = [files[2], files[3], files[0], files[1]]
    assert main(["temporal", "--files", *swapped, "--expect-timing-only", "yes"]) == 1
    capsys.readouterr()


def test_cli_rejects_invalid_temporal_inputs(tmp_path: Path, capsys):
    files = _files(tmp_path, TIMELY)
    assert main(["temporal", "--files", "-", "-", files[2], files[3]]) == 2
    assert "standard input" in capsys.readouterr().err
    reordered = [files[1], files[0], files[2], files[3]]
    assert main(["temporal", "--files", *reordered]) == 2
    invalid = capsys.readouterr()
    assert not invalid.out and "prefix" in invalid.err
    changed = {"hidden-stay": {"a": 0, "b": 1}, "hidden-move": {"a": 1, "b": 0}}
    other = tmp_path / "other"
    other.mkdir()
    payoff = _files(other, TIMELY, changed)
    assert main(["temporal", "--files", *files[:3], payoff[3]]) == 2
    assert "score" in capsys.readouterr().err
    data = json.loads(Path(files[3]).read_text())
    data["scale"] = 2
    Path(files[3]).write_text(json.dumps(data))
    assert main(["temporal", "--files", *files]) == 2
    assert "scale" in capsys.readouterr().err
