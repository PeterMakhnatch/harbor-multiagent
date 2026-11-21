import json
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.attention.protocol import aliases
from examples.seatlab.decision_cross import audit, inputs
from examples.seatlab.turn_fork import packet, protocol
from examples.seatlab.visibility.clarify_run import _encode

_MODEL = "openai-codex/gpt-6.2-sol"
_DIRECT_USAGE = {
    "served_model": "deepseek-v4.1-flash-1017",
    "input_tokens": 13,
    "cache_tokens": 4,
    "output_tokens": 2,
}
type _Document = dict[str, Any]


def _cohort(transport: str = "commands") -> tuple[_Document, _Document, str, _Document]:
    source_id = "s6501-r0"
    source: _Document = {
        "source_id": source_id,
        "seed": 6501,
        "repetition": 0,
        "trial_id": "00000000-0000-0000-0000-000000000001",
        "world_sha256": "a" * 64,
        "upstream": {
            role: {"prompt_sha256": "b" * 64, "response_sha256": "c" * 64}
            for role in ("inventory", "brief")
        },
        "card": "ABCDAB",
        "cases": [
            {
                "id": case_id,
                "utilities": {"A": 4, "B": 3, "C": 1, "D": 2},
                "reports": [
                    {"revision": 1, "available": ["A", "B"]},
                    {"revision": 2, "available": ["B", "D"]},
                ],
                "after": ["B", "D"],
            }
            for case_id in range(6)
        ],
    }
    sources: _Document = {
        "schema": packet.SCHEMA,
        "source_report_sha256": "d" * 64,
        "sources": [source],
    }
    opened = ("Q1", "Q3")
    prompt = sha256(protocol.decide_prompt(source, opened).encode()).hexdigest()
    obs_id = f"{source_id}-{prompt[:12]}"
    board: _Document = {
        "schema": "seatlab.decision-cross-board/v1",
        "packet_sha256": sha256(_encode(sources)).hexdigest(),
        "direct_report_sha256": "e" * 64,
        "commands_report_sha256": "f" * 64,
        "observations": [
            {
                "source_id": source_id,
                "obs_id": obs_id,
                "prompt_sha256": prompt,
                "opened": list(opened),
            }
        ],
        "selections": [
            {
                "source_id": f"s{seed}-r{rep}",
                "sender": sender,
                "arm": arm,
                "obs_id": obs_id if (seed, rep) == (6501, 0) else None,
                "sender_response_sha256": "1" * 64
                if (seed, rep) == (6501, 0)
                else None,
            }
            for seed in packet.SEEDS
            for rep in packet.REPETITIONS
            for sender in ("direct", "commands")
            for arm in ("aligned", "repeat")
        ],
    }
    digest = sha256(_encode(board)).hexdigest()
    choices = {
        alias: "ABCDAB"[case_id]
        for alias, case_id in aliases(source["cases"], 6501, 0).items()
    }
    text = json.dumps({"choices": choices}, indent=2)
    report: _Document = {
        "schema": "seatlab.decision-cross-report/v1",
        "board_sha256": digest,
        "transport": transport,
        "rows": [
            draw
            | {
                "run": inputs.run_id(digest, obs_id, draw["draw"]),
                "state": "complete",
                "turn": {
                    "status": "received",
                    "prompt_sha256": prompt,
                    "response_sha256": sha256(text.encode()).hexdigest(),
                    "usage": dict(_DIRECT_USAGE) if transport == "direct" else None,
                },
                "outcome": protocol.grade(source, opened, text),
            }
            for draw in inputs.draws(board)
        ],
    }
    return sources, board, digest, report


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("draw",), False),
        (("turn", "text"), "private"),
        (("turn", "prompt_sha256"), "0" * 64),
        (("turn", "response_sha256"), "Z" * 64),
        (("turn", "usage"), _DIRECT_USAGE),
        (("outcome", "actions", 0, "id"), False),
        (("outcome", "actions", 0, "choice"), "D"),
        (("outcome", "actions", 0, "optimal"), 0),
        (("outcome", "actions", 0, "feasible"), True),
        (("outcome", "optimal"), 7),
        (("outcome", "opened_sensitive"), 2.0),
    ],
)
def test_verify_rejects_forged_public_fields(
    path: tuple[str | int, ...], replacement: object
) -> None:
    sources, board, digest, report = _cohort()
    assert audit.verify(sources, board, digest, report) is report
    target = report["rows"][0]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    with pytest.raises(ValueError, match="^invalid_decision_cross_report$"):
        audit.verify(sources, board, digest, report)


def test_verify_preserves_missingness_but_requires_both_ordered_draws() -> None:
    sources, board, digest, report = _cohort()
    first, second = report["rows"]
    first.update(state="unclaimed", turn=None, outcome=None)
    second.update(state="incomplete", outcome=None)
    assert audit.verify(sources, board, digest, report) is report
    second["turn"].update(status="callback_error", response_sha256=None)
    assert audit.verify(sources, board, digest, report) is report
    second["turn"] = None
    assert audit.verify(sources, board, digest, report) is report
    for rows in ([first], [first, first], [second, first]):
        with pytest.raises(ValueError, match="^invalid_decision_cross_report$"):
            audit.verify(sources, board, digest, report | {"rows": rows})
    second["state"] = "complete"
    with pytest.raises(ValueError, match="^invalid_decision_cross_report$"):
        audit.verify(sources, board, digest, report)


