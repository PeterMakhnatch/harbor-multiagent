from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from examples.seatlab import evidence
from examples.seatlab.capsule import encode_view
from examples.seatlab.handoff.analysis import summarize
from examples.seatlab.handoff.native_stock import stock
from examples.seatlab.handoff.packet import Block, grid, parse

if TYPE_CHECKING:
    from examples.seatlab.handoff.native_input import Corpus


def _world(seed: int) -> dict[str, Any]:
    rows = []
    for ship in range(4):
        before = ["A", "B"]
        after = ["B", "C"] if ship % 2 else before
        reports = [
            {"revision": 1, "available": ["A", "B"]},
            {"revision": 2, "available": ["A", "D"]},
        ]
        rows.append(
            {
                "id": ship,
                "before": before,
                "after": after,
                "utilities": {"A": 4, "B": 1, "C": 3, "D": 2},
                "reports": reports,
                "visible": reports[::-1],
                "chosen": "D",
            }
        )
    return {
        "schema": "seatlab.world/v1",
        "complete": True,
        "settings": {
            "schema": "seatlab.scenario/v1",
            "condition": "reordered",
            "seed": seed,
            "episodes": 4,
        },
        "cases": rows,
    }


@pytest.fixture
def corpus() -> Corpus:
    def records(revised: bool) -> list[dict[str, str]]:
        return [
            {
                "id": label,
                "old_version": "1",
                "new_version": "2" if change else "1",
                "before": "A, B" if change else "B, C",
                "after": "B, C",
                "question": "Which available bay has the highest utility?",
                "old_best": "A" if change else "C",
                "best": "C",
            }
            for label, change in (("S3", revised), ("S1", False))
        ]

    packet = encode_view(
        {
            "schema": "seatlab.handoff-input/v1",
            "brief": "Dispatch the selected shipments.",
            "choices": list("ABCD"),
            "cases": [
                {"id": "a-change", "cluster": "a", "records": records(True)},
                {"id": "a-stable", "cluster": "a", "records": records(False)},
                {"id": "b-change", "cluster": "b", "records": records(True)},
            ],
        }
    )
    first, second = _world(1), _world(2)
    return cast(
        "Corpus",
        SimpleNamespace(
            packet=packet,
            receipt={},
            worlds={"a-change": first, "a-stable": first, "b-change": second},
            ids=(3, 1),
        ),
    )


def _row(block: Block) -> dict[str, Any]:
    return {
        "block": block.key,
        "fingerprint": block.fingerprint,
        "case": block.case.id,
        "cluster": block.case.cluster,
        "rep": block.rep,
        "state": "unclaimed",
        "turns": [],
        "outcome": {
            "note_status": "missing",
            "arms": {
                name: {
                    "status": "no_note",
                    "lookup": None,
                    "decision_stage": None,
                    "choices": None,
                }
                for name in ("bound", "anonymous")
            },
        },
    }


def _complete(row: dict[str, Any], bound: str, anonymous: str, *, shared: bool) -> None:
    row["state"] = "claimed"
    row["outcome"]["note_status"] = "valid"
    stages = {"note": "route_review"}
    for policy, answer in (("bound", bound), ("anonymous", anonymous)):
        lookup = "S3" if shared or policy == "bound" else "S1"
        stage = "both-decide" if shared else f"{policy}-decide"
        row["outcome"]["arms"][policy] = {
            "status": "complete",
            "lookup": lookup,
            "decision_stage": stage,
            "choices": dict(zip(("S3", "S1"), answer, strict=True)),
        }
        stages[f"{policy}-select"] = "route_decide"
        stages[stage] = "route_decide"
    row["turns"] = [
        {"run": f"{row['block']}-{stage}", "role": role, "status": "received"}
        for stage, role in stages.items()
    ]


