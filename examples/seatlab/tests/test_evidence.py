import json
from copy import deepcopy
from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import TestCase

_SEATLAB = Path(__file__).resolve().parents[1]
_SCORE_PATH = _SEATLAB.parent / "tasks/dispatch-handoff/tests/score.py"
score = run_path(str(_SCORE_PATH))["score"]
_EVIDENCE = run_path(str(_SEATLAB / "evidence.py"))
read_trial = _EVIDENCE["read_trial"]
read_trial_with_world = _EVIDENCE["read_trial_with_world"]

_TRIAL_ID = "10e6cc6c-3107-4ad4-86cf-b91d3951b498"
_ARRIVALS = {
    "silent": (),
    "stale": (0,),
    "fresh": (1,),
    "reordered": (1, 0),
    "pooled": (),
}


def _dossier(condition: str) -> dict[str, Any]:
    cases = []
    for case_id, before, after, chosen in (
        (0, ["A", "B"], ["A", "B"], "B"),
        (1, ["A", "C"], ["A", "D"], "A"),
    ):
        # The inventory report is structurally valid but intentionally wrong.
        reports = [
            {"revision": 1, "available": ["B", "C"]},
            {"revision": 2, "available": ["B", "D"]},
        ]
        cases.append(
            {
                "id": case_id,
                "utilities": {"A": 4, "B": 3, "C": 2, "D": 1},
                "before": before,
                "after": after,
                "reports": None if condition == "pooled" else reports,
                "visible": [deepcopy(reports[i]) for i in _ARRIVALS[condition]],
                "chosen": chosen,
            }
        )
    return {
        "schema": "seatlab.world/v1",
        "settings": {
            "schema": "seatlab.scenario/v1",
            "condition": condition,
            "seed": 17,
            "episodes": len(cases),
        },
        "complete": True,
        "cases": cases,
    }


def _native(condition: str) -> dict[str, Any]:
    inventory_agent = "nop" if condition == "pooled" else "oracle"
    return {
        "schema_version": 1,
        "id": _TRIAL_ID,
        "finished_at": "2026-10-01T00:00:02Z",
        "reward_valid": True,
        "exception_info": None,
        "verifier_result": {"rewards": {"reward": 0.5}},
        "config": {
            "participants": [
                {"seat": "inventory", "name": inventory_agent},
                {"seat": "dispatch", "import_path": "seat_agent:SeatAgent"},
            ]
        },
        # Native seat order does not tell the reader which role earned this usage.
        "seat_results": [
            {
                "seat": seat,
                "under_test": True,
                "exception_info": None,
                "agent_info": {"name": name, "version": "1.0.0", "model_info": model},
                "agent_result": context,
                "agent_execution": {
                    "started_at": "2026-10-01T00:00:00Z",
                    "finished_at": "2026-10-01T00:00:01Z",
                },
            }
            for seat, name, model, context in (
                (
                    "dispatch",
                    "seatlab",
                    {"name": "example-model", "provider": None},
                    {
                        "n_input_tokens": 7,
                        "n_cache_tokens": 0,
                        "n_output_tokens": 0,
                        "cost_usd": 0.0,
                    },
                ),
                (
                    "inventory",
                    inventory_agent,
                    None,
                    {
                        "n_input_tokens": None,
                        "n_cache_tokens": None,
                        "n_output_tokens": None,
                        "cost_usd": None,
                    },
                ),
            )
        ],
    }


def _write_trial(root: Path, native: dict[str, Any], dossier: dict[str, Any]) -> None:
    artifact = root / "artifacts/logs/world/handoff.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(dossier), encoding="utf-8")
    (root / "result.json").write_text(json.dumps(native), encoding="utf-8")


