import asyncio
import json
from contextvars import Context, copy_context
from hashlib import sha256
from pathlib import Path
from typing import Literal, Unpack

import pytest

pytest.importorskip("inspect_ai")

from inspect_ai import eval as inspect_eval
from inspect_ai.dataset import Sample
from inspect_ai.model import (
    ChatMessage,
    ChatMessageSystem,
    ChatMessageUser,
    GenerateConfig,
    GenerateConfigArgs,
    ModelName,
    ModelOutput,
)
from inspect_ai.solver import TaskState
from inspect_ai.tool import ToolChoice, ToolInfo

from examples.seatlab import inspect_replay
from examples.seatlab.committee import ballot_packet, packing

_SCORER = "seatlab_facts_score"
_SYSTEM = "Answer only with JSON."


@pytest.fixture(scope="module")
def source_packet() -> dict[str, object]:
    return ballot_packet.read_packet(packing.PACKET, packing.PACKET_SHA256)


def _alternate_packet(tmp_path: Path) -> tuple[Path, str]:
    # A distinct byte commitment with exactly the same prompt facts and sample IDs.
    raw = packing.PACKET.read_bytes() + b"\n"
    path = tmp_path / "reformatted-packet.json"
    path.write_bytes(raw)
    return path, sha256(raw).hexdigest()


def test_dataset_is_the_sealed_two_message_grid(
    source_packet: dict[str, object],
) -> None:
    task = inspect_replay.seatlab_facts()
    assert task.name == "seatlab_facts"
    assert task.model is None
    assert task.sandbox is None
    assert task.metrics == []
    assert len(task.dataset) == 96
    expected_ids = []
    for block in range(2):
        for map_index in range(6):
            for seat_index in range(4):
                arms = ("separate", "indexed")
                if (block + map_index + seat_index) % 2:
                    arms = arms[::-1]
                for arm in arms:
                    expected_ids.append(
                        f"ix-b{block}-m{map_index}-s{seat_index + 1}-{arm}"
                    )
    assert [sample.id for sample in task.dataset] == expected_ids

    teams = source_packet["teams"]
    assert isinstance(teams, list)
    source_ids: set[str] = set()
    for team in teams:
        match team:
            case {"cell_id": str(source_id)}:
                source_ids.add(source_id)
            case _:
                pytest.fail("source team has no string cell ID")
    for sample, cell in zip(task.dataset, packing.grid(), strict=True):
        assert sample.id == cell.cell_id
        assert sample.target == ""
        assert sample.choices is None
        assert sample.metadata == {
            "block": cell.block,
            "map_index": cell.map_index,
            "seat_index": cell.seat_index,
            "arm": cell.arm,
            "packet_sha256": packing.PACKET_SHA256,
        }
        assert isinstance(sample.input, list)
        system, user = sample.input
        assert isinstance(system, ChatMessageSystem)
        assert system.content == _SYSTEM
        assert isinstance(user, ChatMessageUser)
        user_text = user.content
        assert isinstance(user_text, str)
        assert user_text == packing.prompt(source_packet, cell)
        assert all(item not in user_text for item in source_ids | {cell.cell_id})
    assert inspect_replay.active_cell() is None


def test_packet_override_requires_a_verified_pair(tmp_path: Path) -> None:
    path, digest = _alternate_packet(tmp_path)
    override = inspect_replay.seatlab_facts(path, digest)
    assert len(override.dataset) == 96
    for row in override.dataset:
        assert row.metadata is not None
        assert row.metadata["packet_sha256"] == digest
    for args in ((path, None), (None, digest), ("", digest)):
        with pytest.raises(ValueError, match="must be provided together"):
            inspect_replay.seatlab_facts(*args)
    with pytest.raises(ValueError, match="invalid expected packet SHA-256"):
        inspect_replay.seatlab_facts(path, "unsupported")
    with pytest.raises(ValueError, match="packet SHA-256 mismatch"):
        inspect_replay.seatlab_facts(path, packing.PACKET_SHA256)
    path.write_bytes(b"{}")
    with pytest.raises(ValueError, match="packet SHA-256 mismatch"):
        inspect_replay.seatlab_facts(path, digest)


def _state(sample: Sample, sample_id: int | str | None = None) -> TaskState:
    assert sample.id is not None
    assert isinstance(sample.input, list)
    return TaskState(
        model=ModelName("mockllm/seatlab"),
        sample_id=sample.id if sample_id is None else sample_id,
        epoch=1,
        input=sample.input,
        messages=list(sample.input),
        metadata=dict(sample.metadata or {}),
    )


def test_solver_refuses_stale_ids_commitments_and_prompts(tmp_path: Path) -> None:
    task = inspect_replay.seatlab_facts()
    sample = task.dataset[0]
    later_block_id = "ix-b1-m0-s1-separate"
    later_block_sample = next(row for row in task.dataset if row.id == later_block_id)
    messages = sample.input
    later_messages = later_block_sample.input
    assert isinstance(messages, list)
    assert isinstance(later_messages, list)
    assert [row.content for row in later_messages] == [row.content for row in messages]
    override = inspect_replay.seatlab_facts(*_alternate_packet(tmp_path))
    stale = _state(override.dataset[0])
    injected = _state(sample)
    injected.messages.append(ChatMessageUser(content="Unexpected future ballot"))

    async def generate(
        state: TaskState,
        tool_calls: Literal["loop", "single", "none"] = "loop",
        **kwargs: Unpack[GenerateConfigArgs],
    ) -> TaskState:
        pytest.fail("generation must not run for an unsealed state")

    for state, error in (
        (_state(sample, "not-a-cell"), "unknown SeatLab sample ID"),
        (_state(sample, later_block_id), "metadata does not match"),
        (stale, "metadata does not match"),
        (injected, "messages do not match"),
    ):
        with pytest.raises(ValueError, match=error):
            asyncio.run(task.solver(state, generate))
    assert inspect_replay.active_cell() is None


