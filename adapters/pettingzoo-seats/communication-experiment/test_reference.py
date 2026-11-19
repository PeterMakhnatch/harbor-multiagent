"""Reference census: missing cells, failing and lagged comparisons, contrasts.

No model, no Harbor, no MPE2 episode: a scripted Parallel env stands in for
the engine, so every expected value below is computed by hand.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

_PATH = Path(__file__).resolve().parent / "reference.py"
_SPEC = importlib.util.spec_from_file_location("pz_communication_reference", _PATH)
assert _SPEC and _SPEC.loader
ref = importlib.util.module_from_spec(_SPEC)
sys.modules["pz_communication_reference"] = ref
_SPEC.loader.exec_module(ref)

UUIDS = [f"{n:08x}-2222-4333-8444-5555555555ab" for n in range(1, 16)]
MOVE = {0: (0.0, 0.0), 1: (-0.1, 0.0), 2: (0.1, 0.0), 3: (0.0, -0.1), 4: (0.0, 0.1)}
LANDMARKS = ((0.5, 0.0), (0.0, 0.5), (-0.5, 0.0))


class _Space:
    def __init__(self, n: int) -> None:
        self.n, self.start = n, 0


class Scripted:
    """Toy speaker-listener: one-step message lag, goal = seed % 3, 3 cycles."""

    possible_agents = ["speaker_0", "listener_0"]

    def __init__(self, cycles: int = 3, broken_seed: int | None = None) -> None:
        self.cycles, self.broken_seed, self.agents = cycles, broken_seed, []

    def action_space(self, agent: str) -> _Space:
        return _Space(3 if agent == "speaker_0" else 5)

    def _obs(self) -> dict[str, list[float]]:
        colour = [0.15, 0.15, 0.15]
        colour[self.goal] = 0.65
        rel = []
        for lx, ly in LANDMARKS:
            rel += [lx - self.pos[0], ly - self.pos[1]]
        listener = list(self.vel) + rel + list(self.comm)
        return {"speaker_0": colour, "listener_0": listener}

    def reset(self, seed=None, options=None):
        if seed == self.broken_seed:
            raise RuntimeError("scripted reset failure")
        self.goal, self.t = seed % 3, 0
        self.pos, self.vel, self.comm = [0.0, 0.0], [0.0, 0.0], [0.0, 0.0, 0.0]
        self.agents = list(self.possible_agents)
        return self._obs(), {a: {} for a in self.agents}

    def step(self, actions):
        dx, dy = MOVE[actions["listener_0"]]
        self.pos = [self.pos[0] + dx, self.pos[1] + dy]
        self.vel = [dx, dy]
        self.comm = [0.0, 0.0, 0.0]
        self.comm[actions["speaker_0"]] = 1.0
        gx, gy = LANDMARKS[self.goal]
        reward = -((self.pos[0] - gx) ** 2 + (self.pos[1] - gy) ** 2)
        self.t += 1
        done = self.t >= self.cycles
        names = list(self.agents)
        obs = self._obs()
        if done:
            self.agents = []
        return (
            obs,
            {a: reward for a in names},
            {a: False for a in names},
            {a: done for a in names},
            {a: {} for a in names},
        )

    def close(self) -> None:
        pass


def _factory(**kwargs):
    return lambda: Scripted(**kwargs)


def _trial(cid: str) -> str:
    return f"{cid}-t"


def _plan(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    cells = [
        {
            "id": cid,
            "game": ref.GAME,
            "seed": seed,
            "team": team,
            "speaker": speaker,
            "listener": listener,
            "spec": f"seed-{seed}.json",
            "trial_name": _trial(cid),
        }
        for cid, seed, team, speaker, listener in ref.CELLS
    ]
    doc = {"schema": ref.PLAN_SCHEMA, "cells": cells}
    (root / "plan.json").write_text(json.dumps(doc), encoding="utf-8")


def _doc(cid: str) -> dict:
    """A native-shaped log of the reference episode for one cell."""
    _cid, seed, _team, speaker, listener = next(c for c in ref.CELLS if c[0] == cid)
    trace = ref.simulate(_factory(), seed, speaker, listener)
    steps = [
        {
            "step": index,
            "decisions": {
                role: {
                    "view": copy.deepcopy(step["views"][role]),
                    "view_sha256": "0" * 64,
                    "action": step["actions"][role],
                }
                for role in step["group"]
            },
        }
        for index, step in enumerate(trace["steps"], 1)
    ]
    return {
        "schema": ref.NATIVE_SCHEMA,
        "case_sha256": ref.case_sha256(seed),
        "roles": dict(ref.ROLES),
        "steps": steps,
        "returns": dict(trace["returns"]),
        "terminated": dict(trace["terminated"]),
        "truncated": dict(trace["truncated"]),
    }


def _native(root: Path, cid: str, doc: dict | None, uuid: str) -> None:
    name = _trial(cid)
    (root / "configs").mkdir(exist_ok=True)
    (root / "configs" / f"{cid}.json").write_text(
        json.dumps({"trial_name": name}), encoding="utf-8"
    )
    claim = root / "attempts" / cid
    claim.mkdir(parents=True)
    (claim / "exit.json").write_text(
        json.dumps({"exit_code": 0, "trial_name": name}), encoding="utf-8"
    )
    if doc is None:
        return
    trial = root / "trials" / name
    logs = trial / "artifacts" / "logs" / "world"
    logs.mkdir(parents=True)
    (trial / "result.json").write_text(
        json.dumps({"id": uuid, "exception_info": None}), encoding="utf-8"
    )
    (logs / "pettingzoo.json").write_text(json.dumps(doc), encoding="utf-8")


class ReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        self.root = self.tmp / "native"

    def _build(self, **kwargs) -> dict:
        return ref.build(self.root, _factory(**kwargs))

    @staticmethod
    def _cells(report: dict) -> dict[str, dict]:
        return {cell["id"]: cell for cell in report["cells"]}

    # -- denominators and missing evidence

    def test_missing_root_still_registers_fifteen_cells(self) -> None:
        report = self._build()
        self.assertEqual(report["plan"], "missing_plan")
        self.assertEqual([c["id"] for c in report["cells"]], [c[0] for c in ref.CELLS])
        for cell in report["cells"]:
            self.assertEqual(cell["native"]["status"], "unregistered")
            self.assertIsNone(cell["native"]["returns"])
            self.assertIsNone(cell["comparison"])
            self.assertEqual(cell["independent"]["status"], "ok")
            self.assertEqual(cell["independent"]["world_steps"], 3)
        den = report["denominators"]
        self.assertEqual(den["registered_cells"], 15)
        self.assertEqual(den["native"]["unregistered"], 15)
        self.assertEqual(den["compared_cells"], 0)
        self.assertIsNone(den["max_abs_observation_difference"])
        self.assertIsNone(den["max_abs_payoff_difference"])
        self.assertEqual(den["contrasts"]["registered"], 12)
        self.assertEqual(den["contrasts"]["independent_measured"], 12)
        self.assertEqual(den["contrasts"]["native_measured"], 0)
        self.assertEqual(report["engine"]["source"], "injected")
        self.assertIsNone(report["engine"]["mpe2"])

    def test_planned_but_unrun_cells_are_unattempted(self) -> None:
        _plan(self.root)
        report = self._build()
        self.assertEqual(report["plan"], "ok")
        self.assertEqual(report["denominators"]["native"]["unattempted"], 15)

    def test_claim_without_trial_is_invalid_not_zero(self) -> None:
        _plan(self.root)
        _native(self.root, "s5-plain-plain", None, UUIDS[0])
        cell = self._cells(self._build())["s5-plain-plain"]
        self.assertEqual(cell["native"]["status"], "invalid")
        self.assertEqual(cell["native"]["reason"], "missing_trial")
        self.assertEqual(cell["native"]["exit_code"], 0)
        self.assertIsNone(cell["native"]["returns"])
        self.assertIsNone(cell["comparison"])

    def test_duplicate_native_uuid_is_not_compared(self) -> None:
        _plan(self.root)
        for cid in ("s5-plain-plain", "s5-rotate-rotate"):
            _native(self.root, cid, _doc(cid), UUIDS[0])
        cells = self._cells(self._build())
        for cid in ("s5-plain-plain", "s5-rotate-rotate"):
            self.assertEqual(cells[cid]["native"]["reason"], "duplicate_uuid")
            self.assertIsNone(cells[cid]["comparison"])
            self.assertIsNone(cells[cid]["native"]["returns"])

    # -- comparisons

    def test_matching_native_reports_measured_zero(self) -> None:
        _plan(self.root)
        _native(self.root, "s5-plain-plain", _doc("s5-plain-plain"), UUIDS[0])
        report = self._build()
        comp = self._cells(report)["s5-plain-plain"]["comparison"]
        self.assertEqual(comp["verdict"], "agreed")
        self.assertEqual(comp["steps_compared"], 3)
        self.assertEqual(comp["decisions_compared"], 6)
        self.assertEqual(comp["observation_components_compared"], 3 * (3 + 11))
        self.assertEqual(comp["max_abs_observation_difference"], 0.0)
        self.assertEqual(comp["payoffs_compared"], 2)
        self.assertEqual(comp["max_abs_payoff_difference"], 0.0)
        self.assertIsNone(comp["first_mismatch"])
        self.assertEqual(report["denominators"]["agreed_cells"], 1)

    def test_tolerance_is_separate_from_measured_maximum(self) -> None:
        _plan(self.root)
        inside, outside = _doc("s5-plain-plain"), _doc("s19-plain-plain")
        inside["steps"][1]["decisions"]["p1"]["view"]["observation"][2] += 5e-7
        outside["steps"][1]["decisions"]["p1"]["view"]["observation"][2] += 5e-6
        _native(self.root, "s5-plain-plain", inside, UUIDS[0])
        _native(self.root, "s19-plain-plain", outside, UUIDS[1])
        cells = self._cells(self._build())
        ok = cells["s5-plain-plain"]["comparison"]
        bad = cells["s19-plain-plain"]["comparison"]
        self.assertEqual(ok["verdict"], "agreed")
        self.assertAlmostEqual(ok["max_abs_observation_difference"], 5e-7, places=12)
        self.assertEqual(bad["verdict"], "disagreed")
        self.assertAlmostEqual(bad["max_abs_observation_difference"], 5e-6, places=12)
        self.assertEqual(bad["observations_outside_tolerance"], 1)
        self.assertEqual(
            bad["first_mismatch"], {"where": "observation", "step": 2, "role": "p1"}
        )

    def test_wrong_listener_action_fails_independently(self) -> None:
        _plan(self.root)
        doc = _doc("s5-plain-plain")
        doc["steps"][1]["decisions"]["p1"]["action"] = 0
        _native(self.root, "s5-plain-plain", doc, UUIDS[0])
        comp = self._cells(self._build())["s5-plain-plain"]["comparison"]
        self.assertEqual(comp["verdict"], "disagreed")
        self.assertFalse(comp["exact"]["actions"])
        self.assertEqual(
            comp["first_mismatch"], {"where": "action", "step": 2, "role": "p1"}
        )
        self.assertEqual(comp["max_abs_observation_difference"], 0.0)

    def test_same_step_message_is_a_lag_mismatch(self) -> None:
        _plan(self.root)
        doc = _doc("s5-plain-plain")
        for step in doc["steps"]:
            sent = step["decisions"]["p0"]["action"]
            obs = step["decisions"]["p1"]["view"]["observation"]
            obs[8:11] = [1.0 if i == sent else 0.0 for i in range(3)]
        self.assertEqual(ref.lag(ref.native_trace(doc)), 0)
        _native(self.root, "s5-plain-plain", doc, UUIDS[0])
        cell = self._cells(self._build())["s5-plain-plain"]
        comp = cell["comparison"]
        self.assertEqual(cell["independent"]["communication_lag"], 1)
        self.assertEqual(cell["independent"]["listener_channels"], [None, 2, 2])
        self.assertEqual(comp["verdict"], "disagreed")
        self.assertEqual(
            comp["first_mismatch"], {"where": "observation", "step": 1, "role": "p1"}
        )
        self.assertEqual(comp["max_abs_observation_difference"], 1.0)

    def test_missing_payoff_is_not_counted_as_zero(self) -> None:
        _plan(self.root)
        doc = _doc("s5-plain-plain")
        del doc["returns"]["p1"]
        _native(self.root, "s5-plain-plain", doc, UUIDS[0])
        comp = self._cells(self._build())["s5-plain-plain"]["comparison"]
        self.assertEqual(comp["verdict"], "disagreed")
        self.assertEqual(comp["payoffs_compared"], 1)
        self.assertEqual(comp["payoffs_outside_tolerance"], 1)
        self.assertEqual(comp["max_abs_payoff_difference"], 0.0)

    def test_failed_independent_episode_keeps_cells_unmeasured(self) -> None:
        report = self._build(broken_seed=19)
        cells = self._cells(report)
        for cid in [c[0] for c in ref.CELLS if c[1] == 19]:
            self.assertEqual(cells[cid]["independent"]["status"], "failed")
            self.assertEqual(
                cells[cid]["independent"]["reason"], "engine_error:RuntimeError"
            )
            self.assertIsNone(cells[cid]["independent"]["returns"])
        seed19 = next(e for e in report["by_seed"] if e["seed"] == 19)
        self.assertTrue(all(not c["measured"] for c in seed19["independent"]))
        den = report["denominators"]
        self.assertEqual(den["registered_cells"], 15)
        self.assertEqual(den["independent"], {"ok": 10, "failed": 5})
        self.assertEqual(den["contrasts"]["independent_measured"], 8)

    # -- seed contrasts (hand-computed; seed 5 goal is landmark 2)

    def test_seed_contrasts_against_plain(self) -> None:
        _plan(self.root)
        for n, cid in enumerate(("s5-plain-plain", "s5-rotate-rotate")):
            _native(self.root, cid, _doc(cid), UUIDS[n])
        seed5 = next(e for e in self._build()["by_seed"] if e["seed"] == 5)
        got = {c["team"]: c for c in seed5["independent"]}

        matched = got["rotate-rotate"]
        self.assertEqual(matched["kind"], "matched")
        self.assertEqual(
            matched["first_changed_speaker_action"],
            {"step": 1, "baseline": 2, "team": 0},
        )
        self.assertEqual(
            matched["first_listener_channel"],
            {
                "step": 2,
                "baseline": 2,
                "team": 0,
                "baseline_target": 2,
                "team_target": 2,
            },
        )
        self.assertEqual(matched["first_changed_listener_channel"]["step"], 2)
        self.assertTrue(matched["listener_physical"]["matched"])
        self.assertEqual(matched["listener_physical"]["components_compared"], 24)
        self.assertEqual(matched["listener_physical"]["channel_components_excluded"], 9)
        self.assertTrue(matched["listener_actions"]["matched"])
        self.assertTrue(matched["listener_step_rewards"]["matched"])
        self.assertIsNone(matched["first_changed_listener_action"])
        self.assertIsNone(matched["first_changed_listener_physical"])

        cross = got["plain-rotate"]
        self.assertIsNone(cross["first_changed_speaker_action"])
        self.assertIsNone(cross["first_changed_listener_channel"])
        self.assertEqual(cross["first_listener_channel"]["team_target"], 1)
        self.assertEqual(
            cross["first_changed_listener_action"],
            {"step": 2, "baseline": 1, "team": 4},
        )
        self.assertEqual(cross["first_changed_listener_physical"]["step"], 3)
        self.assertAlmostEqual(
            cross["listener_physical"]["max_abs_difference"], 0.1, places=12
        )
        self.assertFalse(cross["listener_physical"]["matched"])

        self.assertEqual(
            got["rotate-plain"]["first_changed_listener_action"],
            {"step": 2, "baseline": 1, "team": 2},
        )
        blind = got["plain-blind"]
        self.assertEqual(blind["kind"], "blind")
        self.assertEqual(blind["first_listener_channel"]["team_target"], 0)
        self.assertEqual(
            blind["first_changed_listener_action"],
            {"step": 2, "baseline": 1, "team": 2},
        )

        native = {c["team"]: c for c in seed5["native"]}
        self.assertTrue(native["rotate-rotate"]["listener_physical"]["matched"])
        self.assertIsNone(native["rotate-rotate"]["listener_step_rewards"]["matched"])
        self.assertFalse(native["plain-rotate"]["measured"])

    # -- written policy

    def test_policy_follows_written_rules(self) -> None:
        silent = [0.0] * 8 + [0.0, 0.0, 0.0]
        for book in ("plain", "rotate", "blind"):
            self.assertEqual(ref.listen(book, silent), 0)
        self.assertEqual(ref.speak("plain", [0.15, 0.65, 0.15]), 1)
        self.assertEqual(ref.speak("rotate", [0.15, 0.15, 0.65]), 0)
        with self.assertRaises(ref.RefError):
            ref.speak("plain", [0.4, 0.4, 0.15])
        # Velocity lead: rel x 0.05 minus 0.6 * -0.1 = 0.11 -> right.
        heard = [-0.1, 0.0, 0.05, 0.0, 0.0, 0.0, 0.0, -0.2, 1.0, 0.0, 0.0]
        self.assertEqual(ref.listen("plain", heard), 2)
        # Rotated codebook reads channel 0 as landmark 2.
        self.assertEqual(ref.target("rotate", heard), 2)
        self.assertEqual(ref.listen("rotate", heard), 3)
        # Inside the 0.06 dead band the listener waits.
        near = [0.0, 0.0, 0.03, -0.02, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        self.assertEqual(ref.listen("plain", near), 0)
        # Ties go to x; negative x is left.
        tie = [0.0, 0.0, -0.3, 0.3, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        self.assertEqual(ref.listen("plain", tie), 1)
        # Blind ignores the channel value and pursues landmark 0.
        other = [0.0, 0.0, 0.0, -0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]
        self.assertEqual(ref.listen("blind", other), 3)

    # -- output file and isolation

    def test_write_is_new_file_without_host_paths(self) -> None:
        _plan(self.root)
        _native(self.root, "s5-plain-plain", _doc("s5-plain-plain"), UUIDS[0])
        out = self.tmp / "reference.json"
        ref.write_report(self.root, out, _factory())
        text = out.read_text(encoding="ascii")
        self.assertNotIn(str(self.tmp), text)
        self.assertNotIn(str(self.tmp.resolve()), text)
        self.assertEqual(json.loads(text)["schema"], ref.SCHEMA)
        with self.assertRaises(FileExistsError):
            ref.write_report(self.root, out, _factory())
        self.assertEqual(ref.main([str(self.root), "--out", str(out)]), 2)


if __name__ == "__main__":
    unittest.main()
