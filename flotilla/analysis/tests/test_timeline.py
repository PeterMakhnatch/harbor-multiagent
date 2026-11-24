import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import timedelta
from pathlib import Path

from flotilla_analysis.load import load
from flotilla_analysis.timeline import build_timeline, index_row, main, page_name, render_trial
from tests import fixtures as fx

DATA = Path(__file__).resolve().parent / "data"


def event(seq, t, seat, kind, **data):
    return {"seq": seq, "t": t, "seat": seat, "kind": kind, "data": data}


def start(seats=("p1", "p2")):
    return event(1, 0.0, None, "start", condition="talk", seats=list(seats))


def instruction(at):
    return fx.step(1, at, source="user", message="Your task: agree on a number.")


class Timeline(unittest.TestCase):
    """Fixture clock: environment setup starts at 0 s and both seats start at 10 s (wall seconds after
    the fixture epoch). p1's first request is at world t=2, so world zero is bounded at 10 - 2 = 8 s."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def one(self, job="job", name="toy-000-talk__t", **kwargs):
        fx.trial(self.root / job, name, **kwargs)
        trials, _, problems = load([self.root / job / name])
        self.assertEqual(problems, [])
        return trials[0]

    def test_one_clock_orders_events_messages_and_steps(self):
        message = {"id": "m1", "from": "p1", "to": "all", "text": "I say 7.", "t": 5.0}
        trial = self.one(
            events=[
                start(),
                event(2, 2.0, "p1", "whoami"),
                event(3, 3.0, "p2", "whoami"),
                event(4, 5.0, "p1", "send", id="m1", to="all", chars=8),
                event(5, 9.0, "p2", "deliver", ids=["m1"]),
                event(6, 13.0, "p2", "submit", n=1, answer={"x": 7}),
            ],
            messages=[message],
            submissions=[{"n": 1, "seat": "p2", "answer": {"x": 7}, "t": 13.0}],
            trajectories={
                "p1": [instruction(10), fx.step(2, 15, keystrokes=["curl -s $WORLD_URL/messages\n"])],
                "p2": [instruction(10), fx.step(2, 20, keystrokes=["curl -s -X POST $WORLD_URL/submit\n"])],
            },
        )
        timeline = build_timeline(trial)
        self.assertEqual(timeline.bound, fx.EPOCH + timedelta(seconds=8))
        self.assertEqual(
            [(r.at, r.lane, r.kind) for r in timeline.rows],
            [
                (0.0, "world", "start"),
                (2.0, "p1", "agent-start"),
                (2.0, "p2", "agent-start"),
                (2.0, "p1", "whoami"),
                (3.0, "p2", "whoami"),
                (5.0, "p1", "send"),
                (7.0, "p1", "step"),
                (9.0, "p2", "deliver"),
                (12.0, "p2", "step"),
                (13.0, "p2", "submit"),
                (62.0, "p1", "agent-end"),
                (62.0, "p2", "agent-end"),
            ],
        )
        send = timeline.rows[5]
        self.assertEqual(send.summary, "p1 → all (p2): I say 7.")
        self.assertEqual(timeline.rows[7].summary, "1: m1 from p1")
        page = render_trial(timeline)
        positions = [page.index(s) for s in ("I say 7.", "$WORLD_URL/messages", "1: m1 from p1", "/submit")]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("World zero is placed at 2026-11-19T09:00:08.000Z", page)
        self.assertIn("<th>p1<br><small>terminus-2:openrouter/deepseek/deepseek-v4-flash</small></th>", page)

    def test_message_without_send_event_is_placed_from_the_frozen_state(self):
        late = {"id": "m9", "from": "p2", "to": "p1", "text": "late words", "t": 30.0}
        trial = self.one(events=[start(), event(2, 2.0, "p1", "whoami")], messages=[late])
        timeline = build_timeline(trial)
        (row,) = [r for r in timeline.rows if r.kind == "message"]
        self.assertEqual((row.at, row.lane), (30.0, "p2"))
        self.assertTrue(row.summary.startswith("p2 → p1: late words [no send event"))

    def test_untrusted_text_is_escaped_and_long_text_cut(self):
        evil = '<script>alert("m")</script> & </td>'
        trial = self.one(
            events=[
                start(),
                event(2, 2.0, "p1", "send", id="m1", to="all", chars=len(evil)),
                event(3, 3.0, "p1", '"><script>alert(1)</script>', path="/<img src=x onerror=alert(2)>"),
            ],
            messages=[{"id": "m1", "from": "p1", "to": "all", "text": evil, "t": 2.0}],
            trajectories={
                "p1": [
                    instruction(10),
                    fx.step(
                        2,
                        15,
                        keystrokes=['echo "</pre><img src=x onerror=alert(3)>"\n'],
                        observation="<b>bold</b>" + "y" * 5000,
                    ),
                ]
            },
        )
        page = render_trial(build_timeline(trial))
        self.assertEqual(page.count("<script>"), 1)
        self.assertEqual(page.count("</script>"), 1)
        self.assertNotIn("<img", page)
        self.assertNotIn("<b>bold", page)
        self.assertIn("&lt;script&gt;alert(&quot;m&quot;)&lt;/script&gt; &amp; &lt;/td&gt;", page)
        self.assertIn("&lt;b&gt;bold&lt;/b&gt;", page)
        self.assertIn('class="cell k-___script_alert_1___script_"', page)
        self.assertIn("[cut: 3511 more characters, 5011 in total]", page)
        self.assertIn("observation (5011 characters)", page)

    def test_gaps_in_a_seats_steps_are_outlined(self):
        steps = [instruction(10), fx.step(2, 11), fx.step(3, 15), fx.step(4, 50)]
        trial = self.one(events=[start(), event(2, 2.0, "p1", "whoami")], trajectories={"p1": steps})
        timeline = build_timeline(trial, gap_s=30)
        gaps = [(r.kind, r.summary.split(" ")[0], r.gap) for r in timeline.rows if r.gap is not None]
        self.assertEqual(gaps, [("step", "#4", 35.0)])
        self.assertEqual(timeline.stalls, {"p1": 35.0})
        self.assertIn("stall p1 35s", timeline.flags)
        page = render_trial(timeline)
        self.assertEqual(page.count('<tr class="g-step gap">'), 1)
        self.assertIn("gap 35 s since this seat's previous step", page)
        tight = build_timeline(trial, gap_s=10)
        self.assertEqual([r.gap for r in tight.rows if r.gap is not None], [35.0, 20.0])
        self.assertEqual(tight.rows[-1].kind, "agent-end")
        self.assertEqual(build_timeline(trial).stalls, {})

    def test_index_lists_every_trial_with_flags(self):
        gated = [
            start(),
            event(2, 2.0, "p1", "gated", path="/claim", unread=1, ids=["m1"]),
            event(3, 2.0, "p1", "deliver", ids=["m1"], via="gate"),
        ]
        fx.trial(self.root / "jobs" / "j1", "toy-000-talk__a", events=gated, reward=0.5)
        fx.trial(
            self.root / "jobs" / "j2",
            "toy-001-silent__b",
            instance=1,
            condition="silent",
            reward=None,
            events=[start(), event(2, 2.0, "p1", "whoami")],
            trajectories={"p1": [instruction(10), fx.step(2, 11), fx.step(3, 200)]},
        )
        fx.trial(self.root / "jobs" / "other", "toy-002-talk__c", instance=2)
        out = self.root / "html"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--index", str(self.root / "jobs"), "j*", "-o", str(out)]), 0)
        page = (out / "index.html").read_text()
        self.assertEqual(
            sorted(p.name for p in out.iterdir()),
            ["index.html", "j1__toy-000-talk__a.html", "j2__toy-001-silent__b.html"],
        )
        self.assertIn('<a href="j1__toy-000-talk__a.html">j1/toy-000-talk__a</a>', page)
        self.assertIn("gated 1", page)
        self.assertIn("stall p1 189s", page)
        self.assertIn("unscored: no reward", page)
        self.assertNotIn("toy-002", page)

        trials = load([self.root / "jobs" / "j1"])[0]
        row = index_row(build_timeline(trials[0]), page_name(trials[0]))
        self.assertEqual(
            {k: row[k] for k in ("trial", "label", "condition", "instance", "reward", "cost_usd", "flags")},
            {
                "trial": "j1/toy-000-talk__a",
                "label": "toy",
                "condition": "talk",
                "instance": 0,
                "reward": 0.5,
                "cost_usd": 0.002,
                "flags": ["gated 1"],
            },
        )

    def test_cli_needs_one_trial_or_index(self):
        fx.trial(self.root / "job", "toy-000-talk__a")
        fx.trial(self.root / "job", "toy-001-talk__b", instance=1)
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main([str(self.root / "job"), "-o", str(self.root / "x.html")]), 1)
            self.assertEqual(main([str(self.root / "job"), "j*", "-o", str(self.root / "x.html")]), 2)
            self.assertEqual(main(["--index", str(self.root), "none*", "-o", str(self.root / "o")]), 1)
        self.assertIn("holds 2 Flotilla trials", err.getvalue())
        with redirect_stdout(io.StringIO()):
            code = main([str(self.root / "job" / "toy-000-talk__a"), "-o", str(self.root / "a.html")])
        self.assertEqual(code, 0)
        self.assertTrue((self.root / "a.html").read_text().startswith("<!doctype html>"))


class RealOracleTimeline(unittest.TestCase):
    def test_oracle_check_talk_trial(self):
        trials, _, _ = load([DATA / "oracle-check-2seat" / "schedule-000-talk__5YWXpG4"])
        timeline = build_timeline(trials[0])
        self.assertEqual(timeline.lanes, ["world", "p1", "p2"])
        kinds = [(r.lane, r.kind) for r in timeline.rows if r.kind not in {"agent-start", "agent-end"}]
        self.assertEqual(
            kinds,
            [
                ("world", "start"),
                ("p2", "whoami"),
                ("p2", "send"),
                ("p2", "deliver"),
                ("p1", "whoami"),
                ("p1", "send"),
                ("p1", "deliver"),
                ("p2", "deliver"),
                ("p1", "submit"),
                ("p2", "submit"),
                ("world", "verifier"),
            ],
        )
        times = [r.at for r in timeline.rows]
        self.assertEqual(times, sorted(times))
        page = render_trial(timeline)
        self.assertIn("no trajectory", page)
        self.assertIn("<th>reward</th><td>1.000</td>", page)
        for message in timeline.evidence.messages:
            self.assertIn(f"message {message['id']} ({len(message['text'])} characters)", page)


if __name__ == "__main__":
    unittest.main()
