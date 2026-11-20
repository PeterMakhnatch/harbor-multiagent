import json
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest

from examples.seatlab.visibility import calendar_packet as packet

_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")
_START = "2026-01-01T12:00:00"
_FINISH = "2026-01-01T12:01:00"


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _briefing(seat: str) -> dict[str, dict[str, dict[str, list[int]]]]:
    short = set(range(9, 12) if seat == "p1" else range(10, 13))
    special = "Fri" if seat == "p1" else "Tue"
    edge = {15, 16} if seat == "p1" else {9, 10}
    free = {"Mon": short, special: edge}
    busy = {
        day: [hour for hour in range(9, 17) if hour not in free.get(day, ())]
        for day in _DAYS
    }
    return {
        "calendars": {
            f"private-{seat}-C": {day: [] for day in _DAYS},
            f"private-{seat}-B": {
                day: [16, 9] if day == "Wed" else [] for day in _DAYS
            },
            f"private-{seat}-A": busy,
        }
    }


def _source(root: Path, index: int) -> Path:
    name = f"schedule-{index:03d}-talk"
    task = root / name
    for name_in_task in packet._SOURCE_FILES:
        file = task / name_in_task
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("fixture only\n", encoding="utf-8")
    (task / "task.toml").write_text(
        f"""schema_version = "1.4"
[task]
name = "flotilla/{name}"
[metadata]
family = "schedule"
condition = "talk"
difficulty = "easy"
variant = ""
instance = {index}
seed = 0
n_seats = 2
team_seats = 2
max_messages = 3
max_text = 100
[agents.p1]
[agents.p2]
""",
        encoding="utf-8",
    )
    _write_json(
        task / "environment/task.json",
        {
            "family": "schedule",
            "condition": "talk",
            "difficulty": "easy",
            "variant": "",
            "instance": index,
            "seed": 0,
            "world_version": 2,
            "max_messages": 3,
            "max_text": 100,
            "seats": ["p1", "p2"],
            "answer_key": {"day": "Mon", "start": "10:00"},
        },
    )
    compose = """x-player: &player
  build: ./player
services:
  main:
    environment:
      FLOTILLA_SEATS: "p1,p2"
      FLOTILLA_CONDITION: "talk"
      FLOTILLA_MAX_TEXT: "100"
      FLOTILLA_MAX_MESSAGES: "3"
"""
    # Source order is intentionally opposite to the packet seat order.
    for seat in ("p2", "p1"):
        briefing = json.dumps(json.dumps(_briefing(seat)))
        compose += f'''  {seat}:
    <<: *player
    networks: [seat-{seat}]
    environment:
      SEAT: "{seat}"
      SEAT_TOKEN: "not-for-export"
      SEAT_BRIEFING: {briefing}
      WORLD_URL: "unused"
'''
    compose_path = task / "environment/docker-compose.yaml"
    compose_path.write_text(compose + "networks:\n", encoding="utf-8")
    return task


