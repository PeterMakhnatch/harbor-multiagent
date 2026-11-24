import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
SAMPLES = ROOT / "examples" / "seat_handoffs" / "samples"
PUBLIC_TASK = ROOT / "examples" / "tasks" / "revised-dispatch"
EDGES = [
    {"message": "initial", "sender": "reporter", "receiver": "booker"},
    {"message": "revision", "sender": "reporter", "receiver": "booker"},
]
EDGE = ["reporter", "revision", "booker"]
Json = dict[str, Any]
Steps = tuple[tuple[str, ...], ...]
HEAD: Steps = (
    ("sent", "initial"),
    ("returned", "initial"),
    ("acknowledged", "initial"),
)
BEFORE: Steps = (
    *HEAD,
    ("sent", "revision"),
    ("returned", "revision"),
    ("acknowledged", "revision"),
    ("commit",),
)
AFTER: Steps = (
    *HEAD,
    ("sent", "revision"),
    ("returned", "revision"),
    ("commit",),
    ("acknowledged", "revision"),
)
ABSENT: Steps = (*HEAD, ("sent", "revision"), ("commit",))
NO_COMMIT: Steps = (*HEAD, ("sent", "revision"), ("returned", "revision"))
TASK = """
artifacts = [{source = "/logs/world/handoffs.jsonl"}]

[agents.booker]

[agents.reporter]
under_test = false
"""


def frame(record: Json) -> str:
    return json.dumps(record, separators=(",", ":"))


def journal(
    steps: Steps,
    *,
    run: str | None = None,
    edges: list[Json] = EDGES,
    acks: bool = True,
    closed: bool = True,
) -> str:
    start: Json = {
        "seq": 0,
        "type": "start",
        "schema": "harbor.handoffs/v1",
        "run": run or str(uuid.uuid4()),
    }
    start["edges"] = edges
    lines = [frame(start | ({"acknowledgements": True} if acks else {}))]
    played = [step for step in steps if acks or step[0] != "acknowledged"]
    for seq, (kind, *message) in enumerate(played, start=1):
        record: Json = {"seq": seq, "type": kind}
        if kind == "sent":
            record |= {"message": message[0], "sender": "reporter"}
        elif kind == "commit":
            record |= {"receiver": "booker"}
        else:
            record |= {"message": message[0], "receiver": "booker"}
        lines.append(frame(record))
    if closed:
        lines.append(frame({"seq": len(lines), "type": "closed", "reward": 1}))
    return "\n".join(lines) + "\n"


def task_dir(root: Path, text: str = TASK) -> Path:
    root.mkdir()
    (root / "task.toml").write_text(text, encoding="utf-8")
    return root


def journals(root: Path, *texts: str) -> list[Path]:
    paths = []
    for index, body in enumerate(texts):
        path = root / f"journal-{index}.jsonl"
        path.write_text(body, encoding="utf-8")
        paths.append(path)
    return paths


def run_cli(
    task: Path,
    paths: list[Path],
    *,
    edge: list[str] = EDGE,
    boundary: str = "acknowledged",
    as_json: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-BS",
        "-m",
        "examples.seat_handoffs.qualify",
        str(task),
        "--edge",
        *edge,
    ]
    command += ["--boundary", boundary, *(["--json"] if as_json else [])]
    for path in paths:
        command += ["--journal", str(path)]
    return subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=60, check=False
    )


def report(done: subprocess.CompletedProcess[str], code: int) -> Json:
    assert (done.returncode, done.stderr) == (code, "")
    assert '"reward"' not in done.stdout
    return json.loads(done.stdout)


def statuses(found: Json) -> list[str]:
    return [row["status"] for row in found["journals"]]


