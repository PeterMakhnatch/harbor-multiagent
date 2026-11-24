import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.workbench import arm_runtime, input_keys
from examples.seatlab.workbench.arm_runtime import inspect_lock
from examples.seatlab.workbench.input_keys import Cell, cell, record_key, result_view
from harbor.models.job.lock import TaskLock, TrialLock, VerifierLock
from harbor.models.task.id import LocalTaskId
from harbor.models.trial.config import (
    AgentConfig,
    EnvironmentConfig,
    Participant,
    TaskConfig,
    TrialConfig,
)
from harbor.models.trial.result import AgentInfo, TrialResult

SECRET = "sk-live-7f3a9c2e5b1d4068"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
TASK = "/left/root/task"
Json = dict[str, Any]
Mutate = Callable[[Json], Json]


def team(relief: str = "nop") -> list[Participant]:
    return [
        Participant(seat="p1", name="terminus-2", model_name="openai/gpt-5"),
        Participant(seat="p2", name=relief),
    ]


def lock(relief: str = "nop", world: str = "calm") -> Json:
    native = TrialLock(
        task=TaskLock(
            name="org/task",
            type="local",
            digest=f"sha256:{'a' * 64}",
            path=Path(TASK),
        ),
        agent=AgentConfig(name="oracle"),
        participants=team(relief),
        environment=EnvironmentConfig(env={"WORLD": world}),
        verifier=VerifierLock(env={"GRADER": "strict"}),
    )
    return json.loads(native.model_dump_json(indent=4, exclude_none=True))


def result(relief: str = "nop", world: str = "calm") -> Json:
    native = TrialResult(
        task_name="task",
        trial_name="task__abc1234",
        trial_uri="file:///trials/task__abc1234",
        task_id=LocalTaskId(path=Path(TASK)),
        task_checksum="abc",
        config=TrialConfig(
            task=TaskConfig(path=Path(TASK)),
            trial_name="task__abc1234",
            participants=team(relief),
            environment=EnvironmentConfig(env={"WORLD": world}),
        ),
        agent_info=AgentInfo(name="team", version="1"),
    )
    return json.loads(native.model_dump_json(indent=4))


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


def test_lock_and_result_share_one_view(tmp_path: Path) -> None:
    found = cell(lock())

    assert isinstance(found, Cell)
    assert DIGEST.fullmatch(found.key) and DIGEST.fullmatch(found.view)
    assert found.key != found.view
    assert found.key == record_key(lock())
    assert found.view == result_view(result())
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(lock(), indent=4))
    assert inspect_lock(path).record_key == found.key
    path.write_text(json.dumps(dict(reversed(lock().items())), separators=(",", ":")))
    assert inspect_lock(path).record_key == found.key


def test_two_recorded_inputs_have_two_views() -> None:
    base = cell(lock())
    relief = cell(lock(relief="oracle"))
    world = cell(lock(world="stormy"))

    assert len({base.view, relief.view, world.view}) == 3
    assert len({base.key, relief.key, world.key}) == 3
    assert relief.view == result_view(result(relief="oracle"))
    assert world.view == result_view(result(world="stormy"))
    assert result_view(result(world="stormy")) != result_view(result())


@pytest.mark.parametrize(
    "mutate",
    [
        put("timeout_multiplier", value=2.0),
        put("timeout_multiplier", value=1),
        put("agent_timeout_multiplier", value=3.0),
        put("verifier", "env", "GRADER", value="loose"),
        put("future_field", value={"nested": None}),
        put("task", "digest", value=f"sha256:{'c' * 64}"),
    ],
)
def test_inputs_outside_the_view_change_only_the_key(mutate: Mutate) -> None:
    base = cell(lock())
    other = cell(mutate(lock()))

    assert other.key != base.key
    assert other.view == base.view == result_view(result())


def test_task_path_changes_key_and_view() -> None:
    moved = cell(put("task", "path", value="/right/root/task")(lock()))

    assert moved.key != cell(lock()).key
    assert moved.view != cell(lock()).view


@pytest.mark.parametrize(
    ("left", "right", "same"),
    [
        (put("participants", 1, "model_name", value=None), None, True),
        (put("participants", 1, "base_url", value="http://relay"), None, False),
        (drop("participants", 1, "skip_model_parsing"), None, False),
        (put("participants", 1, "future", value=None), None, False),
        (put("participants", 1, "kwargs", value={"a": {"b": None}}), None, False),
        (
            put("participants", 1, "kwargs", value={"a": {"b": None}}),
            put("config", "participants", 1, "kwargs", value={"a": {"b": None}}),
            True,
        ),
        (
            put("participants", 1, "future", value={"b": None}),
            put("config", "participants", 1, "future", value={"b": None}),
            True,
        ),
        (
            None,
            put("config", "participants", 1, "model_name", value="openai/gpt-5"),
            False,
        ),
        (None, put("config", "participants", 1, "kwargs", value={"a": None}), False),
        (None, drop("config", "participants", 1, "kwargs"), False),
    ],
)
def test_view_omits_only_known_top_level_nulls(
    left: Mutate | None, right: Mutate | None, same: bool
) -> None:
    locked = left(lock()) if left else lock()
    recorded = right(result()) if right else result()
    view = result_view(recorded)

    assert view is not None
    assert (cell(locked).view == view) is same


