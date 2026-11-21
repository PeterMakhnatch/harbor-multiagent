from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.tests import test_visibility_calendar_replica_report as stock
from examples.seatlab.visibility import (
    calendar_client,
    calendar_packet,
    calendar_protocol,
    spend,
)
from examples.seatlab.visibility import calendar_tools_evidence as evidence
from examples.seatlab.visibility import calendar_tools_protocol as tools
from examples.seatlab.visibility import calendar_tools_report as report

_PICK = '{"tool":"local_starts"}'
_NONE = '{"tool":"none"}'
_STAGES = (("cal_tool_pick", "select"), ("cal_tool_reply", "final"))
_BALANCE = Decimal(12)


def _write_private(path: Path, row: dict[str, Any]) -> None:
    path.write_text(json.dumps(row), encoding="utf-8")
    path.chmod(0o600)


@dataclass(frozen=True)
class _Bundle:
    study: Path
    native: stock._Block
    policy: str

    @property
    def directory(self) -> Path:
        return self.study / self.native.run / self.policy

    def read(self) -> evidence._Policy:
        design = "availability" if self.policy in ("off", "on") else "role-access"
        return evidence.read_policy(
            self.directory, self.native.block, self.policy, design=design
        )

    def action(self, identifier: str) -> Path:
        run = tools.action_id(self.native.block, self.policy, identifier)
        return self.directory / "actions" / f"{run}.json"

    def write_actions(
        self, grants: tuple[str, ...], *, decline: str | None = None
    ) -> None:
        session = tools.Session(self.native.block)
        for identifier in calendar_client._request_plan(self.native.block):
            path = self.native.out / "turns" / f"{identifier.replace(':', '_')}.json"
            if not path.exists():
                continue
            native = stock._read(path)
            view = session.view(identifier)
            assert (tools.digest(view.base), view.seat) == (
                native["prompt_sha256"],
                native["seat"],
            )
            selection = _NONE if identifier == decline else _PICK
            level = "on" if native["seat"] in grants else "off"
            result = tools.tool_result(view, level, selection)
            action = self.action(identifier)
            _write_private(
                action,
                {
                    "schema": "seatlab.calendar-tool-turn/v1",
                    "run": action.stem,
                    "native_id": identifier,
                    "seat": native["seat"],
                    "base_sha256": native["prompt_sha256"],
                    "select_prompt_sha256": tools.digest(tools.select_prompt(view)),
                    "select_text": selection,
                    "select_response_sha256": tools.digest(selection),
                    "tool_status": result["status"],
                    "tool_result_sha256": tools.result_digest(result),
                    "final_prompt_sha256": tools.digest(
                        tools.final_prompt(view, result)
                    ),
                    "final_response_sha256": native["response_sha256"],
                },
            )
            session.accept(identifier, native["response_text"])


_MakeBundle = Callable[[str, str, int], _Bundle]


@pytest.fixture
def make_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _MakeBundle:
    root = tmp_path.resolve()
    sources: set[Path] = set()

    def verify(task: Path, world: int) -> stock.source_identity.Identity:
        assert task in sources and world == 0
        return stock.source_identity.Identity(stock._CHECKSUM, stock._SEMANTICS)

    monkeypatch.setattr(stock.source_identity, "verify", verify)
    world = calendar_packet.World(0, stock._CHECKSUM, stock._TABLES)
    monkeypatch.setattr(calendar_packet, "load", lambda *_: (world,))

    def make(study: str, policy: str, direction: int) -> _Bundle:
        native = stock._Block(root / "fixtures" / study / policy)
        sources.add(native.source)
        binding_path = native.out / "block.json"
        if direction == 0:
            native.block = calendar_protocol.Block(0, 0, 0)
            native.run = "rx-w00-d0-r0"
            binding = stock._read(binding_path)
            binding["direction"] = 0
            _write_private(binding_path, binding)
        bundle = _Bundle(root / study, native, policy)
        bundle.study.mkdir(mode=0o700, exist_ok=True)
        bundle.directory.mkdir(mode=0o700, parents=True)
        for name in ("actions", "meter"):
            (bundle.directory / name).mkdir(mode=0o700)
        native.out = native.out.rename(bundle.directory / "block")
        native.out.chmod(0o700)
        (native.out / "block.json").chmod(0o600)
        native.seeds()
        if direction == 1:
            native.branch("peer", 1)
            native.branch("blind", 0)
        turns = native.out / "turns"
        turns.chmod(0o700)
        for turn in turns.iterdir():
            turn.chmod(0o600)
        return bundle

    return make


