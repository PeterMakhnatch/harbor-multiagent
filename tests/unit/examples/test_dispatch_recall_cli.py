import hashlib
import json

import pytest

from examples.seatlab.dispatch_recall import registry
from examples.seatlab.dispatch_recall.__main__ import main
from examples.seatlab.dispatch_recall.native import Session

SEEDS = ["1", "2", "3", "4", "5", "6"]
RULE = {"immediate": "correct", "deferred": "wrong", "withheld": "wrong"}
OFFICIAL = "primary_supported"


def _run(capsys, *argv):
    code = main([str(arg) for arg in argv])
    out, err = capsys.readouterr()
    return code, out, err


def _plan(tmp_path, capsys):
    path = tmp_path / "plan.json"
    code, out, _ = _run(
        capsys, "design", "--seeds", *SEEDS, "--order-seed", 77, "--out", path
    )
    assert code == 0 and json.loads(out)["started"] == 0
    return path, json.loads(path.read_text())


def _cut(job):
    s = job["session"]
    session = Session(s["seed"], s["transition"], s["timing"])
    first = session.view("loader")
    session.apply("loader", {"op": "stage", "bay": first["initial_bay"]})
    current = session.view("depot")["current_bay"]
    other = next(bay for bay in first["bays"] if bay != current)
    session.apply("depot", {"op": "report", "bay": current})
    ticket = session.view("loader")["ticket"]
    bay = current if RULE[s["timing"]] == "correct" else other
    session.apply("loader", {"op": "commit", "bay": bay, "ticket": ticket})
    return session.snapshot()


def _cut_args(tmp_path, plan):
    args = []
    for job in plan["jobs"]:
        path = tmp_path / f"{job['job_id']}.json"
        path.write_text(json.dumps(_cut(job)))
        args += ["--cut", job["job_id"], path]
    return args


def test_valid_raw_replay_never_reports_an_official_verdict(tmp_path, capsys):
    plan_path, plan = _plan(tmp_path, capsys)
    code, out, err = _run(capsys, "assess", plan_path, *_cut_args(tmp_path, plan))
    assert code == 0 and err == ""
    result = json.loads(out)
    assert result["supplied_cuts"] == result["planned_jobs"] == 36
    assert result["replay_verdict"] == OFFICIAL
    assert result["verdict"] == "unverified_replay_only"
    assert result["evidence_source"] == "unverified_cut_replay"
    assert result["provider_authenticated"] is False
    carriers = sorted(key for key, value in result.items() if value == OFFICIAL)
    assert carriers == ["replay_verdict"]


def test_assess_without_evidence_claims_no_outcomes(tmp_path, capsys):
    plan_path, _ = _plan(tmp_path, capsys)
    code, out, _ = _run(capsys, "assess", plan_path)
    result = json.loads(out)
    assert code == 0 and result["verdict"] == "no_outcomes"
    assert result["evidence_source"] == "no_evidence" and "replay_verdict" not in result


def _rejected(capsys, *argv):
    code, out, err = _run(capsys, *argv)
    assert code == 2 and out == ""
    return err


def test_registry_requires_root_and_a_pinned_digest(tmp_path, capsys):
    plan_path, _ = _plan(tmp_path, capsys)
    manifest = tmp_path / "registry.json"
    manifest.write_text("{}\n")
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert "--expect" in _rejected(
        capsys, "assess", plan_path, "--registry", manifest, "--root", tmp_path
    )
    assert "--root" in _rejected(
        capsys, "assess", plan_path, "--registry", manifest, "--expect", digest
    )
    assert "digest" in _rejected(
        capsys,
        "assess",
        plan_path,
        "--registry",
        manifest,
        "--root",
        tmp_path,
        "--expect",
        "0" * 64,
    )


def _pinned(tmp_path):
    manifest = dict.fromkeys(registry.MANIFEST_KEYS) | {"schema": registry.SCHEMA}
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(manifest))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_registry_rejects_missing_records(tmp_path, capsys):
    plan_path, _ = _plan(tmp_path, capsys)
    _rejected(
        capsys,
        "assess",
        plan_path,
        "--registry",
        tmp_path / "absent-registry.json",
        "--root",
        tmp_path,
        "--expect",
        "0" * 64,
    )
    manifest, digest = _pinned(tmp_path)
    _rejected(
        capsys,
        "assess",
        plan_path,
        "--registry",
        manifest,
        "--root",
        tmp_path / "absent-root",
        "--expect",
        digest,
    )
    jobs = tmp_path / "jobs"
    jobs.mkdir(mode=0o700)
    assert "unprepared cells" in _rejected(
        capsys,
        "assess",
        plan_path,
        "--registry",
        manifest,
        "--root",
        jobs,
        "--expect",
        digest,
    )


def test_replay_cannot_borrow_registry_options(tmp_path, capsys):
    plan_path, plan = _plan(tmp_path, capsys)
    job = plan["jobs"][0]
    cut = tmp_path / "cut.json"
    cut.write_text(json.dumps(_cut(job)))
    replay = ["--cut", job["job_id"], cut]
    assert "--registry" in _rejected(
        capsys, "assess", plan_path, *replay, "--expect", "0" * 64
    )
    assert "--registry" in _rejected(
        capsys, "assess", plan_path, *replay, "--root", tmp_path
    )
    with pytest.raises(SystemExit) as exit_info:
        main(["assess", str(plan_path), *map(str, replay), "--registry", str(cut)])
    assert exit_info.value.code == 2
    assert "not allowed with" in capsys.readouterr().err
    assert _rejected(
        capsys, "assess", plan_path, "--cut", job["job_id"], tmp_path / "none.json"
    )
