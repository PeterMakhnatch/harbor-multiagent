import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from threading import Event
from typing import Any
from zipfile import ZipFile

import pytest

from examples.seatlab.rolepack import protocol, work

_CONFIG = b'{"dispatch":{"argv":["/bin/cat","private-config-marker"],"env":[]}}\n'
_RUBRIC_ONLY = "do-not-send-this-rubric-only-action"
_PRIVATE_REPLY = "do-not-export-this-private-reply"


def _record(record_id: str, task: str, peer: bool) -> dict[str, Any]:
    text = f"  Logistics prompt {record_id}: café\n\t\n"
    return {
        "id": record_id,
        "task": task,
        "world": record_id,
        "prompt": text,
        "prompt_sha256": sha256(text.encode("utf-8")).hexdigest(),
        "peer": {
            "answer": {"action": "continue"},
            "run": f"prior-{record_id}",
            "source_sha256": "a" * 64,
            "response_sha256": None,
        } if peer else None,
        "rubric": {
            "field": "action",
            "scalar": True,
            "items": [
                {
                    "key": "action",
                    "valid": ["continue", _RUBRIC_ONLY],
                    "optimal": [_RUBRIC_ONLY],
                }
            ],
        },
    }


@pytest.fixture
def study_pack(tmp_path: Path) -> tuple[Path, dict[str, Any], Path]:
    directory = tmp_path.resolve()
    pack = {
        "schema": "seatlab.rolepack/v1",
        "records": [
            _record("ship-s6501-r0", "shipment", True),
            _record("cal-rx-w00-d0-r0-blind", "calendar", True),
            _record("ship-s6502-r0", "shipment", False),
        ],
    }
    config = directory / "original-dispatch.json"
    config.write_bytes(_CONFIG)
    study = directory / "role-study"
    sealed = work.seal(study, pack, config)
    assert str(config) not in json.dumps(sealed)
    assert study.stat().st_mode & 0o777 == 0o700
    return study, pack, config


def _archive(log_dir: Path, ids: list[str], status: str = "success") -> Path:
    log = log_dir / "real-run.eval"
    with ZipFile(log, "x") as archive:
        archive.writestr(
            "header.json",
            json.dumps({
                "version": 2,
                "status": status,
                "eval": {"dataset": {"sample_ids": ids}},
                "metadata": {"private": _PRIVATE_REPLY},
            }),
        )
    return log


def _requests(dataset: Path) -> list[dict[str, Any]]:
    data = dataset.read_text(encoding="utf-8")
    assert _RUBRIC_ONLY not in data
    rows = [json.loads(line) for line in data.splitlines()]
    assert all(set(row) == {"id", "input"} for row in rows)
    return rows


