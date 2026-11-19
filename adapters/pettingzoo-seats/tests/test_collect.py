"""Census statuses for the frozen 8-cell PettingZoo study."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("pz_collect", _ROOT / "collect.py")
assert _SPEC and _SPEC.loader
collect_mod = importlib.util.module_from_spec(_SPEC)
sys.path.insert(0, str(_ROOT))
_SPEC.loader.exec_module(collect_mod)

from study import CELLS  # noqa: E402

_UUID = "11111111-2222-4333-8444-5555555555ab"


def _plan(root: Path, *, drop: str | None = None) -> None:
    cells = []
    for cid, game, _spec, p0, p1 in CELLS:
        if cid == drop:
            continue
        cells.append(
            {
                "id": cid,
                "game": game,
                "policies": {"p0": p0, "p1": p1},
                "trial_name": cid,
            }
        )
    (root / "plan.json").write_text(
        json.dumps({"schema": "seatlab.pz-plan/v1", "cells": cells}),
        encoding="utf-8",
    )


class CollectTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_bad_plan_writes_nothing(self) -> None:
        _plan(self.root, drop="rps-lh")
        out = self.root / "out"
        with self.assertRaises(ValueError):
            collect_mod.collect(self.root, out)
        self.assertFalse(out.exists())
        self.assertFalse(any(self.root.glob(".pz-census-*")))

    def test_unattempted_keeps_registered_denominator(self) -> None:
        _plan(self.root)
        out = self.root / "out"
        index = collect_mod.collect(self.root, out)
        self.assertEqual(index["counts"]["registered"], 8)
        self.assertEqual(index["counts"]["unattempted"], 8)
        self.assertEqual(index["counts"]["measured"], 0)
        self.assertIsNone(index["cells"][0]["returns"])
        self.assertIsNone(index["cells"][0]["completion"])
        self.assertEqual(list((out / "references").iterdir()), [])

    def test_duplicate_uuid_is_distinct_and_has_no_numbers(self) -> None:
        _plan(self.root)
        for cid in ("rps-ll-a", "rps-ll-b"):
            (self.root / "attempts" / cid).mkdir(parents=True)
            trial = self.root / "trials" / cid
            trial.mkdir(parents=True)
            (trial / "result.json").write_text(
                json.dumps({"id": _UUID}), encoding="utf-8"
            )
        out = self.root / "out"
        index = collect_mod.collect(self.root, out)
        rows = {row["id"]: row for row in index["cells"]}
        for cid in ("rps-ll-a", "rps-ll-b"):
            self.assertEqual(rows[cid]["status"], "duplicate_uuid")
            self.assertIsNone(rows[cid]["returns"])
            self.assertIsNone(rows[cid]["completion"])
            self.assertFalse((out / "cells" / cid).exists())
        self.assertEqual(index["counts"]["registered"], 8)
        self.assertEqual(index["counts"]["duplicate_uuid"], 2)
        self.assertEqual(index["counts"]["unattempted"], 6)
        self.assertEqual(list((out / "references").iterdir()), [])

    def test_attempted_launch_without_trial_is_not_unattempted(self) -> None:
        _plan(self.root)
        (self.root / "attempts" / "rps-ll-a").mkdir(parents=True)
        out = self.root / "out"
        index = collect_mod.collect(self.root, out)
        row = next(item for item in index["cells"] if item["id"] == "rps-ll-a")
        self.assertEqual(row["status"], "invalid")
        self.assertEqual(row["reason"], "missing_trial")
        self.assertTrue(row["attempted"])
        self.assertIsNone(row["returns"])
        self.assertIsNone(row["completion"])
        self.assertEqual(index["counts"]["unattempted"], 7)
        self.assertEqual(index["counts"]["measured"], 0)
        self.assertFalse((out / "cells" / "rps-ll-a").exists())

    def test_unclaimed_trial_is_not_measured(self) -> None:
        _plan(self.root)
        trial = self.root / "trials" / "connect-ll-a"
        trial.mkdir(parents=True)
        (trial / "result.json").write_text(json.dumps({"id": _UUID}), encoding="utf-8")
        out = self.root / "out"
        index = collect_mod.collect(self.root, out)
        row = next(item for item in index["cells"] if item["id"] == "connect-ll-a")
        self.assertEqual(row["status"], "invalid")
        self.assertEqual(row["reason"], "unclaimed_trial")
        self.assertFalse(row["attempted"])
        self.assertIsNone(row["native_trial_id"])
        self.assertIsNone(row["returns"])
        self.assertEqual(index["counts"]["measured"], 0)
        self.assertFalse((out / "cells" / "connect-ll-a").exists())


if __name__ == "__main__":
    unittest.main()
