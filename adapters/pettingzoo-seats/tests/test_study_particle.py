"""Prepared particle suite: two cells and three native oracle seats."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "study.py"
_SPEC = importlib.util.spec_from_file_location("pz_study_particle", _PATH)
assert _SPEC and _SPEC.loader
study = importlib.util.module_from_spec(_SPEC)
sys.modules["pz_study_particle"] = study
_SPEC.loader.exec_module(study)


class ParticlePrepareTest(unittest.TestCase):
    def test_two_cells_and_native_roles(self) -> None:
        self.assertEqual(len(study.CELLS), 8)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "particle"
            study.prepare_particle(root)
            plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["schema"], "seatlab.pz-particle-plan/v1")
            self.assertEqual(
                [cell["id"] for cell in plan["cells"]], ["spread-lll", "spread-llh"]
            )
            self.assertEqual(
                [cell["policies"] for cell in plan["cells"]],
                [
                    {"p0": "low", "p1": "low", "p2": "low"},
                    {"p0": "low", "p1": "low", "p2": "high"},
                ],
            )
            for cell in plan["cells"]:
                cfg = json.loads(
                    (root / "configs" / f"{cell['id']}.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(
                    [item["seat"] for item in cfg["participants"]], ["p0", "p1", "p2"]
                )
                self.assertTrue(
                    all(item["name"] == "oracle" for item in cfg["participants"])
                )
                spec = json.loads(
                    (
                        root
                        / "tasks"
                        / cell["trial_name"]
                        / "environment"
                        / "spec.json"
                    ).read_text()
                )
                self.assertEqual(spec["seed"], 19)
                self.assertEqual(set(spec["roles"]), {"p0", "p1", "p2"})


if __name__ == "__main__":
    unittest.main()