class DispatchEvidenceTest(TestCase):
    def test_grade_uses_truth_not_inventory_reports_in_all_conditions(self) -> None:
        for condition in _ARRIVALS:
            with self.subTest(condition=condition):
                self.assertEqual(
                    score(_dossier(condition)),
                    {
                        "episodes": 2,
                        "optimal": 1,
                        "feasible": 2,
                        "changed": 1,
                        "rows": [
                            {
                                "id": 0,
                                "optimal": False,
                                "feasible": True,
                                "changed": False,
                            },
                            {
                                "id": 1,
                                "optimal": True,
                                "feasible": True,
                                "changed": True,
                            },
                        ],
                    },
                )

    def test_grade_rejects_visible_rows_that_differ_from_accepted_reports(self) -> None:
        for condition in _ARRIVALS:
            with self.subTest(condition=condition):
                dossier = _dossier(condition)
                visible = dossier["cases"][0]["visible"]
                if condition == "reordered":
                    visible.reverse()
                elif visible:
                    visible[0]["available"] = ["A", "B"]
                else:
                    visible.append({"revision": 2, "available": ["B", "D"]})
                with self.assertRaises(ValueError):
                    score(dossier)

    def test_grade_rejects_incomplete_or_inconsistent_truth(self) -> None:
        for name, changes in (
            ("duplicate id", {"id": 0}),
            ("no dispatch choice", {"chosen": None}),
            ("no required change", {"after": ["A", "C"]}),
            ("utility tie", {"utilities": {"A": 4, "B": 4, "C": 2, "D": 1}}),
            ("missing row data", {"reports": None}),
        ):
            with self.subTest(name=name):
                dossier = _dossier("fresh")
                dossier["cases"][1].update(changes)
                with self.assertRaises(ValueError):
                    score(dossier)
        dossier = _dossier("fresh")
        dossier["cases"].pop()
        with self.assertRaises(ValueError):
            score(dossier)

    def test_native_identity_usage_and_matched_world_fingerprint(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            dossier = _dossier("fresh")
            _write_trial(root / "one", _native("fresh"), dossier)
            pooled_native = _native("pooled")
            pooled_native["id"] = "1884d922-996e-4521-8056-2ae5b8d91556"
            _write_trial(root / "two", pooled_native, _dossier("pooled"))
            fresh, checked_world = read_trial_with_world(root / "one")
            self.assertEqual(checked_world, dossier)
            pooled = read_trial(root / "two")

            dossier["cases"][0].update(before=["A", "C"], after=["A", "C"])
            _write_trial(root / "one", _native("fresh"), dossier)
            different = read_trial(root / "one")
        self.assertEqual(fresh["trial_id"], _TRIAL_ID)
        self.assertRegex(fresh["fingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual(fresh["fingerprint"], pooled["fingerprint"])
        self.assertNotEqual(fresh["fingerprint"], different["fingerprint"])
        inventory = fresh["seats"]["inventory"]
        dispatch = fresh["seats"]["dispatch"]
        self.assertEqual(inventory["agent_name"], "oracle")
        self.assertEqual(pooled["seats"]["inventory"]["agent_name"], "nop")
        self.assertIsNone(inventory["n_output_tokens"])
        self.assertIsNone(inventory["cost_usd"])
        self.assertEqual(dispatch["n_input_tokens"], 7)
        self.assertEqual(dispatch["n_output_tokens"], 0)
        self.assertEqual(dispatch["cost_usd"], 0.0)

    def test_native_rejects_inconsistent_reward_or_seat_records(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            dossier = _dossier("fresh")
            dispatch, inventory = _native("fresh")["seat_results"]
            for name, changes in (
                ("missing id", {"id": None}),
                ("invalid id", {"id": "named-trial"}),
                ("mismatched reward", {"verifier_result": {"rewards": {"reward": 1}}}),
                ("invalid reward", {"reward_valid": False}),
                ("missing inventory", {"seat_results": [dispatch]}),
                ("extra seat", {"seat_results": [dispatch, inventory, dispatch]}),
                ("duplicate dispatch", {"seat_results": [dispatch, dispatch]}),
                (
                    "unknown seat",
                    {"seat_results": [dispatch, {**inventory, "seat": "spectator"}]},
                ),
                (
                    "seat failed",
                    {"seat_results": [dispatch, {**inventory, "exception_info": {}}]},
                ),
                (
                    "missing usage context",
                    {"seat_results": [dispatch, {**inventory, "agent_result": None}]},
                ),
            ):
                with self.subTest(name=name):
                    _write_trial(root, {**_native("fresh"), **changes}, dossier)
                    with self.assertRaises(ValueError):
                        read_trial(root)

    def test_native_exception_does_not_copy_private_exception_text(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            native = _native("fresh")
            native["exception_info"] = {
                "exception_type": "AgentTimeoutError",
                "exception_message": "private provider output",
            }
            _write_trial(root, native, _dossier("fresh"))
            with self.assertRaisesRegex(ValueError, r"\(AgentTimeoutError\)$"):
                read_trial(root)

    def test_valid_zero_differs_from_incomplete_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            native = _native("fresh")
            native["verifier_result"]["rewards"]["reward"] = 0
            dossier = _dossier("fresh")
            dossier["cases"][0]["chosen"] = "D"
            dossier["cases"][1]["chosen"] = "C"
            _write_trial(root, native, dossier)
            outcome = read_trial(root)["score"]
            self.assertEqual(outcome["optimal"], 0)
            self.assertEqual(outcome["feasible"], 0)
            dossier["complete"] = False
            _write_trial(root, native, dossier)
            with self.assertRaises(ValueError):
                read_trial(root)
            (root / "artifacts/logs/world/handoff.json").unlink()
            with self.assertRaises(FileNotFoundError):
                read_trial(root)
