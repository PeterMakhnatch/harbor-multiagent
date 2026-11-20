import json
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from stat import S_IMODE
from unittest.mock import Mock

import pytest

from examples.seatlab.committee import ballot_packet, ballot_run, ballot_schedule
from examples.seatlab.tests.test_committee_ballot_packet import bound_packet
from examples.seatlab.visibility import deepseek, spend

_BLOCK = tuple(cell for cell in ballot_schedule.grid() if cell.block == 0)
_PRIVATE_TEXT = ' {"choice":"unknown","diagnostic":"/private/provider/é"}\n'


@pytest.fixture(scope="module")
def packet_file(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    return bound_packet(tmp_path_factory.mktemp("ballot-run-packet"))


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        pytest.fail("runner test reached an unmocked provider request")

    monkeypatch.setattr(deepseek, "_request", fail)


def _run(
    packet_file: tuple[Path, str],
    study: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    block: int = 0,
) -> tuple[int, dict]:
    packet, digest = packet_file
    code = ballot_run.main(
        [str(packet), str(study), "--expect-sha256", digest, "--block", str(block)]
    )
    captured = capsys.readouterr()
    assert not captured.err
    assert "/private/provider" not in captured.out
    assert str(packet) not in captured.out
    assert str(study) not in captured.out
    return code, json.loads(captured.out)


def _receipt_path(study: Path, cell: ballot_schedule.Cell) -> Path:
    return study / "cells" / f"{cell.cell_id}.json"


def _read(study: Path, cell: ballot_schedule.Cell) -> dict:
    return json.loads(_receipt_path(study, cell).read_bytes())


def _text(packet: dict, cell: ballot_schedule.Cell) -> tuple[dict, str]:
    first = packet["teams"][cell.map_index]["seats"][cell.seat_index]["first"]
    choice = {"X": "Y", "Y": "Z", "Z": "X"}[first["choice"]]
    ballot = first | {"choice": choice}
    return ballot, " " + json.dumps(ballot) + "\n"


def test_invalid_vote_does_not_retry_or_prevent_independent_cells(
    packet_file: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    study = tmp_path / "study"
    packet = ballot_packet.read_packet(*packet_file)
    next_cell = iter(_BLOCK)

    def turn(prompt: str, *, run_id: str, role: str) -> deepseek.SeatTurn:
        cell = next(next_cell)
        assert (run_id, role) == (cell.cell_id, cell.role)
        assert prompt == ballot_packet.prompt(
            packet, cell.map_index, cell.seat_index, cell.arm
        )
        claim = _read(study, cell)
        assert claim["status"] == "started"
        assert claim["prompt_sha256"] == sha256(prompt.encode("utf-8")).hexdigest()
        assert claim["response_text"] is None
        text = _PRIVATE_TEXT if cell == _BLOCK[0] else _text(packet, cell)[1]
        return deepseek.SeatTurn(text, deepseek.REVISION.lower(), 1, 0, 1)

    call = Mock(side_effect=turn)
    monkeypatch.setattr(deepseek, "turn", call)
    code, summary = _run(packet_file, study, capsys)
    assert (code, summary) == (
        0,
        {
            "block": 0,
            "planned": 72,
            "complete": 71,
            "attempted": 72,
            "incomplete": 1,
            "unattempted": 0,
            "invalid_ballot": 1,
            "stop": None,
        },
    )
    assert call.call_count == 72
    assert S_IMODE(study.stat().st_mode) == 0o700
    assert S_IMODE((study / "cells").stat().st_mode) == 0o700
    assert not (study / "cells" / "pb-b1-m0-s1-private.json").exists()
    for cell, expected_status in zip(
        _BLOCK[:2], ("invalid_ballot", "complete"), strict=True
    ):
        receipt = _read(study, cell)
        assert receipt["schema"] == "seatlab.committee-peer-turn/v1"
        assert receipt["packet_sha256"] == packet_file[1]
        assert receipt["status"] == expected_status
        assert receipt["served_model"] == deepseek.REVISION.lower()
        ballot, text = _text(packet, cell)
        if expected_status == "invalid_ballot":
            ballot, text = None, _PRIVATE_TEXT
        assert receipt["ballot"] == ballot
        assert receipt["response_text"] == text
        assert receipt["response_sha256"] == sha256(text.encode("utf-8")).hexdigest()
        assert S_IMODE(_receipt_path(study, cell).stat().st_mode) == 0o400

    call.reset_mock()
    assert _run(packet_file, study, capsys) == (0, summary)
    call.assert_not_called()

    path = _receipt_path(study, _BLOCK[0])
    modified = _read(study, _BLOCK[0]) | {"response_sha256": "0" * 64}
    path.chmod(0o600)
    path.write_text(json.dumps(modified), encoding="utf-8")
    path.chmod(0o400)
    code, result = _run(packet_file, study, capsys)
    assert (code, result["stop"]) == (1, "evidence_invalid")
    call.assert_not_called()


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        ("balance_unverified", "budget_unresolved"),
        ("budget_guard", "budget_unresolved"),
        ("provider_timeout", "transport_failed"),
        ("served_model_unverified", "identity_failed"),
        ("payload_invalid", "evidence_invalid"),
        ("model_alias", "identity_failed"),
        ("interrupted", "started"),
    ],
)
def test_fatal_or_started_claim_stops_before_next_cell_and_never_repeats(
    packet_file: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
    status: str,
) -> None:
    study = tmp_path / "study"
    packet = ballot_packet.read_packet(*packet_file)
    if failure == "model_alias":
        second = deepseek.SeatTurn(_PRIVATE_TEXT, deepseek.MODEL.upper(), 1, 0, 1)
    elif failure == "interrupted":
        second = KeyboardInterrupt(_PRIVATE_TEXT)
    else:
        second = deepseek.SeatUnavailable(failure)
    call = Mock(
        side_effect=[
            deepseek.SeatTurn(_text(packet, _BLOCK[0])[1], deepseek.MODEL, 1, 0, 1),
            second,
        ]
    )
    monkeypatch.setattr(deepseek, "turn", call)
    code, summary = _run(packet_file, study, capsys)
    assert code == 1
    assert summary["stop"] == status
    assert summary["attempted"] == 2
    assert summary["complete"] == 1
    assert summary["incomplete"] == 1
    assert summary["unattempted"] == 70
    assert call.call_count == 2
    assert not _receipt_path(study, _BLOCK[2]).exists()
    receipt = _read(study, _BLOCK[1])
    assert receipt["status"] == status
    for key in ("served_model", "response_text", "response_sha256", "ballot"):
        assert receipt[key] is None

    call.reset_mock()
    assert _run(packet_file, study, capsys) == (1, summary)
    call.assert_not_called()