@pytest.mark.parametrize(
    ("boundary", "contrast", "kind"),
    [
        ("acknowledged", AFTER, ("confirmed", "after")),
        ("acknowledged", ABSENT, ("absent", "unknown")),
        ("returned", ABSENT, ("absent", "unknown")),
    ],
    ids=["ack-after", "ack-absent", "returned-absent"],
)
def test_matched_controls_on_declared_roles_are_distinguishable(
    tmp_path: Path, boundary: str, contrast: Steps, kind: tuple[str, str]
) -> None:
    task = task_dir(tmp_path / "task")
    paths = journals(tmp_path, journal(BEFORE), journal(contrast))
    found = report(run_cli(task, paths, boundary=boundary), 0)
    assert found["distinguishable"] is True and found["missing"] == []
    assert found["task_binding"] == "operator_supplied"
    assert (
        found["roster"] == ["booker", "reporter"]
        and found["world_artifact_declared"] is True
    )
    assert found["standard_world_export"] is True
    assert statuses(found) == ["positive", "contrast"]
    assert (found["journals"][1]["receipt"], found["journals"][1]["timing"]) == kind
    assert [row["shape"] for row in found["journals"]] == [0, 0]
    assert all(len(row["sha256"]) == 64 and row["closed"] for row in found["journals"])
    assert run_cli(task, paths, boundary=boundary, as_json=False).returncode == 0


def test_journal_free_question_names_both_missing_cases(tmp_path: Path) -> None:
    found = report(run_cli(task_dir(tmp_path / "task"), []), 1)
    assert found["distinguishable"] is False and found["missing"] == [
        "positive",
        "contrast",
    ]
    assert found["journals"] == [] and found["shapes"] == []


@pytest.mark.parametrize(
    ("artifacts", "declared", "standard"),
    [
        ('["/logs/world/handoffs.jsonl"]', True, True),
        (
            '[{source = "/logs/world/handoffs.jsonl", destination = "logs/world/handoffs.jsonl"}]',
            True,
            True,
        ),
        (
            '[{source = "/logs/world/handoffs.jsonl", destination = "world/journal.jsonl"}]',
            True,
            False,
        ),
        (
            '[{source = "/logs/world/other.jsonl", destination = "logs/world/handoffs.jsonl"}]',
            False,
            False,
        ),
        ('[{source = "/logs/world/handoffs.jsonl", service = "main"}]', True, True),
        (
            '[{source = "/logs/world/handoffs.jsonl", service = "receiver"}]',
            True,
            False,
        ),
        ("[]", False, False),
    ],
    ids=[
        "bare",
        "same-destination",
        "remapped",
        "other-source",
        "main-service",
        "receiver-service",
        "none",
    ],
)
def test_remapped_world_artifact_is_not_a_standard_export(
    tmp_path: Path, artifacts: str, declared: bool, standard: bool
) -> None:
    task = task_dir(
        tmp_path / "task",
        f"artifacts = {artifacts}\n\n[agents.booker]\n\n[agents.reporter]\n",
    )
    found = report(run_cli(task, []), 1)
    assert (found["world_artifact_declared"], found["standard_world_export"]) == (
        declared,
        standard,
    )


FOREIGN_EDGES = [
    *EDGES,
    {"message": "audit", "sender": "auditor", "receiver": "booker"},
]
EXTRA_EDGES = [*EDGES, {"message": "note", "sender": "reporter", "receiver": "booker"}]
FALSE_POSITIVES: dict[str, tuple[str, str, str]] = {
    "ack-unsupported": (journal(AFTER, acks=False), "acknowledged", "ack_unsupported"),
    "ack-capability-differs": (journal(ABSENT, acks=False), "returned", "contrast"),
    "no-commit": (journal(NO_COMMIT), "acknowledged", "no_commit"),
    "open-prefix": (journal(ABSENT, closed=False), "acknowledged", "open"),
    "torn-row": (
        journal(ABSENT, closed=False) + '{"seq":6,"type":"clo',
        "acknowledged",
        "open",
    ),
    "unmatched-edge": (
        journal((("sent", "initial"), ("commit",)), edges=EDGES[:1]),
        "acknowledged",
        "edge_not_declared",
    ),
    "unmatched-role": (
        journal(AFTER, edges=FOREIGN_EDGES),
        "acknowledged",
        "roster_mismatch",
    ),
    "changed-topology": (journal(AFTER, edges=EXTRA_EDGES), "acknowledged", "contrast"),
}


