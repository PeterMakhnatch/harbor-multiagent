import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.workbench import arm_runtime
from examples.seatlab.workbench.arm_runtime import GROUPS, Runtime, inspect_lock
from harbor.models.job.lock import (
    ExtraInstructionLock,
    TaskLock,
    TrialLock,
    VerifierLock,
)
from harbor.models.trial.config import AgentConfig, EnvironmentConfig, Participant

SECRET = "sk-live-7f3a9c2e5b1d4068"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
TRIAL = "0f1b62bf-5c80-481a-98ad-51cc31992a67"
Json = dict[str, Any]
Mutate = Callable[[Json], Json]


def native() -> Json:
    lock = TrialLock(
        task=TaskLock(
            name="org/task",
            type="local",
            digest=f"sha256:{'a' * 64}",
            path=Path("/left/root/task"),
        ),
        extra_instructions=[
            ExtraInstructionLock(
                path=Path("/left/root/brief.md"), digest=f"sha256:{'b' * 64}"
            )
        ],
        agent=AgentConfig(name="oracle"),
        participants=[
            Participant(seat="p1", name="terminus-2", model_name="openai/gpt-5"),
            Participant(seat="p2", name="nop"),
        ],
        environment=EnvironmentConfig(env={"WORLD": "calm", "NOISE": "x"}),
        verifier=VerifierLock(env={"GRADER": "strict"}),
    )
    return json.loads(lock.model_dump_json(indent=4, exclude_none=True))


def put(*keys: str | int, value: Any) -> Mutate:
    def mutate(document: Json) -> Json:
        target: Any = document
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
        return document

    return mutate


def drop(*keys: str | int) -> Mutate:
    def mutate(document: Json) -> Json:
        target: Any = document
        for key in keys[:-1]:
            target = target[key]
        del target[keys[-1]]
        return document

    return mutate


def write(directory: Path, document: Any, name: str = "lock.json") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(document, indent=4))
    return path


def inspect(directory: Path, document: Any) -> Runtime:
    return inspect_lock(write(directory, document))


def diff(left: Runtime, right: Runtime) -> set[str]:
    found = {
        field
        for field in ("task", "seats", "order")
        if getattr(left, field) != getattr(right, field)
    }
    for label, one, two in (
        ("participant", left.participants, right.participants),
        ("env", left.env, right.env),
        ("group", left.groups, right.groups),
    ):
        found |= {
            f"{label}:{key}"
            for key in one.keys() | two.keys()
            if one.get(key) != two.get(key)
        }
    return found


def changed(tmp_path: Path, mutate: Mutate) -> set[str]:
    left = inspect(tmp_path / "left", native())
    right = inspect(tmp_path / "right", mutate(native()))
    return diff(left, right)


