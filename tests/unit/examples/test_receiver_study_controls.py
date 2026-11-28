import json
from pathlib import Path
from typing import Any

from examples.seatlab.receiver_memory.study.__main__ import main
from examples.seatlab.receiver_memory.study.manifest import load, seal
from examples.seatlab.receiver_memory.study.report import assess


def test_separate_wire_receivers_make_matched_choices(
    tmp_path: Path, capsys: Any
) -> None:
    root = tmp_path / "control"
    pin = seal(root, 400, "truthful", "scripted_control")["sha256"]
    plan = load(root, pin)
    rows = [
        row
        for row in plan["assignments"]
        if row["seed"] == min(r["seed"] for r in plan["assignments"])
        and row["world"] == "replace"
        and row["age"] == "oldest"
        and row["coin"] == "2"
    ]
    assert {row["arm"] for row in rows} == {"version", "sham"}
    assert main(["tickets", str(root), "--expect", pin]) == 0
    tickets = json.loads(capsys.readouterr().out)
    assert set(tickets) == {"tickets"} and len(set(tickets["tickets"])) == 96
    args = ["control", str(root), "--expect", pin, "--workers", "2"]
    for row in rows:
        args.extend(["--ticket", row["ticket"]])
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["complete"] == 2
    result = assess(root, pin)
    assert result["status_counts"] == {
        "not_started": 94,
        "complete": 2,
        "incomplete": 0,
    }
    for row in rows:
        receipt = json.loads(
            (root / "attempts" / (row["ticket"] + ".result.json")).read_text()
        )
        actions = receipt["actions"]
        expected = int(row["arm"] == "version")
        assert (actions[0] == {"op": "read"}) == bool(expected)
        assert row["scores"][actions[-1]["slot"]] == expected
    assert main(["control", str(root), "--expect", pin, "--all"]) == 2
    assert capsys.readouterr().out == ""
    assert assess(root, pin) == result


def test_reference_cannot_enter_an_external_plan(tmp_path: Path, capsys: Any) -> None:
    root = tmp_path / "external"
    assert (
        main(
            [
                "plan",
                str(root),
                "--start",
                "200",
                "--sender",
                "repeat",
                "--source",
                "external_unverified",
            ]
        )
        == 0
    )
    pin = json.loads(capsys.readouterr().out)["sha256"]
    assert main(["control", str(root), "--expect", pin, "--all"]) == 2
    assert capsys.readouterr().out == ""
    assert main(["assess", str(root), "--expect", pin]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status_counts"] == {
        "not_started": 96,
        "incomplete": 0,
        "complete": 0,
    }
    assert report["version_minus_sham"]["utility"] == {"lower": "-5/4", "upper": "5/4"}