def _report(corpus: Corpus, *, heard: bool) -> dict[str, Any]:
    packet = parse(corpus.packet)
    blocks = grid(packet)
    rows = [_row(block) for block in blocks]
    if heard:
        _complete(rows[0], "CC", "CC", shared=True)
        _complete(rows[1], "CB", "AC", shared=False)
        _complete(rows[2], "CC", "CC", shared=False)
        for policy, status in (
            ("bound", "invalid_decide"),
            ("anonymous", "missing_decide"),
        ):
            rows[2]["outcome"]["arms"][policy].update(status=status, choices=None)
        rows[2]["turns"].pop()
        _complete(rows[4], "AC", "CB", shared=False)
    return {
        "schema": "seatlab.handoff-report/v1",
        "packet_sha256": packet.sha256,
        "outcomes": rows,
        "primary": summarize(blocks, rows)["primary"],
    }


def test_stock_scores_only_heard_decisions(
    corpus: Corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshots = deepcopy(corpus.worlds)
    scorer = evidence._score
    visits: list[tuple[str, ...]] = []

    def record_score(projected: dict[str, Any]) -> dict[str, Any]:
        original = corpus.worlds[
            "a-change" if projected["settings"]["seed"] == 1 else "b-change"
        ]
        assert projected is not original
        assert projected["cases"] is not original["cases"]
        for ship in (0, 2):
            assert projected["cases"][ship] is original["cases"][ship]
        visits.append(tuple(row["chosen"] for row in projected["cases"]))
        return scorer(projected)

    monkeypatch.setattr(evidence, "_score", record_score)
    result = stock(corpus, _report(corpus, heard=True))
    assert visits == [
        ("D", "C", "D", "C"),
        ("D", "B", "D", "C"),
        ("D", "C", "D", "A"),
        ("D", "C", "D", "A"),
        ("D", "B", "D", "C"),
    ]
    assert corpus.worlds == snapshots
    assert result["observed_dossiers"] == 5
    assert result["shared_dossiers"] == 1
    assert result["observed_native_choices"] == 10
    assert result["observed_worlds"] == 2
    assert result["mismatches"] == 0
    assert result["primary"]["scheduled_pairs"] == 4
    assert result["primary"]["complete_pairs"] == 3
    assert result["primary"]["paired_complete_delta"] == -0.25


def test_stock_zero_observed(corpus: Corpus) -> None:
    result = stock(corpus, _report(corpus, heard=False))
    assert result["observed_dossiers"] == 0
    assert result["observed_native_choices"] == 0
    assert result["shared_dossiers"] == 0
    assert result["observed_worlds"] == 0
    assert result["primary"]["complete_pairs"] == 0
    assert result["primary"]["paired_complete_delta"] is None


def test_stock_rejects_wrong_packet(corpus: Corpus) -> None:
    report = _report(corpus, heard=True)
    report["packet_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="packet fingerprint"):
        stock(corpus, report)


def test_stock_rejects_duplicate_row(corpus: Corpus) -> None:
    report = _report(corpus, heard=True)
    report["outcomes"][1] = deepcopy(report["outcomes"][0])
    with pytest.raises(ValueError, match="register key"):
        stock(corpus, report)


@pytest.mark.parametrize("corruption", ("absent", "callback_error", "extra"))
def test_stock_rejects_corrupted_final_turn(corpus: Corpus, corruption: str) -> None:
    report = _report(corpus, heard=True)
    row = report["outcomes"][0]
    turns = row["turns"]
    final = next(turn for turn in turns if turn["run"].endswith("-both-decide"))
    if corruption == "absent":
        turns.remove(final)
    elif corruption == "callback_error":
        final["status"] = "callback_error"
    else:
        turns.append(final | {"run": f"{row['block']}-bound-decide"})
    with pytest.raises(ValueError, match="final turn"):
        stock(corpus, report)


def test_stock_rejects_different_choices_for_a_shared_final(corpus: Corpus) -> None:
    report = _report(corpus, heard=True)
    report["outcomes"][0]["outcome"]["arms"]["anonymous"]["choices"]["S1"] = "D"
    with pytest.raises(ValueError, match="shared handoff decision"):
        stock(corpus, report)


def test_stock_rejects_changed_primary(corpus: Corpus) -> None:
    report = _report(corpus, heard=True)
    report["primary"]["paired_complete_delta"] = 0.0
    with pytest.raises(ValueError, match="registered handoff primary"):
        stock(corpus, report)
