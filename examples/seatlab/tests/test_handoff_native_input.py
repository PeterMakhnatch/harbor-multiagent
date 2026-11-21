import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import TestCase

from examples.seatlab.export_capsules import reconstructed_capsules
from examples.seatlab.handoff.native_input import build
from examples.seatlab.handoff.packet import parse

_DELIVERY = {"stale": (0,), "reordered": (1, 0), "pooled": ()}


def _native_trial(
    trial_id: int, condition: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    dossier: dict[str, Any] = {
        "schema": "seatlab.world/v1",
        "complete": True,
        "settings": {
            "schema": "seatlab.scenario/v1",
            "condition": condition,
            "seed": 651234,
            "episodes": 4,
        },
        "cases": [],
    }
    for row in range(4):
        reports = [
            {"revision": 1, "available": ["C", "D"] if row == 3 else ["B", "D"]},
            {"revision": 2, "available": ["C", "D"]},
        ]
        dossier["cases"].append(
            {
                "id": row,
                "utilities": {"A": 4, "B": 3, "C": 2, "D": 1},
                "before": ["A", "B"],
                "after": ["B", "C"] if row % 2 else ["A", "B"],
                "reports": None if condition == "pooled" else reports,
                "visible": [reports[index] for index in _DELIVERY[condition]],
                "chosen": "D" if row % 2 else "A",
            }
        )
    names = {
        "inventory": "nop" if condition == "pooled" else "private-sender",
        "dispatch": "private-receiver",
    }
    result = {
        "id": f"00000000-0000-4000-8000-{trial_id:012x}",
        "exception_info": None,
        "finished_at": "2026-01-01T00:00:02Z",
        "reward_valid": True,
        "verifier_result": {"rewards": {"reward": 0.5}},
        "config": {
            "participants": [
                {"seat": role, "name": name} for role, name in names.items()
            ]
        },
        "seat_results": [
            {
                "seat": role,
                "under_test": True,
                "exception_info": None,
                "agent_execution": {
                    "started_at": "2026-01-01T00:00:00Z",
                    "finished_at": "2026-01-01T00:00:01Z",
                },
                "agent_info": {"name": name, "version": "private-version"},
                "agent_result": {},
            }
            for role, name in names.items()
        ],
    }
    return result, dossier


def _write_trial(path: Path, result: dict[str, Any], dossier: dict[str, Any]) -> Path:
    world = path / "artifacts/logs/world/handoff.json"
    world.parent.mkdir(parents=True, exist_ok=True)
    world.write_text(json.dumps(dossier), encoding="utf-8")
    (path / "result.json").write_text(json.dumps(result), encoding="utf-8")
    return path


class NativeInputTest(TestCase):
    def test_replay_preserves_incorrect_reports_and_scores_against_truth(self) -> None:
        result, dossier = _native_trial(7, "stale")
        inventory_hash = reconstructed_capsules(dossier)["inventory"]["sha256"]
        dossier["observation_receipts"] = {"inventory": {"ack_sha256": inventory_hash}}
        with TemporaryDirectory() as directory:
            root = Path(directory) / "private-trial-dir"
            corpus = build([_write_trial(root, result, dossier)], [3, 1])

        packet = parse(corpus.packet)
        self.assertEqual(corpus.ids, (3, 1))
        third, first, stable = packet.cases
        for case in packet.cases:
            self.assertEqual([row.id for row in case.records], ["S3", "S1"])
            self.assertIs(corpus.worlds[case.id], corpus.worlds[third.id])
        self.assertEqual(corpus.worlds[third.id], dossier)
        versions = [
            [(row.old_version, row.new_version) for row in case.records]
            for case in packet.cases
        ]
        self.assertEqual(
            versions,
            [
                [("1", "2"), ("2", "2")],
                [("2", "2"), ("1", "2")],
                [("2", "2"), ("2", "2")],
            ],
        )
        equal_reports = third.records[0]
        self.assertEqual(equal_reports.before, equal_reports.after)
        self.assertEqual((equal_reports.old_best, equal_reports.best), ("A", "B"))
        row = first.records[1]
        self.assertEqual(row.before, "Available bays: B, D.")
        self.assertEqual(row.after, "Available bays: C, D.")
        self.assertEqual((row.old_best, row.best), ("A", "B"))
        self.assertEqual(stable.records[1].before, row.after)
        self.assertEqual(stable.records[1].old_best, row.best)
        self.assertIn("A=4, B=3, C=2, D=1", row.question)

        (source,) = corpus.receipt["sources"]
        self.assertEqual(corpus.receipt["packet_sha256"], packet.sha256)
        self.assertEqual(corpus.receipt["registered_cases"], 3)
        self.assertEqual(source["native_score"]["optimal"], 2)
        self.assertEqual(source["condition"], "stale")
        self.assertEqual(source["inventory_sha256"], inventory_hash)
        self.assertEqual(source["inventory_basis"], "bound")
        self.assertEqual(source["dispatch_basis"], "reconstructed")
        public = json.dumps(corpus.receipt) + corpus.packet.decode()
        for private in (result["id"], str(root), "private-", '"seed"', '"chosen"'):
            self.assertNotIn(private, public)

    def test_sources_cluster_by_full_latent_world_not_seed_or_trial(self) -> None:
        result, dossier = _native_trial(7, "stale")
        other_result, other = _native_trial(8, "reordered")
        distinct_result, distinct = _native_trial(9, "stale")
        distinct["cases"][0]["utilities"] = {"A": 4, "B": 3, "C": 1, "D": 2}
        with TemporaryDirectory() as directory:
            root = Path(directory)
            original = _write_trial(root / "original", result, dossier)
            second = _write_trial(root / "other", other_result, other)
            third = _write_trial(root / "distinct", distinct_result, distinct)
            corpus = build([second, original, third], [3, 1])
            baseline = build([original], [3, 1])
            dossier["cases"][0]["reports"][1]["available"] = ["A", "D"]
            changed_message = build([_write_trial(original, result, dossier)], [3, 1])
            result["id"] = other_result["id"]
            changed_trial = build([_write_trial(original, result, dossier)], [3, 1])
            with self.assertRaisesRegex(ValueError, "repeated native trial ID"):
                build([original, second], [3, 1])

        cases = parse(corpus.packet).cases
        self.assertEqual(len(cases), 9)
        self.assertEqual(len({case.cluster for case in cases[:6]}), 1)
        self.assertNotEqual(cases[0].cluster, cases[6].cluster)
        self.assertIsNot(corpus.worlds[cases[0].id], corpus.worlds[cases[3].id])
        for index, condition in ((0, "reordered"), (3, "stale"), (6, "stale")):
            settings = corpus.worlds[cases[index].id]["settings"]
            self.assertEqual(settings["condition"], condition)
        replays = (baseline, changed_message, changed_trial)
        sources = [entry.receipt["sources"][0] for entry in replays]
        self.assertEqual(len({source["world_sha256"] for source in sources}), 1)
        self.assertEqual(len({source["trial_sha256"] for source in sources}), 3)
        self.assertEqual(len({entry.receipt["packet_sha256"] for entry in replays}), 3)

    def test_rejects_unusable_native_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            result, dossier = _native_trial(7, "pooled")
            with self.assertRaisesRegex(ValueError, "pooled trials"):
                build([_write_trial(root, result, dossier)], [1, 3])

            result, dossier = _native_trial(8, "stale")
            dossier["observation_receipts"] = {"dispatch": {"ack_sha256": "0" * 64}}
            with self.assertRaisesRegex(ValueError, "invalid acknowledgment"):
                build([_write_trial(root, result, dossier)], [1, 3])

            result, dossier = _native_trial(9, "stale")
            result["verifier_result"]["rewards"]["reward"] = 1.0
            with self.assertRaisesRegex(ValueError, "invalid scored native trial"):
                build([_write_trial(root, result, dossier)], [1, 3])

            result, dossier = _native_trial(10, "stale")
            dossier["complete"] = False
            with self.assertRaisesRegex(ValueError, "invalid scored native trial"):
                build([_write_trial(root, result, dossier)], [1, 3])

            dossier["complete"] = True
            dossier["cases"][1]["reports"].pop()
            with self.assertRaisesRegex(ValueError, "invalid scored native trial"):
                build([_write_trial(root, result, dossier)], [1, 3])

    def test_rejects_invalid_ship_registers(self) -> None:
        result, dossier = _native_trial(7, "stale")
        with TemporaryDirectory() as directory:
            trial = _write_trial(Path(directory), result, dossier)
            for selected in ([1], [-1, 1], [True, 1], list(range(9))):
                with self.assertRaisesRegex(ValueError, "Select 2 to 8"):
                    build([trial], selected)
            with self.assertRaisesRegex(ValueError, "Duplicate ship ID"):
                build([trial], [1, 1])
            with self.assertRaisesRegex(ValueError, "out of range"):
                build([trial], [1, 4])
            for trials in ([], [trial] * 86):
                with self.assertRaisesRegex(ValueError, "1 to 256 cases"):
                    build(trials, [1, 3])
