from hashlib import sha256
from typing import Any
from uuid import UUID

import pytest

from examples.seatlab.attention.protocol import aliases
from examples.seatlab.turn_fork import analysis, protocol

_DIGEST = "d" * 64
_USAGE = {
    "served_model": "fixture-model",
    "input_tokens": 12,
    "cache_tokens": 3,
    "output_tokens": 4,
}

type _Document = dict[str, Any]
type _Scores = dict[tuple[int, int, str], int | None]


def _hash(text: str) -> str:
    return sha256(text.encode()).hexdigest()


def _source(seed: int, rep: int) -> _Document:
    cases = []
    for native_id in range(6):
        previous, recent = ("AB", "BD") if native_id < 2 else ("AC", "BC")
        cases.append(
            {
                "id": native_id,
                "utilities": dict(zip("ABCD", range(1, 5), strict=True)),
                "reports": [
                    {"revision": 1, "available": list(previous)},
                    {"revision": 2, "available": list(recent)},
                ],
                "after": ["A", "C"],
            }
        )
    return {
        "source_id": f"s{seed}-r{rep}",
        "seed": seed,
        "repetition": rep,
        "trial_id": str(UUID(int=2 * seed + rep)),
        "world_sha256": _hash(f"world-{seed}"),
        "upstream": {
            stage: {
                "prompt_sha256": _hash(f"{stage}-{seed}-{rep}-prompt"),
                "response_sha256": _hash(f"{stage}-{seed}-{rep}-response"),
            }
            for stage in ("inventory", "brief")
        },
        "card": "BBBBBB" if seed == 6506 else "AAABCD",
        "cases": cases,
    }


def _complete(row: _Document, source: _Document, optimal: int) -> None:
    arm = row["arm"]
    native_ids = aliases(source["cases"], source["seed"], source["repetition"])
    mapping = {native: alias for alias, native in native_ids.items()}
    first, second = {"aligned": (0, 1), "shifted": (1, 2), "repeat": (1, 0)}[arm]
    opened = (mapping[first], mapping[second])
    other = "B" if arm == "shifted" else "A"
    row["state"] = "complete"
    row["outcome"] = {
        "opened": list(opened),
        "actions": [
            {
                "id": native_id,
                "choice": "C" if native_id < optimal else other,
                "optimal": native_id < optimal,
                "feasible": native_id < optimal or other == "A",
            }
            for native_id in range(6)
        ],
        "optimal": optimal,
        "feasible": optimal if arm == "shifted" else 6,
        "opened_sensitive": 1 if arm == "shifted" else 2,
    }
    row["turns"] = [
        {
            "stage": stage,
            "role": role,
            "run": f"tf-{_DIGEST[:12]}-{row['source_id']}-{arm}-{stage}",
            "prompt_sha256": _hash(prompt),
            "response_sha256": _hash(f"{row['source_id']}-{arm}-{stage}-response"),
            "usage": dict(_USAGE) if stage == "push" else None,
            "status": "received",
        }
        for stage, role, prompt in (
            ("push", "inventory", protocol.push_prompt(source, arm)),
            ("decide", "dispatch", protocol.decide_prompt(source, opened)),
        )
    ]


def _experiment(
    scores: _Scores, *, absent_seeds: tuple[int, ...] = ()
) -> tuple[_Document, list[_Document]]:
    packet: _Document = {
        "schema": "seatlab.turn-fork-packet/v1",
        "source_report_sha256": "e" * 64,
        "sources": [],
    }
    rows: list[_Document] = []
    arms = ("aligned", "shifted", "repeat")
    for seed in range(6501, 6507):
        for rep in (0, 1):
            source = None if seed in absent_seeds else _source(seed, rep)
            if source is not None:
                packet["sources"].append(source)
            rotation = (seed - 6501 + rep) % 3
            for arm in arms[rotation:] + arms[:rotation]:
                row = {
                    "source_id": f"s{seed}-r{rep}",
                    "seed": seed,
                    "repetition": rep,
                    "arm": arm,
                    "state": "no_source" if source is None else "unclaimed",
                    "outcome": None,
                    "turns": [],
                }
                optimal = scores.get((seed, rep, arm), 6)
                if source is not None and optimal is not None:
                    _complete(row, source, optimal)
                rows.append(row)
    return packet, rows


def test_matched_repetitions_get_equal_world_weight_with_one_missing_side():
    packet, rows = _experiment(
        {
            (6501, 0, "shifted"): 0,
            (6501, 1, "shifted"): None,
            (6501, 1, "aligned"): 4,
            (6501, 1, "repeat"): 3,
        }
    )
    summary = analysis.summarize(packet, _DIGEST, rows)
    primary = summary["primary"]
    assert primary["estimate"] == pytest.approx(1 / 6)
    assert primary["matched_pairs"] == 11
    assert primary["registered_pairs"] == 12
    assert primary["paired_worlds"] == 6
    assert primary["worlds"][0]["mean"] == 1
    assert primary["worlds"][0]["matched_pairs"] == 1
    assert primary["registered_bounds"] == pytest.approx(
        {"lower": 1 / 18, "upper": 5 / 36}
    )
    control = summary["negative_control"]
    assert control["estimate"] == pytest.approx(1 / 72)
    assert control["matched_pairs"] == 12
    assert control["registered_bounds"] == pytest.approx(
        {"lower": 1 / 72, "upper": 1 / 72}
    )
    secondary = summary["secondary"]
    assert secondary["card_rotation"] == {
        "source_cards": 12,
        "card_slots": 72,
        "identical_slots": 32,
        "unchanged_cards": 2,
    }
    assert secondary["aligned_vs_shifted"] == {
        "matched_pairs": 11,
        "opened_set_agreements": 0,
        "action_slots": 66,
        "action_agreements": 60,
    }
    assert secondary["aligned_vs_repeat"]["opened_set_agreements"] == 12
    assert secondary["aligned_vs_repeat"]["action_agreements"] == 71
    assert secondary["targeting"]["shifted"] == {
        "complete_cells": 11,
        "case_slots": 66,
        "sensitive_cases": 22,
        "opened_slots": 22,
        "opened_sensitive": 11,
        "max_sensitive_openable": 22,
    }