def test_inspects_native_trial_lock(tmp_path: Path) -> None:
    runtime = inspect(tmp_path, native())

    assert runtime.seats == ("p1", "p2")
    assert runtime.order == ("p1", "p2")
    assert tuple(runtime.groups) == GROUPS
    assert set(runtime.env) == {"WORLD", "NOISE"}
    assert set(runtime.participants) == {"p1", "p2"}
    assert runtime.task == {
        "name": "org/task",
        "type": "local",
        "digest": f"sha256:{'a' * 64}",
    }
    digests = [
        *runtime.env.values(),
        *runtime.participants.values(),
        *runtime.groups.values(),
    ]
    assert all(DIGEST.fullmatch(digest) for digest in digests)
    assert len(set(runtime.groups.values())) == len(GROUPS)
    assert inspect(tmp_path / "again", native()) == runtime


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (put("environment", "env", "WORLD", value="storm"), {"env:WORLD"}),
        (put("environment", "env", "NOISE", value="y"), {"env:NOISE"}),
        (put("environment", "env", "EXTRA", value="calm"), {"env:EXTRA"}),
        (put("environment", "env", "WORLD", value=""), {"env:WORLD"}),
        (
            lambda doc: {**doc, "participants": doc["participants"][::-1]},
            {"order", "group:seat_order"},
        ),
        (
            put("participants", 0, "model_name", value="openai/gpt-6"),
            {"participant:p1"},
        ),
        (put("participants", 0, "future_knob", value=1), {"participant:p1"}),
        (put("participants", 1, "env", value={"WORLD": "storm"}), {"participant:p2"}),
        (put("verifier", "env", "GRADER", value="lenient"), {"group:verifier"}),
        (put("verifier", "override_timeout_sec", value=5.0), {"group:verifier"}),
        (put("agent", "model_name", value="openai/gpt-5"), {"group:agent"}),
        (put("future_field", value={"x": 1}), {"group:other"}),
        (put("environment", "future_knob", value=True), {"group:environment"}),
        (put("environment", "override_cpus", value=2), {"group:environment"}),
        (put("task", "future_field", value="x"), {"task", "group:task"}),
        (put("task", "source", value="registry"), {"task", "group:task"}),
        (put("task", "version", value="2"), {"task", "group:task"}),
        (put("task", "type", value="package"), {"task", "group:task"}),
        (put("task", "digest", value=f"sha256:{'c' * 64}"), {"task"}),
        (put("task", "name", value="org/other"), {"task"}),
        (put("task", "path", value="/right/elsewhere/task"), set()),
        (
            put("extra_instructions", 0, "path", value="/right/brief.md"),
            {"group:instructions"},
        ),
        (
            put("environment", "extra_docker_compose", value=["/right/extra.yaml"]),
            {"group:environment"},
        ),
        (lambda doc: {"participants": doc.pop("participants"), **doc}, set()),
        (
            put("extra_instructions", 0, "digest", value=f"sha256:{'d' * 64}"),
            {"group:instructions"},
        ),
        (put("timeout_multiplier", value=1), {"group:timeouts"}),
        (put("agent_timeout_multiplier", value=None), {"group:timeouts"}),
        (put("agent_timeout_multiplier", value=2.0), {"group:timeouts"}),
        (put("artifacts", value=["/logs/extra"]), {"group:harness"}),
    ],
)
def test_reports_only_the_changed_input(
    tmp_path: Path, mutate: Mutate, expected: set[str]
) -> None:
    assert changed(tmp_path, mutate) == expected


def test_participant_hash_excludes_its_seat(tmp_path: Path) -> None:
    left = inspect(tmp_path / "left", native())
    right = inspect(
        tmp_path / "right", put("participants", 1, "seat", value="p3")(native())
    )

    assert right.seats == ("p1", "p3")
    assert right.participants["p3"] == left.participants["p2"]
    assert right.participants["p1"] == left.participants["p1"]


def test_world_digest_is_typed_per_value(tmp_path: Path) -> None:
    world = put("environment", "env", "WORLD", value="x")
    runtime = inspect(tmp_path, world(native()))

    assert runtime.env["WORLD"] == runtime.env["NOISE"]
    assert runtime.env["WORLD"] != inspect(tmp_path / "base", native()).env["WORLD"]


def job_lock(document: Json) -> Json:
    return {
        "schema_version": 3,
        "n_concurrent_trials": 1,
        "retry": {},
        "trials": [document],
    }


def duplicate_seat(document: Json) -> Json:
    document["participants"][1]["seat"] = "p1"
    return document


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (job_lock, arm_runtime.JOB_LOCK),
        (put("trials", value=[]), arm_runtime.JOB_LOCK),
        (put("schema_version", value=1), arm_runtime.VERSION),
        (put("schema_version", value=2.0), arm_runtime.VERSION),
        (drop("schema_version"), arm_runtime.VERSION),
        (drop("participants"), arm_runtime.NO_TEAM),
        (put("participants", value=[]), arm_runtime.NO_TEAM),
        (duplicate_seat, arm_runtime.DUPLICATE_SEAT),
        (put("participants", 0, "seat", value="../p1"), arm_runtime.SEAT_NAME),
        (put("participants", 0, "seat", value="p 1"), arm_runtime.SEAT_NAME),
        (drop("participants", 1, "name"), arm_runtime.UNRESOLVED),
        (put("participants", 1, "name", value=None), arm_runtime.UNRESOLVED),
        (put("participants", 1, "name", value=""), arm_runtime.UNRESOLVED),
        (
            lambda doc: put("participants", 1, "import_path", value="")(
                drop("participants", 1, "name")(doc)
            ),
            arm_runtime.UNRESOLVED,
        ),
        (
            put(
                "source_trial",
                value={"action": "regrade", "type": "local", "trial_id": TRIAL},
            ),
            arm_runtime.REGRADE,
        ),
        (put("install_only", value=True), arm_runtime.INSTALL_ONLY),
        (put("verifier", "disable", value=True), arm_runtime.NO_VERIFIER),
        (put("environment", "env", value=["WORLD=calm"]), arm_runtime.NATIVE),
        (put("environment", "env", "bad-name", value="x"), arm_runtime.ENV_SHAPE),
        (put("participants", 0, "skills", value=["x"]), arm_runtime.NATIVE),
        (put("timeout_multiplier", value=None), arm_runtime.NATIVE),
        (put("install_only", value=None), arm_runtime.NATIVE),
        (drop("task"), arm_runtime.NATIVE),
        (put("task", "digest", value="sha256:short"), arm_runtime.NATIVE),
    ],
)
def test_refuses_unsupported_locks(tmp_path: Path, mutate: Mutate, reason: str) -> None:
    with pytest.raises(ValueError) as caught:
        inspect(tmp_path, mutate(native()))
    assert str(caught.value) == reason


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b'{"schema_version": 2, "schema_version": 2}', arm_runtime.NOT_JSON),
        (b'{"schema_version": NaN}', arm_runtime.NOT_JSON),
        (b'{"schema_version": Infinity}', arm_runtime.NOT_JSON),
        (b'{"name": "\xff"}', arm_runtime.NOT_JSON),
        (b'\xef\xbb\xbf{"schema_version": 2}', arm_runtime.NOT_JSON),
        (b"[]", arm_runtime.NOT_OBJECT),
        (b"null", arm_runtime.NOT_OBJECT),
        (b"[" * 100_000, arm_runtime.NOT_JSON),
        (b" " * (arm_runtime.MAX_BYTES + 1), arm_runtime.TOO_LARGE),
    ],
)
def test_refuses_loose_json(tmp_path: Path, data: bytes, reason: str) -> None:
    path = tmp_path / "lock.json"
    path.write_bytes(data)
    with pytest.raises(ValueError) as caught:
        inspect_lock(path)
    assert str(caught.value) == reason


