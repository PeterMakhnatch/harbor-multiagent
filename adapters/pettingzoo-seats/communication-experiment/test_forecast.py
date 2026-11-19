"""Held-out forecast tests: a scripted Parallel env stands in for MPE2.

No model, no Harbor, no native rerun. Goal = seed % 3, so seeds 102, 105, 108
and 111 have target 0 and the other 8 seeds have target 1 or 2.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str, file: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ref = _load("pz_communication_reference", "reference.py")
fc = _load("pz_communication_forecast", "forecast.py")

MOVE = {0: (0.0, 0.0), 1: (-0.1, 0.0), 2: (0.1, 0.0), 3: (0.0, -0.1), 4: (0.0, 0.1)}
LANDMARKS = ((0.5, 0.0), (0.0, 0.5), (-0.5, 0.0))


class _Space:
    def __init__(self, n: int) -> None:
        self.n, self.start = n, 0


class Scripted:
    """Toy speaker-listener: one-step message lag, goal = seed % 3, 3 cycles."""

    possible_agents = ["speaker_0", "listener_0"]
    agents: list[str] = []

    def action_space(self, agent: str) -> _Space:
        return _Space(3 if agent == "speaker_0" else 5)

    def _obs(self) -> dict[str, list[float]]:
        colour = [0.15, 0.15, 0.15]
        colour[self.goal] = 0.65
        rel = []
        for lx, ly in LANDMARKS:
            rel += [lx - self.pos[0], ly - self.pos[1]]
        return {"speaker_0": colour, "listener_0": list(self.vel) + rel + self.comm}

    def reset(self, seed=None, options=None):
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
        done = self.t >= 3
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


def _factory():
    return Scripted()


def _uuid(index: int) -> str:
    return f"{index:08x}-2222-4333-8444-5555555555ab"


def _doc(cid: str) -> dict:
    _c, seed, _t, speaker, listener = next(c for c in fc.CELLS if c[0] == cid)
    trace = ref.simulate(_factory, seed, speaker, listener)
    steps = [
        {
            "step": n,
            "decisions": {
                r: {
                    "view": copy.deepcopy(s["views"][r]),
                    "view_sha256": "0" * 64,
                    "action": s["actions"][r],
                }
                for r in s["group"]
            },
        }
        for n, s in enumerate(trace["steps"], 1)
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


def _write_root(root: Path, skip: frozenset = frozenset(), same_uuid: bool = False):
    """Plan for all 60 cells; native logs for every cell not in ``skip``."""
    root.mkdir(parents=True)
    cells = [
        {
            "id": cid,
            "game": ref.GAME,
            "seed": seed,
            "team": team,
            "speaker": sp,
            "listener": li,
            "trial_name": f"{cid}-t",
        }
        for cid, seed, team, sp, li in fc.CELLS
    ]
    (root / "plan.json").write_text(
        json.dumps({"schema": ref.PLAN_SCHEMA, "cells": cells}), encoding="utf-8"
    )
    (root / "configs").mkdir()
    docs = {}
    for index, (cid, *_rest) in enumerate(fc.CELLS, 1):
        if cid in skip:
            continue
        docs[cid] = _doc(cid)
        name = f"{cid}-t"
        (root / "configs" / f"{cid}.json").write_text(
            json.dumps({"trial_name": name}), encoding="utf-8"
        )
        claim = root / "attempts" / cid
        claim.mkdir(parents=True)
        (claim / "exit.json").write_text(json.dumps({"exit_code": 0}), encoding="utf-8")
        logs = root / "trials" / name / "artifacts" / "logs" / "world"
        logs.mkdir(parents=True)
        uid = _uuid(1 if same_uuid and cid.startswith("s101-") else index)
        (root / "trials" / name / "result.json").write_text(
            json.dumps({"id": uid, "exception_info": None}), encoding="utf-8"
        )
        (logs / "pettingzoo.json").write_text(json.dumps(docs[cid]), encoding="utf-8")
    return docs


def _reference(root: Path, docs: dict) -> dict:
    """The new reference report interface: cells[].native and by_seed[].native."""
    cells, by_seed = [], []
    for index, (cid, seed, *_rest) in enumerate(fc.CELLS, 1):
        if cid in docs:
            native = {
                "status": "complete",
                "native_trial_id": _uuid(index),
                "returns": docs[cid]["returns"],
            }
        else:
            native = {"status": "unattempted", "native_trial_id": None, "returns": None}
        cells.append({"id": cid, "seed": seed, "native": native})
    for seed in fc.SEEDS:
        base = f"s{seed}-{fc.BASE}"
        entry = []
        for team in fc.OTHERS:
            other = f"s{seed}-{team}"
            if base in docs and other in docs:
                item = ref.contrast(
                    ref.native_trace(docs[base]), ref.native_trace(docs[other]), team
                )
            else:
                item = {"team": team, "measured": False}
            entry.append(item)
        by_seed.append({"seed": seed, "native": entry})
    return {"cells": cells, "by_seed": by_seed}


class ForecastTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        self.form = fc.predict(_factory)
        self.raw = fc.dumps(self.form).encode("ascii")
        self.sha = hashlib.sha256(self.raw).hexdigest()

    def _check(self, root: Path, docs: dict, raw=None, sha=None, form=None) -> dict:
        raw = self.raw if raw is None else raw
        sha = hashlib.sha256(raw).hexdigest() if sha is None else sha
        return fc.check(root, raw, sha, _reference(root, docs))

    def _full(self):
        root = self.tmp / "native"
        return root, _write_root(root)

    # -- forecast

    def test_forecast_is_deterministic_and_complete(self) -> None:
        self.assertEqual(fc.dumps(fc.predict(_factory)).encode("ascii"), self.raw)
        self.assertEqual([r["seed"] for r in self.form["seeds"]], list(range(101, 113)))
        self.assertEqual(
            [r["goal"] for r in self.form["seeds"]], [s % 3 for s in fc.SEEDS]
        )
        row = self.form["seeds"][0]
        self.assertEqual(sorted(row["returns"]), sorted(fc.TEAM_NAMES))
        self.assertEqual(sorted(row["contrasts"]), sorted(fc.OTHERS))
        self.assertEqual(row["contrasts"]["rotate-rotate"]["differing_actions"], 0)
        self.assertIsNone(row["contrasts"]["rotate-rotate"]["first_action"])
        # seed 101 goal 2: plain-blind pursues landmark 0, so it first differs at step 2.
        blind = row["contrasts"]["plain-blind"]
        self.assertEqual(blind["first_action"], {"step": 2, "baseline": 1, "team": 2})
        self.assertGreater(blind["differing_actions"], 0)
        self.assertEqual(blind["compared_steps"], 3)
        self.assertEqual(fc.parse_forecast(self.raw), self.form)

    def test_strict_parse_rejects_changes(self) -> None:
        bad = copy.deepcopy(self.form)
        bad["seeds"][0]["extra"] = 1
        with self.assertRaises(fc.ForecastError):
            fc.parse_forecast(json.dumps(bad).encode("ascii"))
        spaced = json.dumps(self.form, sort_keys=True).encode("ascii")
        with self.assertRaises(fc.ForecastError) as ctx:
            fc.parse_forecast(spaced)
        self.assertEqual(ctx.exception.token, "not_canonical")
        short = copy.deepcopy(self.form)
        short["seeds"].pop()
        with self.assertRaises(fc.ForecastError):
            fc.parse_forecast(fc.dumps(short).encode("ascii"))
        flag = copy.deepcopy(self.form)
        flag["seeds"][0]["goal"] = True
        with self.assertRaises(fc.ForecastError):
            fc.parse_forecast(fc.dumps(flag).encode("ascii"))

    # -- check

    def test_agreeing_native_cohort(self) -> None:
        root, docs = self._full()
        report = self._check(root, docs)
        self.assertEqual(report["verdict"], "agreed")
        self.assertEqual(report["registered"]["comparisons"], 48)
        self.assertEqual(
            report["summary"]["goal"],
            {"denominator": 12, "true": 12, "false": 0, "missing": 0},
        )
        for per in report["summary"]["contrasts"].values():
            for tally in per.values():
                self.assertEqual(tally["true"], 12)
        self.assertEqual(
            [c["native_trial_id"] for c in report["cells"]][:2], [_uuid(1), _uuid(2)]
        )
        self.assertTrue(all(c["usable"] for c in report["cells"]))
        groups = report["primary_blind"]["groups"]
        self.assertEqual(
            groups["target_zero"], {"seeds": 4, "measured": 4, "equal": 4, "missing": 0}
        )
        self.assertEqual(
            groups["target_other"],
            {"seeds": 8, "measured": 8, "equal": 0, "missing": 0},
        )
        self.assertEqual(groups["target_unknown"]["seeds"], 0)
        shared = report["shared_rotation"]
        for key in ("actions_preserved", "physical_preserved", "symbol_changed"):
            self.assertEqual(
                shared[key], {"denominator": 12, "true": 12, "false": 0, "missing": 0}
            )
        for team in fc.CROSS:
            got = report["crossed"][team]
            self.assertEqual(got["denominator"], 12)
            self.assertEqual(got["better"] + got["tie"] + got["worse"], 12)
            self.assertEqual(got["better"], 0)
        self.assertEqual(report["crossed"]["total"]["denominator"], 24)
        self.assertTrue(ref._public(report))

    def test_disagreement_is_reported_not_refused(self) -> None:
        root, docs = self._full()
        wrong = copy.deepcopy(self.form)
        wrong["seeds"][0]["goal"] = 0
        wrong["seeds"][1]["returns"]["plain-plain"]["p1"] += 5e-7  # inside 1e-6
        wrong["seeds"][2]["returns"]["plain-plain"]["p1"] += 5e-6  # outside
        wrong["seeds"][3]["contrasts"]["plain-blind"]["differing_actions"] += 1
        report = self._check(root, docs, raw=fc.dumps(wrong).encode("ascii"))
        self.assertEqual(report["verdict"], "disagreed")
        self.assertEqual(report["summary"]["goal"]["false"], 1)
        self.assertEqual(report["summary"]["returns"]["plain-plain"]["false"], 1)
        self.assertEqual(report["summary"]["returns"]["plain-plain"]["true"], 11)
        self.assertEqual(
            report["summary"]["contrasts"]["plain-blind"]["differing_actions"]["false"],
            1,
        )
        self.assertEqual(
            report["summary"]["contrasts"]["plain-blind"]["first_action"]["false"], 0
        )

    def test_missing_native_is_not_false(self) -> None:
        skip = frozenset({"s102-plain-blind", "s103-plain-plain"})
        root = self.tmp / "partial"
        docs = _write_root(root, skip)
        report = self._check(root, docs)
        self.assertEqual(report["verdict"], "incomplete")
        self.assertEqual(
            report["summary"]["goal"],
            {"denominator": 12, "true": 11, "false": 0, "missing": 1},
        )
        blind = report["summary"]["contrasts"]["plain-blind"]["differing_actions"]
        self.assertEqual(
            blind, {"denominator": 12, "true": 10, "false": 0, "missing": 2}
        )
        groups = report["primary_blind"]["groups"]
        self.assertEqual(
            groups["target_zero"], {"seeds": 4, "measured": 3, "equal": 3, "missing": 1}
        )
        self.assertEqual(
            groups["target_unknown"],
            {"seeds": 1, "measured": 0, "equal": 0, "missing": 1},
        )
        self.assertEqual(report["crossed"]["plain-rotate"]["missing"], 1)
        self.assertEqual(sum(not c["usable"] for c in report["cells"]), 2)

    def test_absent_root_and_reference_keep_registered_denominators(self) -> None:
        report = fc.check(self.tmp / "none", self.raw, self.sha, {})
        self.assertEqual(report["plan"], "missing_plan")
        self.assertEqual(report["verdict"], "incomplete")
        self.assertEqual(report["summary"]["goal"]["missing"], 12)
        self.assertEqual(
            report["primary_blind"]["groups"]["target_unknown"]["seeds"], 12
        )
        self.assertEqual(report["crossed"]["total"]["missing"], 24)

    def test_duplicate_uuid_is_unusable(self) -> None:
        root = self.tmp / "dup"
        docs = _write_root(root, same_uuid=True)
        report = fc.check(root, self.raw, self.sha, _reference(root, docs))
        usable = {c["id"]: c["usable"] for c in report["cells"]}
        self.assertFalse(usable["s101-plain-plain"])
        self.assertTrue(usable["s102-plain-plain"])
        self.assertEqual(report["summary"]["goal"]["missing"], 1)

    def test_reference_uuid_must_match_root(self) -> None:
        root, docs = self._full()
        ref_doc = _reference(root, docs)
        ref_doc["cells"][0]["native"]["native_trial_id"] = _uuid(99)
        report = fc.check(root, self.raw, self.sha, ref_doc)
        self.assertFalse(report["cells"][0]["usable"])
        self.assertEqual(report["summary"]["goal"]["missing"], 1)

    # -- command line

    def test_cli_predict_check_and_digest_refusal(self) -> None:
        out = self.tmp / "forecast.json"
        with contextlib.redirect_stdout(io.StringIO()) as shown:
            self.assertEqual(fc.main(["predict", "--out", str(out)], _factory), 0)
        self.assertEqual(out.read_bytes(), self.raw)
        self.assertEqual(shown.getvalue().strip(), self.sha)
        self.assertEqual(fc.main(["predict", "--out", str(out)], _factory), 2)

        root, docs = self._full()
        real = self.tmp / "real.json"
        real.write_text(json.dumps(_reference(root, docs)), encoding="utf-8")
        args = ["check", str(root), "--forecast", str(out), "--reference", str(real)]
        bad = self.tmp / "bad.json"
        with contextlib.redirect_stderr(io.StringIO()):
            code = fc.main([*args, "--expect-sha256", "0" * 64, "--out", str(bad)])
        self.assertEqual(code, 2)
        self.assertFalse(bad.exists())
        good = self.tmp / "check.json"
        self.assertEqual(
            fc.main([*args, "--expect-sha256", self.sha, "--out", str(good)]), 0
        )
        text = good.read_text(encoding="ascii")
        self.assertEqual(json.loads(text)["verdict"], "agreed")
        self.assertNotIn(str(self.tmp), text)
        self.assertEqual(text, fc.dumps(json.loads(text)))
        self.assertEqual(
            fc.main([*args, "--expect-sha256", self.sha, "--out", str(good)]), 2
        )


if __name__ == "__main__":
    unittest.main()
