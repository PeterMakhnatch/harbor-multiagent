import json
from pathlib import Path
from typing import Any

from examples.seatlab.convention_crossplay import ballots
from examples.seatlab.partner_switch import control
from examples.seatlab.partner_switch.__main__ import main


def _freeze(
    plan: Path,
    target: Path,
    capsys: Any,
    changed: bool = False,
    illegal_report: bool = False,
) -> None:
    target.mkdir(mode=0o700)
    manifest = json.loads((plan / "study.json").read_bytes())
    for entry in manifest["decks"]:
        deck = plan / entry["folder"]
        role, name = entry["role"], entry["team"]
        rows = []
        for line in (deck / "requests.jsonl").read_bytes().splitlines():
            item = json.loads(line)
            slot = control.choose(
                1 if name == "alpha" else -1, role, item["prompt"].encode()
            )
            if illegal_report and entry["folder"] == "t00-east":
                view = json.loads(item["prompt"])
                slot = next(slot for slot in view["active"] if slot not in view["own"])
            reply = json.dumps({"slot": slot}) + (" " if changed else "")
            rows.append(
                json.dumps(
                    {
                        "id": item["id"],
                        "prompt_sha256": item["prompt_sha256"],
                        "reply": reply,
                    }
                )
            )
        path = target / (entry["folder"] + ".jsonl")
        path.write_text("\n".join(reversed(rows)) + "\n", encoding="utf-8")
        assert (
            ballots.main(
                [
                    "freeze",
                    "--role",
                    role,
                    "--deck",
                    str(deck),
                    "--deck-sha256",
                    entry["deck_sha256"],
                    "--source",
                    name,
                    "--replies",
                    str(path),
                    "--out",
                    str(target / entry["folder"]),
                ]
            )
            == 0
        )
        assert json.loads(capsys.readouterr().out)["complete"]


def test_child_vote_plan_depends_on_actual_report_capture_bytes(
    tmp_path: Path, capsys: Any
) -> None:
    reports, captures, votes, final = [
        tmp_path / name for name in ("reports", "captures", "votes", "final")
    ]
    assert (
        main(
            [
                "begin",
                "--start",
                "9327",
                "--team",
                "alpha",
                "--team",
                "beta",
                "--out",
                str(reports),
            ]
        )
        == 0
    )
    rpin = json.loads(capsys.readouterr().out)["study_sha256"]
    _freeze(reports, captures, capsys)
    common = [
        "--report-plan",
        str(reports),
        "--report-pin",
        rpin,
        "--report-captures",
        str(captures),
    ]
    assert main(["advance", *common, "--out", str(votes)]) == 0
    vpin = json.loads(capsys.readouterr().out)["study_sha256"]
    _freeze(votes, final, capsys)
    args = [
        "score",
        *common,
        "--vote-plan",
        str(votes),
        "--vote-pin",
        vpin,
        "--vote-captures",
        str(final),
        "--require-matched-reports",
    ]
    assert main([*args, "--expect-overestimate", "positive"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["panel"]["native"]["matrix"] == [["1", "1/2"], ["1/2", "1"]]
    assert report["panel"]["frozen_home_vote"]["matrix"] == [["1", "1"], ["1", "1"]]
    assert (
        report["evidence"]["report_plan_sha256"] == rpin
        and report["evidence"]["vote_plan_sha256"] == vpin
    )
    assert main([*args, "--expect-overestimate", "zero"]) == 1
    assert json.loads(capsys.readouterr().out)["panel"] == report["panel"]
    replacement = tmp_path / "replacement"
    _freeze(reports, replacement, capsys, changed=True)
    assert (
        main([*args, "--report-captures", str(replacement)]) == 2
        and not capsys.readouterr().out
    )
    assert (
        main([*args, "--vote-captures", str(captures)]) == 2
        and not capsys.readouterr().out
    )
    invalid = tmp_path / "invalid"
    _freeze(reports, invalid, capsys, illegal_report=True)
    status = [
        "status",
        "--plan",
        str(reports),
        "--pin",
        rpin,
        "--captures",
        str(invalid),
        "--workers",
        "2",
    ]
    assert main([*status, "--worker", "0"]) == 1
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["all"] == {"expected": 4, "complete": 3, "invalid": 1, "missing": 0}
    assert [row["folder"] for row in blocked["jobs"] if row["state"] == "invalid"] == [
        "t00-east"
    ]
    assert main([*status, "--worker", "1"]) == 0
    west = json.loads(capsys.readouterr().out)
    assert west["selected"]["complete"] == 2 and west["all"] == blocked["all"]
    refused = tmp_path / "refused"
    assert (
        main(
            [
                "advance",
                *common,
                "--report-captures",
                str(invalid),
                "--out",
                str(refused),
            ]
        )
        == 2
    )
    assert not refused.exists() and not capsys.readouterr().out


def test_same_convention_control_does_not_create_a_switch_cost(capsys: Any) -> None:
    assert main(["qualify", "--start", "25", "--control", "same"]) == 0
    report = json.loads(capsys.readouterr().out)
    result = report["panel"]
    assert (
        result["native"]["mixed_mean"]
        == result["frozen_home_vote"]["mixed_mean"]
        == "1"
    )
    assert (
        result["mixed_overestimate"] == "0"
        and result["changed_peer_views"] == result["changed_votes"] == 0
    )
    assert (
        result["matched_report_histograms"]
        and report["model_calls_by_scorer"] == report["live_harbor_trials"] == 0
    )
