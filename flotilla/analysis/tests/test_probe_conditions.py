import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from flotilla_analysis.load import TASK_NAME, load, load_evidence, load_trajectories
from flotilla_analysis.names import PROBE_CONDITIONS
from flotilla_analysis.play import seat_play, world_start_bound
from tests import fixtures as fx


class ProbeConditions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_probe_arms_parse_and_load(self):
        for condition in PROBE_CONDITIONS:
            match = TASK_NAME.match(f"flotilla/probe.conformity-003-{condition}")
            if match is None:
                self.fail(f"{condition} must parse")
            self.assertEqual((match["label"], match["condition"]), ("probe.conformity", condition))
        self.assertEqual(
            TASK_NAME.match("flotilla/probe.conformity-003-talk-quiet")["condition"], "talk-quiet"
        )
        self.assertIsNone(TASK_NAME.match("flotilla/probe.conformity-003-talk-loud"))
        self.assertTrue({"talk-one", "talk-lead"} <= set(PROBE_CONDITIONS))
        fx.trial(
            self.root / "job", "probe-000-talk-wrong__a", label="probe.conformity", condition="talk-wrong"
        )
        trials, _, problems = load([self.root])
        self.assertEqual([(t.label, t.condition) for t in trials], [("probe.conformity", "talk-wrong")])
        self.assertEqual(problems, [])

    def test_scheduled_sends_are_not_seat_requests(self):
        events = [
            {"seq": 1, "t": 0.0, "seat": None, "kind": "start", "data": {}},
            {
                "seq": 2,
                "t": 1.0,
                "seat": "p2",
                "kind": "send",
                "data": {"id": "m-1", "to": "all", "scheduled": True},
            },
            {"seq": 3, "t": 20.0, "seat": "p1", "kind": "whoami", "data": {}},
            {"seq": 4, "t": 30.0, "seat": "p1", "kind": "gated", "data": {"path": "/submit", "ids": ["m-1"]}},
        ]
        seats = [fx.seat_result("p1", start=25.0), fx.seat_result("p2", start=25.0, under_test=False)]
        directory = fx.trial(
            self.root / "job",
            "probe-000-talk-wrong__a",
            label="probe.conformity",
            condition="talk-wrong",
            seats=seats,
            events=events,
        )
        trials, _, _ = load([directory])
        evidence = load_evidence(trials[0])
        bound = world_start_bound(trials[0], evidence, load_trajectories(trials[0]))
        self.assertEqual(bound, fx.EPOCH + timedelta(seconds=5))
        self.assertEqual(seat_play("p2", trials[0], evidence, None).requests, {})
        self.assertEqual(seat_play("p1", trials[0], evidence, None).requests, {"gated": 1, "whoami": 1})


if __name__ == "__main__":
    unittest.main()
