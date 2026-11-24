import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.workbench.generation_receipts import _ledger, route_label
from examples.seatlab.workbench.seat_stream import Malformed

Json = dict[str, Any]
REQUESTED = "openai/gpt-5-mini"
FREE = "meta-llama/llama-3.1-8b-instruct:free"


def started(call: str) -> Json:
    return {
        "event": "started",
        "at": "2026-11-20T15:00:00Z",
        "call_id": call,
        "identity": "author",
        "model": REQUESTED,
        "request_sha256": "0" * 64,
        "reserved_nano": 2_000_000,
        "input_token_bound": 4096,
        "output_token_bound": 512,
    }


def settled(call: str, generation: str, **route: Any) -> Json:
    return {
        "event": "settled",
        "at": "2026-11-20T15:00:02Z",
        "call_id": call,
        "cost_nano": 1_500_000,
        "cost_basis": "openrouter_generation.total_cost",
        "receipt": {"id": generation, "total_cost": 0.0015, **route},
    }


def write(tmp_path: Path, rows: list[Json]) -> str:
    path = tmp_path / "events.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return str(path)


def semantics(path: str, include_routes: bool) -> tuple[Any, ...]:
    ledger = _ledger(path, include_routes=include_routes)
    return (
        ledger.rows,
        ledger.started,
        ledger.repeated_starts,
        ledger.failed,
        ledger.unavailable,
        ledger.held,
        ledger.settled,
        ledger.repeated_settlements,
        ledger.generation_calls,
    )


@pytest.mark.parametrize(
    ("value", "label"),
    [
        (FREE, FREE),
        ("Google AI Studio", "Google AI Studio"),
        ("DeepInfra", "DeepInfra"),
        ("a" * 128, "a" * 128),
        ("a" * 129, None),
        ("", None),
        (" Azure", None),
        ("Azure ", None),
        ("Google  AI", None),
        ("Azure\n", None),
        ("open\x1b[31mai/gpt", None),
        ("open\tai", None),
        ("/openai/gpt", None),
        ("openai/gpt\u00e9", None),
        ("openai/gpt'", None),
        (None, None),
        (7, None),
        (["openai/gpt"], None),
    ],
)
def test_route_label_is_bounded_single_line_or_absent(value, label):
    assert route_label(value) == label


def test_absent_served_route_never_falls_back_to_requested_model(tmp_path):
    path = write(tmp_path, [started("c1"), settled("c1", "gen-a")])
    assert _ledger(path, include_routes=True).routes == {"c1": (None, None)}


def test_unusable_labels_become_absent(tmp_path):
    rows = [
        started("c1"),
        settled("c1", "gen-a", model="openai/\x07gpt", provider_name=" Azure"),
        started("c2"),
        settled("c2", "gen-b", model=REQUESTED, provider_name=7),
    ]
    routes = _ledger(write(tmp_path, rows), include_routes=True).routes
    assert routes == {"c1": (None, None), "c2": (REQUESTED, None)}


def test_unresolved_starts_carry_no_route(tmp_path):
    failed = {
        "event": "failed",
        "at": "2026-11-20T15:00:01Z",
        "call_id": "c3",
        "error_type": "timeout",
        "http_status": None,
        "provider_detail": None,
        "reservation_retained": True,
    }
    rows = [
        started("c1"),
        settled("c1", "gen-a", model=REQUESTED, provider_name="Azure"),
        started("c2"),
        started("c3"),
        failed,
    ]
    routes = _ledger(write(tmp_path, rows), include_routes=True).routes
    assert routes == {"c1": (REQUESTED, "Azure")}


def test_repeated_identical_route_counts_once(tmp_path):
    row = settled("c1", "gen-a", model=REQUESTED, provider_name="Google AI Studio")
    path = write(tmp_path, [started("c1"), row, row])
    ledger = _ledger(path, include_routes=True)
    assert ledger.routes == {"c1": (REQUESTED, "Google AI Studio")}
    assert ledger.settled == {"c1": ("gen-a", Decimal("0.0015"))}
    assert ledger.repeated_settlements == 1
    assert semantics(path, True) == semantics(path, False)


def test_contradictory_route_refuses_without_echoing_labels(tmp_path):
    first = settled("c1", "gen-a", model=REQUESTED, provider_name="Azure")
    second = settled("c1", "gen-a", model=REQUESTED, provider_name="SecretHost")
    path = write(tmp_path, [started("c1"), first, second])
    with pytest.raises(Malformed) as error:
        _ledger(path, include_routes=True)
    message = str(error.value)
    assert "contradictory route" in message
    assert "SecretHost" not in message and "gen-a" not in message
    ledger = _ledger(path)
    assert ledger.routes == {}
    assert ledger.settled == {"c1": ("gen-a", Decimal("0.0015"))}
    assert ledger.repeated_settlements == 1


def test_default_mode_keeps_prior_semantics(tmp_path):
    rows = [
        started("c1"),
        settled("c1", "gen-a", model=REQUESTED, provider_name="Azure"),
        started("c2"),
        settled("c2", "gen-b"),
        started("c3"),
    ]
    path = write(tmp_path, rows)
    assert _ledger(path).routes == {}
    assert semantics(path, True) == semantics(path, False)
    truthy: Any = 1
    with pytest.raises(Malformed):
        _ledger(path, include_routes=truthy)