def _panel(job: Path, model: str, panel_index: int, sources: list[Path]) -> None:
    tasks, locks, rewarded = [], [], []
    participants = [
        {"seat": seat, "name": "terminus-2", "model_name": model}
        for seat in ("p1", "p2")
    ]
    provider, model_name = model.split("/", 1)
    config = {"install_only": False, "participants": participants}
    for index, source in enumerate(sources):
        trial = job / f"{source.name}__trial00"
        trial.mkdir(parents=True)
        path = f"/original/.flotilla-local/tasks/c1/schedule/{source.name}"
        task = {"path": path}
        tasks.append(task)
        trial_config = {**config, "task": task, "trial_name": trial.name}
        legacy, _, package = packet._source_hashes(source)
        trial_lock = {
            **config,
            "task": {
                **task,
                "name": source.name,
                "type": "local",
                "digest": f"sha256:{package}",
            },
        }
        locks.append(trial_lock)
        rewarded.append(trial.name)
        _write_json(trial / "lock.json", trial_lock)
        _write_json(trial / "config.json", trial_config)
        _write_json(
            trial / "result.json",
            {
                "id": str(UUID(int=100 * panel_index + index + 1)),
                "task_id": task,
                "task_name": f"flotilla/{source.name}",
                "task_checksum": legacy,
                "trial_name": trial.name,
                "config": trial_config,
                "started_at": f"{_START}+00:00",
                "finished_at": f"{_FINISH}+00:00",
                "verifier_result": {"rewards": {"reward": 1.0}},
                "exception_info": None,
                "seat_results": [
                    {
                        "seat": row["seat"],
                        "under_test": True,
                        "agent_info": {
                            "name": "terminus-2",
                            "model_info": {"provider": provider, "name": model_name},
                        },
                        "agent_execution": {"finished_at": f"{_FINISH}+00:00"},
                    }
                    for row in participants
                ],
            },
        )
    _write_json(job / "config.json", {**config, "job_name": job.name, "tasks": tasks})
    _write_json(
        job / "lock.json", {"trials": locks + [{}] * 12}
    )  # Unused silent trials.
    _write_json(
        job / "result.json",
        {
            "id": str(UUID(int=1000 + panel_index)),
            "n_total_trials": 24,
            "started_at": _START,
            "finished_at": _FINISH,
            "stats": {
                "n_completed_trials": 24,
                "n_running_trials": 0,
                "n_pending_trials": 0,
                "n_cancelled_trials": 0,
                "evals": {
                    "original group": {
                        "reward_stats": {"reward": {"0.0": [], "1.0": rewarded}},
                        "exception_stats": {},
                    }
                },
            },
        },
    )


@pytest.fixture
def original(tmp_path: Path) -> tuple[Path, Path]:
    schedule, jobs = tmp_path / "schedule", tmp_path / "jobs"
    sources = [_source(schedule, i) for i in range(12)]
    for i, (label, model) in enumerate(packet._PANEL.items()):
        _panel(jobs / label, model, i, sources)
    return schedule, jobs


