import importlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import pytest

with pytest.MonkeyPatch.context() as patch:
    root = Path(__file__).resolve().parents[3]
    patch.syspath_prepend(str(root / "examples" / "seatlab"))
    patch.syspath_prepend(str(root))
    native_panel = importlib.import_module("examples.seatlab.native_panel")

_SEATLAB = Path("examples/seatlab")
_SEAT_SOURCE = _SEATLAB / "history" / "actor.py"
_SHARED_SOURCE = _SEATLAB / "shared_actor.py"
_SEAT_BYTES = b"seat bytes\0\xff\n"
_SHARED_BYTES = b"shared seat bytes\n"
_CELL = {"cell_id": "history-a", "seed": 42, "condition": "fresh", "pairing": "SS"}
_EXPERIMENT = native_panel.Experiment(
    tag="history",
    grid=lambda: [_CELL],
    stage_roles=(("review", "reviewer"),),
    reservation_prefix="history-",
    agent_import="unused",
    seat_sources=(_SEAT_SOURCE.name,),
    shared_seat_sources=(_SHARED_SOURCE.name,),
    inspect_trial=lambda _trial, _config, _cell, _transport: pytest.fail(
        "an unclaimed cell must not inspect a trial"
    ),
    summarize=lambda _cells, rows: {"rows": len(rows)},
)


@dataclass(frozen=True)
class _Study:
    repo: Path
    path: Path
    revision: str
    plan: dict[str, object]


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=History Test",
            "-c",
            "user.email=history@example.invalid",
            *args,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return result.stdout.strip()


def _main(argv: list[str]) -> int:
    return native_panel.main(_EXPERIMENT, argv, description="Native history test")


def _report(study: _Study, *args: str) -> int:
    return _main(["report", "--study", str(study.path), *args])


