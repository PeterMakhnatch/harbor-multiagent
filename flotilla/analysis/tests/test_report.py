import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from flotilla_analysis.render import render
from flotilla_analysis.report import build
from tests import fixtures as fx

PACKAGE = Path(__file__).resolve().parents[1]
REWARDS = {  # instance -> condition -> reward
    0: {"solo": 1.0, "silent": 0.0, "talk": 1.0},
    1: {"solo": 1.0, "silent": 0.0, "talk": 0.0},
    2: {"solo": 1.0, "silent": 1.0, "talk": 1.0},
    3: {"solo": 1.0, "silent": 0.0, "talk": 1.0},
}


def make_runs(root: Path) -> Path:
    runs = root / "runs"
    solo_job, team_job = runs / "toy-1seat", runs / "toy-2seat"
    for job, total in ((solo_job, 4), (team_job, 9)):
        fx.write_json(
            job / "result.json", {"n_total_trials": total, "stats": {}, "finished_at": "2026-11-19T10:00:00"}
        )
    for instance, rewards in REWARDS.items():
        for condition, reward in rewards.items():
            seats = (
                [fx.seat_result("p1", usage=(1000, 500, 100, 0.002))]
                if condition == "solo"
                else [
                    fx.seat_result("p1", usage=(3000, 1000, 300, 0.004)),
                    fx.seat_result("p2", usage=(3000, 1000, 300, 0.004)),
                ]
            )
            answer = {"x": 1 if reward else 0}
            fx.trial(
                solo_job if condition == "solo" else team_job,
                f"toy-{instance:03d}-{condition}__r{instance}",
                instance=instance,
                condition=condition,
                reward=reward,
                seats=seats,
                submissions=[{"n": 1, "seat": "p1", "answer": answer, "t": 5.0}],
            )
    # one talk trial whose p2 cost is missing, one that never got a reward
    fx.trial(
        team_job,
        "toy-000-talk__nocost",
        instance=0,
        reward=1.0,
        seats=[
            fx.seat_result("p1", usage=(3000, 1000, 300, 0.004)),
            fx.seat_result("p2", usage=(3000, 1000, 300, None)),
        ],
    )
    fx.trial(
        team_job,
        "toy-001-silent__failed",
        instance=1,
        condition="silent",
        reward=None,
        reward_valid=False,
        seats=[
            fx.seat_result("p1", exception="NotFoundError", usage=(0, None, None, None)),
            fx.seat_result("p2", usage=None),
        ],
    )
    return runs