def test_export_load_and_native_cache_gate(
    original: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    schedule, jobs = original
    source = schedule / "schedule-000-talk"
    clean, warnings = packet._project(schedule, jobs)
    assert warnings == ()
    digest = sha256(clean).hexdigest()
    monkeypatch.setattr(packet, "PACKET_SHA256", digest)
    cache = source / "environment/__pycache__/family.cpython-312.pyc"
    cache.parent.mkdir()
    cache.write_bytes(b"generated bytecode")
    assert packet.main(["export", str(schedule), str(jobs)]) == 0
    output = capsysbinary.readouterr()
    assert output.out == clean
    assert b"w00: generated bytecode" in output.err
    private = (b"private-", b"not-for-export", b"answer_key", b"10:00", b"trial00")
    assert not any(text in output.out for text in private)
    document = json.loads(output.out)
    assert set(document) == {"schema", "worlds"}
    assert all(
        set(record) == {"index", "task_checksum", "tables"}
        for record in document["worlds"]
    )

    exported = tmp_path / "packet.json"
    exported.write_bytes(output.out)
    assert packet.main(["verify", str(exported)]) == 0
    assert capsysbinary.readouterr().out == f"{digest}\n".encode()
    worlds = packet.load(exported, digest)
    assert tuple(world.index for world in worlds) == tuple(range(12))
    p1, p2 = worlds[0].tables
    assert p1[0] == (248, 255, 255, 255, 63)
    assert p2[0] == (241, 252, 255, 255, 255)
    assert p1[1] == (0, 0, 129, 0, 0)
    assert packet.starts(p1) == (0, 1, 34)
    assert packet.starts(p2) == (1, 2, 7)
    with pytest.raises(ValueError, match="generated bytecode changes"):
        packet.require_native_task_checksum(source, worlds[0].task_checksum)
    cache.unlink()
    packet.require_native_task_checksum(source, worlds[0].task_checksum)

    document["worlds"][0]["tables"][0][0][0] = True
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"))
    changed = (encoded + "\n").encode()
    exported.write_bytes(changed)
    with pytest.raises(ValueError, match="expected SHA-256"):
        packet.load(exported, digest)
    with pytest.raises(ValueError, match="one-byte busy masks"):
        packet.load(exported, sha256(changed).hexdigest())


def test_compare_tables_rejects_one_role_mismatch(
    original: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    schedule, jobs = original
    raw, _ = packet._project(schedule, jobs)
    exported = tmp_path / "frozen.json"
    exported.write_bytes(raw)
    monkeypatch.setattr(packet, "PACKET", exported)
    monkeypatch.setattr(packet, "PACKET_SHA256", sha256(raw).hexdigest())
    args = ["compare-tables", str(schedule)]
    assert packet.main(args) == 0
    matching = capsys.readouterr().out
    assert "12 worlds / 24 roles match" in matching
    assert "not native task identity" in matching

    compose = schedule / "schedule-011-talk/environment/docker-compose.yaml"
    before, marker, p1 = compose.read_text().partition("  p1:\n")
    # Another attendee already blocks these hours. The shared start remains unchanged.
    updated = before + marker + p1.replace("[16, 9]", "[15, 9]")
    compose.write_text(updated, encoding="utf-8")
    assert packet.main(args) == 1
    failure = capsys.readouterr()
    assert failure.out == ""
    assert "w11 p1: seat table differs" in failure.err


@pytest.mark.parametrize("change", ["seat", "overlap", "key", "symlink"])
def test_source_projection_rejects_wrong_role_key_and_path(
    tmp_path: Path, change: str
) -> None:
    source = _source(tmp_path, 0)
    compose = source / "environment/docker-compose.yaml"
    payload = source / "environment/task.json"
    if change == "seat":
        changed = compose.read_text().replace('SEAT: "p1"', 'SEAT: "p2"')
        compose.write_text(changed, encoding="utf-8")
    elif change == "overlap":
        changed = compose.read_text().replace("private-p1-A", "private-p2-A")
        compose.write_text(changed, encoding="utf-8")
    elif change == "key":
        data = json.loads(payload.read_bytes())
        data["answer_key"]["start"] = "11:00"
        _write_json(payload, data)
    else:
        moved = tmp_path / "untrusted.yaml"
        compose.rename(moved)
        compose.symlink_to(moved)
    expected = {
        "seat": "seat identity",
        "overlap": "attendee sets overlap",
        "key": "does not match the stored key",
        "symlink": "unsafe source file",
    }
    with pytest.raises(ValueError, match=expected[change]):
        packet.source_tables(source, 0)


def test_normalize_rejects_out_of_range_and_duplicate_hours() -> None:
    briefing = _briefing("p1")
    for invalid in ((16, 16), (17,), (True,)):
        briefing["calendars"]["private-p1-B"]["Wed"] = list(invalid)
        with pytest.raises(ValueError, match="busy hour"):
            packet.normalize_briefing(briefing)


@pytest.mark.parametrize("change", ["missing", "unlisted", "checksum", "source"])
def test_export_needs_unchanged_source_and_all_four_original_manifests(
    original: tuple[Path, Path], change: str
) -> None:
    schedule, jobs = original
    job = jobs / "c1-qwen36-2seat"
    trial = job / "schedule-011-talk__trial00"
    if change == "missing":
        (trial / "result.json").unlink()
    elif change == "unlisted":
        lock_file = job / "lock.json"
        data = json.loads(lock_file.read_bytes())
        data["trials"][11] = {}
        _write_json(lock_file, data)
    elif change == "checksum":
        result_file = trial / "result.json"
        data = json.loads(result_file.read_bytes())
        data["task_checksum"] = "0" * 64
        _write_json(result_file, data)
    else:
        modified = schedule / "schedule-011-talk/instructions/p2.md"
        modified.write_text("different", encoding="utf-8")
    expected = {
        "missing": "missing or unreadable source file",
        "unlisted": "job manifest refer to different trials",
        "checksum": "panel task checksums disagree",
        "source": "differs from the original recorded checksums",
    }
    with pytest.raises(ValueError, match=expected[change]):
        packet._project(schedule, jobs)
