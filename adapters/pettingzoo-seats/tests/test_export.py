"""Small checks for native PettingZoo trial projection."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from seatlab.tape import digest
from seatlab.world_order import read_world_order

_PATH = Path(__file__).resolve().parent.parent / "export.py"
_SPEC = importlib.util.spec_from_file_location("pz_export", _PATH)
assert _SPEC and _SPEC.loader
export = importlib.util.module_from_spec(_SPEC)
sys.modules["pz_export"] = export
_SPEC.loader.exec_module(export)

_TID = "11111111-2222-4333-8444-5555555555ab"
_ROLES = {"p0": "player_0", "p1": "player_1"}


def _view(role: str, n: int) -> dict[str, object]:
    return {"observation": [n], "legal_actions": [0, 1], "role": role}


def _trial(
    root: Path,
    *,
    tid: str = _TID,
    ack: str | None = None,
    drop_audit: str | None = None,
) -> Path:
    trial = root / "trial"
    views = {role: _view(role, i) for i, role in enumerate(_ROLES)}
    shas = {role: digest(view) for role, view in views.items()}
    steps = [
        {
            "step": 1,
            "decisions": {
                role: {
                    "view": views[role],
                    "view_sha256": ack or shas[role],
                    "action": i,
                }
                for i, role in enumerate(_ROLES)
            },
        }
    ]
    doc = {
        "schema": "seatlab.pz-native/v1",
        "case_sha256": digest({"game": "rps"}),
        "roles": _ROLES,
        "steps": steps,
        "returns": {"p0": 1, "p1": -1},
        "terminated": {"p0": True, "p1": True},
        "truncated": {"p0": False, "p1": False},
    }
    world = trial / "artifacts" / "logs" / "world"
    world.mkdir(parents=True)
    (world / "pettingzoo.json").write_text(json.dumps(doc))
    (trial / "result.json").write_text(
        json.dumps(
            {
                "id": tid,
                "finished_at": "2026-11-19T12:00:10Z",
                "exception_info": None,
                "reward_valid": True,
                "verifier_result": {"rewards": {"reward": 1}},
                "seat_results": [
                    {
                        "seat": role,
                        "under_test": True,
                        "exception_info": None,
                        "agent_execution": {"finished_at": "2026-11-19T12:00:08Z"},
                    }
                    for role in _ROLES
                ],
            }
        )
    )
    (trial / "verifier").mkdir()
    (trial / "verifier" / "reward.txt").write_text("1\n")
    for i, role in enumerate(_ROLES):
        if role == drop_audit:
            continue
        agent = trial / "seats" / role / "agent"
        agent.mkdir(parents=True)
        row = {"step": 1, "view_sha256": ack or shas[role], "action": i}
        (agent / "pz_actions.jsonl").write_text(json.dumps(row) + "\n")
    return trial


class ExportTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_keeps_completion_and_opposing_payoffs(self) -> None:
        trial = _trial(self.root)
        out = self.root / "out"
        outcomes = export.project(trial, out)
        self.assertEqual(outcomes["completion"], 1)
        self.assertEqual(outcomes["returns"], {"p0": 1, "p1": -1})
        self.assertNotIn("team", json.dumps(outcomes))
        order = read_world_order(out / "episodes.jsonl", out / "order.json")
        self.assertEqual(order.native_trial_id, _TID)
        self.assertEqual(order.steps, (("p0", "p1"),))
        self.assertEqual(outcomes["terminated"], {"p0": True, "p1": True})

    def test_completion_scalar_does_not_close_an_unfinished_player(self) -> None:
        trial = _trial(self.root)
        log = trial / "artifacts/logs/world/pettingzoo.json"
        data = json.loads(log.read_text())
        data["terminated"]["p1"] = False
        log.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "final state"):
            export.project(trial, self.root / "out")
        self.assertFalse((self.root / "out").exists())

    def test_wrong_uuid_ack_and_missing_audit_leave_no_output(self) -> None:
        for name, kwargs in (
            ("uuid", {"tid": "not-a-uuid"}),
            ("ack", {"ack": "0" * 64}),
            ("audit", {"drop_audit": "p1"}),
        ):
            with self.subTest(name):
                root = self.root / name
                root.mkdir()
                trial = _trial(root, **kwargs)
                out = root / "out"
                with self.assertRaises(ValueError):
                    export.project(trial, out)
                self.assertFalse(out.exists())
                self.assertFalse(any(root.glob(".pz-export-*")))


if __name__ == "__main__":
    unittest.main()