@pytest.mark.parametrize("name", sorted(FALSE_POSITIVES))
def test_unusable_or_unmatched_contrast_never_passes(tmp_path: Path, name: str) -> None:
    body, boundary, status = FALSE_POSITIVES[name]
    paths = journals(tmp_path, journal(BEFORE), body)
    found = report(run_cli(task_dir(tmp_path / "task"), paths, boundary=boundary), 1)
    assert found["distinguishable"] is False and "contrast" in found["missing"]
    assert statuses(found) == ["positive", status]


def test_unmeasured_absence_without_acknowledgements_is_not_a_contrast(
    tmp_path: Path,
) -> None:
    paths = journals(tmp_path, journal(BEFORE, acks=False), journal(ABSENT, acks=False))
    found = report(run_cli(task_dir(tmp_path / "task"), paths), 1)
    assert statuses(found) == ["ack_unsupported", "ack_unsupported"]
    assert {row["receipt"] for row in found["journals"]} == {"unknown"}
    assert found["missing"] == ["positive", "contrast"]


@pytest.mark.parametrize(
    ("message", "missing", "status", "timings"),
    [
        ("revision", ["positive"], "contrast", ["after", "unknown"]),
        ("initial", ["contrast"], "positive", ["before", "before"]),
    ],
)
def test_public_archive_is_missing_one_case(
    message: str, missing: list[str], status: str, timings: list[str]
) -> None:
    paths = [SAMPLES / "confirm-late.jsonl", SAMPLES / "confirm-stale.jsonl"]
    found = report(run_cli(PUBLIC_TASK, paths, edge=["reporter", message, "booker"]), 1)
    assert found["missing"] == missing and statuses(found) == [status, status]
    assert [row["timing"] for row in found["journals"]] == timings
    assert (
        found["roster"] == ["booker", "reporter"]
        and found["world_artifact_declared"] is True
    )


def write_text(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_invalid_inputs_exit_2_without_report(tmp_path: Path) -> None:
    task = task_dir(tmp_path / "task")
    run = str(uuid.uuid4())
    before = write_text(tmp_path / "before.jsonl", journal(BEFORE, run=run))
    cases = {
        "repeated-file": ([before, before], EDGE),
        "duplicate-run": (
            [before, write_text(tmp_path / "again.jsonl", journal(AFTER, run=run))],
            EDGE,
        ),
        "undeclared-role": ([before], ["auditor", "revision", "booker"]),
        "self-edge": ([before], ["booker", "revision", "booker"]),
        "unsafe-name": ([before], ["reporter", "re vision", "booker"]),
        "malformed": ([write_text(tmp_path / "bad.jsonl", "{}\n")], EDGE),
        "after-close": (
            [write_text(tmp_path / "tail.jsonl", journal(AFTER) + "{")],
            EDGE,
        ),
        "missing-journal": ([tmp_path / "absent.jsonl"], EDGE),
    }
    for name, (paths, edge) in cases.items():
        done = run_cli(task, paths, edge=edge)
        assert (name, done.returncode, done.stdout) == (name, 2, ""), done.stderr
        assert "Traceback" not in done.stderr
    invalid_tasks = {
        "no-roster": "[agent]\n",
        "bad-toml": "[agents.booker\n",
        "seat-scalar": "[agents]\nbooker = 1\n",
    }
    for name, text in invalid_tasks.items():
        done = run_cli(task_dir(tmp_path / name, text), [before])
        assert (name, done.returncode, done.stdout) == (name, 2, "")
    assert run_cli(task, [before], boundary="delivered").returncode == 2
