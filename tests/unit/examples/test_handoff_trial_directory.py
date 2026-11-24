import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
RUN = "0b6f3c1e-8d2a-4f57-9a41-3c7e2d915b08"
OTHER_RUN = "5d0c9a8e-1f3b-4c6d-8e7f-90a1b2c3d4e5"
NATIVE = "7e2a4c90-3b1d-4f68-a5c2-d9e08f1b6a37"
EDGES = [
    {"message": "initial", "sender": "reporter", "receiver": "booker"},
    {"message": "revision", "sender": "reporter", "receiver": "booker"},
]
Json = dict[str, Any]
Steps = tuple[tuple[str, ...], ...]
STEPS: Steps = (
    ("sent", "initial"),
    ("returned", "initial"),
    ("acknowledged", "initial"),
    ("sent", "revision"),
    ("returned", "revision"),
    ("commit",),
    ("acknowledged", "revision"),
)
COUNTS = {"sent": 2, "returned": 2, "acknowledged": 2, "commit": 1}


def frame(record: Json) -> str:
    return json.dumps(record, separators=(",", ":"))


def journal(run: str = RUN, steps: Steps = STEPS) -> list[str]:
    start = {
        "seq": 0,
        "type": "start",
        "schema": "harbor.handoffs/v1",
        "run": run,
        "edges": EDGES,
    }
    lines = [frame(start | {"acknowledgements": True})]
    for seq, (kind, *message) in enumerate(steps, start=1):
        record: Json = {"seq": seq, "type": kind}
        if kind == "sent":
            record |= {"message": message[0], "sender": "reporter"}
        elif kind == "commit":
            record |= {"receiver": "booker"}
        else:
            record |= {"message": message[0], "receiver": "booker"}
        lines.append(frame(record))
    return lines


def closed(lines: list[str], reward: int) -> list[str]:
    return lines + [frame({"seq": len(lines), "type": "closed", "reward": reward})]


def native(reward: int = 1, valid: bool = True) -> Json:
    return {
        "id": NATIVE,
        "reward_valid": valid,
        "verifier_result": {"rewards": {"reward": reward}},
    }


def decision(**overrides: Any) -> Json:
    record: Json = {
        "schema": "harbor.revised-dispatch.decision/v1",
        "run": RUN,
        "draft_changed": False,
        "decision_correct": True,
        "event_counts": COUNTS,
    }
    return record | overrides


def write_trial(root: Path, *, artifact, sealed, result, verdict) -> Path:
    root.mkdir(parents=True)
    files = {
        "artifacts/logs/world/handoffs.jsonl": None
        if artifact is None
        else "\n".join(artifact) + "\n",
        "verifier/handoffs.jsonl": None if sealed is None else "\n".join(sealed) + "\n",
        "verifier/decision.json": None
        if verdict is None
        else json.dumps(verdict) + "\n",
        "result.json": None if result is None else json.dumps(result),
    }
    for name, text in files.items():
        if text is not None:
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding="utf-8")
    return root


def run_cli(
    trial: Path, boundary: str | None = None
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-m",
        "examples.seat_handoffs",
        "events",
        str(trial),
        "--json",
    ]
    if boundary is not None:
        command += ["--boundary", boundary]
    return subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=60, check=False
    )


def accepted(done: subprocess.CompletedProcess[str]) -> Json:
    assert (done.returncode, done.stderr) == (0, "")
    return json.loads(done.stdout)


def timings(report: Json) -> dict[str, tuple[str, str, str]]:
    return {
        row["message"]: (row["receipt"], row["timing"], row["commit"])
        for row in report["findings"]
    }


@pytest.mark.parametrize(
    ("boundary", "basis", "revision"),
    [(None, "sealed_world", "before"), ("acknowledged", "receiver_ack", "after")],
)
def test_sealed_trial_links_native_grade_at_chosen_boundary(
    tmp_path: Path, boundary: str | None, basis: str, revision: str
) -> None:
    trial = write_trial(
        tmp_path / "t",
        artifact=journal(),
        sealed=closed(journal(), 1),
        result=native(),
        verdict=None,
    )
    report = accepted(run_cli(trial, boundary))
    assert report["boundary"] == (boundary or "returned")
    assert report["native_trials"] == [
        {
            "world_run": RUN,
            "native_trial": NATIVE,
            "log": "verifier",
            "native_grade_status": "valid",
            "decision": None,
        }
    ]
    assert timings(report) == {
        "initial": ("confirmed", "before", "recorded"),
        "revision": ("confirmed", revision, "recorded"),
    }
    assert {
        (row["trial"], row["reward"], row["basis"]) for row in report["findings"]
    } == {(RUN, 1, basis)}