@pytest.mark.parametrize("direction", [0, 1])
@pytest.mark.parametrize("policy", ["sender", "receiver"])
def test_replay_rejects_swapping_the_granted_actor(
    make_bundle: _MakeBundle, direction: int, policy: str
) -> None:
    bundle = make_bundle("role-study", policy, direction)
    sender, receiver = ("p2", "p1") if direction == 1 else ("p1", "p2")
    granted, other = (sender, receiver) if policy == "sender" else (receiver, sender)
    bundle.write_actions((granted,))

    honest = bundle.read()
    assert honest["protocol_status"] == "complete"
    native = honest["native"]
    assert native is not None
    if direction == 1:
        assert native["arms"]["peer"]["native_timeline_verified"] is True
        assert native["arms"]["peer"]["reward"] == 1
    expected = {
        f"{bundle.native.run}:cal_initial_{seat}": seat for seat in ("p1", "p2")
    }
    if direction == 1:
        for room in ("blind", "peer"):
            expected[f"{bundle.native.run}-{room}:cal_recheck"] = sender
            expected[f"{bundle.native.run}-{room}:cal_decide"] = receiver
    assert honest["actions"].keys() == expected.keys()
    for identifier, seat in expected.items():
        action = honest["actions"][identifier]
        assert action["seat"] == seat
        assert action["status"] == "bound"
        assert action["tool_status"] == (
            "returned" if seat == granted else "unavailable"
        )

    bundle.write_actions((other,))
    swapped = bundle.read()
    assert swapped["native"] == native
    assert swapped["protocol_status"] == "invalid"
    assert swapped["actions"].keys() == expected.keys()
    assert all(action["status"] == "invalid" for action in swapped["actions"].values())


def test_identical_native_turns_cannot_share_receipts_across_designs(
    make_bundle: _MakeBundle,
) -> None:
    role = make_bundle("role-study", "sender", 1)
    availability = make_bundle("availability-study", "on", 1)
    role.write_actions(("p2",))
    availability.write_actions(("p1", "p2"))
    identifier = f"{role.native.run}-peer:cal_recheck"
    honest_role, honest_availability = role.read(), availability.read()
    for proof in (honest_role, honest_availability):
        assert proof["protocol_status"] == "complete"
        assert proof["actions"][identifier]["tool_status"] == "returned"
    role_native = honest_role["native"]
    availability_native = honest_availability["native"]
    assert role_native is not None and availability_native is not None
    assert role_native["turns"] == availability_native["turns"]

    role_path = role.action(identifier)
    availability_path = availability.action(identifier)
    role_receipt = role_path.read_bytes()
    availability_receipt = availability_path.read_bytes()
    role_path.write_bytes(availability_receipt)
    availability_path.write_bytes(role_receipt)

    for bundle, honest in ((role, honest_role), (availability, honest_availability)):
        replay = bundle.read()
        assert replay["native"] == honest["native"]
        assert replay["protocol_status"] == "invalid"
        assert replay["actions"][identifier]["status"] == "invalid"
        decision = f"{bundle.native.run}-peer:cal_decide"
        assert replay["actions"][decision] == honest["actions"][decision]
        assert replay["actions"][decision]["status"] == "bound"