def test_packet_bytes_and_task_are_checked_before_claiming(
    packet_file: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    call = Mock()
    monkeypatch.setattr(deepseek, "turn", call)
    packet, digest = packet_file
    altered = tmp_path / "altered.json"
    altered.write_bytes(packet.read_bytes() + b"\n")
    study = tmp_path / "study"
    code, result = _run((altered, digest), study, capsys)
    assert (code, result["stop"], result["attempted"]) == (1, "evidence_invalid", 0)
    assert not study.exists()

    data = json.loads(packet.read_bytes())
    data["task_git_tree"] = "0" * 40
    raw = json.dumps(data).encode("utf-8")
    altered.write_bytes(raw)
    code, result = _run((altered, sha256(raw).hexdigest()), study, capsys)
    assert (code, result["stop"], result["attempted"]) == (1, "evidence_invalid", 0)
    assert not study.exists()
    call.assert_not_called()


def test_both_blocks_share_the_same_study_packet_binding(
    packet_file: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    study = tmp_path / "study"
    call = Mock(side_effect=deepseek.SeatUnavailable("provider_timeout"))
    monkeypatch.setattr(deepseek, "turn", call)
    assert _run(packet_file, study, capsys)[0] == 1
    raw = packet_file[0].read_bytes() + b"\n"
    alternate = tmp_path / "second.json"
    alternate.write_bytes(raw)
    other_commitment = alternate, sha256(raw).hexdigest()
    ballot_packet.read_packet(*other_commitment)
    call.reset_mock()
    code, result = _run(other_commitment, study, capsys, block=1)
    assert (code, result["stop"], result["attempted"]) == (1, "evidence_invalid", 0)
    call.assert_not_called()

    assert _run(packet_file, study, capsys, block=1)[0] == 1
    first = next(cell for cell in ballot_schedule.grid() if cell.block == 1)
    assert call.call_args.kwargs == {"run_id": first.cell_id, "role": first.role}
    call.assert_called_once()


def test_no_paid_replacement_with_same_ledger_and_new_study_path(
    packet_file: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ledger = tmp_path / "spend"
    ledger.mkdir(mode=0o700)
    spend.initialize(ledger, Decimal("3.00"), Decimal("0.15"))
    monkeypatch.setenv("SEATLAB_SPEND_DIR", str(ledger))
    monkeypatch.setattr(spend, "_HOST_LEDGER", None)
    generations = []

    def request(path: str, body: bytes | None, timeout: int) -> dict:
        if path == "/user/balance":
            return {
                "is_available": True,
                "balance_infos": [
                    {
                        "currency": "USD",
                        "total_balance": "3.00",
                        "granted_balance": "0.00",
                        "topped_up_balance": "3.00",
                    }
                ],
            }
        generations.append(path)
        raise deepseek.SeatUnavailable("provider_timeout")

    monkeypatch.setattr(deepseek, "_request", request)
    code, result = _run(packet_file, tmp_path / "first", capsys)
    assert (code, result["stop"]) == (1, "transport_failed")
    code, result = _run(packet_file, tmp_path / "replacement", capsys)
    assert (code, result["stop"]) == (1, "budget_unresolved")
    assert generations == ["/chat/completions"]
    assert spend.report(ledger, run_prefix="pb-")["unsettled_calls"] == 1


@pytest.mark.parametrize("unsafe", ["symlink", "repository", "permissions"])
def test_private_study_rejects_unsafe_paths_without_provider_turns(
    packet_file: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    unsafe: str,
) -> None:
    study = tmp_path / "study"
    if unsafe == "symlink":
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir(mode=0o700)
        study.symlink_to(elsewhere)
    elif unsafe == "repository":
        (tmp_path / ".git").touch()
    else:
        study.mkdir()
        study.chmod(0o755)
    call = Mock()
    monkeypatch.setattr(deepseek, "turn", call)
    code, result = _run(packet_file, study, capsys)
    assert (code, result["stop"], result["attempted"]) == (1, "evidence_invalid", 0)
    call.assert_not_called()
