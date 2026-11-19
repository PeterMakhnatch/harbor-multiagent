"""Found, kept and lost answers in talk trials (play.found_lost) on small synthetic schedule trials.

The task folders copy flotilla/families/schedule.py, whose analysis hooks (carries_own_data,
answer_in_text) decide what a message carries. World times are seconds; the right answer is Thu 14:00.
"""

import tempfile
import unittest
from pathlib import Path

from flotilla_analysis.load import load, load_evidence, load_trajectories
from flotilla_analysis.play import ANNOUNCE_BEFORE, analyze
from flotilla_analysis.render import render
from flotilla_analysis.report import build
from flotilla_analysis.tasks import seat_private, task_info
from tests import fixtures as fx

KEY = {"day": "Thu", "start": "14:00"}
FREE = {"Mon": [], "Tue": [], "Wed": [], "Thu": [], "Fri": []}
BRIEFINGS = {
    "p1": {"calendars": {"Ana": {**FREE, "Mon": [9, 10]}, "Chen": {**FREE, "Wed": [11]}}},
    "p2": {"calendars": {"Ben": {**FREE, "Mon": [9, 10], "Thu": [9]}, "Dara": {**FREE, "Mon": [12]}}},
}
RIGHT = {"day": "Thu", "start": "14:00"}
WRONG = {"day": "Mon", "start": "11:00"}
# Requests with format examples: a bare day-and-hour regex counts each of them as data.
REQUEST_OWN_NAMES = "I have Ana and Chen. Please send Ben's and Dara's busy hours (e.g. Mon 09,10)."
REQUEST_RECEIVER_NAMES = "Please send Ben's hours, e.g. Mon 09,10; Tue 13"
REQUEST_NO_NAMES = 'Please share your busy hours in the format "Mon 09,10; Tue 13".'
P2_DATA = "Ben: Mon 09,10; Tue -; Wed -; Thu 09; Fri -\nDara: Mon 12; Tue -; Wed -; Thu -; Fri -"
P1_DATA = "Ana: Mon(09,10), Tue(-). Chen: Wed(11)."


def message(mid, sender, text, t, to="all"):
    return {"id": mid, "from": sender, "to": to, "text": text, "t": t}


def submission(n, seat, answer, t):
    return {"n": n, "seat": seat, "answer": answer, "t": t}


def events(messages, submissions, deliveries):
    """World events: a send per message, a deliver per (t, seat, ids), a submit per submission."""
    rows = [{"t": 0.0, "seat": None, "kind": "start", "data": {"world_version": 2, "seats": ["p1", "p2"]}}]
    rows += [
        {"t": m["t"], "seat": m["from"], "kind": "send", "data": {"id": m["id"], "to": m["to"]}}
        for m in messages
    ]
    rows += [{"t": t, "seat": seat, "kind": "deliver", "data": {"ids": ids}} for t, seat, ids in deliveries]
    rows += [
        {"t": s["t"], "seat": s["seat"], "kind": "submit", "data": {"n": s["n"], "answer": s["answer"]}}
        for s in submissions
    ]
    rows.sort(key=lambda row: row["t"])
    return [{"seq": i + 1, **row} for i, row in enumerate(rows)]


