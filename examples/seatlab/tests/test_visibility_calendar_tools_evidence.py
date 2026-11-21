"""Check the public tool evidence against stock replay and private receipts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.tests import test_visibility_calendar_replica_report as stock
from examples.seatlab.visibility import calendar_packet as packet
from examples.seatlab.visibility import calendar_tools_evidence as evidence_reader
from examples.seatlab.visibility import calendar_tools_protocol as tools

_PICK = '{"tool":"local_starts"}'
_PRIVATE_PICK = "private invalid tool selection"
_PEER_RECHECK = "rx-w00-d1-r0-peer:cal_recheck"
_PEER_DECIDE = "rx-w00-d1-r0-peer:cal_decide"
_BLIND_RECHECK = "rx-w00-d1-r0-blind:cal_recheck"
_BLIND_DECIDE = "rx-w00-d1-r0-blind:cal_decide"
_SEED1 = "rx-w00-d1-r0:cal_initial_p1"
_SEED2 = "rx-w00-d1-r0:cal_initial_p2"
_PLAYED = (_SEED1, _SEED2, _PEER_RECHECK, _PEER_DECIDE, _BLIND_RECHECK)


@dataclass(frozen=True)
class _Evidence:
    directory: Path
    stock: stock._Block
    policy: str
    tables: tuple[packet.Table, packet.Table]
    views: dict[str, tools.View]

    def native_turn(self, identifier: str) -> Path:
        return self.stock.out / "turns" / f"{identifier.replace(':', '_')}.json"

    def action(self, identifier: str) -> Path:
        run = tools.action_id(self.stock.block, self.policy, identifier)
        return self.directory / "actions" / f"{run}.json"

    def read(self) -> evidence_reader._Policy:
        return evidence_reader.read_policy(
            self.directory, self.stock.block, self.policy
        )


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, row: dict[str, Any]) -> None:
    path.write_text(json.dumps(row), encoding="utf-8")
    path.chmod(0o600)


@pytest.fixture
def evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> _Evidence:
    root = tmp_path.resolve()
    fixture = stock._Block(root / "fixture")
    policy = getattr(request, "param", "on")
    directory = root / "study" / fixture.run / policy
    directory.mkdir(mode=0o700, parents=True)
    for name in ("actions", "meter"):
        (directory / name).mkdir(mode=0o700)
    fixture.out = fixture.out.rename(directory / "block")
    fixture.out.chmod(0o700)
    (fixture.out / "block.json").chmod(0o600)
    tables = packet.load(packet.PACKET, packet.PACKET_SHA256)[0].tables
    monkeypatch.setattr(stock, "_TABLES", tables)
    monkeypatch.setattr(
        stock, "_SEEDS", tuple(packet.starts(table) for table in tables)
    )

    def verify(task: Path, world: int) -> stock.source_identity.Identity:
        assert (task, world) == (fixture.source, 0)
        return stock.source_identity.Identity(stock._CHECKSUM, stock._SEMANTICS)

    monkeypatch.setattr(stock.source_identity, "verify", verify)
    fixture.seeds()
    fixture.branch("peer", 0)
    fixture.branch("blind", None, invalid_recheck=True)
    (fixture.out / "turns").chmod(0o700)

    session = tools.Session(fixture.block)
    sample = _Evidence(directory, fixture, policy, tables, {})
    for identifier in _PLAYED:
        native_path = sample.native_turn(identifier)
        native_path.chmod(0o600)
        native = _json(native_path)
        view = sample.views[identifier] = session.view(identifier)
        selection = _PRIVATE_PICK if identifier == _PEER_DECIDE else _PICK
        result = tools.tool_result(view, policy, selection)
        file = sample.action(identifier)
        _write(
            file,
            {
                "schema": "seatlab.calendar-tool-turn/v1",
                "run": file.stem,
                "native_id": identifier,
                "seat": native["seat"],
                "base_sha256": native["prompt_sha256"],
                "select_prompt_sha256": tools.digest(tools.select_prompt(view)),
                "select_text": selection,
                "select_response_sha256": tools.digest(selection),
                "tool_status": result["status"],
                "tool_result_sha256": tools.result_digest(result),
                "final_prompt_sha256": tools.digest(tools.final_prompt(view, result)),
                "final_response_sha256": native["response_sha256"],
            },
        )
        session.accept(identifier, native["response_text"])
    return sample


def test_binds_real_native_turns_without_inventing_an_omitted_decision(
    evidence: _Evidence,
) -> None:
    report = evidence.read()
    native = report["native"]
    assert native is not None
    assert native["arms"]["blind"]["status"] == "model_invalid_scored_zero"
    assert report["protocol_status"] == "complete"
    actions = report["actions"]
    assert set(actions) == set(_PLAYED)
    assert all(row["status"] == "bound" for row in actions.values())
    assert actions[_SEED1]["exact_local_list"] is True
    assert actions[_BLIND_RECHECK]["exact_local_list"] is False
    assert actions[_PEER_DECIDE]["exact_local_list"] is None
    assert actions[_PEER_DECIDE]["selected_slot"] == 0
    assert actions[_PEER_DECIDE]["tool_status"] == "invalid"


@pytest.mark.parametrize("tamper", ["other_calendar", "other_seat", "blind_prompt"])
def test_rejects_wrong_own_or_peer_binding(evidence: _Evidence, tamper: str) -> None:
    path = evidence.action(_PEER_RECHECK)
    row = _json(path)
    view = evidence.views[_PEER_RECHECK]
    if tamper == "other_calendar":
        result: tools.ToolResult = {
            "status": "returned",
            "starts": list(packet.starts(evidence.tables[0])),
        }
        row["tool_result_sha256"] = tools.result_digest(result)
        row["final_prompt_sha256"] = tools.digest(tools.final_prompt(view, result))
    elif tamper == "other_seat":
        row["seat"] = "p1"
    else:
        blind = _json(evidence.action(_BLIND_RECHECK))
        for key in ("base_sha256", "select_prompt_sha256", "final_prompt_sha256"):
            row[key] = blind[key]
    _write(path, row)
    report = evidence.read()
    assert report["protocol_status"] == "invalid"
    assert report["actions"][_PEER_RECHECK]["status"] == "invalid"
    assert report["actions"][_PEER_DECIDE]["status"] == "bound"


@pytest.mark.parametrize("evidence", ["off"], indirect=True)
def test_rejects_a_returned_tool_mislabeled_as_off(evidence: _Evidence) -> None:
    assert evidence.read()["actions"][_SEED1]["tool_status"] == "unavailable"
    view = evidence.views[_SEED1]
    on = tools.tool_result(view, "on", _PICK)
    path = evidence.action(_SEED1)
    row = _json(path)
    row.update(
        tool_status=on["status"],
        tool_result_sha256=tools.result_digest(on),
        final_prompt_sha256=tools.digest(tools.final_prompt(view, on)),
    )
    _write(path, row)
    report = evidence.read()
    assert report["protocol_status"] == "invalid"
    assert report["actions"][_SEED1]["status"] == "invalid"


def test_missing_tool_record_preserves_verified_zero_and_redacts_text(
    evidence: _Evidence,
) -> None:
    evidence.action(_SEED1).unlink()
    report = evidence.read()
    native = report["native"]
    assert native is not None
    assert native["arms"]["peer"]["reward"] == 0
    assert report["protocol_status"] == "missing"
    assert report["actions"][_SEED1]["status"] == "missing"
    assert report["actions"][_PEER_DECIDE]["status"] == "bound"
    public = json.dumps(report)
    for text in (
        _PRIVATE_PICK,
        "private invalid-recheck response",
        str(evidence.stock.source),
        "select_text",
        "response_text",
    ):
        assert text not in public


def test_missing_native_prefix_cannot_prove_a_zero_or_bind_later_turns(
    evidence: _Evidence,
) -> None:
    evidence.native_turn(_PEER_DECIDE).unlink()
    report = evidence.read()
    native = report["native"]
    assert native is not None
    assert native["arms"]["peer"]["reward"] is None
    assert report["protocol_status"] == "invalid"
    assert report["actions"][_PEER_RECHECK]["status"] == "bound"
    assert report["actions"][_PEER_DECIDE]["status"] == "invalid"
    assert report["actions"][_BLIND_RECHECK]["status"] == "invalid"


@pytest.mark.parametrize("field", ["prompt", "response"])
def test_rejects_swapped_phase_digests(evidence: _Evidence, field: str) -> None:
    path = evidence.action(_PEER_RECHECK)
    row = _json(path)
    select, final = f"select_{field}_sha256", f"final_{field}_sha256"
    row[select], row[final] = row[final], row[select]
    _write(path, row)
    report = evidence.read()
    assert report["protocol_status"] == "invalid"
    assert report["actions"][_PEER_RECHECK]["status"] == "invalid"


def test_duplicate_key_is_invalid_even_when_both_values_match(
    evidence: _Evidence,
) -> None:
    path = evidence.action(_SEED1)
    text = path.read_text(encoding="utf-8")
    path.write_text(f'{{"run":"{path.stem}",{text[1:]}', encoding="utf-8")
    report = evidence.read()
    assert report["protocol_status"] == "invalid"
    assert report["actions"][_SEED1]["status"] == "invalid"


def test_receipt_for_omitted_decision_is_stray(evidence: _Evidence) -> None:
    _write(evidence.action(_BLIND_DECIDE), _json(evidence.action(_PEER_DECIDE)))
    report = evidence.read()
    assert report["protocol_status"] == "invalid"
    assert report["actions"][_BLIND_DECIDE]["status"] == "invalid"
