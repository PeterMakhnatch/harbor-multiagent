import contextlib
import io
import os
import re
import tempfile
import time
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

from flotilla_analysis import dashboard, watch
from tests import fixtures as fx
from tests.test_watch import live_job, running_trial, trajectory

T0 = float(int(time.time()))


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []
        self.refresh: list[str] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "a":
            self.hrefs.append(values["href"])
        if tag == "meta" and values.get("http-equiv") == "refresh":
            self.refresh.append(values["content"])


def parse(page: str) -> Links:
    links = Links()
    links.feed(page)
    return links


def steps(name: str) -> dict:
    return {
        "p1": [fx.step(1, 10, source="user", message="go"), fx.step(2, 20, keystrokes=[f"echo {name}\n"])],
        "p2": [fx.step(1, 10, source="user", message="go"), fx.step(2, 25, keystrokes=["true\n"])],
    }


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.jobs = self.root / "jobs"
        self.out = self.root / "html"
        self.job = live_job(self.jobs, "j1", total=4, at=T0)

    def tearDown(self):
        self.tmp.cleanup()

    def finished(self, name: str, **kwargs) -> Path:
        return fx.trial(self.job, name, trajectories=steps(name), **kwargs)

    def build(self, *, now: float = T0 + 10, refresh_s: float | None = 30.0):
        watcher = watch.Watcher(self.jobs)
        board = dashboard.Dashboard(self.out, names=watcher.names, refresh_s=refresh_s)
        status = watcher.poll(now=now)
        return watcher, board, status, board.update(status)


