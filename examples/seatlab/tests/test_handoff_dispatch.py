from copy import deepcopy
from typing import Any
from unittest import TestCase

from examples.seatlab.handoff import dispatch, packet


def _arm(
    lookup: str | None = None,
    stage: str | None = None,
    choices: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "status": "no_note" if choices is None else "complete",
        "lookup": lookup,
        "decision_stage": stage,
        "choices": choices,
    }


def _report() -> dict[str, Any]:
    registered = packet.parse(dispatch.packet_bytes())
    outcomes: list[dict[str, Any]] = []
    for block in packet.grid(registered):
        case = block.case
        changed = case.id.endswith("-p") and block.rep == 0
        shared = (case.id, block.rep) in {("s5801-stable", 0), ("s5801-p", 1)}
        observed = changed or shared
        arms = {"bound": _arm(), "anonymous": _arm()}
        if observed:
            correct = {record.id: record.best for record in case.records}
            if shared:
                arms = {name: _arm("P", "both-decide", correct) for name in arms}
            else:
                wrong = {
                    label: next(bay for bay in registered.choices if bay != best)
                    for label, best in correct.items()
                }
                arms = {
                    "bound": _arm("P", "bound-decide", correct),
                    "anonymous": _arm("Q", "anonymous-decide", wrong),
                }
        outcomes.append(
            {
                "block": block.key,
                "case": case.id,
                "cluster": case.cluster,
                "rep": block.rep,
                "fingerprint": block.fingerprint,
                "state": "claimed" if observed else "unclaimed",
                "outcome": {
                    "note_status": "valid" if observed else "missing",
                    "arms": arms,
                },
                "turns": [],
            }
        )
    return {"packet_sha256": registered.sha256, "outcomes": outcomes}


class HandoffDispatchTest(TestCase):
    def test_stock_separates_observed_choices_from_reference_rows(self) -> None:
        report = _report()
        report["primary"] = {
            "scheduled_pairs": 96,
            "complete_pairs": 13,
            "clusters_with_pairs": 12,
            "enrolled_clusters": 12,
            "paired_complete_delta": 23 / 24,
        }
        proof = dispatch.stock(report)
        self.assertEqual(proof["primary"], report["primary"])
        self.assertEqual(proof["mismatches"], 0)
        self.assertEqual(proof["registered_native_records"], 48)
        self.assertEqual(proof["observed_distinct_native_records"], 48)
        self.assertEqual(proof["observed_dossiers"], 26)
        self.assertEqual(proof["shared_dossiers"], 2)
        self.assertEqual(proof["observed_native_choices"], 104)
        self.assertEqual(proof["reference_even_id_fillers"], 104)
        self.assertEqual(proof["arms"]["bound"]["correct"], 56)
        self.assertEqual(proof["arms"]["anonymous"]["correct"], 8)

    def test_stock_rejects_wrong_register_tuple_or_claimed_reward(self) -> None:
        report = _report()
        wrong_identity = deepcopy(report)
        wrong_identity["outcomes"][0]["cluster"] = "s5802"
        with self.assertRaisesRegex(ValueError, "row identity"):
            dispatch.stock(wrong_identity)

        wrong_choice = deepcopy(report)
        del wrong_choice["outcomes"][0]["outcome"]["arms"]["bound"]["choices"]["P"]
        with self.assertRaisesRegex(ValueError, "complete stock arm"):
            dispatch.stock(wrong_choice)

        report["primary"] = {
            "scheduled_pairs": 96,
            "complete_pairs": 13,
            "clusters_with_pairs": 12,
            "enrolled_clusters": 12,
            "paired_complete_delta": 0.0,
        }
        with self.assertRaisesRegex(ValueError, "does not match native score"):
            dispatch.stock(report)
