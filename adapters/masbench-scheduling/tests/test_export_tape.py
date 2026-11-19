"""Portable tape evidence, privacy, and publication boundaries."""

import hashlib
import importlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from test_study import _attempt, _register

ADAPTER = Path(__file__).resolve().parents[1]


@pytest.fixture
def exporter(monkeypatch):
    monkeypatch.syspath_prepend(str(ADAPTER))
    return importlib.import_module("export_tape")


def _digest(document):
    canonical = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def test_exports_actual_bound_leader_view_and_original_answers(exporter, tmp_path):
    study = exporter.study
    root = tmp_path / "study"
    out = tmp_path / "portable.jsonl"
    plan, cases = _register(study, root, ["2/1"])
    full, first8 = plan["cells"]
    case = cases["2/1"]
    ids = [str(uuid4()), str(uuid4())]
    secret = "PRIVATE-RESULT-METADATA"

    def change_actual_choice(world, result):
        world["choice"] = (
            23  # Official gold, but physically impossible across midnight.
        )
        world["reports"]["m2"] = [42]  # A real report can differ from the control.
        world["proposal_sha256"] = _digest(
            {"role": "m1", "mode": "full", "reports": world["reports"]}
        )
        result["private_metadata"] = secret

    native_dirs = [
        _attempt(
            study, root, full, case, identifier=ids[0], modify=change_actual_choice
        ),
        _attempt(study, root, first8, case, identifier=ids[1]),
    ]
    worlds = [
        json.loads((native / study.NATIVE_FILES["world"]).read_bytes())
        for native in native_dirs
    ]

    # The saved native trial URI points at the original, now absent directory.
    moved = root.rename(tmp_path / "moved")
    assert exporter.export_tape(moved, out) == {
        "attempted": 2,
        "verified": 2,
        "exported": 2,
        "excluded": 0,
    }
    text = out.read_bytes().decode("ascii")
    records = [json.loads(line) for line in text.splitlines()]
    assert len(records) == 2
    assert text.endswith("\n")
    for index, (row, world) in enumerate(zip(records, worlds, strict=True)):
        mode = ("full", "first8")[index]
        assert row == {
            "schema": "seatlab.decision/v1",
            "basis": "bound",
            "key": f"calendar:2-1:{mode}",
            "role": "m1",
            "native_trial_id": ids[index],
            "view": {"role": "m1", "mode": mode, "reports": world["reports"]},
            "view_sha256": world["proposal_sha256"],
            "observed": {"action": (23, None)[index]},
            "answers": {
                "kind": "int-range",
                "min": 0,
                "max": 47,
                "include_null": True,
                "optimal": [10, 23],
                "feasible": [*range(10, 23), *range(24, 47)],
            },
        }
        assert row["view_sha256"] == _digest(row["view"])
        assert row["view_sha256"] != _digest({**row["view"], "role": "m2"})
    assert records[0]["view"]["reports"]["m2"] == [42]
    assert records[1]["observed"] is not None
    for private in (
        str(tmp_path),
        secret,
        "/private/nonexistent/original",
        '"busy"',
        '"members"',
        '"gold"',
        '"initial_view_sha256"',
    ):
        assert private not in text


def test_excludes_unknown_and_rejected_cells_without_publishing_raw_details(
    exporter, tmp_path, capsys
):
    study = exporter.study
    root = tmp_path / "private"
    out = tmp_path / "public.jsonl"
    plan, cases = _register(study, root, ["2/1", "2/2", "2/3", "2/4"])
    repeated_id = str(uuid4())
    for cell in plan["cells"][:2]:
        _attempt(study, root, cell, cases["2/1"], identifier=repeated_id)
    missing_choice, bad_role, tampered, failed, incomplete, _unattempted = plan[
        "cells"
    ][2:]
    _attempt(
        study,
        root,
        missing_choice,
        cases["2/2"],
        modify=lambda world, _result: world.pop("choice"),
    )
    _attempt(
        study,
        root,
        bad_role,
        cases["2/2"],
        modify=lambda world, _result: world.update(
            proposal_sha256=_digest(
                {"role": "m2", "mode": world["mode"], "reports": world["reports"]}
            )
        ),
    )
    native = _attempt(study, root, tampered, cases["2/3"])
    (native / study.NATIVE_FILES["world"]).write_text("PRIVATE-CORRUPT-WORLD")
    _attempt(study, root, failed, cases["2/3"], returncode=1)
    study._paths(root, incomplete)[0].mkdir()

    assert exporter.main(["--native", str(root), "--out", str(out)]) == 0
    assert out.read_bytes() == b""
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "attempted": 7,
        "verified": 0,
        "exported": 0,
        "excluded": 8,
    }
    assert captured.err == ""


@pytest.mark.parametrize("change", ["world-bytes", "world-missing", "source"])
def test_race_after_collection_prevents_any_publication(
    exporter, tmp_path, monkeypatch, change
):
    study = exporter.study
    root = tmp_path / "native"
    out = tmp_path / "exact.jsonl"
    plan, cases = _register(study, root, ["2/1"])
    for cell in plan["cells"]:
        native = _attempt(study, root, cell, cases["2/1"])
    world_path = native / study.NATIVE_FILES["world"]
    collect = study.collect

    def collect_then_change(path):
        report = collect(path)
        assert all(row["status"] == "verified" for row in report["cells"])
        if change == "world-bytes":
            # The parsed JSON is identical. The byte digest must still fail.
            world_path.write_bytes(world_path.read_bytes() + b"\n")
        elif change == "world-missing":
            world_path.unlink()
        else:
            source_path = root / "source.json"
            source = json.loads(source_path.read_bytes())
            source["cases"]["2/1"]["gold"] = [10]
            source_path.write_text(json.dumps(source))
        return report

    monkeypatch.setattr(study, "collect", collect_then_change)
    message = (
        "registered calendar snapshot changed after collection"
        if change == "source"
        else "native world evidence changed after collection"
    )
    with pytest.raises(ValueError, match=f"^{message}$"):
        exporter.export_tape(root, out)
    assert not out.exists()
    assert set(tmp_path.iterdir()) == {root}


@pytest.mark.parametrize("symlink", [False, True])
def test_missing_cells_do_not_replace_existing_output(exporter, tmp_path, symlink):
    root = tmp_path / "study"
    _register(exporter.study, root, ["2/1"])
    out = tmp_path / "exact.jsonl"
    target = tmp_path / "absent"
    if symlink:
        out.symlink_to(target)
    else:
        out.write_bytes(b"keep this content\n")
    with pytest.raises(FileExistsError):
        exporter.export_tape(root, out)
    if symlink:
        assert out.readlink() == target
        assert not target.exists()
    else:
        assert out.read_bytes() == b"keep this content\n"


def test_missing_registration_fails_without_publishing(exporter, tmp_path, capsys):
    root = tmp_path / "absent-private-study"
    out = tmp_path / "exact.jsonl"
    with pytest.raises(SystemExit) as error:
        exporter.main(["--native", str(root), "--out", str(out)])
    assert error.value.code == 1
    assert not out.exists()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "invalid or unreadable native study registration\n"
