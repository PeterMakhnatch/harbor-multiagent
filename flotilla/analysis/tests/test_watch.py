import contextlib
import hashlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from flotilla_analysis import watch
from tests import fixtures as fx

T0 = float(int(time.time()))
MODEL = "openrouter/x/model"


def touch(path: Path, at: float) -> None:
    os.utime(path, (at, at))


def running_trial(
    job: Path, name: str, seats: list[tuple[str, str]], *, at: float, task: Path | None = None
) -> Path:
    """A trial folder as Harbor leaves it during play: config.json and trial.log, no result.json."""
    directory = job / name
    fx.write_json(
        directory / "config.json",
        {
            "trial_name": name,
            "task": {"path": str(task) if task else None},
            "participants": [
                {"seat": seat, "name": agent, "model_name": None if agent == "oracle" else MODEL}
                for seat, agent in seats
            ],
        },
    )
    (directory / "trial.log").write_text("")
    touch(directory / "config.json", at)
    touch(directory / "trial.log", at)
    return directory


def trajectory(trial: Path, seat: str, *, at: float, steps: int = 2, ended: bool = False) -> Path:
    """A live trajectory.json with step costs; final_metrics has prompt tokens only once the run ended."""
    rows = [fx.step(1, 0, source="user", message="instruction")]
    for index in range(steps):
        row = fx.step(index + 2, 10.0 * (index + 1), keystrokes=["curl -s $WORLD_URL/state\n"])
        row["metrics"] = {"cost_usd": 0.001}
        rows.append(row)
    final = {"total_cached_tokens": 0}
    if ended:
        final |= {"total_prompt_tokens": 1000, "total_completion_tokens": 100}
    path = trial / "seats" / seat / "agent" / "trajectory.json"
    fx.write_json(path, {"schema_version": "ATIF-v1.6", "steps": rows, "final_metrics": final})
    touch(path, at)
    return path


def live_job(root: Path, name: str, *, total: int, at: float) -> Path:
    job = root / name
    fx.write_json(job / "config.json", {"job_name": name, "jobs_dir": str(root)})
    fx.write_json(job / "result.json", {"n_total_trials": total, "stats": {}, "finished_at": None})
    touch(job / "result.json", at)
    return job


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()