def test_verify_regenerates_prompts_for_unclaimed_draws() -> None:
    sources, board, digest, report = _cohort()
    for row in report["rows"]:
        row.update(state="unclaimed", turn=None, outcome=None)
    sources["sources"][0]["cases"][0]["reports"][0]["available"] = ["C", "D"]
    with pytest.raises(ValueError, match="^invalid_decision_cross_report$"):
        audit.verify(sources, board, digest, report)


def test_service_consumes_one_real_receipt_per_identical_draw(tmp_path: Path) -> None:
    sources, board, digest, report = _cohort()
    audit.verify(sources, board, digest, report)
    receipts = tmp_path.resolve() / "dispatch"
    receipts.mkdir(mode=0o700)
    turn = report["rows"][0]["turn"]
    receipt = {
        "schema": "seatlab.omp-seat-receipt/v1",
        "prompt_sha256": turn["prompt_sha256"],
        "reply_sha256": turn["response_sha256"],
        "served_model": _MODEL,
        "usage": {
            "prompt_tokens": 11,
            "cached_tokens": 3,
            "completion_tokens": 4,
            "total_tokens": 15,
        },
    }
    first = receipts / "first.json"
    second = receipts / "second.json"
    first.write_bytes(_encode(receipt))
    first.chmod(0o600)
    with pytest.raises(ValueError, match="^invalid_decision_cross_service_evidence$"):
        audit.service(report, receipts, _MODEL)
    second.hardlink_to(first)
    with pytest.raises(ValueError, match="^invalid_decision_cross_service_evidence$"):
        audit.service(report, receipts, _MODEL)
    second.unlink()
    second.write_bytes(_encode(receipt))
    second.chmod(0o600)
    observed = audit.service(report, receipts, _MODEL)
    assert observed["verified_served_model"] == _MODEL
    assert observed["roles"] == {
        "dispatch": {
            "matched": 2,
            "callback_errors": 0,
            "input_tokens": 22,
            "cache_tokens": 6,
            "output_tokens": 8,
        }
    }


def _ledger(*calls: _Document) -> _Document:
    low = sum(Decimal(call["estimated_usd_low"] or "0") for call in calls)
    high = sum(
        Decimal(call["estimated_usd_high"] or call["unsettled_hold_usd"])
        for call in calls
    )
    return {
        "schema": "seatlab.direct-flash-cost/v1",
        "currency": "USD",
        "price_source": "https://api-docs.deepseek.com/quick_start/pricing",
        "known_usd_low": f"{low:.9f}",
        "upper_usd_including_holds": f"{high:.9f}",
        "unsettled_calls": sum(call["estimated_usd_high"] is None for call in calls),
        "calls": list(calls),
    }


@pytest.mark.parametrize("status", ["ledger_only", "unsettled"])
def test_meter_joins_exact_runs_and_preserves_missing_call_costs(status: str) -> None:
    sources, board, digest, report = _cohort("direct")
    first, second = report["rows"]
    second.update(state="incomplete", turn=None, outcome=None)
    audit.verify(sources, board, digest, report)
    settled: _Document = {
        "run": first["run"],
        "role": "dispatch",
        "served_model": _DIRECT_USAGE["served_model"],
        "cache_hit": 4,
        "cache_miss": 9,
        "output": 2,
        "estimated_usd_low": "0.000002562",
        "estimated_usd_high": "0.000005124",
        "unsettled_hold_usd": "0.000000000",
    }
    missing = settled | {"run": second["run"]}
    if status == "unsettled":
        missing.update(
            served_model=None,
            cache_hit=None,
            cache_miss=None,
            output=None,
            estimated_usd_low=None,
            estimated_usd_high=None,
            unsettled_hold_usd="0.010000000",
        )
    observed = audit.meter(report, _ledger(settled, missing))
    assert observed["calls"] == [
        settled | {"status": "matched"},
        missing | {"status": status},
    ]
    assert observed["matched_calls"] == 1
    assert observed[f"{status}_calls"] == 1
    assert observed["known_usd_high"] == (
        "0.000010248" if status == "ledger_only" else "0.000005124"
    )
    assert observed["upper_usd_including_holds"] == (
        "0.000010248" if status == "ledger_only" else "0.010005124"
    )
    for ledger in (
        _ledger(missing),
        _ledger(settled, settled),
        _ledger(settled | {"cache_hit": 3, "cache_miss": 10}),
        _ledger(settled | {"role": "inventory"}),
        _ledger(settled, missing | {"run": f"{second['run']}-extra"}),
    ):
        with pytest.raises(ValueError, match="^invalid_decision_cross_ledger$"):
            audit.meter(report, ledger)
