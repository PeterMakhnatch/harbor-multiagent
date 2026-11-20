import json
import stat
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.visibility import (
    calendar_packet,
    calendar_protocol,
    calendar_turns,
    deepseek,
    spend,
)

_BLOCK = calendar_protocol.Block(0, 0, 0)
_WORLD = calendar_packet.World(
    0, "0" * 64, (((0, 0, 0, 0, 0),) * 3, ((1, 2, 4, 8, 16),) * 3)
)
_MODEL = "DeepSeek-V4.1-Flash-1017"


@pytest.fixture
def folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    study = tmp_path.resolve() / "study"
    ledger = tmp_path.resolve() / "ledger"
    study.mkdir(mode=0o700)
    ledger.mkdir(mode=0o700)
    spend.initialize(ledger, Decimal("2.00"), Decimal("0.10"))
    monkeypatch.setattr(spend, "_HOST_LEDGER", None)
    monkeypatch.setattr(deepseek, "_BOUND", False)
    monkeypatch.setattr(deepseek, "_KEY", None)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("SEATLAB_SPEND_DIR", str(ledger))
    monkeypatch.setattr(deepseek, "_balance", lambda: Decimal("2.00"))
    monkeypatch.setattr(
        deepseek, "_request", lambda *_: pytest.fail("unexpected request")
    )
    monkeypatch.setattr(calendar_packet, "load", lambda *_: (_WORLD,))
    return study, ledger


def _adapter(
    monkeypatch: pytest.MonkeyPatch, *responses: str | None | deepseek.SeatUnavailable
) -> list[str]:
    prompts: list[str] = []
    outcomes = iter(responses)

    def generation(path: str, body: bytes, _timeout: int) -> dict[str, Any]:
        assert path == "/chat/completions"
        prompts.append(json.loads(body)["messages"][1]["content"])
        result = next(outcomes)
        if isinstance(result, deepseek.SeatUnavailable):
            raise result
        return {
            "model": _MODEL,
            "usage": {
                "prompt_cache_hit_tokens": 2,
                "prompt_cache_miss_tokens": 10,
                "prompt_tokens": 12,
                "completion_tokens": 4,
                "total_tokens": 16,
            },
            "choices": [{"message": {"content": result}}],
        }

    monkeypatch.setattr(deepseek, "_request", generation)
    return prompts


def _seed(study: Path, ledger: Path) -> int:
    return calendar_turns.main(
        [
            "seed",
            "--study",
            str(study),
            "--block",
            _BLOCK.id,
            "--spend-dir",
            str(ledger),
        ]
    )


def _receipt_path(study: Path, seat: str) -> Path:
    return study / "seeds" / _BLOCK.id / f"{seat}.json"


@pytest.mark.parametrize(
    ("run", "role"),
    [
        ("cf-w00-d0-r0-blind", "cal_initial_p1"),
        ("cf-w00-d0-r0", "cal_recheck"),
        ("cf-w00-d0-r2-peer", "cal_decide"),
        ("cf-w00-d0-r0-peer", "inventory"),
        ("cf-w12-d0-r0", "cal_initial_p2"),
    ],
)
def test_unregistered_role_and_run_never_reach_adapter(
    monkeypatch: pytest.MonkeyPatch, run: str, role: str
) -> None:
    monkeypatch.setattr(
        deepseek, "turn", lambda *_, **__: pytest.fail("called adapter")
    )
    with pytest.raises(calendar_turns.TurnError, match="^invalid_call_identity$"):
        calendar_turns.measure("fixture", run, role)


@pytest.mark.parametrize(
    ("arm", "role", "text", "decoded"),
    [
        ("peer", "cal_recheck", '{"starts":[]}', ()),
        ("blind", "cal_decide", '{"slot":0}', 0),
    ],
)
def test_registered_native_turn_replays_strict_value_against_original_ledger(
    folders: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    arm: str,
    role: str,
    text: str,
    decoded: tuple[int, ...] | int,
) -> None:
    _, ledger = folders
    calls = _adapter(monkeypatch, text)
    run = f"{_BLOCK.id}-{arm}"
    receipt = calendar_turns.measure("fixture", run, role)
    assert calls == ["fixture"]
    assert receipt["status"] == "accepted"
    assert receipt["input_tokens"] == 12
    assert receipt["cache_tokens"] == 2
    assert receipt["output_tokens"] == 4
    cost = spend.report(ledger)
    assert calendar_turns.verify_turn(receipt, "fixture", run, role, cost) == decoded
    cost["calls"].append(cost["calls"][0])
    with pytest.raises(calendar_turns.TurnError, match="^ledger_ambiguous$"):
        calendar_turns.verify_turn(receipt, "fixture", run, role, cost)


