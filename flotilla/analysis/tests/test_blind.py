import contextlib
import io
import json
import re
import tempfile
import time
import unittest
from pathlib import Path

from flotilla_analysis import dashboard, watch
from tests import fixtures as fx
from tests.test_watch import live_job, running_trial, trajectory

T0 = float(int(time.time()))
REWARD = 0.6180339
OUT_OF_RANGE = 1.4142
ANSWER = "ANSWER-SENTINEL"
SCORE_FIELD = "SCORE-SENTINEL"
LEAKS = ("0.618", "1.414", ANSWER, SCORE_FIELD)


def keys(value) -> set[str]:
    found: set[str] = set()
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            found.update(item)
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return found


def leaked(leak: str, text: str) -> bool:
    """A sentinel as its own value, not digits inside a longer number such as 1795536290.618509."""
    return re.search(r"(?<![0-9])" + re.escape(leak), text) is not None


class Blind(unittest.TestCase):
    """One scored trial (reward 0.618, its answer and a score.json field as sentinels), one unscored trial
    whose unscored reason quotes its out-of-range reward, one trial with a seat exception, and a running
    trial with a stalled seat."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.jobs = self.root / "jobs"
        job = live_job(self.jobs, "j1", total=5, at=T0)
        submission = {"n": 1, "seat": "p2", "answer": {"slot": ANSWER}, "t": 13.0}
        fx.trial(
            job,
            "toy-000-talk__scored",
            reward=REWARD,
            submissions=[submission],
            score={"score": REWARD, "answer": {"slot": ANSWER}, "note": SCORE_FIELD},
            trajectories={
                "p2": [fx.step(1, 10, source="user", message="go"), fx.step(2, 20, keystrokes=[ANSWER])]
            },
        )
        fx.trial(job, "toy-001-talk__range", instance=1, reward=OUT_OF_RANGE)
        fx.trial(
            job,
            "toy-002-talk__error",
            instance=2,
            reward=None,
            seats=[fx.seat_result("p1", exception="AgentTimeoutError"), fx.seat_result("p2")],
        )
        live = running_trial(job, "t-a__live", [("p1", "terminus-2")], at=T0 - 600)
        trajectory(live, "p1", at=T0 - 600)

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, *args: str) -> str:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(watch.main([str(self.jobs), "--once", *args]), 0)
        return stdout.getvalue()

    def surfaces(self, *args: str) -> tuple[str, str, Path]:
        out = self.root / ("blind" if args else "open")
        text = self.run_main("--json", str(out / "status.json"), "--html", str(out / "html"), *args)
        return text, (out / "status.json").read_text(), out / "html"

    def test_the_sentinels_show_without_blind(self):
        text, raw, html = self.surfaces()
        page = (html / "index.html").read_text()
        for surface in (text, raw, page):
            self.assertTrue(leaked("0.618", surface))
        self.assertIn("1.4142", raw)
        timeline = (html / "trials" / "j1__toy-000-talk__scored.html").read_text()
        self.assertIn(ANSWER, timeline)
        self.assertIn(SCORE_FIELD, timeline)

    def test_text_shows_progress_validity_and_errors_only(self):
        text, _, _ = self.surfaces("--blind")
        self.assertTrue(text.startswith("flotilla-watch BLIND "))
        for leak in LEAKS:
            self.assertFalse(leaked(leak, text), leak)
        self.assertNotIn("mean", text.split("ETA")[1])
        self.assertIn("trials 5: done 3 (valid 1, invalid 2), running 1, pending 1", text)
        self.assertIn("p1:AgentTimeoutError", text)
        self.assertIn("STALL", text)
        self.assertIn("rewards: hidden (blind)", text)

    def test_json_has_no_reward_or_score_value(self):
        _, raw, _ = self.surfaces("--blind")
        for leak in LEAKS:
            self.assertFalse(leaked(leak, raw), leak)
        status = json.loads(raw)
        self.assertIs(status["blind"], True)
        self.assertFalse(keys(status) & {"reward", "rewards", "mean_reward", "score", "answer"})
        finished = {t["trial"]: t for t in status["finished"]}
        self.assertEqual(
            {name: (t["valid"], t["unscored"]) for name, t in finished.items()},
            {
                "toy-000-talk__scored": (True, None),
                "toy-001-talk__range": (False, watch.BLIND_UNSCORED),
                "toy-002-talk__error": (False, watch.BLIND_UNSCORED),
            },
        )
        self.assertEqual(finished["toy-002-talk__error"]["exceptions"], ["p1:AgentTimeoutError"])
        self.assertEqual([a["kind"] for a in status["alerts"]], ["stall", "error"])

    def test_html_has_no_reward_score_or_timeline_page(self):
        _, _, html = self.surfaces("--blind")
        page = (html / "index.html").read_text()
        for leak in LEAKS:
            self.assertFalse(leaked(leak, page), leak)
        self.assertIn("<h1>flotilla-watch BLIND: jobs", page)
        self.assertIn("Rewards: hidden (blind)", page)
        self.assertNotIn("mean reward", page)
        self.assertNotIn("<a ", page)
        self.assertFalse((html / "trials").exists())
        self.assertIn("error p1:AgentTimeoutError; unscored: reason hidden (blind)", page)
        self.assertIn("<th>valid</th>", page)
        self.assertIn("STALL", page)

    def test_watcher_never_renders_a_page_across_passes(self):
        watcher = watch.Watcher(self.jobs, blind=True)
        board = dashboard.Dashboard(self.root / "html", names=watcher.names, refresh_s=30)
        for offset in (0, 30, 60):
            board.update(watcher.poll(now=T0 + offset))
        self.assertEqual(board.renders, 0)
        self.assertFalse(leaked("0.618", watch.render(watcher.poll(now=T0 + 90))))


if __name__ == "__main__":
    unittest.main()
