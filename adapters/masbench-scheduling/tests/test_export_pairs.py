"""Pair real receipt shapes without launching Harbor or a model."""

import hashlib
import importlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from test_cases import _calendar, _reference, _source_case, _write_json
from test_study import _attempt, _case, _register

ADAPTER = Path(__file__).resolve().parents[1]


@pytest.fixture
def exporter(monkeypatch):
    monkeypatch.syspath_prepend(str(ADAPTER))
    return importlib.import_module("export_pairs")


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _register_snapshots(study, root, cases, modes):
    plan = study._plan(uuid4().hex, cases, list(modes), "1/1", len(cases))
    (root / "cells").mkdir(parents=True)
    study._write_once(root / "plan.json", plan)
    study._write_once(
        root / "source.json", {"dataset_commit": study.DATASET_COMMIT, "cases": cases}
    )
    return plan


def test_builds_deterministic_pair_bundle_from_separate_native_studies(
    exporter, tmp_path, capsys
):
    study = exporter.study
    source_root, target_root = tmp_path / "source", tmp_path / "target"
    source, cases = _register(study, source_root, ["2/10", "2/2"], ("full",))
    target, target_cases = _register(study, target_root, ["4/1", "2/2"], ("first8",))
    source_id, target_id, target_only_id = (str(uuid4()) for _ in range(3))

    def actual_report(world, result):
        world["choice"] = 23
        world["reports"]["m2"] = [42]
        world["proposal_sha256"] = _sha(
            _canonical({"mode": "full", "role": "m1", "reports": world["reports"]})
        )
        result["private_metadata"] = "DO NOT EXPORT THIS VALUE"

    _attempt(
        study,
        source_root,
        source["cells"][0],
        cases["2/2"],
        identifier=source_id,
        modify=actual_report,
    )
    for cell, identifier in zip(
        target["cells"], (target_id, target_only_id), strict=True
    ):
        _attempt(
            study,
            target_root,
            cell,
            target_cases[cell["case_id"]],
            identifier=identifier,
        )

    bundle, reversed_bundle = tmp_path / "bundle", tmp_path / "reversed"
    assert (
        exporter.main(
            [
                "--native",
                str(target_root),
                "--native",
                str(source_root),
                "--out",
                str(bundle),
            ]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary == exporter.export_pairs([source_root, target_root], reversed_bundle)
    assert summary["pairs"] == 3
    assert summary["verified"] == {"source": 1, "target": 2}
    assert summary["missing"] == {"source": 2, "target": 1}
    paths = {file.name: file.read_bytes() for file in bundle.iterdir()}
    assert set(paths) == {
        "decisions.jsonl",
        "sources.jsonl",
        "pairs.json",
        "UPSTREAM-LICENSE.txt",
    }
    assert paths == {file.name: file.read_bytes() for file in reversed_bundle.iterdir()}
    assert summary["sha256"] == {name: _sha(content) for name, content in paths.items()}
    assert b"Copyright (c) 2026 Qizhi Chu" in paths["UPSTREAM-LICENSE.txt"]

    tape_lines = paths["decisions.jsonl"].splitlines(keepends=True)
    records = [json.loads(line) for line in tape_lines]
    full, first8, target_only = records
    assert paths["sources.jsonl"] == tape_lines[0]
    assert [record["native_trial_id"] for record in records] == [
        source_id,
        target_id,
        target_only_id,
    ]
    assert all(record["basis"] == "bound" for record in records)
    assert full["view"]["reports"]["m2"] == [42]
    assert full["observed"] == {"action": 23}
    assert first8["observed"] == {"action": None}
    assert [record["view"]["mode"] for record in records] == [
        "full",
        "first8",
        "first8",
    ]
    assert full["answers"] == first8["answers"]

    pair_document = json.loads(paths["pairs.json"])
    assert paths["pairs.json"] == _canonical(pair_document) + b"\n"
    assert pair_document.keys() == {"schema", "pairs"}
    assert pair_document["schema"] == "seatlab.calibration/v1"
    assert [pair["id"] for pair in pair_document["pairs"]] == [
        "calendar:2-2",
        "calendar:2-10",
        "calendar:4-1",
    ]
    by_key = {
        record["key"]: line for record, line in zip(records, tape_lines, strict=True)
    }
    for pair, case_id in zip(
        pair_document["pairs"], ("2/2", "2/10", "4/1"), strict=True
    ):
        assert pair.keys() == {"id", "source", "target"}
        for side, mode in (("source", "full"), ("target", "first8")):
            key = f"{pair['id']}:{mode}"
            line = by_key.get(key)
            record = json.loads(line) if line else None
            if record is not None:
                assert line == _canonical(record) + b"\n"
            assert pair[side] == {
                "key": key,
                "case_sha256": _sha(_canonical((cases | target_cases)[case_id])),
                "native_trial_id": record["native_trial_id"] if record else None,
                "decision_sha256": _sha(_canonical(record)) if record else None,
            }

    for text in (paths["decisions.jsonl"], paths["sources.jsonl"], paths["pairs.json"]):
        for private in (str(tmp_path).encode(), b"DO NOT EXPORT", b'"busy"', b'"gold"'):
            assert private not in text


def test_failed_or_unacknowledged_native_arms_stay_missing(exporter, tmp_path):
    study = exporter.study
    root = tmp_path / "study"
    plan, cases = _register(study, root, ["2/1"])
    full, first8 = plan["cells"]
    _attempt(study, root, full, cases["2/1"], returncode=1)
    _attempt(
        study,
        root,
        first8,
        cases["2/1"],
        modify=lambda world, _result: world.update(acknowledged=False),
    )
    out = tmp_path / "bundle"
    summary = exporter.export_pairs([root], out)
    assert summary["missing"] == {"source": 1, "target": 1}
    assert (out / "decisions.jsonl").read_bytes() == b""
    assert (out / "sources.jsonl").read_bytes() == b""
    [pair] = json.loads((out / "pairs.json").read_bytes())["pairs"]
    for side in ("source", "target"):
        assert pair[side]["decision_sha256"] is pair[side]["native_trial_id"] is None


def test_changes_between_collections_prevent_publication(
    exporter, monkeypatch, tmp_path
):
    study = exporter.study
    root, out = tmp_path / "study", tmp_path / "bundle"
    plan, cases = _register(study, root, ["2/1"], ("full",))
    native = _attempt(study, root, plan["cells"][0], cases["2/1"])
    world = native / study.NATIVE_FILES["world"]
    collect = study.collect

    def collect_then_change(path):
        report = collect(path)
        if report["cells"][0]["status"] == "verified":
            world.write_bytes(world.read_bytes() + b"\n")
        return report

    monkeypatch.setattr(study, "collect", collect_then_change)
    with pytest.raises(ValueError, match="native decision tape is missing"):
        exporter.export_pairs([root], out)
    assert set(tmp_path.iterdir()) == {root}


@pytest.mark.parametrize("same_study", [False, True])
def test_refuses_duplicate_native_trial_uuid(exporter, tmp_path, same_study):
    study = exporter.study
    source_root = tmp_path / "source"
    source, cases = _register(
        study, source_root, ["2/1"], ("full", "first8") if same_study else ("full",)
    )
    identifier = str(uuid4())
    for cell in source["cells"]:
        _attempt(study, source_root, cell, cases["2/1"], identifier=identifier)
    roots = [source_root]
    if not same_study:
        target_root = tmp_path / "target"
        target, cases = _register(study, target_root, ["2/1"], ("first8",))
        _attempt(
            study, target_root, target["cells"][0], cases["2/1"], identifier=identifier
        )
        roots.append(target_root)
    with pytest.raises(ValueError, match="duplicate native trial UUID"):
        exporter.export_pairs(roots, tmp_path / "bundle")
    assert set(tmp_path.iterdir()) == set(roots)


@pytest.mark.parametrize("kind", ["root", "cell", "hash"])
def test_refuses_conflicting_native_registrations(exporter, tmp_path, kind):
    study = exporter.study
    first, other = tmp_path / "first", tmp_path / "other"
    _register(study, first, ["2/1"], ("full",))
    if kind == "root":
        other.symlink_to(first, target_is_directory=True)
        message = "duplicate native study root"
    elif kind == "cell":
        _register(study, other, ["2/1"], ("full",))
        message = "duplicate registered calendar cell"
    else:
        cases = {"2/1": _case(study, "2/1", gold=(10,))}
        _register_snapshots(study, other, cases, ("first8",))
        message = "registered case SHA-256 mismatch"
    with pytest.raises(ValueError, match=message):
        exporter.export_pairs([first, other], tmp_path / "bundle")
    assert set(tmp_path.iterdir()) == {first, other}


def _fake_git(study, monkeypatch, dataset, *, revision=None, changed=""):
    dataset = dataset.resolve()

    def command(args):
        assert args[:4] == ["git", "--no-optional-locks", "-C", str(dataset)]
        if args[4:] == ["rev-parse", "--show-toplevel"]:
            return str(dataset)
        if args[4:] == ["rev-parse", "HEAD"]:
            return study.DATASET_COMMIT if revision is None else revision
        assert args[4:] == [
            "diff",
            "--name-only",
            "--no-ext-diff",
            "HEAD",
            "--",
            study.SOURCE_DIR,
        ]
        return changed

    monkeypatch.setattr(study, "_command", command)


def test_all_cases_hashes_pinned_source_files_even_if_no_native_run_exists(
    exporter, monkeypatch, tmp_path
):
    study = exporter.study
    dataset, root, out = (tmp_path / name for name in ("dataset", "study", "bundle"))
    _fake_git(study, monkeypatch, dataset)
    ids = [
        f"{stratum}/{index}"
        for stratum, count in ((2, 40), (3, 60), (4, 60), (5, 40))
        for index in range(1, count + 1)
    ]
    for case_id in ids:
        source = _source_case(dataset, case_id)
        source.mkdir(parents=True)
        for seat in range(1, 6):
            _write_json(source / f"Member{seat}_final_schedule.json", _calendar(seat))
        _write_json(source / "task_plans", _reference([]))
    case = study.load_case(dataset, "2/1")
    plan = _register_snapshots(study, root, {"2/1": case}, ("full",))
    _attempt(study, root, plan["cells"][0], case)

    summary = exporter.export_pairs([root], out, dataset=dataset, all_cases=True)
    assert summary["pairs"] == 200
    assert summary["missing"] == {"source": 199, "target": 200}
    pairs = json.loads((out / "pairs.json").read_bytes())["pairs"]
    assert [pair["id"] for pair in pairs] == [
        f"calendar:{case_id.replace('/', '-')}" for case_id in ids
    ]
    for index, (case_id, pair) in enumerate(zip(ids, pairs, strict=True)):
        expected = _sha(_canonical(study.load_case(dataset, case_id)))
        assert (
            pair["source"]["case_sha256"] == pair["target"]["case_sha256"] == expected
        )
        assert pair["target"]["native_trial_id"] is None
        assert (pair["source"]["native_trial_id"] is None) == (index != 0)
    assert len({pair["source"]["case_sha256"] for pair in pairs}) == 200

    # Changing only raw file bytes still changes the canonical loaded case hash.
    reference = _source_case(dataset, "2/1") / "task_plans"
    reference.write_bytes(reference.read_bytes() + b"\n")
    rejected = tmp_path / "rejected"
    with pytest.raises(
        ValueError, match="registered case does not match pinned MASBench: 2/1"
    ):
        exporter.export_pairs([root], rejected, dataset=dataset, all_cases=True)
    assert not rejected.exists()


def test_all_cases_requires_exact_pin_and_a_checkout(exporter, monkeypatch, tmp_path):
    study = exporter.study
    dataset, root, out = (tmp_path / name for name in ("dataset", "study", "bundle"))
    dataset.mkdir()
    _register(study, root, ["2/1"], ("full",))
    with pytest.raises(ValueError, match="--all-cases requires --dataset"):
        exporter.export_pairs([root], out, all_cases=True)
    _fake_git(study, monkeypatch, dataset, revision="wrong revision")
    with pytest.raises(ValueError, match=study.DATASET_COMMIT):
        exporter.export_pairs([root], out, all_cases=True, dataset=dataset)
    _fake_git(study, monkeypatch, dataset, changed="Scheduling/modified.json")
    with pytest.raises(ValueError, match=study.DATASET_COMMIT):
        exporter.export_pairs([root], out, all_cases=True, dataset=dataset)
    assert set(tmp_path.iterdir()) == {dataset, root}


@pytest.mark.parametrize("existing", ["empty-directory", "dangling-symlink", "race"])
def test_does_not_replace_existing_destination(
    exporter, tmp_path, monkeypatch, existing
):
    root, out, absent = (tmp_path / name for name in ("study", "bundle", "absent"))
    _register(exporter.study, root, ["2/1"])
    if existing == "empty-directory":
        out.mkdir()
    elif existing == "dangling-symlink":
        out.symlink_to(absent, target_is_directory=True)
    else:
        rename = exporter._rename_new_directory

        def race(stage, destination):
            destination.mkdir()
            rename(stage, destination)

        monkeypatch.setattr(exporter, "_rename_new_directory", race)
    with pytest.raises(FileExistsError):
        exporter.export_pairs([root], out)
    if existing == "dangling-symlink":
        assert out.readlink() == absent and not absent.exists()
    else:
        assert out.is_dir() and not list(out.iterdir())
    assert set(tmp_path.iterdir()) == {root, out}