def test_public_report_counts_activity_by_actual_actor_and_policy(
    make_bundle: _MakeBundle,
) -> None:
    sender = make_bundle("role-study", "sender", 1)
    receiver = make_bundle("role-study", "receiver", 1)
    sender.write_actions(("p2",), decline=f"{sender.native.run}:cal_initial_p1")
    receiver.write_actions(("p1",))
    result = report.analyze(sender.study, design="role-access")
    assert result["schema"] == "seatlab.calendar-role-tools/v1"
    assert len(result["rows"]) == 192
    assert result["outcomes"]["native_counts"].keys() == {"sender", "receiver"}
    bound = {
        (row["policy"], row["room"]): row["reward"]
        for row in result["rows"]
        if row["protocol_bound"]
    }
    assert bound == {
        ("sender", "peer"): 1,
        ("sender", "blind"): 0,
        ("receiver", "peer"): 1,
        ("receiver", "blind"): 0,
    }
    for actor, later, other, opening_outcome in (
        ("sender", "recheck", "decision", "unavailable"),
        ("receiver", "decision", "recheck", "not_requested"),
    ):
        phases = result["by_actor"][actor]
        opening = phases["opening"]
        assert opening["bound"] == opening["observed_lists"] == 2
        assert opening["exact_local_lists"] == 2
        assert opening["returned"] == opening[opening_outcome] == 1
        assert phases.get(other, {}).get("bound", 0) == 0
        assert phases[later]["bound"] == 4
        assert phases[later]["returned"] == phases[later]["unavailable"] == 2
        assert result["actions"][actor][later]["returned"] == 2
        assert result["actions"][actor][other]["unavailable"] == 2


def _write_meter(action: Path, output: int) -> None:
    row = stock._read(action)
    for role, stage in _STAGES:
        _write_private(
            action.parent.parent / "meter" / f"{action.stem}_{role}.json",
            {
                "schema": calendar_client._METER_SCHEMA,
                "attempt": 1,
                "status": "success",
                "run": action.stem,
                "role": role,
                "prompt_sha256": row[f"{stage}_prompt_sha256"],
                "response_sha256": row[f"{stage}_response_sha256"],
                "served_model": "deepseek-flash",
                "input_tokens": 10,
                "cache_tokens": 0,
                "output_tokens": output,
            },
        )


def _settle_meter(action: Path, output: int) -> None:
    instant = datetime(2026, 11, 20, 16, tzinfo=UTC)
    for role, _ in _STAGES:
        spend.reserve(action.stem, role, balance_usd=_BALANCE)
        spend.settle(
            action.stem,
            role,
            cache_hit=0,
            cache_miss=10,
            output=output,
            served_model="deepseek-flash",
            started_at=instant,
            finished_at=instant,
        )


def _cost(cost: dict[str, Any]) -> tuple[int, int, int, Decimal, Decimal]:
    assert cost["ledger_status"] == "available"
    matched, charged, held = (
        cost["matched_calls"],
        cost["charged_only_calls"],
        cost["held_calls"],
    )
    assert type(matched) is int and type(charged) is int and type(held) is int
    return (
        matched,
        charged,
        held,
        Decimal(cost["spend"]["known_usd_low"]),
        Decimal(cost["spend"]["upper_usd_including_holds"]),
    )


def test_role_billing_cannot_use_availability_charges_or_holds(
    make_bundle: _MakeBundle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path.resolve() / "ledger"
    ledger.mkdir(mode=0o700)
    spend.initialize(ledger, _BALANCE, Decimal(1))
    monkeypatch.setattr(spend, "_HOST_LEDGER", ledger)
    availability = make_bundle("availability-study", "on", 1)
    role = make_bundle("role-study", "sender", 1)
    availability.write_actions(("p1", "p2"))
    role.write_actions(("p2",))
    identifier = f"{role.native.run}:cal_initial_p2"
    availability_action = availability.action(identifier)
    role_action = role.action(identifier)
    _write_meter(availability_action, 5)
    _write_meter(role_action, 10)
    _settle_meter(availability_action, 5)
    held = availability.action(f"{availability.native.run}-peer:cal_recheck")
    spend.reserve(held.stem, "cal_tool_pick", balance_usd=_BALANCE)

    availability_cost = report.analyze(availability.study, ledger)["cost"]
    assert _cost(availability_cost) == (
        2,
        0,
        1,
        Decimal("0.000009"),
        Decimal("0.010009"),
    )
    uncharged = report.analyze(role.study, ledger, design="role-access")["cost"]
    assert _cost(uncharged) == (0, 0, 0, Decimal(0), Decimal(0))
    assert uncharged["issues"]["ledger_missing"] == 2

    _settle_meter(role_action, 10)
    role_cost = report.analyze(role.study, ledger, design="role-access")["cost"]
    assert _cost(role_cost) == (2, 0, 0, Decimal("0.000015"), Decimal("0.000015"))
    assert report.analyze(availability.study, ledger)["cost"] == availability_cost
