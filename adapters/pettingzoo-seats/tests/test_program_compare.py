"""One cross-check: action mismatch, absent cell, and refusal to score a failure."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from seatlab.tape import digest

_PATH = Path(__file__).resolve().parent.parent / "program_compare.py"
_SPEC = importlib.util.spec_from_file_location("pz_program_compare", _PATH)
assert _SPEC and _SPEC.loader
compare_mod = importlib.util.module_from_spec(_SPEC)
sys.modules["pz_program_compare"] = compare_mod
_SPEC.loader.exec_module(compare_mod)

PROG = "11111111-2222-4333-8444-5555555555ab"
REF = "22222222-3333-4444-8555-6666666666cd"
BAD = "33333333-4444-4555-8666-7777777777ef"
CASE = digest({"seed": 19})
VIEW = {"observation": 0, "legal_actions": [0, 1, 2]}
SHA = digest(VIEW)


def _plan(root: Path, schema: str, cells: list[dict[str, object]]) -> None:
    (root / "plan.json").write_text(
        json.dumps({"schema": schema, "cells": cells}), encoding="utf-8"
    )
    (root / "configs").mkdir()
    for cell in cells:
        (root / "configs" / f"{cell['id']}.json").write_text(
            json.dumps({"trial_name": cell["trial_name"]}), encoding="utf-8"
        )


def _trial(
    root: Path, name: str, tid: str, actions: dict[str, int], *, meta: str
) -> None:
    trial = root / "trials" / name
    decisions = {
        role: {"view": VIEW, "view_sha256": SHA, "action": action}
        for role, action in actions.items()
    }
    world = {
        "schema": "seatlab.pz-native/v1",
        "case_sha256": CASE,
        "roles": {"p0": "player_0", "p1": "player_1"},
        "steps": [{"step": 1, "decisions": decisions}],
        "returns": {"p0": -1, "p1": 1},
        "terminated": {"p0": True, "p1": True},
        "truncated": {"p0": False, "p1": False},
    }
    logs = trial / "artifacts" / "logs" / "world"
    logs.mkdir(parents=True)
    (logs / "pettingzoo.json").write_text(json.dumps(world), encoding="utf-8")
    seats = []
    for role, action in actions.items():
        agent = trial / "seats" / role / "agent"
        agent.mkdir(parents=True)
        (agent / "pz_actions.jsonl").write_text(
            json.dumps({"step": 1, "view_sha256": SHA, "action": action}) + "\n",
            encoding="utf-8",
        )
        result = {
            "metadata": {"program_calls": 1, "role": role, "stage": "done"},
        }
        if role == meta:
            (agent / "trajectory.json").write_text(
                json.dumps({"extra": {"metadata": result["metadata"]}}),
                encoding="utf-8",
            )
            result = {}
        seats.append(
            {
                "seat": role,
                "under_test": True,
                "exception_info": None,
                "agent_info": {"name": "program-seat", "version": "1"},
                "agent_result": result,
                "agent_execution": {"finished_at": "2026-11-19T12:00:08Z"},
            }
        )
    (trial / "result.json").write_text(
        json.dumps(
            {
                "id": tid,
                "finished_at": "2026-11-19T12:00:10Z",
                "exception_info": None,
                "reward_valid": True,
                "verifier_result": {"rewards": {"reward": 1.0}},
                "seat_results": seats,
            }
        ),
        encoding="utf-8",
    )
    verifier = trial / "verifier"
    verifier.mkdir()
    (verifier / "reward.txt").write_text("1\n", encoding="utf-8")


class ProgramCompareTests(unittest.TestCase):
    def test_mismatch_unknown_and_refusal(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        program, native = root / "program", root / "native"
        program.mkdir()
        native.mkdir()
        _plan(
            program,
            "seatlab.pz-program-plan/v1",
            [
                {
                    "id": "program-mixed",
                    "reference": "rps-copy-beat",
                    "trial_name": "program-mixed",
                },
                {
                    "id": "program-both",
                    "reference": "rps-copy-beat",
                    "trial_name": "program-both",
                },
                {
                    "id": "program-second",
                    "reference": "connect-lh",
                    "trial_name": "program-second",
                },
                {
                    "id": "program-invalid",
                    "reference": None,
                    "trial_name": "program-invalid",
                },
            ],
        )
        _plan(
            native,
            "seatlab.pz-plan/v1",
            [
                {
                    "id": "rps-copy-beat",
                    "game": "rps_v2",
                    "policies": {"p0": "rps-copy", "p1": "rps-beat-last"},
                    "trial_name": "rps-copy-beat",
                },
                {
                    "id": "connect-lh",
                    "game": "connect_four_v3",
                    "policies": {"p0": "low", "p1": "high"},
                    "trial_name": "connect-lh",
                },
            ],
        )
        _trial(program, "program-both", PROG, {"p0": 0, "p1": 0}, meta="p1")
        _trial(native, "rps-copy-beat", REF, {"p0": 0, "p1": 1}, meta="p0")
        failed = program / "trials" / "program-invalid"
        failed.mkdir(parents=True)
        (failed / "result.json").write_text(
            json.dumps(
                {
                    "id": BAD,
                    "exception_info": {
                        "exception_type": "ValueError",
                        "exception_message": "/tmp/secret/not-a-payoff",
                    },
                    "seat_results": [
                        {
                            "seat": "p0",
                            "agent_info": {"name": "/tmp/secret/agent", "version": "1"},
                            "agent_result": {"metadata": {"program_calls": 1}},
                            "agent_execution": {"finished_at": "2026-11-19T12:00:08Z"},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        out = root / "fresh"
        stdout = io.BytesIO()

        class _Capture:
            buffer = stdout

        old = sys.stdout
        sys.stdout = _Capture()
        try:
            code = compare_mod.main(
                [
                    str(program),
                    str(native),
                    "--out",
                    str(out),
                    "--python",
                    sys.executable,
                ]
            )
        finally:
            sys.stdout = old
        self.assertEqual(code, 0)
        report = json.loads((out / "index.json").read_text(encoding="utf-8"))
        text = json.dumps(report)
        self.assertEqual(report["schema"], "seatlab.pz-program-compare/v1")
        self.assertNotIn("matched", {row["status"] for row in report["cells"]})
        self.assertNotIn("score", report)
        self.assertNotIn("/tmp/secret", text)
        self.assertNotIn("/tmp/secret/not-a-payoff", text)
        self.assertEqual(list(out.iterdir()), [out / "index.json"])
        self.assertEqual(report["denominator"], 4)
        self.assertEqual(report["unique_native_ids"], 3)
        self.assertEqual(report["observed_external_calls"], 3)
        rows = {row["id"]: row for row in report["cells"]}
        both = rows["program-both"]
        self.assertEqual(both["status"], "checked")
        self.assertEqual(both["completion"], 1.0)
        self.assertIs(type(both["completion"]), float)
        self.assertEqual(both["returns"], {"p0": -1, "p1": 1})
        self.assertNotEqual(both["completion"], both["returns"])
        self.assertTrue(both["case_equal"])
        self.assertTrue(both["returns_equal"])
        self.assertEqual(
            both["first_mismatch"], {"step": 1, "role": "p1", "kind": "action"}
        )
        self.assertEqual(both["roles"]["p0"]["comparisons"], 1)
        self.assertEqual(both["roles"]["p0"]["agreements"], 1)
        self.assertEqual(both["roles"]["p1"]["comparisons"], 1)
        self.assertEqual(both["roles"]["p1"]["agreements"], 0)
        self.assertEqual(both["roles"]["p1"]["replay_calls"], 1)
        self.assertEqual(both["roles"]["p1"]["replay_agreements"], 0)
        self.assertEqual(both["roles"]["p1"]["observed_calls"], 1)
        self.assertEqual(both["roles"]["p0"]["observed_calls"], 1)
        self.assertEqual(both["roles"]["p0"]["participant"], "program-seat")
        self.assertTrue(both["roles"]["p0"]["executed"])
        self.assertIsNone(rows["program-mixed"]["returns"])
        self.assertIsNone(rows["program-mixed"]["completion"])
        self.assertEqual(rows["program-mixed"]["status"], "absent")
        self.assertEqual(rows["program-second"]["status"], "absent")
        invalid = rows["program-invalid"]
        self.assertEqual(invalid["status"], "failed")
        self.assertEqual(invalid["exception_type"], "ValueError")
        self.assertIsNone(invalid["returns"])
        self.assertIsNone(invalid["completion"])
        self.assertEqual(invalid["roles"]["p0"]["observed_calls"], 1)
        self.assertIsNone(invalid["roles"]["p0"]["participant"])
        self.assertIsNone(invalid["roles"]["p0"]["comparisons"])
        self.assertIsNone(invalid["roles"]["p0"]["agreements"])
        refused = root / "refused"
        with self.assertRaises(ValueError):
            compare_mod.compare(root / "missing-plan", native, refused)
        self.assertFalse(refused.exists())
        self.assertFalse(any(root.glob(".pz-program-compare-*")))


if __name__ == "__main__":
    unittest.main()