def test_registered_bounds_use_observed_side_when_other_arm_never_plays():
    scores = {
        (seed, rep, arm): score
        for seed in range(6501, 6507)
        for rep in (0, 1)
        for arm, score in (("shifted", None), ("aligned", 4))
    }
    packet, rows = _experiment(scores)
    primary = analysis.summarize(packet, _DIGEST, rows)["primary"]
    assert primary["estimate"] is None
    assert primary["matched_pairs"] == 0
    assert primary["registered_bounds"] == pytest.approx(
        {"lower": -1 / 3, "upper": 2 / 3}
    )


def test_missing_world_nulls_both_estimates_without_leaving_the_register():
    scores = {(seed, rep, "shifted"): 0 for seed in range(6501, 6507) for rep in (0, 1)}
    packet, rows = _experiment(scores, absent_seeds=(6506,))
    summary = analysis.summarize(packet, _DIGEST, rows)
    assert summary["available_sources"] == 10
    assert summary["cell_states"]["no_source"] == 6
    for name in ("primary", "negative_control"):
        contrast = summary[name]
        assert contrast["estimate"] is None
        assert contrast["registered_worlds"] == 6
        assert contrast["paired_worlds"] == 5
        assert contrast["matched_pairs"] == 10
        assert contrast["worlds"][-1]["mean"] is None
    assert summary["primary"]["registered_bounds"] == pytest.approx(
        {"lower": 2 / 3, "upper": 1}
    )
    assert summary["negative_control"]["registered_bounds"] == pytest.approx(
        {"lower": -1 / 6, "upper": 1 / 6}
    )


def test_count_tokens_only_for_metered_receipts_and_not_for_failed_calls():
    packet, rows = _experiment({(6501, 1, "shifted"): None})
    row = rows[0]
    row.update(state="incomplete", outcome=None)
    row["turns"] = row["turns"][:1]
    row["turns"][0].update(status="callback_error", response_sha256=None, usage=None)
    meter = analysis.summarize(packet, _DIGEST, rows)["meter"]
    assert meter["push"] == {
        "absent_registered_turns": 1,
        "callback_errors": 1,
        "received_metered": 34,
        "received_unmetered": 0,
        "reported_tokens": {
            "input_tokens": 408,
            "cache_tokens": 102,
            "output_tokens": 136,
        },
    }
    assert meter["decide"] == {
        "absent_registered_turns": 2,
        "callback_errors": 0,
        "received_metered": 0,
        "received_unmetered": 34,
        "reported_tokens": None,
    }


@pytest.mark.parametrize(
    ("path", "value", "message"),
    (
        (("outcome", "actions", 0, "choice"), "A", "action grades"),
        (("outcome", "actions", 0, "optimal"), 1, "action grades"),
        (("outcome", "optimal"), 5, "outcome totals"),
        (("outcome", "feasible"), 5, "outcome totals"),
        (("outcome", "opened_sensitive"), 1, "outcome totals"),
        (("turns", 0, "usage", "cache_tokens"), 13, "invalid turn usage"),
        (("turns", 0, "usage"), dict.fromkeys(_USAGE), "invalid turn usage"),
        (("turns", 0, "prompt_sha256"), "f" * 64, "push prompt digest"),
        (("turns", 1, "prompt_sha256"), "f" * 64, "decide prompt digest"),
        (("turns", 0, "response_sha256"), "non-hash text", "response digest"),
        (("turns", 1, "role"), "inventory", "turn identity"),
    ),
)
def test_rejects_tampered_decisions_and_turns(path, value, message):
    packet, rows = _experiment({})
    target: Any = rows[0]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError, match=message):
        analysis.summarize(packet, _DIGEST, rows)


def test_rejects_omitted_meter_and_dispatch_after_push_error():
    packet, rows = _experiment({})
    turn = rows[0]["turns"][0]
    del turn["usage"]
    with pytest.raises(ValueError):
        analysis.summarize(packet, _DIGEST, rows)
    turn.update(status="callback_error", response_sha256=None, usage=None)
    rows[0].update(state="incomplete", outcome=None)
    with pytest.raises(ValueError, match="callback error"):
        analysis.summarize(packet, _DIGEST, rows)


def test_requires_register_order_and_correct_missing_source_state():
    packet, rows = _experiment({}, absent_seeds=(6506,))
    for malformed in (rows[:-1], [rows[0], *rows[:-1]], [rows[1], rows[0], *rows[2:]]):
        with pytest.raises(ValueError, match="registered"):
            analysis.summarize(packet, _DIGEST, malformed)
    rows[-1]["state"] = "unclaimed"
    with pytest.raises(ValueError, match="source coverage"):
        analysis.summarize(packet, _DIGEST, rows)


def test_repeated_trial_uuid_cannot_identify_a_different_world():
    packet, rows = _experiment({})
    packet["sources"][2]["trial_id"] = packet["sources"][0]["trial_id"]
    with pytest.raises(ValueError, match="registered world"):
        analysis.summarize(packet, _DIGEST, rows)
