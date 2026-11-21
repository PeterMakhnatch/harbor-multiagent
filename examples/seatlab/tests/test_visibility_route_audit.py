"""Reconstruction tests on the published observations; no provider or native services."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from examples.seatlab.visibility import (
    route_audit,
    route_cli,
    route_packet,
    route_protocol,
    route_run,
    spend,
)


@pytest.fixture
def study(tmp_path):
    root = (tmp_path / "receipts").resolve()
    root.mkdir(mode=0o700)
    digest = route_cli._COMMITMENT.read_text().strip()
    cases = route_packet.load(route_cli._PUBLIC, digest)
    block = next(
        b
        for b in route_protocol.blocks()
        if cases[b.policy * 6 + b.round - 1].reports is not None
    )
    return root, digest, block


def _read(study, **kwargs):
    root, digest, block = study
    all_data = route_audit.collect(route_cli._PUBLIC, digest, root, **kwargs)
    rows = {
        (r["lens"], r["channel"]): r
        for r in all_data["decisions"]
        if (r["policy"], r["round"], r["rotation"], r["swap"], r["rep"])
        == (block.policy, block.round, block.rotation, block.swap, block.rep)
    }
    return rows, all_data["meter"]


def test_corrected_reviewer_numbers_break_only_numeric_child(study):
    root, digest, block = study
    review = '{"vote":"B","a":[5,11],"b":[8,14]}'
    route_run.play_block(
        route_cli._PUBLIC,
        digest,
        root,
        block,
        reviewer=lambda _: review,
        decider=lambda _: '{"choice":"A"}',
    )
    filename = root / block.id / "duplicate-review.json"
    record = json.loads(filename.read_bytes())
    record["text"] = review.replace("[5,11]", "[6,11]")
    record["response_sha256"] = hashlib.sha256(record["text"].encode()).hexdigest()
    filename.write_text(json.dumps(record))
    rows, meter = _read(study)
    assert rows["duplicate", "vote"]["status"] == "valid"
    assert rows["duplicate", "evidence"]["status"] == "invalid_evidence"
    assert rows["complementary", "evidence"]["label"] == "A"
    assert meter["direct_ledger_supplied"] is False
    assert meter["known_usd_low"] is None


def test_invalid_reviewer_cannot_donate_a_decision_from_another_branch(study):
    root, digest, block = study
    calls = iter(["{}", '{"vote":"B","a":[5,11],"b":[8,14]}'])
    route_run.play_block(
        route_cli._PUBLIC,
        digest,
        root,
        block,
        reviewer=lambda _: next(calls),
        decider=lambda _: '{"choice":"B"}',
    )
    empty, valid = route_protocol.lenses(block)
    src = root / block.id / f"{valid}-vote.json"
    dst = root / block.id / f"{empty}-vote.json"
    dst.write_bytes(src.read_bytes())
    dst.chmod(0o600)
    rows, _ = _read(study)
    assert rows[empty, "vote"]["status"] == "unbound_child"
    assert rows[empty, "evidence"]["status"] == "invalid_review"
    assert rows[valid, "vote"]["label"] == "B"


def test_mutating_usage_does_not_rewrite_an_observed_decision(study, monkeypatch):
    root, digest, block = study
    ledger = root.parent / "accounting"
    ledger.mkdir(mode=0o700)
    spend.initialize(ledger, Decimal("4"), Decimal("0.50"))
    monkeypatch.setattr(spend, "_HOST_LEDGER", ledger)

    def measured(prompt, run, role):
        now = datetime.now(UTC)
        spend.reserve(run, role, balance_usd=Decimal("4"))
        spend.settle(
            run,
            role,
            cache_hit=2,
            cache_miss=5,
            output=8,
            served_model="deepseek-flash",
            started_at=now,
            finished_at=now,
        )
        text = (
            '{"vote":"B","a":[5,11],"b":[8,14]}'
            if role == "route_review"
            else '{"choice":"B"}'
        )
        return route_run.Answer(text, "deepseek-flash", 7, 2, 8)

    route_run._play(route_cli._PUBLIC, digest, root, block, measured)
    filename = root / block.id / "duplicate-vote.json"
    record = json.loads(filename.read_bytes())
    record["usage"]["input_tokens"] += 1
    filename.write_text(json.dumps(record))
    rows, meter = _read(study, spend_dir=ledger)
    assert all(row["label"] == "B" for row in rows.values())
    assert meter["by_match"]["matched"] == 5
    assert meter["by_match"]["mismatch"] == 1
    assert Decimal(meter["upper_usd_including_holds"]) > 0
