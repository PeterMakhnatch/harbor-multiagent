from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.tests import test_visibility_calendar_replica_report as stock
from examples.seatlab.visibility import (
    calendar_client,
    calendar_query_report,
    calendar_query_run,
)
from examples.seatlab.visibility import calendar_tools_protocol as tools

_EMPTY_ARMS = {
    arm: {"reward": None, "slot": None, "trial_id_sha256": None}
    for arm in ("blind", "peer")
}


@dataclass(frozen=True)
class _Bundle:
    study: Path
    native: stock._Block
    turns: list[dict[str, str]]

    def action(self, identifier: str) -> Path:
        return (
            self.study
            / self.native.run
            / "actions"
            / (identifier.replace(":", "_") + ".json")
        )

    def read(self) -> dict[str, Any]:
        return calendar_query_report.read_block(self.study, self.native.block)


def _bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, invalid_seed: bool = False
) -> _Bundle:
    root = tmp_path.resolve()
    study = root / "study"
    study.mkdir(mode=0o700)
    native = stock._Block(root / "stock")
    directory = study / native.run
    directory.mkdir(mode=0o700)
    (directory / "actions").mkdir(mode=0o700)
    native.out = native.out.rename(directory / "block")
    native.out.chmod(0o700)
    native.seeds(first="private invalid seed" if invalid_seed else None)
    if not invalid_seed:
        native.branch("peer", 1)
        native.branch("blind", None, invalid_recheck=True)
    (native.out / "turns").chmod(0o700)

    def verify(task: Path, world: int) -> stock.source_identity.Identity:
        assert (task, world) == (native.source, 0)
        return stock.source_identity.Identity(stock._CHECKSUM, stock._SEMANTICS)

    monkeypatch.setattr(stock.source_identity, "verify", verify)
    monkeypatch.setattr(
        stock.packet,
        "load",
        lambda *_: (stock.packet.World(0, stock._CHECKSUM, stock._TABLES),),
    )
    monkeypatch.setattr(calendar_query_run, "blocks", lambda: (native.block,))
    bundle = _Bundle(study, native, [])
    session = tools.Session(native.block)
    for identifier in calendar_client._request_plan(native.block):
        path = native.out / "turns" / bundle.action(identifier).name
        if not path.exists():
            continue
        path.chmod(0o600)
        reply = stock._read(path)
        view = session.view(identifier)
        turn = {
            "id": identifier,
            "role": "receiver" if view.seat == native.block.receiver else "sender",
            "prompt_sha256": tools.digest(
                calendar_query_run.model_prompt(view.role, view.base)
            ),
            "response_sha256": reply["response_sha256"],
        }
        target = bundle.action(identifier)
        stock._write(
            target,
            {
                **turn,
                "schema": "seatlab.calendar-query-turn/v1",
                "seat": reply["seat"],
                "base_sha256": reply["prompt_sha256"],
            },
        )
        target.chmod(0o600)
        bundle.turns.append(turn)
        session.accept(identifier, reply["response_text"])
    return bundle


@pytest.fixture
def complete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Bundle:
    return _bundle(tmp_path, monkeypatch)


def _assert_withheld(row: dict[str, Any], status: str) -> None:
    assert row == {
        "world": row["world"],
        "direction": row["direction"],
        "repetition": 0,
        "status": status,
        "receiver": row["receiver"],
        "starts": {"p1": None, "p2": None},
        "revised": {"blind": None, "peer": None},
        "arms": _EMPTY_ARMS,
        "turns": [],
    }


def test_missing_study_and_missing_run(tmp_path: Path) -> None:
    study = tmp_path.resolve() / "study"
    block = calendar_query_run.blocks()[0]
    _assert_withheld(calendar_query_report.read_block(study, block), "not_started")
    study.mkdir(mode=0o700)
    _assert_withheld(calendar_query_report.read_block(study, block), "not_started")


def test_extracts_native_win_and_scored_invalid_reply(complete: _Bundle) -> None:
    native = complete.native
    trials = native.out / "native"
    digests = {
        arm: tools.digest(
            stock._read(trials / f"{native.run}-{arm}" / "result.json")["id"]
        )
        for arm in ("blind", "peer")
    }
    assert complete.read() == {
        "world": 0,
        "direction": 1,
        "repetition": 0,
        "status": "complete",
        "receiver": "p1",
        "starts": {"p1": [0, 1, 2], "p2": [1]},
        "revised": {"blind": None, "peer": [1]},
        "arms": {
            "blind": {"reward": 0, "slot": None, "trial_id_sha256": digests["blind"]},
            "peer": {"reward": 1, "slot": 1, "trial_id_sha256": digests["peer"]},
        },
        "turns": complete.turns,
    }


def test_invalid_seed_keeps_both_opening_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path, monkeypatch, invalid_seed=True)
    row = bundle.read()
    assert row["status"] == "invalid_seed"
    assert row["starts"] == {"p1": None, "p2": [1]}
    assert row["arms"] == _EMPTY_ARMS
    assert row["turns"] == bundle.turns
    assert len(row["turns"]) == 2


def test_withholds_both_rewards_without_every_action(complete: _Bundle) -> None:
    complete.action(complete.turns[-1]["id"]).unlink()
    _assert_withheld(complete.read(), "partial")


def test_partial_native_trial_cannot_count_a_pair(complete: _Bundle) -> None:
    native = complete.native
    (native.out / "native" / f"{native.run}-blind" / "result.json").unlink()
    row = complete.read()
    assert row["status"] == "partial"
    assert row["turns"] == complete.turns
    assert row["arms"] == _EMPTY_ARMS


@pytest.mark.parametrize(
    "field", ["seat", "role", "base_sha256", "prompt_sha256", "response_sha256"]
)
def test_rejects_tampered_action(complete: _Bundle, field: str) -> None:
    path = complete.action(complete.turns[0]["id"])
    receipt = stock._read(path)
    receipt[field] = receipt["base_sha256"] if field == "prompt_sha256" else "wrong"
    stock._write(path, receipt)
    _assert_withheld(complete.read(), "invalid_evidence")


def test_rejects_receipt_for_skipped_decision(complete: _Bundle) -> None:
    path = complete.action(f"{complete.native.run}-blind:cal_decide")
    path.write_bytes(complete.action(complete.turns[-1]["id"]).read_bytes())
    path.chmod(0o600)
    _assert_withheld(complete.read(), "invalid_evidence")


def test_rejects_link_even_when_receipt_matches(complete: _Bundle) -> None:
    path = complete.action(complete.turns[0]["id"])
    target = path.rename(complete.study / "target.json")
    path.symlink_to(target)
    _assert_withheld(complete.read(), "invalid_evidence")


def test_stock_verifier_conflict_withholds_both_arms(complete: _Bundle) -> None:
    native = complete.native
    peer = native.out / "native" / f"{native.run}-peer"
    stock._write(peer / "verifier" / "reward.json", {"reward": 0})
    _assert_withheld(complete.read(), "invalid_evidence")