def test_refuses_symlink_directory_and_missing(tmp_path: Path) -> None:
    real = write(tmp_path / "real", native())
    link = tmp_path / "link.json"
    link.symlink_to(real)

    for path, reason in (
        (link, arm_runtime.NOT_REGULAR),
        (tmp_path, arm_runtime.NOT_REGULAR),
        (tmp_path / "absent.json", arm_runtime.UNREADABLE),
    ):
        with pytest.raises(ValueError) as caught:
            inspect_lock(path)
        assert str(caught.value) == reason


def test_refuses_a_lock_that_moves_while_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write(tmp_path, native())
    real = arm_runtime._stamp
    calls: list[Path] = []

    def moving(found: Path) -> tuple[int, ...]:
        if calls:
            os.utime(found, ns=(1, 1))
        calls.append(found)
        return real(found)

    monkeypatch.setattr(arm_runtime, "_stamp", moving)
    with pytest.raises(ValueError) as caught:
        inspect_lock(path)
    assert str(caught.value) == arm_runtime.MOVED
    assert len(calls) == 2


def secret_lock(document: Json) -> Json:
    document["environment"]["env"]["OPENAI_API_KEY"] = SECRET
    document["participants"][0]["env"] = {"OPENAI_API_KEY": SECRET}
    return document


@pytest.mark.parametrize(
    "mutate",
    [
        put("timeout_multiplier", value=SECRET),
        put("participants", 0, "seat", value=f"{SECRET}/../x"),
        put("environment", "env", f"{SECRET}-name", value=SECRET),
        put("task", "digest", value=SECRET),
        put(
            "source_trial", value={"action": "regrade", "type": "local", "path": SECRET}
        ),
    ],
)
def test_validation_errors_leak_nothing_raw(tmp_path: Path, mutate: Mutate) -> None:
    directory = tmp_path / f"root-{SECRET}"
    with pytest.raises(ValueError) as caught:
        inspect(directory, mutate(secret_lock(native())))
    error = caught.value
    assert SECRET not in f"{error!s}{error!r}"
    assert str(tmp_path) not in str(error)
    assert error.__cause__ is None and error.__context__ is None


def test_duplicate_secret_name_leaks_nothing(tmp_path: Path) -> None:
    path = tmp_path / "lock.json"
    path.write_text(f'{{"{SECRET}": 1, "{SECRET}": 2}}')
    with pytest.raises(ValueError) as caught:
        inspect_lock(path)
    assert SECRET not in repr(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_runtime_keeps_no_raw_env_or_participant_values(tmp_path: Path) -> None:
    runtime = inspect(tmp_path, secret_lock(native()))

    assert SECRET not in repr(runtime)
    assert "OPENAI_API_KEY" in runtime.env
    assert "calm" not in repr(runtime)