class Progress(Base):
    def make_run(self) -> Path:
        repo = self.root / "repo"
        jobs = repo / ".flotilla-local" / "jobs"
        run = repo / ".flotilla-local" / "runs" / "w1"
        plan = repo / "flotilla" / "plans" / "w1.json"
        fx.write_json(plan, {"name": "w1", "budget": {"cap_usd": 4.0}, "make_jobs": {"concurrency": 2}})
        keys = [json.dumps([0, i, "talk", MODEL]) for i in range(4)]
        fx.write_json(
            run / "state.json",
            {
                "plan": "w1",
                "plan_sha256": hashlib.sha256(plan.read_bytes()).hexdigest(),
                "order": [{"key": k} for k in keys],
                "spent_usd": 0.5,
                "finished": {
                    keys[0]: {
                        "valid": True,
                        "job": "w1-aaaaaa",
                        "attempts": [{"job": "w1-aaaaaa", "valid": True}],
                    },
                    keys[1]: {
                        "valid": False,
                        "job": "w1-bbbbbb",
                        "attempts": [{"job": "w1-bbbbbb", "valid": False}],
                    },
                },
            },
        )
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(T0 - 600))
        lines = [f"{stamp} ET order 4 cells", f"{stamp} ET start spacing 10.0 s"]
        for job in ("w1-aaaaaa", "w1-bbbbbb", "w1-cccccc", "w1-dddddd"):
            lines.append(f"{stamp} ET start {job} set 0 instance 1 talk {MODEL}")
        for job in ("w1-aaaaaa", "w1-bbbbbb"):
            lines.append(f"{stamp} ET end {job} set 0 instance 1 talk {MODEL} exit 0 valid True")
        (run / "progress.log").write_text("\n".join(lines) + "\n")
        fx.write_json(run / "configs" / "w1-aaaaaa.json", {"job_name": "w1-aaaaaa", "jobs_dir": str(jobs)})
        fx.trial(jobs / "w1-aaaaaa", "toy-000-talk__a", reward=1.0)
        fx.trial(
            jobs / "w1-bbbbbb",
            "toy-001-talk__b",
            instance=1,
            reward=None,
            seats=[fx.seat_result("p1", exception="AgentTimeoutError"), fx.seat_result("p2")],
        )
        trial = running_trial(
            jobs / "w1-cccccc", "t-ccc__x", [("p1", "terminus-2"), ("p2", "terminus-2")], at=T0 - 120
        )
        trajectory(trial, "p1", at=T0 - 30, steps=3)
        return run

    def test_run_folder_counts(self):
        run = self.make_run()
        status = watch.Watcher(run).poll(now=T0)
        wave = status["wave"]
        self.assertEqual(wave["state"], "running")
        got = {k: wave[k] for k in ("total", "done", "valid", "invalid", "running", "pending", "abandoned")}
        self.assertEqual(
            got, {"total": 4, "done": 2, "valid": 1, "invalid": 1, "running": 2, "pending": 0, "abandoned": 0}
        )
        self.assertEqual((wave["spent_usd"], wave["cap_usd"], wave["concurrency"]), (0.5, 4.0, 2))
        self.assertAlmostEqual(wave["in_flight_usd"], 0.003)
        self.assertEqual(wave["mean_trial_s"], 100.0)
        states = {r["job"]: r["state"] for r in status["running"]}
        self.assertEqual(states, {"w1-cccccc": "running", "w1-dddddd": "starting"})
        starting = next(r for r in status["running"] if r["job"] == "w1-dddddd")
        self.assertAlmostEqual(starting["elapsed_s"], 600.0, delta=1.0)
        self.assertEqual(wave["eta_s"], 0.0)  # nothing pending; both running cells are past the mean
        rows = {(r["label"], r["condition"]): r for r in status["rewards"]}
        self.assertEqual(rows[("toy", "talk")]["trials"], 2)
        self.assertEqual(rows[("toy", "talk")]["scored"], 1)
        self.assertEqual(rows[("toy", "talk")]["mean_reward"], 1.0)
        text = watch.render(status)
        self.assertIn("cells 4: done 2 (valid 1, invalid 1), running 2, pending 0", text)
        self.assertIn("of cap $4.0000 (12%)", text)

    def test_cap_flag_and_stop(self):
        run = self.make_run()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(T0))
        with (run / "progress.log").open("a") as handle:
            handle.write(f"{stamp} ET stop cap: spent $0.5000\n")
        status = watch.Watcher(run, cap_usd=0.75).poll(now=T0)
        wave = status["wave"]
        self.assertEqual(wave["state"], "stopped (cap)")
        self.assertEqual(wave["cap_usd"], 0.75)
        self.assertEqual((wave["running"], wave["abandoned"], wave["pending"]), (0, 1, 2))
        self.assertIsNone(wave["eta_s"])
        self.assertEqual(status["alerts"], [e for e in status["alerts"] if not e["live"]])

    def test_eta_from_mean_and_concurrency(self):
        wave = {"state": "running", "pending": 3, "concurrency": 2}
        live = [{"elapsed_s": 40.0}, {"elapsed_s": 150.0}]
        self.assertEqual(watch.eta(wave, live, 100.0), (3 * 100 + 60 + 0) / 2)
        self.assertIsNone(watch.eta(wave, live, None))
        self.assertEqual(watch.eta({**wave, "concurrency": None}, live, 100.0), 360 / 2)

    def test_progress_log_draining_stop_is_not_final(self):
        path = self.root / "progress.log"
        path.write_text(
            "2026-11-24 01:00:00 ET start w1-a set 0 instance 1 talk m\n"
            "2026-11-24 01:00:05 ET stop: openrouter balance read failed; draining in-flight jobs\n"
        )
        self.assertIsNone(watch.read_progress(path)["stopped"])
        with path.open("a") as handle:
            handle.write("2026-11-24 01:01:00 ET end w1-a set 0 instance 1 talk m exit 0\n")
            handle.write("2026-11-24 01:01:00 ET stop balance: spent $0.1000\n")
        progress = watch.read_progress(path)
        self.assertEqual((progress["stopped"], progress["ended"]), ("balance", {"w1-a"}))
        with path.open("a") as handle:
            handle.write("2026-11-24 02:00:00 ET resume 1 finished\n")
        self.assertIsNone(watch.read_progress(path)["stopped"])

    def test_jobs_folder_counts(self):
        jobs = self.root / "jobs"
        job = live_job(jobs, "j1", total=4, at=T0 - 60)
        fx.trial(job, "toy-000-talk__a", reward=0.5)
        trial = running_trial(job, "toy-001-talk__b", [("p1", "terminus-2")], at=T0 - 60)
        trajectory(trial, "p1", at=T0 - 10)
        dead = live_job(jobs, "j0", total=3, at=T0 - 4 * 3600)
        running_trial(dead, "toy-002-talk__c", [("p1", "terminus-2")], at=T0 - 4 * 3600)
        status = watch.Watcher(jobs).poll(now=T0)
        wave = status["wave"]
        got = {k: wave[k] for k in ("total", "done", "valid", "running", "pending", "abandoned")}
        self.assertEqual(got, {"total": 4, "done": 1, "valid": 1, "running": 1, "pending": 2, "abandoned": 1})
        self.assertEqual(wave["state"], "running")
        self.assertAlmostEqual(wave["spent_usd"], 0.002)
        self.assertEqual(status["alerts"], [])
        one = watch.Watcher(job).poll(now=T0)["wave"]
        self.assertEqual((one["done"], one["running"], one["pending"]), (1, 1, 2))


