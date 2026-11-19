"""Recorded branch comparison. A disagreement is a result, not a bad file."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from seatlab import author
from seatlab.tape import canonical, digest, write_lines
from seatlab.world_order import WORLD_ORDER_SCHEMA

_PATH = Path(__file__).resolve().parent.parent / "compare_branch.py"
_SPEC = importlib.util.spec_from_file_location("pz_compare_branch", _PATH)
assert _SPEC and _SPEC.loader
compare_branch = importlib.util.module_from_spec(_SPEC)
sys.modules["pz_compare_branch"] = compare_branch
_SPEC.loader.exec_module(compare_branch)

CASE = digest({"game": "fixture", "seed": 19})
OTHER = digest({"game": "other"})
SRC = "11111111-2222-4333-8444-5555555555ab"
TGT = "22222222-3333-4444-8555-6666666666cd"


def _view(role: str, n: int, mark: int = 0) -> dict[str, object]:
    return {"observation": [n, mark], "legal_actions": [0, 1], "role": role}


def _joint(
    actions: list[tuple[int, int]], mark: int = 0
) -> list[dict[str, tuple[dict[str, object], int]]]:
    script = []
    for step, pair in enumerate(actions, 1):
        script.append(
            {
                "p0": (_view("p0", step, mark), pair[0]),
                "p1": (_view("p1", step, mark), pair[1]),
            }
        )
    return script


def _sim(
    script: list[dict[str, tuple[dict[str, object], int]]], start: int
) -> list[dict[str, object]]:
    steps = []
    for step, joint in enumerate(script[start - 1 :], start):
        steps.append(
            {
                "step": step,
                "decisions": {
                    role: {"view": view, "view_sha256": digest(view), "action": action}
                    for role, (view, action) in joint.items()
                },
            }
        )
    return steps


class CompareBranchTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.n = 0

    def _publish(
        self,
        trial: str,
        script: list[dict[str, tuple[dict[str, object], int]]],
        *,
        case: str = CASE,
        returns: dict[str, int] | None = None,
        terminated: dict[str, bool] | None = None,
        truncated: dict[str, bool] | None = None,
        completion: int = 0,
    ) -> Path:
        self.n += 1
        folder = self.root / f"{trial}-{self.n}"
        folder.mkdir()
        per: dict[str, list[tuple[dict[str, object], int]]] = {"p0": [], "p1": []}
        for joint in script:
            for role, item in joint.items():
                per[role].append(item)
        episodes = []
        for role in ("p0", "p1"):
            frames = [
                author.accepted_frame(
                    turn=i, view=view, acknowledged_sha256=digest(view), action=action
                )
                for i, (view, action) in enumerate(per[role], 1)
            ]
            episodes.append(
                author.episode_from_frames(
                    id=f"pz:{trial}:{role}",
                    case_sha256=case,
                    role=role,
                    native_trial_id=trial,
                    closed=True,
                    frames=frames,
                )
            )
        write_lines(folder / "episodes.jsonl", episodes)
        order = {
            "schema": WORLD_ORDER_SCHEMA,
            "case_sha256": case,
            "native_trial_id": trial,
            "episodes": {ep["role"]: ep["episode_sha256"] for ep in episodes},
            "steps": [sorted(joint) for joint in script],
        }
        (folder / "order.json").write_bytes(canonical(order) + b"\n")
        outcomes = {
            "schema": "seatlab.pz-outcomes/v1",
            "native_trial_id": trial,
            "case_sha256": case,
            "completion": completion,
            "returns": returns or {"p0": 1, "p1": -1},
            "terminated": terminated or {"p0": True, "p1": True},
            "truncated": truncated or {"p0": False, "p1": False},
        }
        (folder / "outcomes.json").write_bytes(canonical(outcomes) + b"\n")
        return folder

    def _branch(
        self,
        steps: list[dict[str, object]],
        *,
        at: int = 2,
        claimed: int = 99,
        returns: dict[str, int] | None = None,
        terminated: dict[str, bool] | None = None,
        truncated: dict[str, bool] | None = None,
        policies: dict[str, str] | None = None,
        case: str = CASE,
        trial: str = SRC,
    ) -> Path:
        self.n += 1
        path = self.root / f"branch-{self.n}.json"
        doc = {
            "schema": "seatlab.pz-branch/v1",
            "source_native_trial_id": trial,
            "case_sha256": case,
            "at_step": at,
            "matched_prefix_steps": claimed,
            "policies": policies or {"p0": "low", "p1": "low"},
            "simulated_steps": steps,
            "returns": returns or {"p0": 1, "p1": -1},
            "terminated": terminated or {"p0": True, "p1": True},
            "truncated": truncated or {"p0": False, "p1": False},
        }
        path.write_bytes(canonical(doc) + b"\n")
        return path

    def _run(self, branch: Path, source: Path, target: Path) -> dict[str, object]:
        return compare_branch.compare(branch, source, target)

    def test_same_policy_control_matches_without_trusting_claimed_length(self) -> None:
        script = _joint([(0, 0), (0, 0), (1, 1)])
        source = self._publish(SRC, script)
        target = self._publish(TGT, script, completion=0)
        report = self._run(self._branch(_sim(script, 2), claimed=0), source, target)
        self.assertEqual(report["status"], "measured")
        self.assertEqual(report["label"], "simulation")
        self.assertIs(report["prefix_mismatch"], False)
        self.assertEqual(report["measured_prefix_steps"], 1)
        self.assertIs(report["branch_views_equal"], True)
        self.assertIs(report["inputs_equal"], True)
        self.assertIs(report["actions_equal"], True)
        self.assertIsNone(report["first_mismatch"])
        self.assertEqual(report["returns_equal"], {"p0": True, "p1": True})
        self.assertEqual(report["terminated_equal"], {"p0": True, "p1": True})
        self.assertEqual(report["truncated_equal"], {"p0": True, "p1": True})
        self.assertNotIn("completion", report)
        self.assertNotIn("reward", report)
        self.assertNotIn("matched_prefix_steps", report)

    def test_negative_control_disagreement_is_completed_json(self) -> None:
        source_script = _joint([(0, 0), (0, 0), (1, 1)])
        target_script = _joint([(0, 0), (0, 1), (9, 9)])
        sim = _sim(source_script, 2)
        # Keep the low action. A later planted view must not become the mismatch.
        sim[1]["decisions"]["p0"]["view"] = {
            "observation": [9],
            "legal_actions": [0],
            "role": "p0",
        }
        sim[1]["decisions"]["p0"]["view_sha256"] = digest(
            sim[1]["decisions"]["p0"]["view"]
        )
        source = self._publish(SRC, source_script)
        target = self._publish(TGT, target_script, returns={"p0": 0, "p1": 0})
        branch = self._branch(
            sim, policies={"p0": "low", "p1": "low"}, returns={"p0": 1, "p1": -1}
        )
        report = self._run(branch, source, target)
        self.assertEqual(report["status"], "measured")
        self.assertIs(report["actions_equal"], False)
        self.assertIs(report["inputs_equal"], True)
        self.assertEqual(
            report["first_mismatch"], {"step": 2, "kind": "action", "role": "p1"}
        )
        self.assertEqual(report["measured_prefix_steps"], 1)
        self.assertIs(report["branch_views_equal"], True)
        self.assertEqual(report["counts"]["compared_steps"], 1)
        self.assertEqual(report["returns_equal"], {"p0": False, "p1": False})
        self.assertEqual(report["terminated_equal"], {"p0": True, "p1": True})
        self.assertEqual(report["policies"], {"p0": "low", "p1": "low"})
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = compare_branch.main([str(branch), str(source), str(target)])
        self.assertEqual(code, 0)
        self.assertEqual(err.getvalue(), "")
        self.assertIs(json.loads(out.getvalue())["actions_equal"], False)

    def test_bool_action_is_not_an_int(self) -> None:
        script = _joint([(0, 0), (1, 1)])
        sim = _sim(script, 2)
        sim[0]["decisions"]["p0"]["action"] = True
        report = self._run(
            self._branch(sim), self._publish(SRC, script), self._publish(TGT, script)
        )
        self.assertEqual(report["status"], "measured")
        self.assertIs(report["actions_equal"], False)
        self.assertEqual(report["first_mismatch"]["kind"], "action")
        self.assertIsInstance(report["actions_equal"], bool)

    def test_prefix_mismatch_does_not_invent_returns_or_use_claimed_length(
        self,
    ) -> None:
        source_script = _joint([(0, 0), (0, 1)])
        target_script = _joint([(1, 0), (0, 1)])
        report = self._run(
            self._branch(_sim(source_script, 2), claimed=50),
            self._publish(SRC, source_script),
            self._publish(TGT, target_script),
        )
        self.assertEqual(report["status"], "prefix_mismatch")
        self.assertIs(report["prefix_mismatch"], True)
        self.assertEqual(report["measured_prefix_steps"], 0)
        self.assertIsNone(report["branch_views_equal"])
        self.assertIsNone(report["inputs_equal"])
        self.assertIsNone(report["actions_equal"])
        self.assertIsNone(report["returns_equal"])
        self.assertIsNone(report["terminated_equal"])
        self.assertIsNone(report["truncated_equal"])

    def test_equal_length_with_different_view_is_not_a_prefix(self) -> None:
        source_script = _joint([(0, 0), (0, 0)], mark=1)
        target_script = _joint([(0, 0), (0, 0)], mark=2)
        report = self._run(
            self._branch(_sim(source_script, 2), claimed=2),
            self._publish(SRC, source_script),
            self._publish(TGT, target_script),
        )
        self.assertEqual(report["status"], "prefix_mismatch")
        self.assertEqual(
            report["first_mismatch"], {"step": 1, "kind": "view", "role": "p0"}
        )
        self.assertEqual(report["measured_prefix_steps"], 0)
        self.assertIsNone(report["branch_views_equal"])
        self.assertEqual(
            report["counts"]["source_steps"], report["counts"]["target_steps"]
        )

    def test_branch_step_compares_views_not_source_action(self) -> None:
        source_script = _joint([(0, 0), (0, 0), (1, 0)])
        target_script = _joint([(0, 0), (1, 1), (1, 1)])
        report = self._run(
            self._branch(_sim(target_script, 2)),
            self._publish(SRC, source_script),
            self._publish(TGT, target_script),
        )
        self.assertEqual(report["status"], "measured")
        self.assertEqual(report["measured_prefix_steps"], 1)
        self.assertIs(report["branch_views_equal"], True)
        self.assertIs(report["actions_equal"], True)
        self.assertIsNone(report["first_mismatch"])

    def test_branch_at_one_counts_zero_and_verifies_views(self) -> None:
        script = _joint([(0, 0), (1, 1)])
        report = self._run(
            self._branch(_sim(script, 1), at=1, claimed=9),
            self._publish(SRC, script),
            self._publish(TGT, script),
        )
        self.assertEqual(report["status"], "measured")
        self.assertEqual(report["measured_prefix_steps"], 0)
        self.assertIs(report["branch_views_equal"], True)
        self.assertIs(report["actions_equal"], True)

    def test_branch_step_view_failure_is_prefix_mismatch(self) -> None:
        source_script = [
            {"p0": (_view("p0", 1), 0), "p1": (_view("p1", 1), 0)},
            {"p0": (_view("p0", 2, 1), 0), "p1": (_view("p1", 2, 1), 1)},
        ]
        target_script = [
            {"p0": (_view("p0", 1), 0), "p1": (_view("p1", 1), 0)},
            {"p0": (_view("p0", 2, 9), 0), "p1": (_view("p1", 2, 9), 1)},
        ]
        report = self._run(
            self._branch(_sim(source_script, 2)),
            self._publish(SRC, source_script),
            self._publish(TGT, target_script),
        )
        self.assertEqual(report["status"], "prefix_mismatch")
        self.assertEqual(report["measured_prefix_steps"], 1)
        self.assertIs(report["branch_views_equal"], False)
        self.assertEqual(
            report["first_mismatch"], {"step": 2, "kind": "view", "role": "p0"}
        )
        self.assertIsNone(report["returns_equal"])

    def test_turn_order_uses_actor_frames_not_world_step_index(self) -> None:
        script = [
            {"p0": (_view("p0", 1), 0)},
            {"p1": (_view("p1", 1), 3)},
            {"p0": (_view("p0", 2), 1)},
            {"p1": (_view("p1", 2), 4)},
        ]
        source = self._publish(SRC, script)
        target = self._publish(TGT, script)
        report = self._run(self._branch(_sim(script, 2)), source, target)
        self.assertEqual(report["status"], "measured")
        self.assertEqual(report["measured_prefix_steps"], 1)
        self.assertIs(report["branch_views_equal"], True)
        self.assertIs(report["actions_equal"], True)
        self.assertEqual(report["counts"]["compared_steps"], 3)
        wrong = _sim(script, 2)
        wrong[0]["decisions"]["p1"]["action"] = 0
        report = self._run(self._branch(wrong), source, target)
        self.assertEqual(
            report["first_mismatch"], {"step": 2, "kind": "action", "role": "p1"}
        )

    def test_returns_and_flags_stay_separate(self) -> None:
        script = _joint([(0, 0), (0, 0)])
        report = self._run(
            self._branch(_sim(script, 2), terminated={"p0": False, "p1": True}),
            self._publish(SRC, script),
            self._publish(TGT, script, returns={"p0": 1.0, "p1": -1}),
        )
        self.assertIs(report["actions_equal"], True)
        self.assertEqual(report["returns_equal"], {"p0": True, "p1": True})
        self.assertIs(report["terminated_equal"]["p0"], False)
        self.assertIs(report["terminated_equal"]["p1"], True)
        self.assertIs(report["truncated_equal"]["p0"], True)

    def test_shorter_suffix_is_measured_not_corrupt(self) -> None:
        script = _joint([(0, 0), (0, 0), (1, 1)])
        report = self._run(
            self._branch(_sim(script, 2)[:1]),
            self._publish(SRC, script),
            self._publish(TGT, script),
        )
        self.assertEqual(report["status"], "measured")
        self.assertIs(report["actions_equal"], True)
        self.assertEqual(
            report["first_mismatch"], {"step": 3, "kind": "length", "role": None}
        )

    def test_different_case_same_trial_and_missing_target(self) -> None:
        script = _joint([(0, 0), (0, 0)])
        source = self._publish(SRC, script)
        branch = self._branch(_sim(script, 2))
        with self.assertRaisesRegex(compare_branch.Reject, "different case rejected"):
            self._run(branch, source, self._publish(TGT, script, case=OTHER))
        with self.assertRaisesRegex(compare_branch.Reject, "distinct native trial"):
            self._run(branch, source, self._publish(SRC, script))
        missing = self._run(branch, source, self.root / "absent")
        self.assertEqual(missing["status"], "missing")
        self.assertIsNone(missing["target_native_trial_id"])
        self.assertIsNone(missing["returns_equal"])
        self.assertIsNone(missing["prefix_mismatch"])
        err = io.StringIO()
        with redirect_stderr(err):
            code = compare_branch.main(
                [str(branch), str(source), str(self._publish(TGT, script, case=OTHER))]
            )
        self.assertEqual(code, 1)
        self.assertIn("different case rejected", err.getvalue())

    def test_bad_suffix_numbering_is_corrupt(self) -> None:
        script = _joint([(0, 0), (0, 0)])
        sim = _sim(script, 2)
        sim[0]["step"] = 1
        with self.assertRaises(compare_branch.Reject):
            self._run(
                self._branch(sim),
                self._publish(SRC, script),
                self._publish(TGT, script),
            )


if __name__ == "__main__":
    unittest.main()
