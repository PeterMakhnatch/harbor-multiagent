from itertools import combinations
from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import TestCase

from examples.seatlab.dispatch_native_return import inspect_trial
from examples.seatlab.evidence import read_trial_with_world
from examples.seatlab.export_capsules import reconstructed_capsules
from examples.seatlab.tests.test_evidence import _TRIAL_ID, _native, _write_trial, score

_WORLD = (
    Path(__file__).resolve().parents[2]
    / "tasks/dispatch-handoff/environment/seatworld.py"
)
_WORLD_TYPE = run_path(str(_WORLD))["World"]
_OTHER_TRIAL_ID = "1884d922-996e-4521-8056-2ae5b8d91556"
_ZERO_TRIAL_ID = "00000000-0000-0000-0000-000000000000"
_SEED = 17


def _completed_world(
    condition: str = "reordered", *, misleading: bool = False
) -> dict[str, Any]:
    world = _WORLD_TYPE(
        {
            "schema": "seatlab.scenario/v1",
            "seed": _SEED,
            "episodes": 3,
            "condition": condition,
        }
    )
    inventory = []
    dispatch = []
    for case in world.evidence()["cases"]:
        episode = case["id"]
        revision_1, revision_2 = case["before"], case["after"]
        utilities = case["utilities"]
        choice = max(revision_2, key=utilities.__getitem__)
        if misleading and episode == 0:
            assert revision_1 == revision_2
            revision_2 = sorted(utilities.keys() - set(revision_2))
            choice = max(revision_2, key=utilities.__getitem__)
        if misleading and episode == 1:
            revision_1 = next(
                list(pair)
                for pair in combinations(sorted(utilities.keys() - {choice}), 2)
                if pair != tuple(revision_1)
            )
        inventory.append(
            {
                "id": episode,
                "reports": [
                    {"revision": 1, "available": revision_1},
                    {"revision": 2, "available": revision_2},
                ],
            }
        )
        dispatch.append({"id": episode, "bay": choice})
    if condition != "pooled":
        world.submit("inventory", {"rows": inventory})
    world.submit("dispatch", {"rows": dispatch})
    return world.evidence()


def _store_trial(root: Path, dossier: dict[str, Any]) -> dict[str, Any]:
    condition = dossier["settings"]["condition"]
    native = _native(condition)
    if condition != "pooled":
        native["config"]["participants"][0] = {
            "seat": "inventory",
            "import_path": "fixture:Inventory",
        }
        native["seat_results"][1]["agent_info"]["name"] = "fixture-inventory"
    grade = score(dossier)
    native["verifier_result"]["rewards"]["reward"] = (
        grade["optimal"] / grade["episodes"]
    )
    _write_trial(root, native, dossier)
    return native


