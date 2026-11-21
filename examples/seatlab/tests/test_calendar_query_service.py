from collections.abc import Callable
from copy import deepcopy
from typing import Any

import pytest

from examples.seatlab.visibility.calendar_query_service import verify

_MODELS = {"sender": "writer", "receiver": "reader"}


def _case() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    prompt, reply = "a" * 64, "b" * 64
    turn = {"role": "sender", "prompt_sha256": prompt, "response_sha256": reply}
    rows = [{"world": 1, "direction": 0, "repetition": 0, "turns": [turn, turn]}]
    receipt = {
        "world": 1,
        "direction": 0,
        "role": "sender",
        "receipt": {
            "schema": "seatlab.omp-seat-receipt/v1",
            "served_model": "writer",
            "prompt_sha256": prompt,
            "reply_sha256": reply,
            "usage": {
                "prompt_tokens": 7,
                "completion_tokens": 3,
                "cached_tokens": 2,
                "total_tokens": 10,
            },
        },
    }
    return rows, [receipt, deepcopy(receipt)]


def test_identical_native_prompts_still_need_separate_service_calls() -> None:
    rows, receipts = _case()
    roles = verify(rows, receipts, _MODELS)["roles"]
    assert (
        roles["sender"]["matched"],
        roles["sender"]["total_tokens"],
        roles["receiver"]["matched"],
    ) == (2, 20, 0)
    with pytest.raises(ValueError, match="missing"):
        verify(rows, receipts[:1], _MODELS)
    with pytest.raises(ValueError, match="repeated"):
        verify(rows, [*receipts, receipts[0]], _MODELS)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda x: x.update(direction=1),
        lambda x: x["receipt"].update(served_model="other"),
        lambda x: x.update(role="receiver"),
        lambda x: x["receipt"]["usage"].update(prompt_tokens=True),
        lambda x: x["receipt"]["usage"].update(cached_tokens=8),
        lambda x: x["receipt"]["usage"].update(total_tokens=9),
    ],
)
def test_misbound_or_inconsistent_receipts_do_not_count(
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    rows, receipts = _case()
    mutate(receipts[0])
    with pytest.raises(ValueError):
        verify(rows, receipts, _MODELS)