class FoundLost(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tasks = {
            condition: fx.task_dir(
                self.root / "tasks",
                label="schedule",
                condition=condition,
                briefings=BRIEFINGS,
                answer_key=KEY,
                family_source=fx.SCHEDULE,
            )
            for condition in ("talk", "silent")
        }

    def tearDown(self):
        self.tmp.cleanup()

    def trial(self, name, *, messages, submissions, deliveries, condition="talk", job="job"):
        final = submissions[-1]["answer"] if submissions else None
        return fx.trial(
            self.root / job,
            f"schedule-000-{condition}__{name}",
            label="schedule",
            condition=condition,
            reward=1.0 if final == RIGHT else 0.0,
            events=events(messages, submissions, deliveries),
            messages=messages,
            submissions=submissions,
            task_path=str(self.tasks[condition]),
        )

    def play(self, directory, hook_dirs=None):
        (trial,) = [t for t in load([directory.parent])[0] if t.trial_name == directory.name]
        evidence = load_evidence(trial)
        info = task_info(trial, evidence, [], hook_dirs)
        self.assertIsNone(info.problem)
        return analyze(trial, evidence, load_trajectories(trial), info)

    def test_lost_to_a_teammate_overwrite(self):
        """p1 gets p2's calendars, submits the right answer and stops; p2 only ever got p1's requests and
        overwrites it with a guess."""
        messages = [
            message("m1", "p1", REQUEST_OWN_NAMES, 10.0),
            message("m2", "p2", P2_DATA, 12.0),
            message("m3", "p1", REQUEST_RECEIVER_NAMES, 30.0, to="p2"),
        ]
        submissions = [submission(1, "p1", RIGHT, 25.0), submission(2, "p2", WRONG, 60.0)]
        deliveries = [(15.0, "p2", ["m1"]), (20.0, "p1", ["m2"]), (35.0, "p2", ["m1", "m3"])]
        fl = self.play(
            self.trial("lost", messages=messages, submissions=submissions, deliveries=deliveries)
        ).found_lost
        self.assertEqual((fl.found, fl.final_right, fl.lost), (True, False, True))
        self.assertEqual((fl.solver, fl.first_right_t), ("p1", 25.0))
        self.assertEqual((fl.teammate_overwrite, fl.overwriter, fl.overwrite_t), (True, "p2", 60.0))
        self.assertEqual((fl.solver_had_teammate_data, fl.solver_data_messages), (True, ["m2"]))
        # m1 names p1's own attendees, but its only day and hour are a format example in parentheses
        self.assertEqual((fl.teammate_had_solver_data, fl.overwriter_data_messages), (False, []))
        self.assertIs(fl.solver_announced, False)  # m3 is inside the window but names no answer
        self.assertEqual((fl.later_by_solver, fl.later_by_others), (0, 1))
        self.assertEqual(fl.notes, [])

    def test_a_request_with_a_format_example_is_not_data(self):
        """The solver received only requests whose day-and-hour text is a format example; p1 sent its data
        before the right submission, but p2 fetched it only after, so it does not count either."""
        messages = [
            message("m1", "p1", REQUEST_RECEIVER_NAMES, 5.0),
            message("m2", "p1", REQUEST_NO_NAMES, 6.0),
            message("m3", "p1", P1_DATA, 25.0),
        ]
        submissions = [submission(1, "p2", RIGHT, 30.0)]
        deliveries = [(10.0, "p2", ["m1", "m2"]), (45.0, "p2", ["m1", "m2", "m3"])]
        fl = self.play(
            self.trial("guess", messages=messages, submissions=submissions, deliveries=deliveries)
        ).found_lost
        self.assertEqual((fl.found, fl.final_right, fl.lost, fl.solver), (True, True, False, "p2"))
        self.assertEqual((fl.solver_had_teammate_data, fl.solver_data_messages), (False, []))
        self.assertEqual(
            (fl.teammate_overwrite, fl.teammate_had_solver_data, fl.overwriter), (False, None, None)
        )

    def test_announcement_window(self):
        """A candidate list long before the submission is not an announcement; a message a few seconds
        before it is, and so is one after it."""
        early = 40.0 - ANNOUNCE_BEFORE - 20.0
        messages = [
            message("m1", "p1", "Free for Ana and Chen: Mon 11:00, Thu 14:00, Fri 09:00", early),
            message("m2", "p2", P2_DATA, 12.0),
            message("m3", "p1", "The slot is Thursday 14:00-16:00. Submitting now.", 34.0),  # 6 s before
            message("m4", "p1", 'SUBMITTED: {"day": "Thu", "start": "14:00"}', 41.0, to="p2"),
        ]
        submissions = [submission(1, "p1", RIGHT, 40.0), submission(2, "p2", RIGHT, 50.0)]
        deliveries = [(15.0, "p1", ["m2"]), (45.0, "p2", ["m1", "m3", "m4"])]
        fl = self.play(
            self.trial("won", messages=messages, submissions=submissions, deliveries=deliveries)
        ).found_lost
        self.assertEqual((fl.found, fl.final_right, fl.lost), (True, True, False))
        self.assertEqual((fl.solver_announced, fl.announce_messages), (True, ["m3", "m4"]))
        self.assertEqual((fl.teammate_overwrite, fl.later_by_solver, fl.later_by_others), (False, 0, 1))
        alone = [message("m1", "p1", "Free for Ana and Chen: Mon 11:00, Thu 14:00, Fri 09:00", early)]
        fl = self.play(
            self.trial("early", messages=alone, submissions=submissions[:1], deliveries=[], job="early")
        ).found_lost
        self.assertEqual((fl.solver_announced, fl.solver_had_teammate_data), (False, False))

    def test_the_solver_keeps_submitting(self):
        """No teammate data and no overwrite: the solver itself replaces its right answer."""
        submissions = [submission(1, "p1", RIGHT, 20.0), submission(2, "p1", WRONG, 21.0)]
        submissions += [submission(3, "p1", {"day": "Fri", "start": "09:00"}, 22.0)]
        fl = self.play(self.trial("spray", messages=[], submissions=submissions, deliveries=[])).found_lost
        self.assertEqual((fl.found, fl.lost, fl.teammate_overwrite), (True, True, False))
        self.assertEqual((fl.later_by_solver, fl.later_by_others), (2, 0))
        self.assertEqual((fl.solver_had_teammate_data, fl.solver_announced), (False, False))

    def test_never_found_and_not_a_talk_condition(self):
        fl = self.play(
            self.trial("none", messages=[], submissions=[submission(1, "p2", WRONG, 9.0)], deliveries=[])
        ).found_lost
        self.assertEqual((fl.found, fl.final_right, fl.lost, fl.solver), (False, False, False, None))
        silent = self.trial(
            "silent",
            condition="silent",
            messages=[],
            submissions=[submission(1, "p1", RIGHT, 9.0)],
            deliveries=[],
        )
        self.assertIsNone(self.play(silent).found_lost)

    def test_without_hooks_the_data_measures_are_unknown(self):
        """A task folder built before the hooks existed: found and lost are known, data and announcements
        are unknown unless no message qualifies. --family-hooks supplies the hooks."""
        source = fx.SCHEDULE.read_text()
        old = source[: source.index("# --- Analysis hooks")]
        folder = fx.task_dir(
            self.root / "old-tasks", label="schedule", briefings=BRIEFINGS, answer_key=KEY, family_source=old
        )
        messages = [message("m1", "p2", P2_DATA, 12.0), message("m2", "p1", "Thu 14:00, submitting", 21.0)]
        submissions = [submission(1, "p1", RIGHT, 20.0)]
        fx.trial(
            self.root / "old-job",
            "schedule-000-talk__old",
            label="schedule",
            reward=1.0,
            events=events(messages, submissions, [(15.0, "p1", ["m1"])]),
            messages=messages,
            submissions=submissions,
            task_path=str(folder),
        )
        directory = self.root / "old-job" / "schedule-000-talk__old"
        fl = self.play(directory).found_lost
        self.assertEqual((fl.found, fl.final_right, fl.lost, fl.solver), (True, True, False, "p1"))
        self.assertEqual((fl.solver_had_teammate_data, fl.solver_announced), (None, None))
        self.assertIn("no carries_own_data hook", " ".join(fl.notes))
        hooks = self.root / "hooks"
        hooks.mkdir()
        (hooks / "schedule.py").write_text(source)
        fl = self.play(directory, hook_dirs=[hooks]).found_lost
        self.assertEqual((fl.solver_had_teammate_data, fl.solver_announced, fl.notes), (True, True, []))
        other = self.root / "other-hooks"
        other.mkdir()
        (other / "schedule.py").write_text(source.replace('FAMILY = "schedule"', 'FAMILY = "toy"'))
        self.assertIsNone(self.play(directory, hook_dirs=[other]).found_lost.solver_had_teammate_data)

    def test_report_counts_and_text(self):
        lost = [message("m1", "p1", REQUEST_OWN_NAMES, 10.0), message("m2", "p2", P2_DATA, 12.0)]
        self.trial(
            "a",
            messages=lost,
            submissions=[submission(1, "p1", RIGHT, 25.0), submission(2, "p2", WRONG, 60.0)],
            deliveries=[(15.0, "p2", ["m1"]), (20.0, "p1", ["m2"])],
        )
        won = [message("m1", "p2", P2_DATA, 12.0), message("m2", "p1", "Thursday 14:00 it is", 26.0)]
        self.trial(
            "b",
            messages=won,
            submissions=[submission(1, "p1", RIGHT, 25.0)],
            deliveries=[(15.0, "p1", ["m1"])],
        )
        self.trial("c", messages=[], submissions=[submission(1, "p2", WRONG, 9.0)], deliveries=[])
        report = build([str(self.root / "job")], resamples=100, draws=100)
        (row,) = report["found_lost"]["conditions"]
        self.assertEqual(
            (row["trials"], row["found"], row["final_right"], row["won"], row["lost"]), (3, 2, 1, 1, 1)
        )
        self.assertEqual(row["teammate_overwrite"], {"lost": 1, "won": 0})
        self.assertEqual(row["solver_had_teammate_data"], {"yes": 2, "no": 0, "unknown": 0})
        self.assertEqual(row["teammate_had_solver_data"]["lost"], {"yes": 0, "no": 1, "unknown": 0})
        self.assertEqual(row["solver_announced"]["lost"], {"yes": 0, "no": 1, "unknown": 0})
        self.assertEqual(row["solver_announced"]["won"], {"yes": 1, "no": 0, "unknown": 0})
        self.assertEqual((row["later_by_solver"], row["later_by_others"]), (0, 1))
        self.assertEqual(len(report["found_lost"]["trials"]), 3)
        self.assertEqual(report["family_hooks"], {"task folder": 3})
        self.assertEqual(report["settings"]["announce_before_s"], ANNOUNCE_BEFORE)
        text = render(report, "text")
        self.assertIn("Found and lost answers (talk conditions)", text)
        self.assertIn("p2 at 60.0", text)
        self.assertIn("Family hooks: task folder: 3", text)
        self.assertIn(f"at most {ANNOUNCE_BEFORE:g} s before", text)

    def test_a_scripted_peer_posts_its_part(self):
        info = task_info(
            load(
                [self.trial("x", messages=[], submissions=[submission(1, "p1", RIGHT, 1.0)], deliveries=[])]
            )[0][0],
            None,
            [],
        )
        info.briefings["p3"] = {
            "part": {"calendars": {"Gus": FREE}},
            "script": "liar",
            "text": "- Gus is busy",
        }
        self.assertEqual(seat_private(info, "p3"), {"calendars": {"Gus": FREE}})
        self.assertEqual(seat_private(info, "p1"), BRIEFINGS["p1"])