class Stalls(Base):
    def setUp(self):
        super().setUp()
        task = self.root / "task"
        task.mkdir()
        (task / "task.toml").write_text("[agents.p1]\n[agents.p4]\nunder_test = false\n")
        self.job = live_job(self.root / "jobs", "j1", total=1, at=T0)
        seats = [
            ("p1", "terminus-2"),
            ("p2", "terminus-2"),
            ("p3", "oracle"),
            ("p4", "terminus-2"),
            ("p5", "terminus-2"),
        ]
        self.trial = running_trial(self.job, "t-x__a", seats, at=T0, task=task)
        trajectory(self.trial, "p1", at=T0 + 100)
        trajectory(self.trial, "p2", at=T0 + 50, ended=True)
        trajectory(self.trial, "p4", at=T0 + 20)

    def seats(self, status):
        return {s["seat"]: s for s in status["running"][0]["seats"]}

    def test_stall_needs_quiet_seat_under_test_that_has_not_ended(self):
        watcher = watch.Watcher(self.root / "jobs", stall_seconds=300)
        seats = self.seats(watcher.poll(now=T0 + 250))
        self.assertEqual([s for s, row in seats.items() if row["stalled"]], [])
        self.assertEqual((seats["p1"]["steps"], seats["p1"]["age_s"]), (2, 150.0))
        seats = self.seats(watcher.poll(now=T0 + 401))
        self.assertTrue(seats["p1"]["stalled"])
        self.assertFalse(seats["p2"]["stalled"])  # ended: its run wrote final totals
        self.assertTrue(seats["p2"]["ended"])
        self.assertFalse(seats["p3"]["stalled"])  # Harbor's oracle agent: a scripted seat
        self.assertTrue(seats["p3"]["scripted"])
        self.assertTrue(seats["p4"]["scripted"])  # under_test = false in task.toml
        self.assertTrue(seats["p5"]["stalled"])  # no trajectory yet: counted from the trial start
        self.assertFalse(seats["p5"]["first_step"])
        self.assertEqual(seats["p5"]["age_s"], 401.0)

    def test_finished_trial_does_not_stall(self):
        fx.write_json(
            self.trial / "result.json",
            {
                "trial_name": "t-x__a",
                "task_name": "flotilla/toy-000-talk",
                "finished_at": fx.iso(100),
                "started_at": fx.iso(0),
            },
        )
        status = watch.Watcher(self.root / "jobs").poll(now=T0 + 10_000)
        self.assertEqual(status["running"], [])
        self.assertEqual(status["alerts"], [])

    def test_abandoned_trial_does_not_stall(self):
        status = watch.Watcher(self.root / "jobs").poll(now=T0 + watch.ABANDONED_SECONDS + 200)
        self.assertEqual(status["running"][0]["state"], "abandoned")
        self.assertEqual([s for s in status["running"][0]["seats"] if s["stalled"]], [])
        self.assertEqual(status["alerts"], [])

    def test_half_written_trajectory_keeps_last_good_read(self):
        watcher = watch.Watcher(self.root / "jobs")
        watcher.poll(now=T0 + 120)
        path = self.trial / "seats" / "p1" / "agent" / "trajectory.json"
        path.write_text('{"steps": [')
        touch(path, T0 + 130)
        seats = self.seats(watcher.poll(now=T0 + 140))
        self.assertEqual((seats["p1"]["steps"], seats["p1"]["age_s"]), (2, 10.0))


