"""A failed or modified sample must not cause another paid provider call."""

import asyncio

import pytest

pytest.importorskip("inspect_ai")
from inspect_ai.model import (
    ChatMessage,
    ChatMessageSystem,
    ChatMessageUser,
    GenerateConfig,
)

from examples.seatlab import inspect_replay
from examples.seatlab.committee import inspect_direct, packing
from examples.seatlab.visibility import deepseek


@pytest.mark.parametrize("failure", ["modified", "no_scope", "provider"])
def test_failure_suppresses_even_the_next_valid_transport(
    failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cell = packing.grid()[0]
    active: tuple[packing.Cell, str] | None = (cell, "sealed view")
    monkeypatch.setattr(inspect_replay, "active_cell", lambda: active)
    attempted: list[tuple[str, str]] = []

    def transport(prompt: str, *, run_id: str, role: str) -> deepseek.SeatTurn:
        attempted.append((run_id, role))
        raise TimeoutError("direct request did not return")

    monkeypatch.setattr(deepseek, "turn", transport)
    config = GenerateConfig(temperature=0.7, max_tokens=512, max_retries=0, cache=False)
    provider = inspect_direct.DirectSeat(deepseek.MODEL, config=config)
    valid: list[ChatMessage] = [
        ChatMessageSystem(content=deepseek._SYSTEM),
        ChatMessageUser(content="sealed view"),
    ]
    if failure == "no_scope":
        active = None
    first = (
        [*valid[:1], ChatMessageUser(content="another seat's view")]
        if failure == "modified"
        else valid
    )
    with pytest.raises(TimeoutError if failure == "provider" else ValueError):
        asyncio.run(provider.generate(first, [], "auto", config))

    active = (cell, "sealed view")
    with pytest.raises(RuntimeError, match="previous direct turn stopped"):
        asyncio.run(provider.generate(valid, [], "auto", config))
    assert attempted == ([(cell.cell_id, cell.role)] if failure == "provider" else [])
