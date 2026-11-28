import argparse
import json
from pathlib import Path

import pytest

from examples.seatlab.convention_crossplay import ballots, loader
from examples.seatlab.partner_switch import coverage, engine, panel, planio

NAMES = ("beta", "alpha")


@pytest.fixture
def study(tmp_path: Path) -> tuple[Path, planio.Plan, Path]:
    path = tmp_path / "study"
    packets = panel.report_inputs(engine.cases(19), NAMES)
    receipt = planio.write(path, "report", 19, NAMES, {}, packets)
    root = tmp_path / "captures"
    root.mkdir(mode=0o700)
    return path, planio.load(path, receipt["study_sha256"]), root


def _freeze(
    study: Path,
    plan: planio.Plan,
    root: Path,
    name: str,
    role: str,
    source: str | None = None,
) -> Path:
    deck = plan.decks[name, role]
    requests = study / deck.folder / "requests.jsonl"
    rows = [json.loads(line) for line in requests.read_bytes().splitlines()]
    replies = root.parent / f"{root.name}-{deck.folder}.jsonl"
    replies.write_text(
        "".join(
            json.dumps(
                {
                    "id": row["id"],
                    "prompt_sha256": row["prompt_sha256"],
                    "reply": json.dumps({"slot": json.loads(row["prompt"])["own"][0]}),
                }
            )
            + "\n"
            for row in rows
        )
    )
    out = root / deck.folder
    code, receipt = ballots.freeze(
        argparse.Namespace(
            deck=study / deck.folder,
            deck_sha256=deck.pin,
            role=role,
            source=name if source is None else source,
            replies=[replies],
            out=out,
        )
    )
    assert code == 0 and receipt["complete"]
    return out


def _snapshot(root: Path) -> dict[Path, object]:
    return {
        p.relative_to(root): (
            p.lstat().st_mode,
            p.lstat().st_mtime_ns,
            p.readlink() if p.is_symlink() else p.read_bytes() if p.is_file() else None,
        )
        for p in root.rglob("*")
    }


def test_mixed_coverage_is_read_only(study):
    path, plan, root = study
    complete = _freeze(path, plan, root, "beta", "east")
    corrupt = _freeze(path, plan, root, "beta", "west")
    (corrupt / "evidence.json").write_bytes(b"not json")
    (root / "t01-west").symlink_to(root / "absent", target_is_directory=True)
    (root / "unrelated").write_text("not a job")
    raw = (complete / "evidence.json").read_bytes() + b" "
    (complete / "evidence.json").write_bytes(raw)
    before = _snapshot(root)

    result = coverage.inspect(plan, root, 2, None)
    assert result.keys() == {
        "schema",
        "stage",
        "study_sha256",
        "workers",
        "worker",
        "all",
        "selected",
        "jobs",
        "scope",
    }
    assert result["schema"] == "seatlab.partner-switch-coverage/v1"
    assert result["stage"] == "report" and result["study_sha256"] == plan.pin
    assert result["workers"] == 2 and result["worker"] is None
    assert (
        result["all"]
        == result["selected"]
        == {
            "expected": 4,
            "missing": 1,
            "invalid": 2,
            "complete": 1,
        }
    )
    assert "read-only" in result["scope"].lower()
    assert "no calls were made" in result["scope"]
    assert result["jobs"] == [
        {
            "team": name,
            "role": role,
            "folder": plan.decks[name, role].folder,
            "deck_sha256": plan.decks[name, role].pin,
            "assigned_worker": i % 2,
            "state": state,
            "expected": len(plan.decks[name, role].packets),
            "capture_sha256": loader.digest(raw) if state == "complete" else None,
        }
        for i, ((name, role), state) in enumerate(
            zip(plan.decks, ("complete", "invalid", "missing", "invalid"), strict=True)
        )
    ]
    assert _snapshot(root) == before


def _assignments(plan: planio.Plan, root: Path) -> dict[int, list[int]]:
    assignments = {}
    for n in range(1, 13):
        all_jobs = coverage.inspect(plan, root, n, None)
        rows = all_jobs["jobs"]
        assignments[n] = [row["assigned_worker"] for row in rows]
        assert assignments[n] == [i % n for i in range(len(plan.decks))]
        selected = []
        for worker in range(n):
            result = coverage.inspect(plan, root, n, worker)
            assert result["all"] == all_jobs["all"]
            assert result["jobs"] == rows[worker::n]
            assert result["selected"]["expected"] == len(rows[worker::n])
            assert sum(
                result["selected"][s] for s in ("complete", "missing", "invalid")
            ) == len(rows[worker::n])
            selected.extend(row["folder"] for row in result["jobs"])
        assert sorted(selected) == sorted(deck.folder for deck in plan.decks.values())
    return assignments


def test_assignments_independent_of_completion(study):
    path, plan, root = study
    before = _assignments(plan, root)
    _freeze(path, plan, root, "alpha", "west")
    assert _assignments(plan, root) == before
    selected = coverage.inspect(plan, root, 4, 0)
    assert selected["selected"] == {
        "expected": 1,
        "missing": 1,
        "invalid": 0,
        "complete": 0,
    }
    assert selected["all"] == {
        "expected": 4,
        "missing": 3,
        "invalid": 0,
        "complete": 1,
    }


def test_foreign_capture_wrong_source_and_non_directory_are_invalid(study):
    path, plan, root = study
    _freeze(path, plan, root, "beta", "east", source="alpha")
    foreign = path.parent / "foreign"
    receipt = planio.write(
        foreign, "report", 19, NAMES, {}, panel.report_inputs(engine.cases(19), NAMES)
    )
    _freeze(
        foreign, planio.load(foreign, receipt["study_sha256"]), root, "beta", "west"
    )
    (root / "t01-east").write_bytes(b"not a directory")
    result = coverage.inspect(plan, root, 1, None)
    assert result["all"] == {
        "expected": 4,
        "missing": 1,
        "invalid": 3,
        "complete": 0,
    }
    assert all(job["capture_sha256"] is None for job in result["jobs"])


@pytest.mark.parametrize(
    ("workers", "worker"),
    [
        (0, None),
        (13, None),
        (True, None),
        (1.0, None),
        (1, -1),
        (1, 1),
        (2, False),
        (2, 0.0),
    ],
)
def test_invalid_worker_arguments(study, workers, worker):
    _, plan, root = study
    with pytest.raises(ValueError):
        coverage.inspect(plan, root, workers, worker)


def test_root_must_exist_and_be_a_real_directory(study):
    _, plan, root = study
    absent = root / "absent"
    file = root / "file"
    file.write_bytes(b"not a directory")
    link = root / "link"
    link.symlink_to(root, target_is_directory=True)
    for invalid in (absent, file, link):
        with pytest.raises(ValueError):
            coverage.inspect(plan, invalid, 1, None)