def test_calendar_role_uses_original_floor(
    folders: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, ledger = folders
    monkeypatch.setattr(deepseek, "_balance", lambda: Decimal("1.50"))
    receipt = calendar_turns.measure("fixture", _BLOCK.id, "cal_initial_p1")
    assert (receipt["status"], receipt["reason"]) == ("failed", "budget_guard")
    assert spend.report(ledger)["calls"] == []


def test_seed_cli_is_exclusive_and_keeps_the_other_seats_independent_result(
    folders: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    study, ledger = folders
    calls = _adapter(monkeypatch, '{"starts":[2,1]}', '{"starts":[4]}')
    claimed = _receipt_path(study, "p1").parent
    claimed.mkdir(mode=0o700, parents=True)
    claimed.parent.chmod(0o700)
    assert _seed(study, ledger) == 1
    assert calls == []
    claimed.rmdir()

    assert _seed(study, ledger) == 1  # Invalid model choice, not a missing seed.
    assert calls == [calendar_protocol.initial_prompt(table) for table in _WORLD.tables]
    assert _seed(study, ledger) == 1
    assert len(calls) == 2
    first = _receipt_path(study, "p1")
    second = _receipt_path(study, "p2")
    assert stat.S_IMODE(first.stat().st_mode) == 0o600
    assert stat.S_IMODE(second.stat().st_mode) == 0o600
    receipt = json.loads(first.read_bytes())
    assert receipt["status"] == "invalid"
    assert receipt["output_tokens"] == 4
    with pytest.raises(calendar_turns.SeedPairError) as error:
        calendar_turns.load_seed_pair(study, _BLOCK, _WORLD, ledger)
    assert error.value.issues == {"p1": "model_invalid"}
    assert error.value.values == {"p2": (4,)}


def test_seed_cli_refuses_symlink_to_other_directory(
    folders: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    study, ledger = folders
    archive = study.parent / "untouched"
    archive.mkdir(mode=0o700)
    (study / "seeds").symlink_to(archive, target_is_directory=True)
    calls = _adapter(monkeypatch)
    assert _seed(study, ledger) == 1
    assert calls == []
    assert not list(archive.iterdir())


@pytest.mark.parametrize("settled", [False, True])
def test_adapter_failure_stops_seed_without_losing_known_usage(
    folders: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, settled: bool
) -> None:
    study, ledger = folders
    result = None if settled else deepseek.SeatUnavailable("provider_timeout")
    calls = _adapter(monkeypatch, result)
    assert _seed(study, ledger) == 1
    assert len(calls) == 1
    receipt = json.loads(_receipt_path(study, "p1").read_bytes())
    assert receipt["status"] == "failed"
    reason = "provider_response_invalid" if settled else "provider_timeout"
    assert receipt["reason"] == reason
    assert receipt["output_tokens"] == (4 if settled else None)
    assert receipt["response_text"] is None
    with pytest.raises(calendar_turns.SeedPairError) as error:
        calendar_turns.load_seed_pair(study, _BLOCK, _WORLD, ledger)
    assert error.value.issues == {"p1": "turn_failed", "p2": "receipt_missing"}
    assert spend.report(ledger)["unsettled_calls"] == int(not settled)


@pytest.mark.parametrize(
    ("tamper", "category"),
    [
        ("prompt", "prompt_mismatch"),
        ("text", "response_mismatch"),
        ("tokens", "ledger_mismatch"),
        ("model", "ledger_mismatch"),
    ],
)
def test_partial_pair_still_rejects_forged_second_seat(
    folders: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
    category: str,
) -> None:
    study, ledger = folders
    _adapter(monkeypatch, '{"starts":[1]}', '{"starts":[4]}')
    assert _seed(study, ledger) == 0
    assert calendar_turns.load_seed_pair(study, _BLOCK, _WORLD, ledger) == ((1,), (4,))
    _receipt_path(study, "p1").unlink()
    path = _receipt_path(study, "p2")
    receipt = json.loads(path.read_bytes())
    if tamper == "prompt":
        receipt["prompt_sha256"] = "0" * 64
    elif tamper == "text":
        receipt["response_text"] = '{"starts":[9]}'
    elif tamper == "tokens":
        receipt["cache_tokens"] = 3
    else:
        receipt["served_model"] = deepseek.MODEL
    path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(calendar_turns.SeedPairError) as error:
        calendar_turns.load_seed_pair(study, _BLOCK, _WORLD, ledger)
    assert error.value.issues == {"p1": "receipt_missing", "p2": category}
    assert error.value.values == {}