def swap_seats(document: Json) -> Json:
    config = document["config"]
    config["participants"] = list(reversed(config["participants"]))
    return document


def solo(document: Json) -> Json:
    document["participants"] = document["participants"][:1]
    return document


def solo_result(document: Json) -> Json:
    solo(document["config"])
    return document


@pytest.mark.parametrize(
    "mutate",
    [
        put("task_id", "path", value="/right/root/task"),
        put("task_id", "path", value="/left/root/task/"),
        put("config", "task", "path", value="/right/root/task"),
        put("task_id", "git_url", value="https://example.invalid/repo.git"),
        put("config", "task", "git_url", value="https://example.invalid/repo.git"),
        put("config", "task", "ref", value="latest"),
        drop("config", "task", "path"),
        put("config", "trial_name", value=""),
        put("config", "install_only", value=True),
        put("config", "environment", "env", "WORLD", value=1),
        put("config", "environment", "env", "bad-name", value="x"),
        put("config", "participants", 1, "seat", value="p1"),
        put("config", "participants", 1, "seat", value="../p2"),
        drop("config", "participants", 1, "name"),
        put("config", "participants", 1, "name", value=None),
        solo_result,
        drop("config", "participants"),
        put("config", "timeout_multiplier", value="fast"),
        put("config", "environment", "kwargs", value={"x": float("nan")}),
        put("config", "environment", "kwargs", value={"x": (1, 2)}),
        put("config", "environment", "kwargs", value={1: "x"}),
        drop("task_id"),
        drop("config"),
    ],
)
def test_result_view_refuses_unsupported_results(mutate: Mutate) -> None:
    assert result_view(mutate(result())) is None


def test_result_view_keeps_seat_order() -> None:
    swapped = result_view(swap_seats(result()))

    assert swapped is not None
    assert swapped != result_view(result())


def test_old_records_agree_on_omission_not_on_an_empty_or_nonempty_override() -> None:
    archived = drop("environment", "env")(lock())
    old_result = drop("config", "environment", "env")(result())
    assert cell(archived).view == result_view(old_result)
    assert cell(archived).view != result_view(
        put("config", "environment", "env", value={})(result())
    )
    assert cell(archived).view != result_view(result())


def test_result_view_refuses_non_objects() -> None:
    for document in ([], None, "result"):
        assert result_view(document) is None


def job_lock(document: Json) -> Json:
    return {
        "schema_version": 3,
        "n_concurrent_trials": 1,
        "retry": {},
        "trials": [document],
    }


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (job_lock, arm_runtime.JOB_LOCK),
        (put("schema_version", value=1), arm_runtime.VERSION),
        (solo, input_keys.SOLO),
        (drop("participants"), arm_runtime.NO_TEAM),
        (put("participants", 1, "seat", value="p1"), arm_runtime.DUPLICATE_SEAT),
        (drop("task", "path"), input_keys.NOT_LOCAL),
        (put("task", "type", value="package"), input_keys.NOT_LOCAL),
        (
            put("task", "git_url", value="https://example.invalid/r.git"),
            input_keys.NOT_LOCAL,
        ),
        (put("environment", "env", "WORLD", value=1), arm_runtime.NATIVE),
        (put("future_field", value=float("nan")), arm_runtime.NOT_JSON),
        (put("future_field", value=(1, 2)), arm_runtime.NOT_JSON),
        (put("future_field", value={1: "x"}), arm_runtime.NOT_JSON),
        (put("future_field", value=object()), arm_runtime.NOT_JSON),
        (put("future_field", value="x" * arm_runtime.MAX_BYTES), arm_runtime.TOO_LARGE),
        (put("install_only", value=True), arm_runtime.INSTALL_ONLY),
    ],
)
def test_cell_refuses_unsupported_locks(mutate: Mutate, reason: str) -> None:
    with pytest.raises(ValueError) as caught:
        cell(mutate(lock()))
    assert str(caught.value) == reason


def test_record_key_refuses_what_inspection_refuses() -> None:
    with pytest.raises(ValueError) as caught:
        record_key(job_lock(lock()))
    assert str(caught.value) == arm_runtime.JOB_LOCK
    assert DIGEST.fullmatch(record_key(solo(lock())))


@pytest.mark.parametrize(
    "mutate",
    [
        put("task", "type", value="git"),
        put("participants", 1, "seat", value=f"{SECRET}/../x"),
        put("future_field", value=float("nan")),
        put("timeout_multiplier", value=SECRET),
    ],
)
def test_cell_errors_leak_nothing_raw(mutate: Mutate) -> None:
    document = put("task", "git_url", value=SECRET)(mutate(lock()))
    document["environment"].setdefault("env", {})["OPENAI_API_KEY"] = SECRET
    with pytest.raises(ValueError) as caught:
        cell(document)
    error = caught.value
    assert SECRET not in f"{error!s}{error!r}"
    assert error.__cause__ is None and error.__context__ is None


def test_outputs_keep_no_raw_values() -> None:
    document = put("environment", "env", "OPENAI_API_KEY", value=SECRET)(lock())
    found = cell(document)

    assert SECRET not in repr(found) and TASK not in repr(found)
    assert "calm" not in repr(found)
