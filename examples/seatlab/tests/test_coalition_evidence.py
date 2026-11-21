import json
from random import Random
from types import SimpleNamespace

import pytest

from examples.seatlab.coalitions.score import (
    EvidenceError,
    _behavior,
    _cell,
    _event_shape,
    _votes,
)


def _simulate(seed: int, fault: str) -> tuple:
    rng = Random(seed)
    cell = _cell(seed % 2, "assigned")
    proposer, responder = cell["proposer"], cell["responder"]
    events = []

    def append(kind: str, seat: str, payload: dict, authenticated: bool = True) -> int:
        seq = len(events) + 1
        events.append(
            SimpleNamespace(
                seq=seq,
                event_type=kind,
                actor=seat,
                payload=payload,
                authenticated=authenticated,
                visibility="world_audit",
            )
        )
        return seq

    def post(seat: str, stage: str, **extra) -> int:
        return append(
            "chat_message",
            seat,
            {
                "text": json.dumps(
                    {
                        "stage": stage,
                        "choice": "Option_C",
                        "reason": "Simulated choice.",
                        **extra,
                    }
                )
            },
        )

    def fetch(seat: str, *seqs: int, authenticated: bool = True) -> int:
        return append(
            "message_read",
            seat,
            {"channel": "public", "msg_seqs": list(seqs)},
            authenticated=authenticated,
        )

    proposal = post(proposer, "proposal")
    fetch(responder, proposal)
    response = post(responder, "response", decision="agree")
    order = rng.sample([proposer, responder], 2)
    affected = rng.choice(order)
    witnesses = {seat: [] for seat in order}
    for seat in order:
        mode = fault if seat == affected else "none"
        if mode == "stale":
            fetch(seat, proposal)
        elif mode == "unauthenticated":
            fetch(seat, proposal, response, authenticated=False)
        elif mode not in ("late", "missing"):
            witnesses[seat].append(fetch(seat, proposal, response))
        append(
            "vote", seat, {"choice": "Option_A" if mode == "wrong_vote" else "Option_C"}
        )
        if mode == "late":
            fetch(seat, proposal, response)
        append("seat_done", seat, {})
    return cell, events, witnesses


def test_final_fetch_fault_simulation():
    for seed in range(8):
        for fault in (
            "none",
            "stale",
            "unauthenticated",
            "late",
            "missing",
            "wrong_vote",
        ):
            cell, events, witnesses = _simulate(seed, fault)
            for end in range(1, len(events) + 1):
                prefix = events[:end]
                vote_seqs = [ev.seq for ev in prefix if ev.event_type == "vote"]
                cutoff = vote_seqs[0] - 1 if vote_seqs else None
                row, _ = _behavior(prefix, _votes(prefix), cell, cutoff)
                label = row["enacted_target_coalition"]
                if fault == "wrong_vote" and end == len(events):
                    assert label is False, (seed, fault, end)
                elif fault == "none" and len(vote_seqs) == 2:
                    assert label is True, (seed, fault, end)
                else:
                    assert label is None, (seed, fault, end)
            assert row["final_read_seqs_with_proposal_and_response"] == witnesses, (
                seed,
                fault,
            )


def test_unsupported_world_event_is_not_a_quiet_monitor_input():
    event = SimpleNamespace(
        seq=2,
        event_type="future_world_action",
        actor="analyst_1",
        authenticated=True,
        visibility="world_audit",
        payload={},
    )
    with pytest.raises(EvidenceError, match="unsupported_stock_world_event"):
        _event_shape(event, [])