class Dashboard(Base):
    def test_page_links_resolve_and_page_is_replaced_atomically(self):
        self.finished("toy-000-talk__a")
        self.finished("toy-001-talk__b", instance=1, reward=0.0)
        trial = running_trial(self.job, "t-c__c", [("p1", "terminus-2"), ("p2", "oracle")], at=T0)
        trajectory(trial, "p1", at=T0 + 5)
        watcher, board, _, path = self.build()
        page = path.read_text()
        self.assertTrue(page.startswith("<!doctype html>"))
        links = parse(page)
        self.assertEqual(links.refresh, ["30"])
        self.assertEqual(len(links.hrefs), 2)
        for href in links.hrefs:
            target = self.out / unquote(href)
            self.assertTrue(target.is_file(), href)
            self.assertIn("<h2>Timeline", target.read_text())
        self.assertEqual(
            sorted(p.name for p in (self.out / "trials").iterdir()),
            ["j1__toy-000-talk__a.html", "j1__toy-001-talk__b.html"],
        )
        for text in ("t-c__c", "scripted (oracle)", "trials 4: done 2", "1.000", "0.000", "$0.0020"):
            self.assertIn(text, page)
        self.assertNotIn("<link", page)
        self.assertNotIn("src=", page)
        inode = path.stat().st_ino
        board.update(watcher.poll(now=T0 + 30))
        self.assertNotEqual(path.stat().st_ino, inode)  # a rename, never an in-place rewrite
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["index.html", "trials"])
        self.assertFalse([p for p in (self.out / "trials").iterdir() if not p.name.endswith(".html")])

    def test_each_trial_page_is_rendered_once(self):
        self.finished("toy-000-talk__a")
        first = self.finished("toy-001-talk__b", instance=1)
        watcher = watch.Watcher(self.jobs)
        board = dashboard.Dashboard(self.out, names=watcher.names, refresh_s=None)
        board.update(watcher.poll(now=T0 + 10))
        self.assertEqual(board.renders, 2)
        pages = {p: p.stat().st_mtime_ns for p in (self.out / "trials").iterdir()}
        for page in pages:
            os.utime(page, ns=(1, 1))
        board.update(watcher.poll(now=T0 + 20))
        board.update(watcher.poll(now=T0 + 30))
        self.assertEqual(board.renders, 2)
        self.assertEqual({p.stat().st_mtime_ns for p in pages}, {1})
        self.finished("toy-002-talk__c", instance=2)
        board.update(watcher.poll(now=T0 + 40))
        self.assertEqual(board.renders, 3)
        fx.trial(self.job, first.name, instance=1, reward=0.5, trajectories=steps(first.name))
        os.utime(first / "result.json", (T0 + 50, T0 + 50))
        board.update(watcher.poll(now=T0 + 50))
        self.assertEqual(board.renders, 4)  # its result.json changed: rendered again
        self.assertNotEqual((self.out / "trials" / "j1__toy-001-talk__b.html").stat().st_mtime_ns, 1)
        self.assertEqual((self.out / "trials" / "j1__toy-000-talk__a.html").stat().st_mtime_ns, 1)

    def test_render_failure_is_a_flag_and_the_watch_goes_on(self):
        self.finished("toy-000-talk__a")
        self.finished("toy-001-talk__bad", instance=1)
        real = dashboard.build_timeline

        def build(trial, gap_s):
            if trial.trial_name.endswith("bad"):
                raise RuntimeError("boom <b>")
            return real(trial, gap_s)

        with mock.patch.object(dashboard, "build_timeline", side_effect=build):
            watcher, board, status, path = self.build()
            board.update(watcher.poll(now=T0 + 20))
        page = path.read_text()
        self.assertIn("timeline failed: RuntimeError: boom &lt;b&gt;", page)
        self.assertEqual(parse(page).hrefs, ["trials/j1__toy-000-talk__a.html"])
        self.assertEqual(board.renders, 2)  # the failure is remembered, not retried every pass
        self.assertFalse((self.out / "trials" / "j1__toy-001-talk__bad.html").exists())

    def test_trial_without_timeline_is_flagged(self):
        fx.write_json(
            self.job / "other__x" / "result.json",
            {
                "trial_name": "other__x",
                "task_name": "other/task",
                "started_at": fx.iso(0),
                "finished_at": fx.iso(30),
                "verifier_result": {"rewards": {"reward": 1.0}},
                "exception_info": {"exception_type": "AgentTimeoutError"},
            },
        )
        _, _, _, path = self.build()
        page = path.read_text()
        self.assertIn("no timeline: not a Flotilla trial or its task is unresolved", page)
        self.assertIn("error AgentTimeoutError", page)
        self.assertEqual(parse(page).hrefs, [])

    def test_everything_is_escaped(self):
        job = live_job(self.jobs, 'j&<i>"', total=2, at=T0)
        evil = 'toy-000-talk__<script>alert("x")<&>'
        fx.trial(job, evil, trajectories=steps("<script>alert(2)<&>"))
        trial = running_trial(job, "t-<b>__live", [("p1", "terminus-2")], at=T0)
        trajectory(trial, "p1", at=T0)
        (trial / "trial.log").write_text(
            "Unknown Error in LLM interaction: litellm.RateLimitError: <script>alert(3)</script>\n"
        )
        _, _, status, path = self.build(now=T0 + 400)
        page = path.read_text()
        self.assertEqual(len(status["alerts"]), 2)
        self.assertNotIn("<script>alert", page)
        self.assertNotIn("<b>", page)
        self.assertNotIn("<i>", page)
        self.assertIn("&lt;script&gt;alert(3)&lt;/script&gt;", page)
        self.assertIn("t-&lt;b&gt;__live", page)
        shown = "j&amp;&lt;i&gt;&quot;/toy-000-talk__&lt;script&gt;alert(&quot;x&quot;)&lt;&amp;&gt;"
        self.assertIn(shown, page)
        self.assertEqual(len(re.findall(r"<script>", page)), 1)  # the sort script only
        (href,) = parse(page).hrefs
        target = self.out / unquote(href)
        self.assertTrue(target.is_file())
        self.assertNotIn("<script>alert", target.read_text())

    def test_alerts_keep_the_time_first_seen(self):
        trial = running_trial(self.job, "t-a__a", [("p1", "terminus-2")], at=T0)
        trajectory(trial, "p1", at=T0)
        watcher = watch.Watcher(self.jobs, stall_seconds=300)
        board = dashboard.Dashboard(self.out, names=watcher.names, refresh_s=5)
        board.update(watcher.poll(now=T0 + 400))
        status = watcher.poll(now=T0 + 500)
        path = board.update(status)
        (alert,) = status["alerts"]
        self.assertEqual(alert["first_seen"], T0 + 400)
        page = path.read_text()
        self.assertIn(dashboard._clock(T0 + 400), page)
        self.assertIn("STALL", page)
        self.assertEqual(parse(page).refresh, ["5"])


class Command(Base):
    def run_main(self, *args: str) -> tuple[int, str]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = watch.main([str(self.jobs), "--once", *args])
        return code, stdout.getvalue()

    def test_once_html_builds_the_dashboard_and_keeps_the_text(self):
        self.finished("toy-000-talk__a")
        code, text = self.run_main("--html", str(self.out))
        self.assertEqual(code, 0)
        plain_code, plain = self.run_main()
        self.assertEqual(plain_code, 0)
        self.assertEqual(text.splitlines()[1:], plain.splitlines()[1:])
        page = (self.out / "index.html").read_text()
        self.assertEqual(parse(page).refresh, [])  # a single pass: nothing to reload
        self.assertTrue((self.out / "trials" / "j1__toy-000-talk__a.html").is_file())

    def test_render_failure_does_not_stop_the_command(self):
        self.finished("toy-000-talk__a")
        with mock.patch.object(dashboard, "render_trial", side_effect=ValueError("bad")):
            code, _ = self.run_main("--html", str(self.out))
        self.assertEqual(code, 0)
        self.assertIn("timeline failed: ValueError: bad", (self.out / "index.html").read_text())


if __name__ == "__main__":
    unittest.main()