def test_concurrent_ranges_claim_each_eligible_sample_once(
    study_pack: tuple[Path, dict[str, Any], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    study, pack, config = study_pack
    plan = protocol.plan(pack)
    records = {record["id"]: record for record in pack["records"]}
    first_requests = [row for row in plan[:5] if row["prompt_sha256"] is not None]
    second_requests = [row for row in plan[5:] if row["prompt_sha256"] is not None]
    first_entered, second_entered = Event(), Event()
    calls = []

    def record(dataset: Path, snapshot: Path, log_dir: Path) -> Path:
        rows = _requests(dataset)
        assert snapshot != config and snapshot.read_bytes() == _CONFIG
        assert log_dir.stat().st_mode & 0o777 == 0o700
        calls.append(rows)
        if rows[0]["id"] == first_requests[0]["sample_id"]:
            first_entered.set()
            assert second_entered.wait(10)
        else:
            second_entered.set()
        return _archive(log_dir, [row["id"] for row in rows])

    monkeypatch.setattr(work, "run_inspect", record)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(work.run, study, pack, 0, 5)
        assert first_entered.wait(10)
        second = pool.submit(work.run, study, pack, 3, len(plan))
        first_job, second_job = first.result(timeout=15), second.result(timeout=15)

    assert calls == [
        [
            {"id": row["sample_id"], "input": protocol.prompt(records[row["record_id"]], row["arm"])}
            for row in enrolled
        ]
        for enrolled in (first_requests, second_requests)
    ]
    assert first_job["claimed"] == len(first_requests)
    assert second_job["claimed"] == len(second_requests)
    assert second_job["already_claimed"] == sum(row["index"] >= 3 for row in first_requests)
    assert work.pending(study, pack) == []
    assert work.run(study, pack, 0, len(plan))["claimed"] == 0
    assert len(calls) == 2
    assert work.logs(study, pack) == [Path(job["inspect_log"]) for job in (first_job, second_job)]
    public = json.dumps([first_job, second_job])
    assert all(marker not in public for marker in (_RUBRIC_ONLY, _PRIVATE_REPLY, str(config)))


def test_interrupted_batch_is_never_replayed_or_exposed(
    study_pack: tuple[Path, dict[str, Any], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    study, pack, _ = study_pack
    entered = []

    def interrupt(dataset: Path, _config: Path, log_dir: Path) -> Path:
        ids = [row["id"] for row in _requests(dataset)]
        entered.extend(ids)
        _archive(log_dir, ids)
        raise RuntimeError(_PRIVATE_REPLY)

    monkeypatch.setattr(work, "run_inspect", interrupt)
    with pytest.raises(ValueError, match="^inspect_run_failed$"):
        work.run(study, pack, 0, 1)
    assert entered == [protocol.plan(pack)[0]["sample_id"]]
    assert 0 not in work.pending(study, pack)
    assert work.run(study, pack, 0, 1)["inspect_log"] is None
    assert work.logs(study, pack) == []
    assert len(entered) == 1


def test_changes_fail_before_a_call(
    study_pack: tuple[Path, dict[str, Any], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    study, pack, config = study_pack
    calls = []

    def record(dataset: Path, _config: Path, log_dir: Path) -> Path:
        ids = [row["id"] for row in _requests(dataset)]
        calls.extend(ids)
        return _archive(log_dir, ids)

    monkeypatch.setattr(work, "run_inspect", record)
    original_pending = work.pending(study, pack)
    with pytest.raises(FileExistsError):
        work.seal(study, pack, config)
    changed = deepcopy(pack)
    changed["records"][0]["world"] = "different-world"
    with pytest.raises(ValueError, match="^study_pack_changed$"):
        work.run(study, changed, 0, 1)
    with pytest.raises(ValueError, match="^range_outside_study$"):
        work.run(study, pack, False, 1)
    config.write_bytes(_CONFIG + b"\n")
    with pytest.raises(ValueError, match="^study_config_changed$"):
        work.run(study, pack, 0, 1)
    assert calls == []
    assert work.pending(study, pack) == original_pending


def test_only_registered_unchanged_native_archives_are_listed(
    study_pack: tuple[Path, dict[str, Any], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    study, pack, _ = study_pack

    def record(dataset: Path, _config: Path, log_dir: Path) -> Path:
        return _archive(log_dir, [row["id"] for row in _requests(dataset)])

    monkeypatch.setattr(work, "run_inspect", record)
    _archive(study, [protocol.plan(pack)[0]["sample_id"]])
    assert work.logs(study, pack) == []
    job = work.run(study, pack, 0, 1)
    log = Path(job["inspect_log"])
    assert work.logs(study, pack) == [log]
    with log.open("ab") as out:
        out.write(b"changed-log")
    with pytest.raises(ValueError, match="^inspect_archive_changed$"):
        work.logs(study, pack)
    marker = log.parent.parent / "claim-0.json"
    marker.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="^invalid_batch_claims$"):
        work.run(study, pack, 0, 1)


def test_archive_from_another_batch_is_not_pinned(
    study_pack: tuple[Path, dict[str, Any], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    study, pack, _ = study_pack

    def wrong_ids(_dataset: Path, _config: Path, log_dir: Path) -> Path:
        return _archive(log_dir, ["other-sample"])

    monkeypatch.setattr(work, "run_inspect", wrong_ids)
    with pytest.raises(ValueError, match="^invalid_registered_inspect_archive$"):
        work.run(study, pack, 0, 1)
    assert 0 not in work.pending(study, pack)
    assert work.logs(study, pack) == []