class Errors(Base):
    def test_error_kinds(self):
        llm = "Unknown Error in LLM interaction: litellm."
        cases = {
            f"{llm}RateLimitError: RateLimitError: OpenrouterException": "rate-limit",
            f"{llm}Timeout: APITimeoutError - Request timed out": "timeout",
            f"{llm}InternalServerError: OpenrouterException 502": "server",
            "Trial t-1__a failed: litellm.NotFoundError: NotFoundError: OpenrouterException": "NotFoundError",
            "Trial t-1__a failed: docker compose build failed": "error",
        }
        for line, kind in cases.items():
            self.assertEqual(watch.error_kind(line), kind, line)

    def test_log_scan_ignores_keystrokes_and_tracebacks(self):
        log = self.root / "trial.log"
        log.write_text(
            "Sending keys: ['echo litellm.RateLimitError Timeout\\n'] min_timeout_sec: 0.1\n"
            "Unknown Error in LLM interaction: litellm.RateLimitError: 429 Too Many Requests\n"
            '  File "x.py", line 1, in litellm.APIError\n'
            "Trajectory dumped to /x/seats/p1/agent/trajectory.json\n"
            "Unknown Error in LLM interaction: litellm.RateLimitError: 429 Too Many Requests\n"
        )
        found = watch.read_log_errors(log)
        self.assertEqual(list(found), ["rate-limit"])
        self.assertEqual(found["rate-limit"]["count"], 2)

    def test_running_and_finished_errors(self):
        jobs = self.root / "jobs"
        job = live_job(jobs, "j1", total=2, at=T0)
        trial = running_trial(job, "t-a__a", [("p1", "terminus-2")], at=T0)
        trajectory(trial, "p1", at=T0)
        (trial / "trial.log").write_text(
            "Unknown Error in LLM interaction: litellm.ServiceUnavailableError: 503\n"
        )
        touch(trial / "trial.log", T0)
        fx.trial(
            job, "toy-000-talk__b", reward=None, seats=[fx.seat_result("p1", exception="AgentTimeoutError")]
        )
        status = watch.Watcher(jobs).poll(now=T0 + 10)
        self.assertEqual(list(status["running"][0]["errors"]), ["server"])
        self.assertEqual(status["errors"][0]["exceptions"], ["p1:AgentTimeoutError"])
        self.assertEqual(
            sorted(a["key"] for a in status["alerts"]),
            ["error:j1/t-a__a:server", "exception:j1/toy-000-talk__b"],
        )
        self.assertIn("p1:AgentTimeoutError", watch.render(status))


