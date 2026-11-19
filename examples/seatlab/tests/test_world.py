from copy import deepcopy
from pathlib import Path
from runpy import run_path
from unittest import TestCase

_WORLD_PATH = (
    Path(__file__).resolve().parents[2]
    / "tasks"
    / "dispatch-handoff"
    / "environment"
    / "seatworld.py"
)
_WORLD = run_path(str(_WORLD_PATH))
World, NotReady, BAYS = _WORLD["World"], _WORLD["NotReady"], _WORLD["BAYS"]


def settings(condition: str, seed: int = 17, episodes: int = 4) -> dict[str, object]:
    return {
        "schema": "seatlab.scenario/v1",
        "seed": seed,
        "episodes": episodes,
        "condition": condition,
    }


class WorldTest(TestCase):
    def test_matched_cases_and_condition_visibility(self) -> None:
        expected_arrivals = {
            "silent": [],
            "stale": [1],
            "fresh": [2],
            "reordered": [2, 1],
            "pooled": [],
        }
        reference = World(settings("pooled")).evidence()["cases"]
        for condition, revisions in expected_arrivals.items():
            with self.subTest(condition=condition):
                config = settings(condition)
                world = World(config)
                self.assertFalse(world.evidence()["complete"])
                inventory = world.view("inventory")
                self.assertEqual(set(inventory), {"role", "condition", "cases"})
                self.assertEqual(inventory["role"], "inventory")
                self.assertEqual(inventory["condition"], condition)
                rows = inventory["cases"]
                choices = {"rows": [{"id": row["id"], "bay": "A"} for row in rows]}
                if condition != "pooled":
                    with self.assertRaises(NotReady):
                        world.view("dispatch")
                    with self.assertRaises(NotReady):
                        world.submit("dispatch", choices)
                    world.submit("inventory", {"rows": rows})
                    self.assertFalse(world.evidence()["complete"])

                dispatch = world.view("dispatch")
                self.assertEqual(set(dispatch), {"role", "condition", "cases"})
                self.assertEqual(dispatch["role"], "dispatch")
                self.assertEqual(dispatch["condition"], condition)
                world.submit("dispatch", choices)
                evidence = world.evidence()
                self.assertTrue(evidence["complete"])
                self.assertEqual(evidence["schema"], "seatlab.world/v1")
                self.assertEqual(evidence["settings"], config)
                for i, (truth, report, decision, recorded) in enumerate(
                    zip(
                        reference,
                        rows,
                        dispatch["cases"],
                        evidence["cases"],
                        strict=True,
                    )
                ):
                    before, after = truth["before"], truth["after"]
                    self.assertEqual(len(set(before)), 2)
                    self.assertEqual(len(set(after)), 2)
                    self.assertTrue(set(before + after) <= set(BAYS))
                    self.assertEqual(len(set(before) - set(after)), i % 2)
                    self.assertEqual(set(truth["utilities"]), set(BAYS))
                    self.assertEqual(sorted(truth["utilities"].values()), [1, 2, 3, 4])
                    reports = [
                        {"revision": 1, "available": before},
                        {"revision": 2, "available": after},
                    ]
                    messages = [reports[revision - 1] for revision in revisions]
                    self.assertEqual(report, {"id": i, "reports": reports})
                    self.assertEqual(
                        decision,
                        {
                            "id": i,
                            "utilities": truth["utilities"],
                            "messages": messages,
                            "pooled_available": after
                            if condition == "pooled"
                            else None,
                        },
                    )
                    self.assertEqual(
                        recorded,
                        {
                            **truth,
                            "reports": None if condition == "pooled" else reports,
                            "visible": messages,
                            "chosen": "A",
                        },
                    )

    def test_rejects_invalid_settings(self) -> None:
        for change in (
            {"schema": "seatlab.world/v1"},
            {"seed": True},
            {"seed": "17"},
            {"episodes": False},
            {"episodes": 0},
            {"episodes": 257},
            {"condition": []},
            {"condition": "unknown"},
            {"unexpected": 1},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                World({**settings("fresh"), **change})
        for episodes in (1, 256):
            with self.subTest(episodes=episodes):
                world = World(settings("fresh", seed=-1, episodes=episodes))
                self.assertEqual(len(world.view("inventory")["cases"]), episodes)

    def test_invalid_submissions_do_not_partially_commit(self) -> None:
        world = World(settings("fresh", episodes=2))
        first, second = world.view("inventory")["cases"]
        initial = world.evidence()
        for bad_batch in (
            None,
            {"rows": [first]},
            {"rows": [first, first]},
            {"rows": [first, {**second, "id": True}]},
            {"rows": [first, {**second, "id": 2}]},
            {"rows": [first, {**second, "reports": [second["reports"][0]]}]},
            {"rows": [first, {**second, "reports": [second["reports"][0]] * 2}]},
            {"rows": [first, second], "role": "dispatch"},
        ):
            with self.subTest(batch=bad_batch), self.assertRaises(ValueError):
                world.submit("inventory", bad_batch)
        for change in (
            {"revision": True},
            {"revision": 3},
            {"available": ["A", "A"]},
            {"available": ["A", "Z"]},
            {"available": "AB"},
        ):
            bad_report = {**second["reports"][0], **change}
            bad_row = {**second, "reports": [bad_report, second["reports"][1]]}
            with self.subTest(change=change), self.assertRaises(ValueError):
                world.submit("inventory", {"rows": [first, bad_row]})
        self.assertEqual(world.evidence(), initial)
        with self.assertRaises(NotReady):
            world.view("dispatch")
        with self.assertRaises(ValueError):
            world.view("unknown")
        with self.assertRaises(ValueError):
            world.submit("unknown", {})

        world.submit("inventory", {"rows": [first, second]})
        before_dispatch = world.evidence()
        choice = {"id": 0, "bay": "A"}
        for bad_batch in (
            {"rows": [choice]},
            {"rows": [choice, {"id": 1, "bay": "Z"}]},
            {"rows": [choice, {"id": 1, "bay": "B", "utilities": {}}]},
        ):
            with self.subTest(batch=bad_batch), self.assertRaises(ValueError):
                world.submit("dispatch", bad_batch)
        self.assertEqual(world.evidence(), before_dispatch)
        world.submit("dispatch", {"rows": [choice, {"id": 1, "bay": "B"}]})
        self.assertTrue(world.evidence()["complete"])

    def test_valid_wrong_actions_are_accepted_and_sealed(self) -> None:
        world = World(settings("reordered"))
        truth = world.view("inventory")["cases"]
        false_reports = [
            {
                "id": row["id"],
                "reports": [
                    {
                        "revision": report["revision"],
                        "available": sorted(set(BAYS) - set(report["available"])),
                    }
                    for report in row["reports"]
                ],
            }
            for row in truth
        ]
        wrong_choices = [
            {"id": row["id"], "bay": row["reports"][1]["available"][0]}
            for row in false_reports
        ]
        inventory_ack = world.submit("inventory", {"rows": false_reports})
        for row, report in zip(
            world.view("dispatch")["cases"], false_reports, strict=True
        ):
            self.assertEqual(row["messages"], list(reversed(report["reports"])))
        dispatch_ack = world.submit("dispatch", {"rows": wrong_choices})
        sealed = world.evidence()
        self.assertTrue(sealed["complete"])
        for row in sealed["cases"]:
            self.assertNotIn(row["chosen"], row["after"])

        unordered_reports = deepcopy(list(reversed(false_reports)))
        for row in unordered_reports:
            row["reports"].reverse()
            for report in row["reports"]:
                report["available"].reverse()
        self.assertEqual(
            world.submit("inventory", {"rows": unordered_reports}), inventory_ack
        )
        self.assertEqual(
            world.submit("dispatch", {"rows": list(reversed(wrong_choices))}),
            dispatch_ack,
        )
        with self.assertRaises(ValueError):
            world.submit("inventory", {"rows": truth})
        new_choices = deepcopy(wrong_choices)
        new_choices[0]["bay"] = sealed["cases"][0]["after"][0]
        with self.assertRaises(ValueError):
            world.submit("dispatch", {"rows": new_choices})
        self.assertEqual(world.evidence(), sealed)

    def test_pooled_inventory_cannot_commit_late(self) -> None:
        world = World(settings("pooled", episodes=1))
        dispatch = world.view("dispatch")
        pooled_available = dispatch["cases"][0]["pooled_available"].copy()
        dispatch["cases"][0]["pooled_available"].clear()
        self.assertEqual(
            world.view("dispatch")["cases"][0]["pooled_available"], pooled_available
        )
        world.submit("dispatch", {"rows": [{"id": 0, "bay": "A"}]})
        sealed = world.evidence()
        self.assertTrue(sealed["complete"])
        with self.assertRaises(ValueError):
            world.submit("inventory", {"rows": world.view("inventory")["cases"]})
        self.assertEqual(world.evidence(), sealed)

    def test_worlds_and_public_values_are_isolated(self) -> None:
        config = settings("fresh")
        first = World(config)
        unrelated = World(settings("stale", seed=29))
        second = World(settings("fresh"))
        initial = first.evidence()
        self.assertEqual(second.evidence(), initial)
        config["condition"] = "silent"
        config["seed"] = 29
        self.assertNotEqual(unrelated.evidence()["cases"], initial["cases"])
        self.assertEqual(first.evidence(), initial)

        rows = first.view("inventory")["cases"]
        original_rows = deepcopy(rows)
        rows[0]["reports"][0]["available"].clear()
        self.assertEqual(first.view("inventory")["cases"], original_rows)
        response = first.submit("inventory", {"rows": original_rows})
        self.assertEqual(second.evidence(), initial)
        public = first.view("dispatch")
        original_public = deepcopy(public)
        original_rows[0]["reports"][1]["available"].clear()
        response["accepted"] = False
        public["cases"][0]["messages"][0]["available"].clear()
        public["cases"][0]["utilities"].clear()
        self.assertEqual(first.view("dispatch"), original_public)
        self.assertEqual(
            first.submit("inventory", {"rows": first.view("inventory")["cases"]}),
            {"accepted": True},
        )

        choices = {"rows": [{"id": row["id"], "bay": "B"} for row in rows]}
        first.submit("dispatch", choices)
        choices["rows"][0]["bay"] = "C"
        recorded = first.evidence()
        copy = first.evidence()
        self.assertEqual(recorded["cases"][0]["chosen"], "B")
        copy["settings"]["seed"] = 29
        for field in ("reports", "visible"):
            copy["cases"][0][field][0]["available"].clear()
        for field in ("before", "after", "utilities"):
            copy["cases"][0][field].clear()
        self.assertEqual(first.evidence(), recorded)
        self.assertEqual(second.evidence(), initial)

        second_rows = second.view("inventory")["cases"]
        second.submit("inventory", {"rows": second_rows[::-1]})
        choices["rows"][0]["bay"] = "B"
        second.submit("dispatch", {"rows": choices["rows"][::-1]})
        self.assertEqual(second.evidence(), recorded)