def test_solver_revokes_copied_context_when_generation_raises() -> None:
    task = inspect_replay.seatlab_facts()
    sample = task.dataset[0]
    messages = sample.input
    assert isinstance(messages, list)
    _, user = messages
    assert isinstance(user, ChatMessageUser)
    retained: list[Context] = []

    async def generate(
        state: TaskState,
        tool_calls: Literal["loop", "single", "none"] = "loop",
        **kwargs: Unpack[GenerateConfigArgs],
    ) -> TaskState:
        assert tool_calls == "none"
        assert kwargs.get("cache") is False
        scope = inspect_replay.active_cell()
        assert scope is not None
        assert scope[0].cell_id == sample.id
        assert scope[1] == user.content
        retained.append(copy_context())
        raise RuntimeError("memory generation stopped")

    with pytest.raises(RuntimeError, match="memory generation stopped"):
        asyncio.run(task.solver(_state(sample), generate))
    assert inspect_replay.active_cell() is None
    assert retained[0].run(inspect_replay.active_cell) is None


def _ballot_from_separate_prompt(prompt: str, *, exact: bool) -> str:
    _, dossier, submissions, *_ = prompt.splitlines()
    facts = {
        fact["fact"]: fact
        for fact in (
            json.loads(dossier.removeprefix("Dossier: "))["facts"]
            + json.loads(submissions.removeprefix("Submissions: "))
        )
    }
    totals = dict.fromkeys("XYZ", 0)
    for fact in facts.values():
        totals[fact["option"]] += fact["points"]
    if not exact:
        totals["X"] += 1
    return json.dumps({"totals": totals, "choice": "X", "cited": [min(facts)]})


def test_inspect_generation_and_scoring_keep_parallel_samples_scoped(
    source_packet: dict[str, object], tmp_path: Path
) -> None:
    task = inspect_replay.seatlab_facts()
    cells = packing.grid()[:4]
    assert [cell.arm for cell in cells] == [
        "separate",
        "indexed",
        "indexed",
        "separate",
    ]
    expected = {cell.cell_id: packing.prompt(source_packet, cell) for cell in cells}
    malformed = "unparseable output must stay out of scores"
    responses = {
        cells[0].cell_id: _ballot_from_separate_prompt(
            expected[cells[0].cell_id], exact=True
        ),
        cells[1].cell_id: malformed,
        cells[2].cell_id: "",
        cells[3].cell_id: _ballot_from_separate_prompt(
            expected[cells[3].cell_id], exact=False
        ),
    }
    seen: set[str] = set()
    entered = asyncio.Event()

    async def respond(
        messages: list[ChatMessage],
        tools: list[ToolInfo],
        tool_choice: ToolChoice,
        config: GenerateConfig,
    ) -> ModelOutput:
        scope = inspect_replay.active_cell()
        assert scope is not None
        cell, user_text = scope
        assert cell.cell_id in expected and cell.cell_id not in seen
        assert user_text == expected[cell.cell_id]
        assert cell.cell_id not in user_text
        assert [(message.role, message.content) for message in messages] == [
            ("system", _SYSTEM),
            ("user", user_text),
        ]
        assert tools == [] and tool_choice == "none"
        assert config.cache is False
        seen.add(cell.cell_id)
        if len(seen) == len(cells):
            entered.set()
        await asyncio.wait_for(entered.wait(), timeout=10)
        assert inspect_replay.active_cell() == scope
        return ModelOutput.from_content("mockllm/seatlab", responses[cell.cell_id])

    [log] = inspect_eval(
        task,
        model="mockllm/seatlab",
        model_args={"custom_outputs": respond},
        log_dir=str(tmp_path),
        display="none",
        sample_id=list(expected),
        max_samples=4,
        max_connections=4,
        cache=True,
        system_message="Do not add this to the sealed task messages.",
        debug_errors=True,
    )
    assert log.status == "success"
    assert seen == set(expected)
    assert inspect_replay.active_cell() is None
    samples = log.samples
    assert samples is not None
    by_id = {sample.id: sample for sample in samples}
    for cell, value in zip(cells, ("C", "N", "N", "I"), strict=True):
        scores = by_id[cell.cell_id].scores
        assert scores is not None
        score = scores[_SCORER]
        assert score.value == value
        assert score.metadata is not None
        assert score.metadata == packing.score(
            source_packet, cell, responses[cell.cell_id]
        )
        assert score.answer is None and score.explanation is None
        assert malformed not in score.model_dump_json()
    assert log.results is not None
    assert all(result.metrics == {} for result in log.results.scores)


def test_file_reference_evaluates_with_a_standard_inspect_model(tmp_path: Path) -> None:
    task_file = inspect_replay.__file__
    assert task_file is not None
    [log] = inspect_eval(
        f"{Path(task_file).absolute()}@seatlab_facts",
        model="mockllm/seatlab-file",
        log_dir=str(tmp_path),
        limit=1,
        display="none",
        debug_errors=True,
    )
    assert log.status == "success"
    assert log.eval.task == "seatlab_facts"
    assert log.samples is not None
    [sample] = log.samples
    assert sample.id == "ix-b0-m0-s1-separate"
    assert sample.scores is not None
    score = sample.scores[_SCORER]
    assert score.value == "N"
    assert score.metadata is not None
    assert score.metadata["status"] == "invalid_response"
