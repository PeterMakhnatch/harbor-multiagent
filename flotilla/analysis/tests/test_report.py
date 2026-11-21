import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from flotilla_analysis.load import load
from flotilla_analysis.render import render
from flotilla_analysis.report import MixedBuildsError, build
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


class BuildChecks(unittest.TestCase):
    def test_different_builds_are_refused_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job"
            seed = 4_402_366_163_412_422
            folder = fx.task_dir(
                root / "tasks",
                briefings={"p1": {"value": 1}, "p2": {"value": 2}},
                answer_key={"x": 3},
                extra={"seed": seed, "max_messages": 10, "max_text": 60, "world_version": 5},
            )
            (folder / "task.toml").write_text(
                "[task]\nname = 'flotilla/toy-000-talk'\n[metadata]\n"
                "difficulty = 'easy'\nmax_messages_per_round = 2\nround_seconds = 10.0\n"
            )
            fx.trial(job, "toy-000-talk__own", task_path=str(folder), events=[], reward=0.0)
            opaque = "flotilla/t-111111111111"
            other = fx.trial(job, "toy-001-talk__archived", instance=1, events=[], reward=0.0)
            result = json.loads((other / "result.json").read_text())
            fx.write_json(other / "result.json", result | {"task_name": opaque})
            manifest = root / "archived.json"
            expected = {
                "seed": seed + 1,
                "max_text": 30,
                "max_messages": 5,
                "max_messages_per_round": 1,
                "round_seconds": 5.0,
                "difficulty": "hard",
                "world_version": 6,
                "scripted_seats": ["p2"],
                "submitter": "p1",
                "announce": True,
            }
            fx.write_json(
                manifest,
                {"tasks": {opaque: {"label": "toy", "condition": "talk", "instance": 1, "build": expected}}},
            )
            (own, archived), _, _ = load([job], manifests=[manifest])
            self.assertEqual(own.build["seed"], seed)
            self.assertEqual(own.build["round_seconds"], 10)
            self.assertEqual(archived.build["seed"], seed + 1)
            cmd = [sys.executable, "-m", "flotilla_analysis", str(job), "--manifest", str(manifest)]
            env = {**os.environ, "PYTHONPATH": str(PACKAGE)}
            run = subprocess.run(cmd, capture_output=True, text=True, env=env)
            self.assertEqual(run.returncode, 2, run.stderr)
            self.assertIn("refusing to pool", run.stderr)
            self.assertNotIn(str(seed), run.stderr)
            for field in expected:
                self.assertIn(field, run.stderr)
            run = subprocess.run(
                [*cmd, "--allow-mixed-builds", "--format", "json"], capture_output=True, text=True, env=env
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("WARNING", run.stderr)
            self.assertNotIn(str(seed), run.stdout)
            (check,) = json.loads(run.stdout)["build_checks"]
            self.assertEqual(check["differing_fields"], sorted(expected))

    def test_unknown_build_does_not_pool_with_known_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = fx.trial(root / "job", "toy-000-solo__old", condition="solo", events=[])
            report = build([str(legacy)])
            self.assertFalse(report["play"]["trials"][0]["build_known"])
            self.assertIn("1 trial with unknown build", render(report, "text"))
            folder = fx.task_dir(
                root / "tasks", briefings={"p1": {}, "p2": {}}, answer_key={"x": 1}, extra={"seed": 123}
            )
            fx.trial(root / "job", "toy-000-talk__new", events=[], task_path=str(folder))
            with self.assertRaisesRegex(MixedBuildsError, "known vs unknown"):
                build([str(root / "job")])

    def test_world_evidence_detects_different_legacy_worlds(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "job"
            fx.trial(job, "toy-000-talk__v1")
            fx.trial(
                job,
                "toy-000-talk__v2",
                events=[{"kind": "start", "data": {"world_version": 2}}],
            )
            with self.assertRaisesRegex(MixedBuildsError, "world_version"):
                build([str(job)])


class EvidenceReports(unittest.TestCase):
    def test_checked_reward_controls_correctness_and_nonfinite_scores_are_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job"
            folder = fx.task_dir(
                root / "tasks",
                label="toy.liar",
                condition="talk-liar",
                briefings={"p1": {"value": 1}, "p2": {"value": 2}},
                answer_key={"x": 3},
                extra={"decoy": {"x": 7}},
            )
            for name, reward, score, answer in (
                ("disagrees", 0.0, 1.0, 7),
                ("bad-reward", float("nan"), 1.0, 3),
                ("bad-score", 1.0, float("nan"), 3),
            ):
                fx.trial(
                    job,
                    name,
                    label="toy.liar",
                    condition="talk-liar",
                    reward=reward,
                    score={"score": score},
                    task_path=str(folder),
                    submissions=[{"n": 1, "seat": "p1", "answer": {"x": answer}, "t": 1}],
                )
            report = json.loads(render(build([str(job)]), "json"))
        (scores,) = report["scores"]
        self.assertEqual((scores["trials"], scores["unscored"], scores["mean"]["value"]), (2, 1, 0.5))
        trials = {row["trial"]: row for row in report["play"]["trials"]}
        self.assertEqual(
            (trials["disagrees"]["final_score"], trials["disagrees"]["liar_outcome"]), (0, "deceived")
        )
        self.assertIn("differs from checked reward", trials["disagrees"]["score_json_issue"])
        self.assertIsNone(trials["bad-reward"]["final_score"])
        self.assertEqual(trials["bad-reward"]["acted_on"], "unknown")
        self.assertEqual(
            (trials["bad-score"]["final_score"], trials["bad-score"]["liar_outcome"]), (1, "correct")
        )
        self.assertIn("treated as missing", trials["bad-score"]["score_json_issue"])
        (condition,) = report["play"]["conditions"]
        self.assertEqual(condition["score_json_issues"], 2)
        found = {row["trial"]: row["final_right"] for row in report["found_lost"]["trials"]}
        self.assertEqual(found, {"disagrees": False, "bad-reward": None, "bad-score": True})

    def test_missing_event_log_leaves_delivery_and_sight_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "job"
            message = {
                "id": "m1",
                "from": "p1",
                "to": "p2",
                "text": "The complete answer is in this message.",
            }
            trial = fx.trial(job, "toy-000-talk__missing", messages=[message])
            (trial / "artifacts" / "logs" / "world" / "events.jsonl").unlink()
            report = build([str(job)])
        (row,) = report["play"]["trials"]
        (pair,) = row["message_pairs"]
        self.assertEqual((pair["delivered"], pair["seen"], pair["note"]), (None, None, "no events.jsonl"))
        (condition,) = report["play"]["conditions"]
        for entry in (row, condition):
            self.assertIsNone(entry["delivered"])
            self.assertIsNone(entry["seen"])
            self.assertEqual((entry["delivered_unknown"], entry["seen_unknown"]), (1, 1))
        self.assertIn("| unknown (1 pair) | unknown (1 pair) |", render(report, "markdown"))

    def test_recovery_needs_a_minimum_gap_and_shows_its_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "job"
            for instance in range(2):
                for condition, reward in (("solo", 0.01), ("silent", 0.0), ("talk", 0.5)):
                    fx.trial(
                        job, f"{instance}-{condition}", instance=instance, condition=condition, reward=reward
                    )
            report = build([str(job)])
        (row,) = report["decomposition"]
        self.assertIsNone(row["recovery"]["value"])
        self.assertIn("below 0.05 reward points", row["recovery"]["note"])
        self.assertEqual(row["gap_solo_minus_silent"]["low"], 0.01)
        self.assertEqual(row["gap_solo_minus_silent"]["high"], 0.01)
        self.assertIn(
            "| gap = solo - silent | 0.010 | [0.010, 0.010] | "
            "all instance values equal: zero-width interval |\n"
            "| recovery = gain / gap | undefined |",
            render(report, "markdown"),
        )


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


class LiarFinalWriter(unittest.TestCase):
    def test_final_writer_by_outcome_in_lying_conditions(self):
        from types import SimpleNamespace

        from flotilla_analysis.report import liar_rows

        def play(liar, last):
            return SimpleNamespace(liar=liar, churn=SimpleNamespace(last_submitter=last))

        trials = [
            SimpleNamespace(path="a", condition="talk", instance=0, reward=1.0),
            SimpleNamespace(path="b", condition="talk-liar", instance=0, reward=0.0),
            SimpleNamespace(path="c", condition="talk-liar-warned", instance=0, reward=1.0),
            SimpleNamespace(path="d", condition="talk-liar", instance=1, reward=0.0),
        ]
        plays = {
            "a": play("correct", "p2"),
            "b": play("deceived", "p2"),
            "c": play("correct", "p1"),
            "d": play(None, None),
        }
        (row,) = liar_rows({("toy.liar", "team"): trials}, plays, seed=1, draws=100)
        # the honest-peer "talk" trial is not counted; a trial without submissions counts as "none"
        self.assertEqual(
            row["final_writer"], {"none": {"unknown": 1}, "p1": {"correct": 1}, "p2": {"deceived": 1}}
        )