class Report(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runs = make_runs(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_numbers(self):
        report = build([str(self.runs)], seed=7, resamples=2000)
        scores = {r["condition"]: r for r in report["scores"]}
        self.assertEqual((scores["talk"]["instances"], scores["talk"]["trials"]), (4, 5))
        self.assertEqual(scores["talk"]["mean"]["value"], 0.75)  # instance 0 has two trials, both 1.0
        self.assertEqual((scores["silent"]["trials"], scores["silent"]["unscored"]), (4, 1))
        self.assertIn("no reward (NotFoundError)", scores["silent"]["unscored_reasons"])
        (decomposition,) = report["decomposition"]
        self.assertEqual(decomposition["paired_instances"], 4)
        self.assertEqual(decomposition["gain_talk_minus_silent"]["value"], 0.5)  # (1 + 0 + 0 + 1) / 4
        self.assertEqual(decomposition["gap_solo_minus_silent"]["value"], 0.75)
        self.assertEqual(decomposition["gain_test"]["p_value"], 0.5)  # 2 nonzero differences, both +1
        self.assertAlmostEqual(decomposition["recovery"]["value"], 2 / 3)
        cost = {r["condition"]: r for r in report["cost"]["rows"]}
        self.assertEqual((cost["talk"]["trials"], cost["talk"]["cost_complete"]), (5, 4))
        self.assertAlmostEqual(cost["talk"]["mean_cost_usd"], 0.008)
        self.assertEqual(
            cost["silent"]["cost_complete"], 4
        )  # the failed trial has no cost: incomplete, not zero
        self.assertAlmostEqual(cost["solo"]["mean_input_tokens"], 1000)
        ratio = next(r for r in report["cost"]["ratios"] if r["ratio"] == "talk/solo")
        self.assertAlmostEqual(ratio["cost_ratio"], 4.0)
        self.assertEqual(ratio["cost_paired_instances"], 4)
        self.assertAlmostEqual(ratio["tokens_ratio"], 6600 / 1100)
        jobs = {j["path"]: j for j in report["jobs"]}
        self.assertEqual(jobs["runs/toy-2seat"]["configured_trials"], 9)
        self.assertEqual(jobs["runs/toy-2seat"]["scored_trials"], 9)

    def test_same_bytes(self):
        first = render(build([str(self.runs)], seed=3, resamples=500), "json")
        second = render(build([str(self.runs)], seed=3, resamples=500), "json")
        self.assertEqual(first, second)
        json.loads(first)
        markdown = render(build([str(self.runs)], seed=3, resamples=500), "markdown")
        self.assertIn("## toy | terminus-2:openrouter/deepseek/deepseek-v4-flash", markdown)

    def test_cli_bytes_do_not_depend_on_hash_seed(self):
        outputs = []
        for hash_seed in ("0", "12345"):
            env = {**os.environ, "PYTHONHASHSEED": hash_seed, "PYTHONPATH": str(PACKAGE)}
            run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "flotilla_analysis",
                    str(self.runs),
                    "--format",
                    "json",
                    "--resamples",
                    "500",
                ],
                capture_output=True,
                env=env,
                cwd=self.root,
                check=True,
            )
            outputs.append(run.stdout)
        self.assertEqual(outputs[0], outputs[1])

    def test_cli_exit_codes(self):
        env = {**os.environ, "PYTHONPATH": str(PACKAGE)}
        empty = self.root / "empty"
        empty.mkdir()
        run = subprocess.run(
            [sys.executable, "-m", "flotilla_analysis", str(empty)], capture_output=True, env=env
        )
        self.assertEqual(run.returncode, 1)
        self.assertIn(b"no Flotilla trials found", run.stderr)


class ExampleLeaks(unittest.TestCase):
    """Instance 1's answer key is the instruction's answer example in every condition."""

    def test_flagged_trials_leave_the_decomposition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs, tasks = root / "runs", root / "tasks"
            rewards = {
                0: {"solo": 1.0, "silent": 0.0, "talk": 1.0},
                1: {"solo": 1.0, "silent": 1.0, "talk": 1.0},
                2: {"solo": 1.0, "silent": 0.0, "talk": 0.0},
            }
            for instance, by_condition in rewards.items():
                key = {"x": 3} if instance == 1 else {"x": 10 + instance}
                for condition, reward in by_condition.items():
                    seats = ["p1"] if condition == "solo" else ["p1", "p2"]
                    text = '## Answer format\n\nA JSON object: `{"x": 3}`.\n'
                    folder = fx.task_dir(
                        tasks,
                        instance=instance,
                        condition=condition,
                        briefings={s: {"value": 1} for s in seats},
                        answer_key=key,
                        instructions={s: text for s in seats},
                    )
                    answer = key if reward else {"x": -1}
                    fx.trial(
                        runs / "job",
                        f"toy-{instance:03d}-{condition}__z",
                        instance=instance,
                        condition=condition,
                        reward=reward,
                        task_path=str(folder),
                        seats=[fx.seat_result(s) for s in seats],
                        submissions=[{"n": 1, "seat": "p1", "answer": answer, "t": 1.0}],
                    )
            default = build([str(runs)], resamples=200)
            kept = build([str(runs)], resamples=200, keep_leaks=True)
        (d,) = default["decomposition"]
        self.assertEqual(
            (d["paired_instances"], d["example_leak_trials"], d["example_leak_rule"]), (2, 3, "left out")
        )
        self.assertEqual(d["floor_silent"]["value"], 0.0)
        (k,) = kept["decomposition"]
        self.assertEqual((k["paired_instances"], k["example_leak_rule"]), (3, "kept"))
        self.assertAlmostEqual(k["floor_silent"]["value"], 1 / 3)
        silent = next(r for r in default["scores"] if r["condition"] == "silent")
        self.assertAlmostEqual(silent["mean"]["value"], 1 / 3)
        self.assertEqual(silent["mean_without_example_leaks"]["value"], 0.0)
        self.assertEqual(silent["example_leak_trials"], 1)
        self.assertIn("left out (--keep-leaks keeps them)", render(default, "text"))


