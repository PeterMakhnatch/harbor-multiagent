import hashlib
import json
import os

import pytest

from examples.native_study import record_bytes, write_exclusive
from examples.seatlab.dispatch_recall import registry
from examples.seatlab.dispatch_recall.design import design
from examples.seatlab.dispatch_recall.prepare import CONFIG, LOCK, RECEIPT
from examples.seatlab.exhibit_capture import read_record

PLAN = design([1], 77)
PLAN_SHA = hashlib.sha256(record_bytes(PLAN)).hexdigest()


TURNS = ("stage", "report", "commit", "close")
ADAPTER = "examples.seatlab.dispatch_recall.participant:RecallParticipant"


def _paths(job_id):
    return {turn: f"/tmp/rs.{job_id}/{turn}.sock" for turn in TURNS}


def _receipt(job_id, kind="host_adapter", endpoints=None):
    receipt = {
        "schema": "seatlab.dispatch-recall.prepared/v1",
        "job_id": job_id,
        "plan_sha256": PLAN_SHA,
        "team": {
            "kind": kind,
            "agents": {seat: {"import_path": ADAPTER} for seat in ("loader", "depot")},
            "owner_endpoints": endpoints or _paths(job_id),
        },
        "role_file_digests": {},
        "role_instruction_digests": {},
        "world_scope": {"service": "main"},
        "verifier_env": {"RECALL_RETAIN_CUT": "1"},
        "claims": {"run": "not_started"},
    }
    receipt.update({key: f"pin-{key}" for key in registry.PINNED})
    return receipt


def _config(job_id, sockets=None):
    paths = sockets or _paths(job_id)
    loader = {k: paths[k] for k in ("stage", "commit", "close")}
    return {
        "participants": [
            {
                "seat": "loader",
                "import_path": ADAPTER,
                "kwargs": {"role": "loader", "owner_sockets": loader},
            },
            {
                "seat": "depot",
                "import_path": ADAPTER,
                "kwargs": {
                    "role": "depot",
                    "owner_sockets": {"report": paths["report"]},
                },
            },
        ]
    }


def _replace(root, job_id, name, record):
    os.unlink(root / job_id / name)
    write_exclusive(root / job_id / name, record, 0o600)


def _cell(root, job_id, receipt=None, config=None):
    folder = root / job_id
    folder.mkdir(mode=0o700)
    write_exclusive(folder / CONFIG, config or _config(job_id), 0o600)
    write_exclusive(folder / LOCK, {}, 0o600)
    write_exclusive(folder / RECEIPT, receipt or _receipt(job_id), 0o600)


@pytest.fixture
def root(tmp_path, monkeypatch):
    def slot(plan, job_id, folder, prepared):
        assert prepared.name == job_id and plan is PLAN
        receipt, data = read_record(folder, RECEIPT)
        config, _ = read_record(folder, CONFIG)
        built = {CONFIG: config, LOCK: {}, RECEIPT: receipt}
        return built, hashlib.sha256(data).hexdigest()

    monkeypatch.setattr(registry, "prepared_slot", slot)
    study = tmp_path / "study"
    study.mkdir()
    for job in PLAN["jobs"]:
        _cell(study, job["job_id"])
    return study


def _job(index):
    return PLAN["jobs"][index]["job_id"]


def test_seal_accepts_distinct_endpoints_and_pins_cells_in_plan_order(root):
    endpoints = [
        json.loads((root / _job(i) / RECEIPT).read_text())["team"]["owner_endpoints"]
        for i in range(6)
    ]
    assert all(len(e) == 4 for e in endpoints)
    assert len({path for e in endpoints for path in e.values()}) == 24
    manifest = registry.seal(PLAN, root)
    assert set(manifest) == registry.MANIFEST_KEYS
    assert [job["job_id"] for job in manifest["jobs"]] == [_job(i) for i in range(6)]
    assert manifest["team_kind"] == "host_adapter" and manifest["executions"] == 0
    assert "model" not in json.dumps(manifest) and str(root) not in json.dumps(manifest)
    (root / _job(2) / "trials").mkdir()
    entries = registry.checked_entries(PLAN, root, manifest)
    assert [(j, p.name) for j, p, _ in entries] == [
        (_job(i), _job(i)) for i in range(6)
    ]
    pin = hashlib.sha256((root / _job(0) / RECEIPT).read_bytes()).hexdigest()
    assert entries[0][2] == pin == manifest["jobs"][0]["receipt_sha256"]


def test_seal_refuses_partial_unknown_and_started_studies(root):
    (root / ".dispatch-recall-prepare-x").mkdir()
    with pytest.raises(ValueError, match="unplanned entries"):
        registry.seal(PLAN, root)
    os.rmdir(root / ".dispatch-recall-prepare-x")
    (root / _job(4) / "trials").mkdir()
    with pytest.raises(ValueError, match="already started"):
        registry.seal(PLAN, root)
    for name in (CONFIG, LOCK, RECEIPT):
        os.unlink(root / _job(4) / name)
    os.rmdir(root / _job(4) / "trials")
    os.rmdir(root / _job(4))
    with pytest.raises(ValueError, match="unprepared cells"):
        registry.seal(PLAN, root)


def test_seal_rejects_shared_job_directory_modes(root):
    os.chmod(root / _job(1), 0o755)
    with pytest.raises(ValueError, match="not a private prepared directory"):
        registry.seal(PLAN, root)


def test_seal_rejects_mixed_controls(root):
    _replace(root, _job(3), RECEIPT, _receipt(_job(3), "scripted_control"))
    with pytest.raises(ValueError, match=r"differs from the study in \['team'\]"):
        registry.seal(PLAN, root)


def test_seal_rejects_a_receipt_for_another_cell(root):
    _replace(root, _job(5), RECEIPT, _receipt(_job(0), endpoints=_paths(_job(5))))
    with pytest.raises(ValueError, match="binds another cell or plan"):
        registry.seal(PLAN, root)


def test_seal_rejects_receipt_endpoints_that_differ_from_config(root):
    _replace(root, _job(2), RECEIPT, _receipt(_job(2), endpoints=_paths(_job(3))))
    with pytest.raises(ValueError, match="differ from its configured owner sockets"):
        registry.seal(PLAN, root)


def test_seal_rejects_an_owner_socket_reused_across_cells(root):
    sockets = _paths(_job(1)) | {"report": _paths(_job(0))["report"]}
    _replace(root, _job(1), CONFIG, _config(_job(1), sockets))
    _replace(root, _job(1), RECEIPT, _receipt(_job(1), endpoints=sockets))
    with pytest.raises(ValueError, match="more than one cell"):
        registry.seal(PLAN, root)


def test_checked_entries_reject_injected_or_stale_manifests(root):
    manifest = registry.seal(PLAN, root)
    with pytest.raises(ValueError, match="not a sealed"):
        registry.checked_entries(PLAN, root, manifest | {"root": "/elsewhere"})
    moved = json.loads(json.dumps(manifest))
    moved["jobs"][0]["job_id"] = "../outside"
    with pytest.raises(ValueError, match="differ from the sealed registry"):
        registry.checked_entries(PLAN, root, moved)
    _replace(root, _job(0), RECEIPT, _receipt(_job(0)) | {"extra": 1})
    with pytest.raises(ValueError, match="differ from the sealed registry"):
        registry.checked_entries(PLAN, root, manifest)
