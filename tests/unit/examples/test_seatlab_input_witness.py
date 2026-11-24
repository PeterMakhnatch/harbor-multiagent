import hashlib
import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from examples.seatlab.workbench import input_witness
from examples.seatlab.workbench.input_witness import (
    Witness,
    binding,
    encode,
    read_starts,
)
from harbor.models.task.id import LocalTaskId
from harbor.models.trial.config import (
    EnvironmentConfig,
    Participant,
    TaskConfig,
    TrialConfig,
)
from harbor.models.trial.result import AgentInfo, ExceptionInfo, TrialResult
from harbor.models.verifier.result import VerifierResult

SECRET = "sk-live-7f3a9c2e5b1d4068"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
TASK = "/left/root/task"
SLOT_NAME = "task__abc1234"
JOB = str(uuid4())
OTHER_JOB = str(uuid4())
Json = dict[str, Any]
Mutate = Callable[[Json], Json]


def native(**changes: Any) -> TrialResult:
    return TrialResult(
        task_name="task",
        trial_name=SLOT_NAME,
        trial_uri=f"file:///trials/{SLOT_NAME}",
        task_id=LocalTaskId(path=Path(TASK)),
        task_checksum="c" * 64,
        config=TrialConfig(
            task=TaskConfig(path=Path(TASK)),
            trial_name=SLOT_NAME,
            participants=[
                Participant(seat="p1", name="terminus-2", model_name="openai/gpt-5"),
                Participant(seat="p2", name="nop"),
            ],
            environment=EnvironmentConfig(env={"OPENAI_API_KEY": SECRET}),
        ),
        agent_info=AgentInfo(name="team", version="1"),
        **changes,
    )


def result(**changes: Any) -> Json:
    return json.loads(native(**changes).model_dump_json(indent=4))


def put(*keys: str, value: Any) -> Mutate:
    def mutate(document: Json) -> Json:
        target: Any = document
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
        return document

    return mutate


def swap_seats(document: Json) -> Json:
    document["config"]["participants"].reverse()
    return document


def reorder(document: Json) -> Json:
    config = document["config"]
    return {**document, "config": dict(reversed(list(config.items())))}


def test_binding_ignores_missing_grade_and_outcome() -> None:
    start = native()
    started = binding(start.model_dump(mode="json"))
    graded = result(
        verifier_result=VerifierResult(rewards={"reward": 1}),
        exception_info=ExceptionInfo(
            exception_type="E",
            exception_message=SECRET,
            exception_traceback="",
            occurred_at=datetime(2026, 11, 24, tzinfo=UTC),
        ),
    )
    assert started is not None and DIGEST.fullmatch(started)
    assert started == binding(json.loads(start.model_dump_json())) == binding(graded)
    assert binding(reorder(result())) == started


@pytest.mark.parametrize(
    "mutate",
    [
        put("task_checksum", value="d" * 64),
        put("task_id", "path", value="/right/root/task"),
        put("config", "environment", "env", "OPENAI_API_KEY", value="other"),
        put("config", "future_field", value={"nested": [None, 1]}),
        swap_seats,
    ],
)
def test_binding_separates_every_bound_dimension(mutate: Mutate) -> None:
    moved = binding(mutate(result()))
    assert moved is not None and moved != binding(result())


def test_binding_is_domain_separated() -> None:
    document = result()
    payload = {key: document[key] for key in ("config", "task_checksum", "task_id")}
    plain = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    assert binding(document) != f"sha256:{hashlib.sha256(plain).hexdigest()}"


@pytest.mark.parametrize(
    "mutate",
    [
        put("task_id", value={"git_url": "https://example.invalid/x", "path": "t"}),
        put("task_id", value={"org": "o", "name": "n", "ref": None}),
        put("task_checksum", value="abc"),
        put("task_checksum", value="C" * 64),
        put("config", value=[]),
        put("config", "future_field", value=float("nan")),
        put("config", "future_field", value={1: "x"}),
        put("config", "future_field", value=(1, 2)),
        put("config", "future_field", value="x" * (4 * 1024 * 1024)),
    ],
)
def test_binding_refuses_unsupported_results(mutate: Mutate) -> None:
    assert binding(mutate(result())) is None


def slot(name: str = SLOT_NAME) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


def witness(job: str = JOB, slot_name: str = SLOT_NAME, trial: str = "") -> Witness:
    found = binding(result())
    assert found is not None
    return Witness(
        job, slot(slot_name), trial or str(uuid4()), f"sha256:{'1' * 64}", found
    )