class Alerts(Base):
    def test_each_alert_once(self):
        jobs = self.root / "jobs"
        job = live_job(jobs, "j1", total=3, at=T0)
        fx.trial(
            job, "toy-000-talk__old", reward=None, seats=[fx.seat_result("p1", exception="CancelledError")]
        )
        trial = running_trial(job, "t-a__a", [("p1", "terminus-2")], at=T0)
        path = trajectory(trial, "p1", at=T0)
        sent: list[tuple[str, str]] = []
        watcher = watch.Watcher(
            jobs, stall_seconds=300, notifier=lambda title, message: sent.append((title, message))
        )
        watcher.poll(now=T0 + 100)
        self.assertEqual(sent, [])  # the old trial's error is history on the first pass
        status = watcher.poll(now=T0 + 400)
        self.assertEqual(len(sent), 1)
        self.assertIn("stall", sent[0][0])
        self.assertEqual(len(status["new_alerts"]), 1)
        watcher.poll(now=T0 + 500)
        watcher.poll(now=T0 + 900)
        self.assertEqual(len(sent), 1)  # the same stall is not sent again
        trajectory(trial, "p1", at=T0 + 950, steps=3)
        watcher.poll(now=T0 + 1000)
        self.assertEqual(len(sent), 1)
        watcher.poll(now=T0 + 1300)
        self.assertEqual(len(sent), 2)  # it moved, then stalled again: a new event
        (trial / "trial.log").write_text(
            "Unknown Error in LLM interaction: litellm.RateLimitError: 429\n" * 3
        )
        watcher.poll(now=T0 + 1310)
        watcher.poll(now=T0 + 1320)
        self.assertEqual(len(sent), 3)
        self.assertIn("rate-limit", sent[2][0])
        fx.trial(
            job,
            "toy-001-talk__new",
            instance=1,
            reward=None,
            seats=[fx.seat_result("p1", exception="AgentTimeoutError")],
        )
        watcher.poll(now=T0 + 1330)
        watcher.poll(now=T0 + 1340)
        self.assertEqual(len(sent), 4)
        self.assertIn("toy-001-talk__new", sent[3][1])
        self.assertTrue(path.exists())

    def test_notifications_per_pass_are_capped(self):
        jobs = self.root / "jobs"
        job = live_job(jobs, "j1", total=8, at=T0)
        for index in range(8):
            running_trial(job, f"t-{index}__a", [("p1", "terminus-2")], at=T0)
        sent: list[tuple[str, str]] = []
        watch.Watcher(jobs, notifier=lambda title, message: sent.append((title, message))).poll(now=T0 + 400)
        self.assertEqual(len(sent), watch.NOTIFY_PER_PASS + 1)
        self.assertIn("3 more new alerts", sent[-1][1])


class Command(Base):
    def test_once_writes_json(self):
        jobs = self.root / "jobs"
        job = live_job(jobs, "j1", total=2, at=T0)
        fx.trial(job, "toy-000-solo__a", condition="solo", reward=1.0, seats=[fx.seat_result("p1")])
        trial = running_trial(job, "t-a__a", [("p1", "terminus-2")], at=time.time() - 30)
        trajectory(trial, "p1", at=time.time() - 5)
        out = self.root / "out" / "status.json"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = watch.main([str(jobs), "--once", "--json", str(out), "--stall-seconds", "60"])
        self.assertEqual(code, 0)
        data = json.loads(out.read_text())
        self.assertEqual(data["mode"], "jobs")
        self.assertEqual(data["stall_seconds"], 60)
        self.assertEqual((data["wave"]["done"], data["wave"]["running"]), (1, 1))
        self.assertEqual(data["running"][0]["seats"][0]["steps"], 2)
        self.assertEqual(data["rewards"][0]["condition"], "solo")
        self.assertEqual(data["alerts"], [])
        self.assertIn("trials 2: done 1", stdout.getvalue())
        self.assertEqual(list(out.parent.iterdir()), [out])

    def test_bad_target(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(watch.main([str(self.root / "absent"), "--once"]), 2)


if __name__ == "__main__":
    unittest.main()