@pytest.fixture
def study(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> _Study:
    for name in (
        "GIT_DIR",
        "GIT_COMMON_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = tmp_path.resolve() / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet", "--object-format=sha1", "--template=")
    (repo / _SEAT_SOURCE).parent.mkdir(parents=True)
    (repo / _SEAT_SOURCE).write_bytes(_SEAT_BYTES)
    (repo / _SHARED_SOURCE).write_bytes(_SHARED_BYTES)
    module_file = repo / _SEATLAB / "native_panel.py"
    module_file.touch()
    monkeypatch.setattr(native_panel, "__file__", str(module_file))
    examples = repo / "examples"
    monkeypatch.setattr(native_panel.panel, "EXAMPLES_DIR", examples)
    for name in native_panel.panel._source_names("arrival"):
        path = examples / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")
    _git(repo, "add", "--", "examples")
    _git(repo, "commit", "--quiet", "-m", "Register synthetic sources")
    revision = _git(repo, "rev-parse", "HEAD")
    config = tmp_path.resolve() / "commands.json"
    command = {"argv": [sys.executable], "env": []}
    config.write_text(
        json.dumps({"reviewer": command, "decider": command}), encoding="utf-8"
    )
    path = tmp_path.resolve() / "study"
    assert (
        _main(
            [
                "seal",
                "--study",
                str(path),
                "--transport",
                "commands",
                "--config",
                str(config),
            ]
        )
        == 0
    )
    return _Study(repo, path, revision, json.loads(capsys.readouterr().out))


@pytest.mark.parametrize(
    "source", [_SEAT_SOURCE, _SHARED_SOURCE], ids=["seat", "shared"]
)
def test_original_revision_replays_registered_source_stamps(
    study: _Study, source: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert study.plan["seat_sources"] == {
        _SEAT_SOURCE.name: sha256(_SEAT_BYTES).hexdigest(),
        f"shared/{_SHARED_SOURCE.name}": sha256(_SHARED_BYTES).hexdigest(),
    }
    assert _report(study) == 0
    original_report = json.loads(capsys.readouterr().out)
    changed = b"changed tracked source\n"
    (study.repo / source).write_bytes(changed)
    _git(study.repo, "add", "--", str(source))
    _git(study.repo, "commit", "--quiet", "-m", "Change registered source")
    current_revision = _git(study.repo, "rev-parse", "HEAD")
    assert _report(study) == 1
    assert capsys.readouterr().out == ""

    assert _report(study, "--source-revision", study.revision) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == original_report
    assert report["plan"] == study.plan
    assert _git(study.repo, "rev-parse", "HEAD") == current_revision
    assert (study.repo / source).read_bytes() == changed


def test_collect_with_original_revision_reports_an_unclaimed_cell(
    study: _Study, capsys: pytest.CaptureFixture[str]
) -> None:
    (study.repo / _SHARED_SOURCE).write_bytes(b"changed shared source\n")
    collect = ["collect", "--study", str(study.path), "--cell", "history-a"]
    assert _main(collect) == 1
    assert capsys.readouterr().out == ""
    assert _main([*collect, "--source-revision", study.revision]) == 1
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == dict(_CELL, state="unclaimed", outcome=None)


def test_original_revision_rejects_corrupted_sealed_stamp(
    study: _Study, capsys: pytest.CaptureFixture[str]
) -> None:
    register = study.path / "register.json"
    private = json.loads(register.read_bytes())
    private["plan"]["seat_sources"][f"shared/{_SHARED_SOURCE.name}"] = "0" * 64
    register.chmod(0o600)
    register.write_text(json.dumps(private), encoding="utf-8")
    register.chmod(0o400)
    assert _report(study, "--source-revision", study.revision) == 1
    assert capsys.readouterr().out == ""


def test_original_revision_does_not_restore_task_sources(
    study: _Study, capsys: pytest.CaptureFixture[str]
) -> None:
    task_file = study.repo / "examples/tasks/dispatch-handoff/environment/scenario.json"
    task_file.write_bytes(b"changed task source\n")
    assert _report(study, "--source-revision", study.revision) == 1
    assert capsys.readouterr().out == ""


def test_revision_missing_a_registered_blob_is_rejected(
    study: _Study, capsys: pytest.CaptureFixture[str]
) -> None:
    _git(study.repo, "rm", "--cached", "--", str(_SHARED_SOURCE))
    _git(study.repo, "commit", "--quiet", "-m", "Omit a registered source")
    missing_blob = _git(study.repo, "rev-parse", "HEAD")
    assert _report(study) == 0
    capsys.readouterr()
    assert _report(study, "--source-revision", missing_blob) == 1
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("kind", ["missing-commit", "tree", "symbolic", "abbreviated"])
def test_source_revision_requires_an_existing_full_commit_id(
    study: _Study, kind: str, capsys: pytest.CaptureFixture[str]
) -> None:
    match kind:
        case "missing-commit":
            revision = "0" * 40
        case "tree":
            revision = _git(study.repo, "rev-parse", "HEAD^{tree}")
        case "symbolic":
            revision = "HEAD"
        case "abbreviated":
            revision = study.revision[:12]
        case _:
            pytest.fail(f"unexpected revision kind: {kind}")
    assert _report(study, "--source-revision", revision) == 1
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("mode", ["run", "pending", "seal", "plan", "verify"])
def test_other_modes_reject_source_revision_at_parse_time(
    tmp_path: Path, mode: str, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = [mode]
    if mode == "verify":
        argv.append(str(tmp_path / "report.json"))
    elif mode != "plan":
        argv.extend(["--study", str(tmp_path / "study")])
    if mode == "run":
        argv.extend(["--cell", "history-a", "--harbor", str(tmp_path / "harbor")])
    elif mode == "seal":
        argv.extend(["--transport", "commands", "--config", str(tmp_path / "commands")])
    with pytest.raises(SystemExit) as stopped:
        _main([*argv, "--source-revision", "1" * 40])
    assert stopped.value.code == 2
    assert "unrecognized arguments: --source-revision" in capsys.readouterr().err