def store(directory: Path, found: Witness, *, body: bytes | None = None) -> Path:
    directory.mkdir(mode=0o700, exist_ok=True)
    name, data = encode(found)
    path = directory / name
    path.write_bytes(data if body is None else body)
    path.chmod(0o600)
    return path


def test_receipts_round_trip_and_skip_other_jobs(tmp_path: Path) -> None:
    starts = tmp_path / "starts"
    assert read_starts(starts, JOB) == {}
    starts.mkdir(mode=0o700)
    assert read_starts(starts, JOB) == {}
    first, second = witness(), witness(slot_name="task__def5678")
    store(starts, first)
    store(starts, second)
    store(starts, witness(job=OTHER_JOB), body=b"not json")
    assert read_starts(starts, JOB) == {first.trial: first, second.trial: second}
    name, body = encode(first)
    assert name == f"{JOB}.{first.slot}.{first.trial}.start.json"
    assert body.endswith(b"}\n") and json.loads(body)["schema"] == input_witness.SCHEMA
    assert SECRET.encode() not in body and TASK.encode() not in body


def test_encode_refuses_non_canonical_keys() -> None:
    found = witness()
    for broken in (
        found._replace(job=found.job.upper()),
        found._replace(slot=SLOT_NAME),
        found._replace(binding=found.binding.removeprefix("sha256:")),
    ):
        with pytest.raises(ValueError, match=input_witness.NOT_WITNESS):
            encode(broken)


def different_slot(starts: Path) -> None:
    found = witness()
    store(starts, found, body=encode(found._replace(slot=slot("other")))[1])


def foreign_body(starts: Path) -> None:
    found = witness()
    store(starts, found, body=encode(found._replace(job=OTHER_JOB))[1])


def indented(starts: Path) -> None:
    found = witness()
    document = json.loads(encode(found)[1])
    store(starts, found, body=json.dumps(document, indent=1).encode() + b"\n")


def no_newline(starts: Path) -> None:
    found = witness()
    store(starts, found, body=encode(found)[1].rstrip(b"\n"))


def symlink(starts: Path) -> None:
    real = store(starts.parent / "elsewhere", witness())
    (starts / real.name).symlink_to(real)


def partial(starts: Path) -> None:
    name = encode(witness())[0]
    (starts / f".{name}.{'0' * 32}.tmp").write_bytes(b"{")


def unrelated(starts: Path) -> None:
    (starts / "notes.txt").write_text(SECRET)


def shared(starts: Path) -> None:
    store(starts, witness()).chmod(0o644)


def duplicate(starts: Path) -> None:
    found = witness()
    store(starts, found)
    store(starts, found._replace(slot=slot("task__def5678")))


@pytest.mark.parametrize(
    ("arrange", "reason"),
    [
        (different_slot, input_witness.NOT_RECEIPT),
        (foreign_body, input_witness.NOT_RECEIPT),
        (indented, input_witness.NOT_RECEIPT),
        (no_newline, input_witness.NOT_RECEIPT),
        (symlink, input_witness.NOT_PRIVATE),
        (partial, input_witness.UNRELATED),
        (unrelated, input_witness.UNRELATED),
        (shared, input_witness.NOT_PRIVATE),
        (duplicate, input_witness.DUPLICATE),
    ],
)
def test_read_refuses_unsafe_archives(
    tmp_path: Path, arrange: Callable[[Path], None], reason: str
) -> None:
    starts = tmp_path / "starts"
    starts.mkdir(mode=0o700)
    store(starts, witness())
    arrange(starts)
    with pytest.raises(ValueError) as caught:
        read_starts(starts, JOB)
    error = caught.value
    assert str(error) == reason
    assert str(tmp_path) not in str(error) and SECRET not in str(error)
    assert error.__cause__ is None and error.__context__ is None


def test_read_refuses_a_shared_directory_even_when_the_receipts_are_private(
    tmp_path: Path,
) -> None:
    starts = tmp_path / "starts"
    store(starts, witness())
    starts.chmod(0o755)
    with pytest.raises(ValueError, match=input_witness.NOT_PRIVATE_DIR):
        read_starts(starts, JOB)


def test_read_refuses_bad_arguments(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "linked").symlink_to(real)
    cases: list[tuple[Any, Any, str]] = [
        (tmp_path / "linked", JOB, input_witness.NOT_DIRECTORY),
        (real, JOB.upper(), input_witness.NOT_JOB),
    ]
    for directory, job, reason in cases:
        with pytest.raises(ValueError, match=reason):
            read_starts(directory, job)
    with pytest.raises(TypeError, match=input_witness.NOT_PATH):
        read_starts(str(real), JOB)
    assert os.listdir(real) == []