class DispatchNativeReturnTest(TestCase):
    def test_measures_actual_delivered_reports_not_reconstructed_truth(self) -> None:
        dossier = _completed_world(misleading=True)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _store_trial(root, dossier)
            result = inspect_trial(root, _TRIAL_ID, _WORLD_TYPE)

        counts = {
            "qualified_different_latest": 0,
            "qualified_different_last": 0,
            "qualified_different_neither": 0,
            "qualified_same_best": 0,
            "qualified_same_other": 0,
        }
        incorrect = {**counts, "qualified_different_latest": 2}
        self.assertEqual(
            result,
            {
                "schema": "seatlab.dispatch-native-return/v1",
                "trial_id": _TRIAL_ID,
                "seed": _SEED,
                "condition": "reordered",
                "episodes": 3,
                "status": "measured",
                "grade": {"optimal": 2, "feasible": 2},
                "observations": {
                    "dispatch": "reconstructed",
                    "inventory": "reconstructed",
                },
                "truthful_rules": {
                    **counts,
                    "qualified_same_best": 1,
                    "unqualified_reports": 2,
                },
                "delivered_rules": {**incorrect, "qualified_same_best": 1},
                "delivered_with_incorrect_inventory": incorrect,
            },
        )
        for key in (
            "grade",
            "truthful_rules",
            "delivered_rules",
            "delivered_with_incorrect_inventory",
        ):
            for count in result[key].values():
                self.assertIs(type(count), int)

    def test_one_receipt_does_not_certify_the_other_role(self) -> None:
        dossier = _completed_world()
        ack = reconstructed_capsules(dossier)["inventory"]["sha256"]
        dossier["observation_receipts"] = {"inventory": {"ack_sha256": ack}}
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _store_trial(root, dossier)
            result = inspect_trial(root, _TRIAL_ID, _WORLD_TYPE)
            self.assertEqual(
                result["observations"],
                {"inventory": "bound", "dispatch": "reconstructed"},
            )
            dossier["observation_receipts"]["inventory"]["ack_sha256"] = "0" * 64
            _store_trial(root, dossier)
            with self.assertRaisesRegex(ValueError, "invalid native trial evidence"):
                inspect_trial(root, _TRIAL_ID, _WORLD_TYPE)

    def test_rejects_a_wrong_native_reward(self) -> None:
        dossier = _completed_world()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            native = _store_trial(root, dossier)
            native["verifier_result"]["rewards"]["reward"] = 0.0
            _write_trial(root, native, dossier)
            with self.assertRaises(ValueError):
                inspect_trial(root, _TRIAL_ID, _WORLD_TYPE)

    def test_rejects_delivery_that_does_not_match_accepted_reports(self) -> None:
        dossier = _completed_world()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            native = _store_trial(root, dossier)
            row = dossier["cases"][0]
            row["visible"][0]["available"] = sorted(
                row["utilities"].keys() - set(row["after"])
            )
            _write_trial(root, native, dossier)
            with self.assertRaises(ValueError):
                inspect_trial(root, _TRIAL_ID, _WORLD_TYPE)

    def test_rejects_a_different_native_trial_uuid(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _store_trial(root, _completed_world())
            with self.assertRaises(ValueError) as error:
                inspect_trial(root, _OTHER_TRIAL_ID, _WORLD_TYPE)
        self.assertNotIn(_TRIAL_ID, str(error.exception))
        self.assertNotIn(_OTHER_TRIAL_ID, str(error.exception))

    def test_validates_expected_uuid_before_reading(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for invalid in ("not-a-uuid", _TRIAL_ID.upper(), _ZERO_TRIAL_ID):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    inspect_trial(root, invalid, _WORLD_TYPE)
            with self.assertRaises(FileNotFoundError):
                inspect_trial(root, _TRIAL_ID, _WORLD_TYPE)

    def test_rejects_fingerprint_substitution_with_the_same_native_grade(self) -> None:
        dossier = _completed_world()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            native = _store_trial(root, dossier)
            original, _ = read_trial_with_world(root)
            first = dossier["cases"][0]
            utility = first["utilities"]
            unavailable_a, unavailable_b = sorted(utility.keys() - set(first["after"]))
            utility[unavailable_a], utility[unavailable_b] = (
                utility[unavailable_b],
                utility[unavailable_a],
            )
            _write_trial(root, native, dossier)
            substituted, _ = read_trial_with_world(root)
            self.assertEqual(original["score"], substituted["score"])
            self.assertNotEqual(original["fingerprint"], substituted["fingerprint"])
            with self.assertRaises(ValueError):
                inspect_trial(root, _TRIAL_ID, _WORLD_TYPE)

    def test_keeps_native_grade_without_comparison_in_nonreordered_worlds(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for condition in ("fresh", "stale", "silent", "pooled"):
                with self.subTest(condition=condition):
                    _store_trial(root, _completed_world(condition))
                    self.assertEqual(
                        inspect_trial(root, _TRIAL_ID, _WORLD_TYPE),
                        {
                            "schema": "seatlab.dispatch-native-return/v1",
                            "trial_id": _TRIAL_ID,
                            "seed": _SEED,
                            "condition": condition,
                            "episodes": 3,
                            "status": "not_reordered",
                            "grade": {"optimal": 3, "feasible": 3},
                            "observations": (
                                {"dispatch": "reconstructed"}
                                if condition == "pooled"
                                else {
                                    "dispatch": "reconstructed",
                                    "inventory": "reconstructed",
                                }
                            ),
                            "truthful_rules": None,
                            "delivered_rules": None,
                            "delivered_with_incorrect_inventory": None,
                        },
                    )