def test_artifact_frame_prefix_of_sealed_log_is_accepted(tmp_path: Path) -> None:
    trial = write_trial(
        tmp_path / "t",
        artifact=journal()[:4],
        sealed=closed(journal(), 1),
        result=native(),
        verdict=None,
    )
    report = accepted(run_cli(trial))
    assert [link["log"] for link in report["native_trials"]] == ["verifier"]
    assert timings(report) == {
        "initial": ("confirmed", "before", "recorded"),
        "revision": ("confirmed", "before", "recorded"),
    }


def test_open_world_artifact_leaves_missing_receipt_unknown(tmp_path: Path) -> None:
    trial = write_trial(
        tmp_path / "t",
        artifact=journal(steps=STEPS[:2]),
        sealed=None,
        result=native(),
        verdict=None,
    )
    report = accepted(run_cli(trial))
    assert report["native_trials"] == [
        {
            "world_run": RUN,
            "native_trial": NATIVE,
            "log": "world",
            "native_grade_status": "valid",
            "decision": None,
        }
    ]
    assert timings(report) == {
        "initial": ("confirmed", "unknown", "unknown"),
        "revision": ("unknown", "unknown", "unknown"),
    }


def test_invalid_native_grade_is_null_not_zero_or_sealed_reward(tmp_path: Path) -> None:
    trial = write_trial(
        tmp_path / "t",
        artifact=journal(),
        sealed=closed(journal(), 1),
        result=native(0, valid=False),
        verdict=None,
    )
    report = accepted(run_cli(trial))
    assert [link["native_grade_status"] for link in report["native_trials"]] == [
        "invalid"
    ]
    assert [row["reward"] for row in report["findings"]] == [None, None]


@pytest.mark.parametrize("result", [native(), None], ids=["graded", "ungraded"])
def test_decision_links_receipt_before_commit_with_unchanged_draft(
    tmp_path: Path, result: Json | None
) -> None:
    trial = write_trial(
        tmp_path / "t",
        artifact=journal(),
        sealed=closed(journal(), 1),
        result=result,
        verdict=decision(),
    )
    report = accepted(run_cli(trial))
    [link] = report["native_trials"]
    assert link["decision"] == {"draft_changed": False, "decision_correct": True}
    assert link["native_grade_status"] == ("valid" if result else "unavailable")
    assert timings(report)["revision"] == ("confirmed", "before", "recorded")


REJECTIONS = {
    "missing_everything": dict(artifact=None, sealed=None, result=None, verdict=None),
    "native_grade_differs_from_closure": dict(sealed=closed(journal(), 0)),
    "unrelated_artifact_run": dict(artifact=journal(run=OTHER_RUN)),
    "nonprefix_artifact": dict(artifact=journal(steps=(STEPS[0], STEPS[1], STEPS[3]))),
    "closed_appended_to_artifact_prefix": dict(artifact=closed(journal()[:4], 1)),
    "closed_in_unsealed_artifact": dict(
        artifact=closed(journal(steps=STEPS[:1]), 1), sealed=None, verdict=None
    ),
    "decision_run": dict(verdict=decision(run=OTHER_RUN)),
    "decision_count": dict(verdict=decision(event_counts=COUNTS | {"acknowledged": 1})),
    "decision_grade": dict(verdict=decision(decision_correct=False)),
}


@pytest.mark.parametrize("name", sorted(REJECTIONS))
def test_inconsistent_trial_directory_exits_2_without_report(
    tmp_path: Path, name: str
) -> None:
    layout = (
        dict(
            artifact=journal(),
            sealed=closed(journal(), 1),
            result=native(),
            verdict=decision(),
        )
        | REJECTIONS[name]
    )
    done = run_cli(write_trial(tmp_path / "t", **layout))
    assert (done.returncode, done.stdout) == (2, "")
    assert done.stderr.startswith("Cannot inspect handoffs: ")
    assert "Traceback" not in done.stderr
