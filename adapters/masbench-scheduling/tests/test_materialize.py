import json
import stat
from importlib import import_module
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from harbor.models.task.task import Task

pytestmark = pytest.mark.unit

ROLES = ("m1", "m2", "m3", "m4", "m5")
PYTHON_IMAGE = "python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b"


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    return import_module("materialize")


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    root = tmp_path / "masbench"
    source = root / "Scheduling/meeting/minutes_10/5days/4/1"
    source.mkdir(parents=True)
    for member in range(1, 6):
        calendar = {
            "start_time": 0,
            "end_time": 720,
            "time_unit": "minutes_10",
            "total_days": 5,
            "member_id": member,
            "member_name": f"Member{member}",
            "activities": [
                {
                    "description": "Busy: PRIVATE_PROSE",
                    "date": 1,
                    "week_id": 1,
                    "duration": f"0{member}:00-0{member}:10",
                    "day_of_week": "Monday",
                }
            ],
        }
        (source / f"Member{member}_final_schedule.json").write_text(
            json.dumps(calendar)
        )
    plans = {
        "meeting": {
            "task": "PRIVATE_PROSE",
            "task_type": "meeting",
            "duration": 2,
            "solutions": [{"solution_id": 0, "start_time": 36, "end_time": 38}],
        }
    }
    (source / "task_plans").write_text(json.dumps(plans))
    return root


@pytest.mark.parametrize("mode", ["full", "first8"])
def test_materialize_native_task(
    adapter: ModuleType, dataset: Path, tmp_path: Path, mode: str
) -> None:
    output = tmp_path / "private" / "case"
    assert adapter.materialize(dataset, "4/1", mode, output) == output.resolve()
    task = Task(output)
    assert task.config.schema_version == "1.4"
    assert tuple(task.config.agents) == ROLES
    assert all(seat.under_test for seat in task.config.agents.values())
    assert task.config.artifacts == ["/logs/world/calendar.json"]

    files = {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    }
    assert files.keys() == {
        "task.toml",
        "environment/Dockerfile",
        "environment/docker-compose.yaml",
        "environment/calendar_world.py",
        "environment/case.json",
        "environment/player/Dockerfile",
        "solution/solve.sh",
        "solution/client.py",
        "tests/test.sh",
        "tests/verify.py",
        *(f"instructions/{role}.md" for role in ROLES),
    }
    encoded = files["environment/case.json"]
    payload = json.loads(encoded)
    assert set(payload) == {"case", "mode"}
    assert payload["mode"] == mode
    assert payload["case"]["case_id"] == "4/1"
    assert payload["case"]["gold"] == [36]
    assert payload["case"]["members"] == {
        role: [member * 6] for member, role in enumerate(ROLES, start=1)
    }
    assert encoded == json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    assert all(b"PRIVATE_PROSE" not in data for data in files.values())
    for role in ROLES:
        instruction = task.seat_instruction(role)
        assert f"You are `{role}`" in instruction
        assert f"`{mode}` policy" in instruction
        assert "/exchange/input.json" in instruction
        assert "/exchange/out/report.json" in instruction
        assert "/exchange/done.json" in instruction
        assert ("/exchange/proposals.json" in instruction) == (role == "m1")
        assert ("/exchange/out/choice.json" in instruction) == (role == "m1")
        assert "case.json" not in instruction

    assert stat.S_IMODE(output.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(output.stat().st_mode) == 0o755
    for path in output.rglob("*"):
        expected = 0o755 if path.is_dir() or path.suffix == ".sh" else 0o644
        assert stat.S_IMODE(path.stat().st_mode) == expected

    repeat = adapter.materialize(dataset, "4/1", mode, output.with_name("repeat"))
    assert all((repeat / name).read_bytes() == data for name, data in files.items())


def test_compose_limits_role_inputs(
    adapter: ModuleType, dataset: Path, tmp_path: Path
) -> None:
    output = adapter.materialize(dataset, "4/1", "full", tmp_path / "private" / "task")
    environment = output / "environment"
    compose = yaml.safe_load((environment / "docker-compose.yaml").read_text())
    assert set(compose["services"]) == {"main", *ROLES}
    assert compose["volumes"] == {f"exchange-{role}": {} for role in ROLES}
    assert "secrets" not in compose

    services = compose["services"]
    main = services["main"]
    assert main["user"] == "root"
    assert main["build"] == "."
    assert main["command"] == [
        "python3",
        "-B",
        "-u",
        "/opt/calendar/calendar_world.py",
        "--keepalive",
    ]
    assert main["volumes"] == [f"exchange-{role}:/ports/{role}" for role in ROLES]
    assert "environment" not in main
    for name, service in services.items():
        assert service["network_mode"] == "none"
        assert not {
            "configs",
            "env_file",
            "networks",
            "ports",
            "privileged",
            "secrets",
            "volumes_from",
        }.intersection(service)
        if name != "main":
            assert service["user"] == "player"
            assert service["build"] == "./player"
            assert service["environment"] == {"CALENDAR_ROLE": name}
            assert service["volumes"] == [f"exchange-{name}:/exchange"]
            assert service["depends_on"] == {"main": {"condition": "service_healthy"}}

    world_image = (environment / "Dockerfile").read_text()
    seat_image = (environment / "player/Dockerfile").read_text()
    assert world_image.splitlines()[0] == f"FROM {PYTHON_IMAGE}"
    assert seat_image.splitlines()[0] == f"FROM {PYTHON_IMAGE}"
    assert "COPY calendar_world.py case.json ./" in world_image
    assert all(
        not line.startswith(("COPY ", "ADD ")) for line in seat_image.splitlines()
    )
    assert "--uid 10001 --gid 10001" in seat_image
    assert "--gid 10001 player" in seat_image


@pytest.mark.parametrize("existing", ["directory", "file", "broken_symlink"])
def test_never_replaces_existing_path(
    adapter: ModuleType, dataset: Path, tmp_path: Path, existing: str
) -> None:
    output = tmp_path / "task"
    if existing == "directory":
        output.mkdir()
        sentinel = output / "sentinel"
        sentinel.write_text("keep")
    elif existing == "file":
        sentinel = output
        sentinel.write_text("keep")
    else:
        sentinel = tmp_path / "missing"
        output.symlink_to(sentinel)

    with pytest.raises(FileExistsError, match="output already exists"):
        adapter.materialize(dataset, "4/1", "full", output)
    if existing == "broken_symlink":
        assert output.is_symlink()
        assert output.readlink() == sentinel
        assert not sentinel.exists()
    else:
        assert sentinel.read_text() == "keep"


def test_rejects_invalid_inputs_before_creating_task(
    adapter: ModuleType, dataset: Path, tmp_path: Path
) -> None:
    output = tmp_path / "private" / "task"
    with pytest.raises(ValueError, match="unsupported mode"):
        adapter.materialize(dataset, "4/1", "unchecked", output)
    with pytest.raises(ValueError):
        adapter.materialize(dataset, "../1", "full", output)
    assert not output.parent.exists()


def test_rejects_public_output_parent(
    adapter: ModuleType, dataset: Path, tmp_path: Path
) -> None:
    parent = tmp_path / "public"
    parent.mkdir()
    parent.chmod(0o755)
    output = parent / "task"
    with pytest.raises(PermissionError, match="output parent must be private"):
        adapter.materialize(dataset, "4/1", "full", output)
    assert not output.exists()
    assert stat.S_IMODE(parent.stat().st_mode) == 0o755