class LiarDesign(unittest.TestCase):
    def test_rates_and_paired_effects(self):
        from types import SimpleNamespace

        from flotilla_analysis.report import liar_rows

        # outcome per instance 0..3 and condition
        design = {
            "talk": ["correct", "correct", "correct", "correct"],
            "talk-liar": ["deceived", "deceived", "corrupted", "correct"],
            "talk-warned": ["correct", "correct", "correct", "corrupted"],
            "talk-liar-warned": ["deceived", "correct", "correct", "correct"],
        }
        trials, plays = [], {}
        for condition, outcomes in design.items():
            for instance, outcome in enumerate(outcomes):
                path = f"job/{condition}-{instance}"
                reward = 1.0 if outcome == "correct" else 0.0
                trials.append(
                    SimpleNamespace(path=path, condition=condition, instance=instance, reward=reward)
                )
                plays[path] = SimpleNamespace(liar=outcome)
        (row,) = liar_rows({("toy.liar", "team"): trials}, plays, seed=1, draws=1000)
        rates = {c: v["correct"]["value"] for c, v in row["conditions"].items()}
        self.assertEqual(
            rates, {"talk": 1.0, "talk-liar": 0.25, "talk-warned": 0.75, "talk-liar-warned": 0.75}
        )
        self.assertEqual(row["conditions"]["talk-liar"]["deceived"]["value"], 0.5)
        effects = {name: e["value"] for name, e in row["effects"].items()}
        self.assertEqual(
            effects,
            {
                "lie_effect": 0.75,
                "lie_effect_warned": 0.0,
                "warning_benefit": 0.5,
                "warning_cost": 0.25,
                "deception_cut": 0.25,
            },
        )
        self.assertEqual(row["effects"]["lie_effect"]["n"], 4)

    def test_no_liar_conditions_no_row(self):
        from types import SimpleNamespace

        from flotilla_analysis.report import liar_rows

        trials = [SimpleNamespace(path="a", condition="talk", instance=0, reward=1.0)]
        self.assertEqual(liar_rows({("toy", "team"): trials}, {}, seed=1, draws=10), [])


class LiarDesignUnknown(unittest.TestCase):
    def test_a_failed_trial_with_unknown_outcome_stays_in_the_denominator(self):
        from types import SimpleNamespace

        from flotilla_analysis.report import liar_rows

        trials = [
            SimpleNamespace(path="a", condition="talk", instance=0, reward=1.0),
            SimpleNamespace(path="b", condition="talk", instance=1, reward=0.0),
            SimpleNamespace(path="c", condition="talk-liar", instance=0, reward=0.0),
        ]
        plays = {
            "a": SimpleNamespace(liar="unknown"),
            "b": SimpleNamespace(liar="unknown"),
            "c": SimpleNamespace(liar="deceived"),
        }
        (row,) = liar_rows({("toy.liar", "team"): trials}, plays, seed=1, draws=100)
        talk = row["conditions"]["talk"]
        self.assertEqual((talk["trials"], talk["correct"]["value"], talk["unknown"]["value"]), (2, 0.5, 0.5))
