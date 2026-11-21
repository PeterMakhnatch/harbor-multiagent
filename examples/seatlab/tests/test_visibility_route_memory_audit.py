from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from examples.seatlab.visibility import (
    route_cli,
    route_memory,
    route_memory_audit,
    route_memory_run,
    route_packet,
    route_protocol,
    route_run,
    spend,
)

_REVIEW = '{"vote":"B","a":[5,11],"b":[8,14]}'
_FINAL = '{"choice":"A"}'
_Study = tuple[Path, str, route_protocol.Block]


@pytest.fixture
def study(tmp_path: Path) -> _Study:
    root = (tmp_path / "receipts").resolve()
    root.mkdir(mode=0o700)
    digest = route_cli._COMMITMENT.read_text(encoding="ascii").strip()
    cases = route_packet.load(route_cli._PUBLIC, digest)
    block = next(b for b in route_memory.blocks() if route_memory.menu(cases, b))
    return root, digest, block


def _read(study: _Study, *, spend_dir: Path | None = None):
    root, digest, block = study
    report = route_memory_audit.collect(
        route_cli._PUBLIC, digest, root, spend_dir=spend_dir
    )
    rows = {
        r["arm"]: r
        for r in report["decisions"]
        if (r["policy"], r["round"], r["rotation"], r["swap"], r["rep"])
        == (block.policy, block.round, block.rotation, block.swap, block.rep)
    }
    turns = {t["run"]: t for t in report["turns"]}
    return (
        report,
        rows,
        {
            stage: turns[route_memory.run_id(block, stage)]
            for stage in route_memory.STAGES
        },
    )


def _play_shared(study: _Study) -> None:
    root, digest, block = study
    selections_and_final = iter(['{"ask":"X"}', '{"ask":"X"}', _FINAL])
    reviews = iter([_REVIEW])
    route_memory_run.play_block(
        route_cli._PUBLIC,
        digest,
        root,
        block,
        reviewer=lambda _: next(reviews),
        decider=lambda _: next(selections_and_final),
    )


def test_shared_choice_has_two_outcome_rows_and_one_final_turn(study: _Study) -> None:
    _play_shared(study)
    report, rows, turns = _read(study)
    assert len(report["decisions"]) == 288
    assert len(report["turns"]) == 864
    assert {
        arm: (r["status"], r["selected"], r["label"]) for arm, r in rows.items()
    } == {
        "bound": ("valid", "X", "A"),
        "anonymous": ("valid", "X", "A"),
    }
    assert report["meter"]["by_status"]["received"] == 4
    assert turns["x-decide"]["status"] == "received"
    assert turns["x-decide"]["response_sha256"] is not None
    assert turns["x-decide"]["meter"] == "no_ledger_supplied"
    assert turns["y-review"]["status"] == turns["y-decide"]["status"] == "not_requested"
    assert report["meter"]["known_usd_low"] is None
    assert report["meter"]["upper_usd_including_holds"] is None
    # No selector ever ran in the other blocks. Their branches are unknown.
    assert report["meter"]["by_status"]["not_requested"] == 2
    assert report["meter"]["by_status"]["missing"] == 6 * 143


def test_unselected_artifact_invalidates_both_outcomes(study: _Study) -> None:
    _play_shared(study)
    root, _, block = study
    directory = root / route_memory.key(block)
    foreign = directory / "y-decide.json"
    foreign.write_bytes((directory / "x-decide.json").read_bytes())
    foreign.chmod(0o600)

    report, rows, turns = _read(study)
    assert report["claims"]["invalid_evidence"] == 1
    assert all(row["status"] == "invalid_evidence" for row in rows.values())
    assert all(
        row["selected"] is None and row["label"] is None for row in rows.values()
    )
    assert turns["y-decide"]["status"] == "unbound_child"
    assert turns["y-decide"]["response_sha256"] is None


def test_missing_or_foreign_final_preserves_other_branch_and_costs(
    study: _Study, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, digest, block = study
    ledger = root.parent / "accounting"
    ledger.mkdir(mode=0o700)
    spend.initialize(ledger, Decimal("4"), Decimal("0.50"))
    monkeypatch.setattr(spend, "_HOST_LEDGER", ledger)
    now = datetime(2026, 1, 4, tzinfo=UTC)

    def measured(_prompt: str, run: str, role: str) -> route_run.Answer:
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
        if role == "route_review":
            text = _REVIEW
        elif run == route_memory.run_id(block, "bound"):
            text = '{"ask":"X"}'
        elif run == route_memory.run_id(block, "anonymous"):
            text = '{"ask":"Y"}'
        else:
            text = _FINAL
        return route_run.Answer(text, "deepseek-flash", 7, 2, 8)

    route_memory_run._play(route_cli._PUBLIC, digest, root, block, measured)
    unclaimed = next(b for b in route_memory.blocks() if b != block)
    spend.reserve(
        route_memory.run_id(unclaimed, "x-review"),
        "route_review",
        balance_usd=Decimal("4"),
    )
    directory = root / route_memory.key(block)
    missing = directory / "x-decide.json"
    missing.unlink()

    report, rows, turns = _read(study, spend_dir=ledger)
    assert rows["bound"]["selected"] == "X"
    assert rows["bound"]["status"] == "missing"
    assert rows["bound"]["label"] is None
    assert rows["anonymous"]["status"] == "valid"
    assert rows["anonymous"]["label"] == "A"
    assert turns["x-decide"]["meter"] == "ledger_without_receipt"
    assert turns["y-decide"]["meter"] == "matched"
    assert report["meter"]["by_match"]["matched"] == 5
    assert report["meter"]["by_match"]["unsettled"] == 1
    assert report["meter"]["unexpected_ledger_rows"] == 1
    assert report["meter"]["known_usd_low"] == "0.000033336"
    assert report["meter"]["upper_usd_including_holds"] == "0.010033336"

    missing.write_bytes((directory / "y-decide.json").read_bytes())
    missing.chmod(0o600)
    _, rows, turns = _read(study, spend_dir=ledger)
    assert rows["bound"]["status"] == "invalid_evidence"
    assert rows["bound"]["label"] is None
    assert rows["anonymous"]["status"] == "valid"
    assert turns["x-decide"]["response_sha256"] is None
    assert turns["y-decide"]["meter"] == "matched"
