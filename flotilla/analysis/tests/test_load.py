import tempfile
import unittest
from pathlib import Path

from flotilla_analysis.load import TASK_NAME, load, load_evidence
from flotilla_analysis.report import build
from flotilla_analysis.tasks import final_answer
from tests import fixtures as fx


class TaskNames(unittest.TestCase):
    def test_labels_and_conditions(self):
        match = TASK_NAME.match("flotilla/schedule.3seat-004-talk-liar")
        if match is None:
            self.fail("a variant label with talk-liar must parse")
        self.assertEqual(
            (match["label"], match["instance"], match["condition"]), ("schedule.3seat", "004", "talk-liar")
        )
        self.assertIsNone(TASK_NAME.match("flotilla/schedule-4-talk"))
        self.assertIsNone(TASK_NAME.match("other/schedule-004-talk"))

    def test_warned_conditions(self):
        for condition in ("talk-warned", "talk-liar-warned"):
            match = TASK_NAME.match(f"flotilla/schedule.liar-007-{condition}")
            if match is None:
                self.fail(f"{condition} must parse")
            got = (match["label"], match["instance"], match["condition"])
            self.assertEqual(got, ("schedule.liar", "007", condition))


class Walk(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_walk_jobs_and_problems(self):
        job = self.root / "runs" / "job-a"
        fx.write_json(job / "result.json", {"n_total_trials": 4, "stats": {}, "finished_at": None})
        fx.trial(job, "toy-000-talk__a", instance=0)
        fx.trial(job, "toy-001-solo__b", instance=1, condition="solo", seats=[fx.seat_result("p1")])
        (job / "toy-002-talk__running").mkdir()
        (job / "toy-002-talk__running" / "config.json").write_text("{}")
        fx.write_json(
            job / "other__c" / "result.json", {"trial_name": "other__c", "task_name": "hello/world"}
        )
        copy = self.root / "copy"
        fx.trial(copy, "toy-000-talk__a", instance=0)  # same result id: a copied trial counts once
        trials, jobs, problems = load([self.root / "runs", self.root])
        self.assertEqual([t.trial_name for t in trials], ["toy-001-solo__b", "toy-000-talk__a"])
        self.assertEqual(len(jobs), 1)
        self.assertEqual((jobs[0].configured, jobs[0].unfinished_dirs), (4, 1))
        reasons = sorted(p.reason for p in problems)
        self.assertTrue(any("not a Flotilla task name" in r for r in reasons))
        self.assertTrue(any("duplicate" in r for r in reasons))
        missing, _, problems = load([self.root / "absent"])
        self.assertEqual(missing, [])
        self.assertEqual(problems[0].reason, "path does not exist")

    def test_rewards_and_teams(self):
        job = self.root / "job"
        fx.trial(job, "toy-000-talk__ok", reward=0.5)
        fx.trial(
            job,
            "toy-001-talk__invalid",
            reward=1.0,
            reward_valid=False,
            seats=[fx.seat_result("p1", exception="ApiError"), fx.seat_result("p2")],
        )
        fx.trial(job, "toy-002-talk__none", reward=None)
        fx.trial(job, "toy-003-talk__running", reward=None, finished=False)
        fx.trial(job, "toy-004-talk__range", reward=1.5)
        fx.trial(
            job, "toy-005-talk__mixed", seats=[fx.seat_result("p1"), fx.seat_result("p2", model="other/m")]
        )
        fx.trial(
            job,
            "toy-006-talk__liar",
            seats=[
                fx.seat_result("p1"),
                fx.seat_result("p2"),
                fx.seat_result("p3", agent="oracle", model=None, under_test=False),
            ],
        )
        trials = {t.trial_name: t for t in load([job])[0]}
        self.assertEqual(trials["toy-000-talk__ok"].reward, 0.5)
        self.assertIn("reward_valid is false (ApiError)", str(trials["toy-001-talk__invalid"].unscored))
        self.assertEqual(trials["toy-002-talk__none"].unscored, "no reward")
        self.assertEqual(trials["toy-003-talk__running"].unscored, "trial not finished")
        self.assertIn("outside [0, 1]", str(trials["toy-004-talk__range"].unscored))
        self.assertEqual(trials["toy-000-talk__ok"].team, "terminus-2:openrouter/deepseek/deepseek-v4-flash")
        self.assertTrue(trials["toy-005-talk__mixed"].team.startswith("mixed(p1="))
        self.assertEqual(
            trials["toy-006-talk__liar"].team, "terminus-2:openrouter/deepseek/deepseek-v4-flash"
        )

    def test_world_state_is_authoritative(self):
        job = self.root / "job"
        subs = [
            {"n": 1, "seat": "p1", "answer": {"x": 1}, "t": 5.0},
            {"n": 2, "seat": "p2", "answer": {"x": 3}, "t": 9.0},
        ]
        msgs = [{"id": "m1", "from": "p1", "to": "all", "text": "hello there", "t": 2.0}]
        directory = fx.trial(job, "toy-000-talk__x", submissions=subs, messages=msgs)
        # the artifacts were collected before the last submission and before m2
        fx.write_jsonl(
            directory / "artifacts/logs/world/events.jsonl",
            [{"seq": 1, "t": 5.0, "seat": "p1", "kind": "submit", "data": {"n": 1, "answer": {"x": 1}}}],
        )
        state = {
            "seats": ["p1", "p2"],
            "messages": msgs + [{"id": "m2", "from": "p2", "to": "all", "text": "late", "t": 9.5}],
            "submissions": subs,
        }
        fx.write_json(directory / "verifier/world-state.json", state)
        evidence = load_evidence(load([job])[0][0])
        self.assertEqual([m["id"] for m in evidence.messages or []], ["m1", "m2"])
        self.assertEqual(len(evidence.submissions or []), 2)
        self.assertTrue(any("messages.jsonl lacks m2" in p for p in evidence.problems))
        self.assertTrue(any("events.jsonl has 1 submissions" in p for p in evidence.problems))

    def test_truncated_event_answer_does_not_count_as_null(self):
        job = self.root / "job"
        events = [
            {"kind": "submit", "seat": "p1", "data": {"n": 1, "answer": None}},
            {
                "kind": "submit",
                "seat": "p2",
                "data": {"n": 2, "answer_sha256": "a" * 64, "answer_bytes": 2048, "answer_prefix": '{"x":'},
            },
        ]
        fx.trial(job, "toy-000-talk__truncated", reward=0.0, events=events, world_state=False)
        evidence = load_evidence(load([job])[0][0])
        self.assertIsNone(evidence.submissions[0]["answer"])
        self.assertNotIn("answer", evidence.submissions[1])
        self.assertEqual(final_answer(evidence), (False, None))
        report = build([str(job)])
        (row,) = report["play"]["trials"]
        self.assertEqual(
            (row["submissions"], row["distinct_answers"], row["answer_changes"]), (2, None, None)
        )
        self.assertIn("answer for submission 2 was truncated", report["problems"][0]["reason"])
