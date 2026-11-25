"""Rehearse a built seat task over HTTP, using trusted local commands instead of containers.

    uvx --quiet --python 3.12 --with pyyaml python flotilla/playtest.py --help

Before a session with a sandbox profile, check that a seat's shell works like an ordinary one (no task, no model):

    python3 flotilla/playtest.py --check-seat --sandbox-profile flotilla/playtest_seats/seat.sb
"""

from __future__ import annotations

import argparse
import contextlib
import graphlib
import json
import math
import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import parse_qs

import qa

SEAT_SCRIPTS = Path(__file__).resolve().parent / "playtest_seats"
SEAT_PATH = "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
HOST_PYTHONS = ("/opt/homebrew/bin/python3", "/usr/local/bin/python3",
                "/Library/Developer/CommandLineTools/usr/bin/python3")  # never /usr/bin/python3: a stub on macOS
PYTHON_PROBE = ("import os, sys; print(os.path.realpath(getattr(sys, '_base_executable', None) or sys.executable)); "
                "print(os.path.realpath(sys.base_prefix))")
CHECK_SHELLS = (("zsh -lc", ("/bin/zsh", "-lc")), ("zsh -c", ("/bin/zsh", "-c")), ("bash -lc", ("/bin/bash", "-lc")))
PREFACE = ("You are working in a sandbox directory.\n"
           "Work only inside it; use shell commands.\n"
           "Stop when you are done.\n")
PLACEHOLDER = re.compile(r"\{(instruction|seat|workdir|log)\}")
PATH_MENTION = re.compile(
    r"(?<![\w:/])(?:\"((?:/|~/|\.\.?/)[^\"\r\n]+)\"|'((?:/|~/|\.\.?/)[^'\r\n]+)'"
    r"|((?:/|~/|\.\.?/)[^\s\"'`<>|;,)\]}]+)|(~|\.\.)(?=$|[\s\"';,)]))"
)
ROOT_ACCESS = re.compile(r"\b(?:find|ls|cd|du|tree|rg|grep)\b[^;&|\n]*?(?<=\s)[\"']?/[\"']?(?=$|[\s;&|)])")
IGNORED_PATHS = frozenset({"/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr", "/dev/tty",
                           "/bin/bash", "/bin/sh", "/bin/zsh"})
WORLD_BOOT = """\
import sys
import json
import threading
import time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import world
requests = Path(sys.argv[3])
requests.touch()
responses = requests.with_name("responses.jsonl")
responses.touch()
request_lock = threading.Lock()
request_seq = 0
origin = time.monotonic()
Path(sys.argv[2]).with_name("clock").write_text(str(origin))

class RecordedWorld(world.World):
    request_id = None

    def _handle(self, method):
        global request_seq
        self.request_id = None
        self.wait_expired = False
        self.arrived = time.monotonic()
        seat = self._caller()
        if seat in world.SEATS:
            with request_lock:
                request_seq += 1
                self.request_id = request_seq
                with requests.open("a") as log:
                    log.write(json.dumps({"seq": request_seq, "seat": seat, "method": method,
                                          "path": self.path.split("?", 1)[0],
                                          "query": self.path.partition("?")[2],
                                          "t": round(time.monotonic() - origin, 3)}) + "\\n")
        return super()._handle(method)

    def _reply(self, status, body, *args, **kwargs):
        path = self.path.split("?", 1)[0]
        self.wait_expired = (status == 200 and isinstance(body, dict)
                             and ((path == "/await" and body.get("reason") == "timeout")
                                  or (path == "/wait" and time.monotonic() - self.arrived >= world.WAIT_SECONDS)))
        return super()._reply(status, body, *args, **kwargs)

    def send_response(self, code, message=None):
        if self.request_id is not None:
            with request_lock, responses.open("a") as log:
                log.write(json.dumps({"seq": self.request_id, "status": code,
                                      "wait_expired": self.wait_expired,
                                      "t": round(time.monotonic() - origin, 3)}) + "\\n")
        return super().send_response(code, message)

world.World = RecordedWorld
server = world.serve('127.0.0.1', 0)
Path(sys.argv[2]).with_name("routes").write_text(json.dumps(sorted(world.ROUTES)))
Path(sys.argv[2]).write_text(str(server.server_address[1]))
server.serve_forever()
"""
VERIFY = """\
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import score
score.WORLD_DIR = Path(sys.argv[1])
score.WORLD = sys.argv[2]
score.VERIFIER = Path(sys.argv[3])
score.main()
"""


@dataclass
class SeatRun:
    seat: str
    mode: str
    root: Path
    output: Path
    workdir: Path
    env: dict[str, str]
    command: str
    argv: list[str]
    after: tuple[str, ...]
    mapping: dict[str, str]
    harness: str
    model: str | None
    process: subprocess.Popen | None = None
    started: float | None = None
    ended: float | None = None
    exit_code: int | None = None
    timed_out: bool = False
    stopped_after_freeze: bool = False
    error: str | None = None

    def start(self, now: float) -> None:
        self.started = now
        try:
            with (self.output / "seat.log").open("ab") as log:
                self.process = subprocess.Popen(self.argv, cwd=self.workdir, env=self.env, stdin=subprocess.DEVNULL,
                                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as exc:
            self.error = str(exc)
            self.ended = now

    def finish(self, now: float, timeout: bool = False) -> None:
        if self.process is not None:
            stop_process(self.process)
            self.exit_code = self.process.returncode
            self.process = None
        self.ended, self.timed_out = now, timeout

    def result(self, requests: list[dict]) -> dict[str, Any]:
        first_read = next((r["seq"] for r in requests if r["method"] == "GET"
                           and r["path"] not in ("/whoami", "/await", "/health")), None)
        first_submit = next((r["seq"] for r in requests if r["method"] == "POST"
                             and r["path"] in ("/submit", "/commit")), None)
        return {"mode": self.mode, "command": self.command, "exit_code": self.exit_code,
                "harness": self.harness, "model": self.model,
                "seconds": None if self.started is None else self.ended - self.started,
                "started_sec": self.started, "ended_sec": self.ended, "timed_out": self.timed_out,
                "stopped_after_freeze": self.stopped_after_freeze,
                "error": self.error, "start_after": list(self.after), "workdir": str(self.workdir),
                "http_4xx": sum(400 <= (r.get("status") or 0) < 500 for r in requests),
                "mapping": self.mapping, "world_calls": len(requests),
                "first_read": first_read, "first_submit": first_submit,
                "read_before_submit": None if first_read is None or first_submit is None else first_read < first_submit}


def stop_process(process: subprocess.Popen) -> None:
    """Reap the leader and stop ordinary descendants in its process group."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def host_env() -> dict[str, str]:
    return {name: value for name, value in os.environ.items()
            if not name.startswith("FLOTILLA_") and name not in ("SEAT", "SEAT_TOKEN", "SEAT_BRIEFING", "WORLD_URL")}


class SeatPython(NamedTuple):
    """A real interpreter on the host and its install folder, which the sandbox profile lets a seat read."""
    executable: Path
    install: Path


def real_python(executable: str, install: str) -> SeatPython | None:
    """Refuse a shim under ~/.local/bin, and an install folder that would open the user's home to the seat."""
    home = Path.home().resolve()
    found = SeatPython(Path(os.path.realpath(executable)), Path(os.path.realpath(install)))
    if (not found.executable.is_file() or not os.access(found.executable, os.X_OK)
            or found.executable.is_relative_to(home / ".local" / "bin")
            or not found.install.is_dir() or home.is_relative_to(found.install)):
        return None
    return found


def host_pythons() -> Iterator[SeatPython]:
    """Real interpreters, best first: the one that runs this tool (its base install, not a venv), then host installs."""
    own = real_python(getattr(sys, "_base_executable", None) or sys.executable, sys.base_prefix)
    if own is not None:
        yield own
    for candidate in HOST_PYTHONS:
        if not Path(candidate).is_file():
            continue
        try:
            probe = subprocess.run([candidate, "-I", "-c", PYTHON_PROBE], stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, timeout=20, check=True)
        except (OSError, subprocess.SubprocessError):
            continue
        lines = probe.stdout.splitlines()
        if len(lines) == 2 and (found := real_python(*lines)) is not None:
            yield found


def seat_shell(root: Path, python: SeatPython | None) -> dict[str, str]:
    """Make the seat's shell an ordinary one: a private bin that stays first on PATH, and a private temp folder.

    A macOS login shell runs path_helper, which moves unknown PATH entries to the end. zsh reads `.zprofile` after
    it and bash reads $BASH_ENV, so both put the seat's bin back in front. zsh writes here-documents to TMPPREFIX.
    """
    bin_dir, tmp, zdot = root / "bin", root / "tmp", root / "zdot"
    for folder in (bin_dir, tmp, zdot):
        folder.mkdir(mode=0o700)
    if python is not None:
        (bin_dir / "python3").symlink_to(python.executable)
    first = shlex.quote(str(bin_dir))
    keep_first = f'case "${{PATH-}}" in {first}:*) ;; *) PATH={first}"${{PATH:+:$PATH}}"; export PATH ;; esac\n'
    for name in (".zshenv", ".zprofile"):
        (zdot / name).write_text(keep_first)
    return {"PATH": f"{bin_dir}:{SEAT_PATH}", "TMPDIR": str(tmp), "TMPPREFIX": str(tmp / "zsh"),
            "ZDOTDIR": str(zdot), "BASH_ENV": str(zdot / ".zshenv")}


def seat_env(task: qa.Task, seat: str, root: Path, pass_env: tuple[str, ...],
             python: SeatPython | None) -> dict[str, str]:
    names = ("HOME", "USER", *pass_env)
    return ({name: os.environ[name] for name in names if name in os.environ}
            | {"HOME": str(Path.home())} | task.env(seat) | seat_shell(root, python))


def sandbox_command(argv: list[str], profile: Path, root: Path, scripts: Path = SEAT_SCRIPTS,
                    python: SeatPython | None = None) -> list[str]:
    return ["/usr/bin/sandbox-exec", "-f", str(profile), "-D", f"SEATROOT={root}",
            "-D", f"HOSTHOME={Path.home().resolve()}", "-D", f"HOSTTMP={Path(tempfile.gettempdir()).resolve()}",
            "-D", f"SEATSCRIPTS={scripts.resolve()}",
            "-D", f"SEATPYTHON={python.install if python is not None else root}", *argv]


def sandbox_profile(path: Path | None) -> Path | None:
    if path is None:
        return None
    if sys.platform != "darwin":
        print("playtest: --sandbox-profile skipped: sandbox-exec is only available on macOS", file=sys.stderr)
        return None
    if not path.is_file():
        raise ValueError(f"sandbox profile not found: {path}")
    return path.resolve()


def runs_in_sandbox(python: SeatPython, profile: Path, scripts: Path) -> bool:
    with tempfile.TemporaryDirectory(prefix=".playtest-seat-", dir=Path.home()) as folder:
        root = Path(folder).resolve()
        env = {"HOME": str(Path.home())} | seat_shell(root, python)
        argv = sandbox_command([str(root / "bin" / "python3"), "-c", "pass"], profile, root, scripts, python)
        try:
            return subprocess.run(argv, cwd=root, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                  timeout=30, check=False).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False


def find_python(profile: Path | None, scripts: Path) -> SeatPython | None:
    """The first real interpreter that also runs under the seats' sandbox profile; say so when there is none."""
    for python in host_pythons():
        if profile is None or runs_in_sandbox(python, profile, scripts):
            return python
    print("playtest: no real python3 on this host runs in a seat; seats get no python3 of their own", file=sys.stderr)
    return None


def dependencies(agents: dict) -> dict[str, tuple[str, ...]]:
    result = {}
    for seat, spec in agents.items():
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", seat):
            raise ValueError(f"invalid seat name: {seat}")
        after = spec.get("start_after", [])
        if not isinstance(after, list) or any(not isinstance(s, str) or s not in agents for s in after):
            raise ValueError(f"invalid start_after for {seat}")
        result[seat] = tuple(after)
    tuple(graphlib.TopologicalSorter(result).static_order())
    return result


def copy_seat_files(task: qa.Task, seat: str, workdir: Path) -> None:
    build = task.build(seat)
    if build is None:
        raise ValueError(f"no local player build for {seat}")
    if task.services[seat].get("volumes"):
        raise ValueError(f"host playtest does not support seat volumes: {seat}")
    for source in build.copied:
        target = workdir / source.name
        if not source.is_relative_to(task.env_dir) or target.exists() or not source.is_file():
            raise ValueError(f"cannot copy player file {source} for {seat}")
        shutil.copyfile(source, target)


def policy_command(task: qa.Task, seat: str, mode: str, root: Path) -> list[str]:
    solution = root / "solution"
    shutil.copytree(task.path / "solution", solution)
    if (solution / "policy.json").is_file():
        settings = json.loads((solution / "policy.json").read_text())
        if mode == "careful":
            settings["seats"][seat] = "careful"
        (solution / "policy.json").write_text(json.dumps(settings))
        return [sys.executable, "-B", str(solution / "policy.py")]
    return [sys.executable, "-B", str(solution / "oracle.py")]


def host_mapping(task: qa.Task, seat: str, workdir: Path, url: str) -> dict[str, str]:
    return {"container_workdir": task.services[seat]["working_dir"], "host_workdir": str(workdir),
            "container_world_url": task.env(seat)["WORLD_URL"], "host_world_url": url,
            "workdir_env": "PWD", "world_url_env": "WORLD_URL"}


def host_preface(mapping: dict[str, str]) -> bytes:
    return (PREFACE
            + "You are on the host, not in a container. In the instructions below:\n"
            + f"- {mapping['container_workdir']} maps to {mapping['host_workdir']} (initial $PWD).\n"
            + f"- {mapping['container_world_url']} maps to {mapping['host_world_url']} ($WORLD_URL).\n"
            + "Use $SEAT, $SEAT_TOKEN and $SEAT_BRIEFING as stated in the instructions.\n\n").encode()


def prepare_seat(task: qa.Task, seat: str, mode: str, root: Path, template: str,
                 output: Path, after: tuple[str, ...], token: str, url: str,
                 pass_env: tuple[str, ...], profile: Path | None, scripts: Path, model: str | None,
                 python: SeatPython | None) -> SeatRun:
    workdir = root / "work"
    workdir.mkdir()
    copy_seat_files(task, seat, workdir)
    prompt = root / "prompt.md"
    mapping = host_mapping(task, seat, workdir, url)
    prompt.write_bytes(host_preface(mapping) + (task.path / "instructions" / f"{seat}.md").read_bytes())
    output.mkdir(parents=True)
    shutil.copyfile(prompt, output / "prompt.md")
    (output / "seat.log").touch()
    native_log = root / "command.log"
    native_log.touch()
    env = (seat_env(task, seat, root, pass_env, python)
           | {"SEAT_TOKEN": token, "PWD": str(workdir), "WORLD_URL": url})
    if mode == "external":
        values = {"instruction": str(prompt), "seat": seat, "workdir": str(workdir), "log": str(native_log)}
        command = PLACEHOLDER.sub(lambda m: shlex.quote(values[m[1]]), template)
        harness = harness_name(template)
        argv = ["/usr/bin/env", "/bin/bash", "-c", command]
        if profile is not None:
            argv = sandbox_command(argv, profile, root, scripts, python)
            env["PLAYTEST_SANDBOX"] = str(profile)
    else:
        argv = policy_command(task, seat, mode, root)
        command = shlex.join(argv)
        harness = Path(argv[-1]).name
    return SeatRun(seat, mode, root, output, workdir, env, command, argv, after, mapping, harness, model)


@contextlib.contextmanager
def world_server(task: qa.Task, out: Path, root: Path, tokens: dict[str, str], timeout: float):
    logs = out / "artifacts" / "logs" / "world"
    env = host_env() | task.env("main") | {
        "FLOTILLA_TOKENS": ",".join(f"{seat}:{token}" for seat, token in tokens.items()),
        "FLOTILLA_ADMIN": secrets.token_hex(16), "FLOTILLA_TASK_JSON": str(task.env_dir / "task.json"),
        "FLOTILLA_LOGS": str(logs), "FLOTILLA_PLAY_SECONDS": str(timeout),
    }
    for name in ("FLOTILLA_GAME", "FLOTILLA_GAME_PARAMS"):
        if env.get(name, "").startswith("/world/"):
            env[name] = str(task.env_dir / env[name].removeprefix("/world/"))
    port_file = root / "port"
    with (out / "world.log").open("wb") as log:
        process = subprocess.Popen([sys.executable, "-B", "-c", WORLD_BOOT, str(task.env_dir), str(port_file),
                                    str(out / "requests.jsonl")],
                                   cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and process.poll() is None:
                if port_file.exists() and (port := port_file.read_text()).isdigit():
                    break
                time.sleep(0.02)
            else:
                raise RuntimeError(f"world did not start; see {out / 'world.log'}")
            yield f"http://127.0.0.1:{port}", env
        finally:
            stop_process(process)


def read_delta(stream) -> list[dict]:
    rows = []
    while True:
        offset = stream.tell()
        line = stream.readline()
        if not line.endswith("\n"):
            stream.seek(offset)
            return rows
        rows.append(json.loads(line))


def is_wait(request: dict) -> bool:
    if request["method"] != "GET":
        return False
    query = parse_qs(request["query"])
    if request["path"] == "/await":
        until, after = query.get("until", ["active"]), query.get("after", ["0"])
        return (len(until) == 1 and until[0] in ("active", "message", "tick")
                and len(after) == 1 and after[0].isdigit() and len(after[0]) <= 12)
    return request["path"] == "/wait" and query.get("round", [""])[0].isdigit()


class WaitWatch:
    def __init__(self, requests, responses, events, clock: float):
        self.requests, self.responses, self.events = requests, responses, events
        self.clock = clock
        self.latest = {}
        self.finished = {}
        self.since = {}
        self.progress = time.monotonic()
        self.frozen_at = None

    def update(self) -> None:
        requests = read_delta(self.requests)
        for response in read_delta(self.responses):
            self.finished[response["seq"]] = response
        for request in requests:
            seat = request["seat"]
            previous = self.latest.get(seat)
            finish = self.finished.get(previous["seq"]) if previous else None
            continuous = (previous is not None and is_wait(previous)
                          and (finish is None or (finish["wait_expired"] and request["t"] - finish["t"] <= 1)))
            if not continuous or not is_wait(request):
                self.since[seat] = self.clock + request["t"]
            self.latest[seat] = request
        for event in read_delta(self.events):
            if event["kind"] == "freeze" and self.frozen_at is None:
                self.frozen_at = time.monotonic()
            if event["kind"] not in {"await", "wait", "whoami", "health", "state", "bad_token", "invalid_http",
                                      "invalid", "denied", "probe", "late", "inactive", "look", "repeat"}:
                self.progress = time.monotonic()

    def stalled(self, running: list[SeatRun], ended_at: float, stall_sec: float) -> bool:
        now = time.monotonic()
        if not running or now - max(ended_at, self.progress) < stall_sec:
            return False
        for seat in running:
            request = self.latest.get(seat.seat)
            if request is None or not is_wait(request) or now - self.since[seat.seat] < stall_sec:
                return False
            finish = self.finished.get(request["seq"])
            if finish is not None and (not finish["wait_expired"] or now - self.clock - finish["t"] > 1):
                return False
        return True


def run_seats(seats: list[SeatRun], timeout: float, max_parallel: int, watch: WaitWatch,
              seat_timeout: float | None, stall_sec: float, grace_sec: float) -> tuple[bool, str | None]:
    origin = time.monotonic()
    pending = list(seats)
    running: list[SeatRun] = []
    ended: set[str] = set()
    last_exit = None
    ended_at = origin
    skipped_after_freeze = False
    try:
        while pending or running:
            now = time.monotonic() - origin
            for seat in running[:]:
                expired = seat_timeout is not None and now - seat.started >= seat_timeout
                if seat.process.poll() is not None or expired:
                    seat.finish(now, timeout=expired and seat.process.poll() is None)
                    running.remove(seat)
                    ended.add(seat.seat)
                    last_exit, ended_at = seat.seat, time.monotonic()
            if not pending and not running:
                return False, "world frozen before seats started" if skipped_after_freeze else None
            if now >= timeout:
                for seat in running + pending:
                    seat.finish(now, timeout=True)
                return True, "total timeout"
            watch.update()
            if watch.frozen_at is not None:
                for seat in pending:
                    seat.stopped_after_freeze = True
                    seat.finish(now)
                skipped_after_freeze |= bool(pending)
                pending.clear()
                if not running:
                    return False, "world frozen before seats started" if skipped_after_freeze else None
                if time.monotonic() - watch.frozen_at >= grace_sec:
                    for seat in running:
                        seat.stopped_after_freeze = seat.process.poll() is None
                        seat.finish(now)
                    return False, "world frozen; grace expired"
                time.sleep(min(0.02, max(0, timeout - now)))
                continue
            if last_exit is not None and watch.stalled(running, ended_at, stall_sec):
                reason = f"seat {last_exit} exited; others wait"
                for seat in running + pending:
                    seat.error = reason
                    seat.finish(now)
                return False, reason
            external = sum(seat.mode == "external" for seat in running)
            for seat in pending[:]:
                if time.monotonic() - origin >= timeout:
                    break
                if not ended.issuperset(seat.after) or (seat.mode == "external" and external >= max_parallel):
                    continue
                pending.remove(seat)
                seat.start(time.monotonic() - origin)
                if seat.process is None:
                    ended.add(seat.seat)
                    last_exit, ended_at = seat.seat, time.monotonic()
                else:
                    running.append(seat)
                    external += seat.mode == "external"
            time.sleep(min(0.02, max(0, timeout - (time.monotonic() - origin))))
        return False, None
    finally:
        for seat in running:
            if seat.process is not None:
                seat.finish(time.monotonic() - origin)


def verify(task: qa.Task, out: Path, url: str, env: dict[str, str], timeout: float) -> tuple[float | None, dict]:
    verifier = out / "verifier"
    with (out / "verifier.log").open("wb") as log:
        process = subprocess.Popen([sys.executable, "-B", "-c", VERIFY, str(task.env_dir), url, str(verifier)],
                                   cwd=out, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        failure = None
        try:
            if process.wait(timeout=timeout):
                failure = f"verifier failed; see {out / 'verifier.log'}"
        except subprocess.TimeoutExpired:
            failure = f"verifier timed out after {timeout:g} s"
        finally:
            stop_process(process)
    try:
        score = json.loads((verifier / "score.json").read_text())
        reason = score.get("invalid") or score.get("error") or failure
        if reason:
            return None, score | {"error": reason}
        return json.loads((verifier / "reward.json").read_text())["reward"], score
    except (OSError, ValueError, KeyError) as exc:
        return None, {"error": failure or f"verifier output unavailable: {exc}"}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def read_requests(out: Path) -> list[dict]:
    statuses = {r["seq"]: r["status"] for r in read_jsonl(out / "responses.jsonl")}
    path = out / "requests.jsonl"
    requests = [r | {"status": statuses.get(r["seq"])} for r in read_jsonl(path)]
    path.write_text("".join(json.dumps(r) + "\n" for r in requests))
    (out / "responses.jsonl").unlink()
    return requests


def world_causes(out: Path) -> dict:
    events = read_jsonl(out / "artifacts" / "logs" / "world" / "events.jsonl")
    freeze = next((e for e in events if e["kind"] == "freeze"), None)
    return {"freeze_reason": (freeze["data"].get("reason", "admin freeze") if freeze else None),
            "budget_events": [e for e in events if e["kind"] == "call_budget"]}


def outside(raw: str, workdir: Path, root: Path) -> str | None:
    """The absolute path when it lies outside the seat root; a relative path starts at the initial cwd."""
    path = Path(os.path.abspath(workdir / Path(raw).expanduser()))
    return None if path.is_relative_to(root) else str(path)


def outside_paths(command: str, workdir: Path, root: Path, routes: frozenset[str] = frozenset()) -> list[str]:
    """Conservative scan of one shell command, not an access trace. `routes` are URL paths of the world, not files."""
    paths = set()
    if ROOT_ACCESS.search(command):
        paths.add("/")
    for match in PATH_MENTION.finditer(command):
        raw = next(value for value in match.groups() if value is not None).rstrip(":")
        if raw.startswith("//") or raw in IGNORED_PATHS or raw.partition("?")[0].rstrip("/") in routes:
            continue
        if (path := outside(raw, workdir, root)) is not None:
            paths.add(path)
    return sorted(paths)


def tool_calls(text: str) -> tuple[list[str], list[str]]:
    """Shell commands and file-tool paths in Codex `--json` and Claude `stream-json` records, each call once."""
    commands, paths = [], []
    seen = set()
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        items = []
        if event.get("type") in ("item.started", "item.completed"):
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "command_execution":
                items.append((item.get("id"), "command", item.get("command")))
        elif event.get("type") == "assistant":
            message = event.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            for item in content if isinstance(content, list) else []:
                inputs = item.get("input") if isinstance(item, dict) and item.get("type") == "tool_use" else None
                if not isinstance(inputs, dict):
                    continue
                fields = ("command",) if item.get("name") == "Bash" else ("file_path", "path", "notebook_path")
                items += [(item.get("id"), field, inputs.get(field)) for field in fields]
        for item_id, field, value in items:
            if not isinstance(value, str) or (isinstance(item_id, str) and (item_id, field) in seen):
                continue
            seen.add((item_id, field))
            (commands if field == "command" else paths).append(value)
    return commands, paths


def seat_log(output: Path) -> str:
    return "\n".join((output / name).read_text(errors="replace") for name in ("seat.log", "command.log"))


def seat_commands(output: Path) -> list[str]:
    return tool_calls(seat_log(output))[0]


def audit_seat(output: Path, workdir: Path, root: Path, routes: frozenset[str]) -> list[str] | None:
    """Outside paths in the parsed tool calls only, never in free log text; None when the log has no such record."""
    commands, file_paths = tool_calls(seat_log(output))
    if not commands and not file_paths:
        return None
    found = {path for command in commands for path in outside_paths(command, workdir, root, routes)}
    return sorted(found | {path for raw in file_paths if (path := outside(raw, workdir, root)) is not None})


def archive_seats(seats: list[SeatRun]) -> None:
    for seat in seats:
        shutil.copyfile(seat.root / "command.log", seat.output / "command.log")


def timeline(out: Path, result: dict, audit: dict[str, list[str] | None], requests: list[dict]) -> None:
    world = out / "artifacts" / "logs" / "world"
    events = read_jsonl(world / "events.jsonl")
    messages = {m["id"]: m for m in read_jsonl(world / "messages.jsonl")}
    state_file = out / "verifier" / "world-state.json"
    state = json.loads(state_file.read_text()) if state_file.is_file() else {}
    submissions = iter(state.get("submissions", []))
    by_seat = {seat: [] for seat in result["seats"]}
    clock = "tick" if result["score"].get("mode") == "extension" else "t (seconds)"
    for event in events:
        kind, data = event["kind"], event["data"]
        seat = event["seat"]
        if kind == "submit" and (submission := next(submissions, None)) is not None:
            data = data | {"answer": submission["answer"]}
        if seat not in by_seat:
            continue
        if kind == "deliver":
            data = data | {"messages": [messages.get(mid, {"id": mid}) for mid in data.get("ids", [])]}
        elif kind == "send" and data.get("id") in messages:
            data = data | {"text": messages[data["id"]]["text"]}
            if result["seats"][seat]["mode"] == "scripted":
                kind = "scripted send"
        by_seat[seat].append(f"- #{event['seq']} {clock}={event['t']} {kind}: `{json.dumps(data, ensure_ascii=True)}`")
    lines = ["# Local playtest", "", f"Reward: {result['reward']}; timed out: {result['timed_out']}",
             f"Valid: {result['valid']}; invalid reason: {result['invalid_reason']}",
             f"Stop reason: {result['stop_reason']}",
             f"Freeze reason: {result['freeze_reason']}; budget events: {json.dumps(result['budget_events'])}",
             (f"Model: {result['model']}; harness: {result['harness']}; "
              f"sandbox profile: {result['sandbox_profile'] or 'none (host processes are not isolated)'}"),
             f"Seat python3: {result['python3'] or 'none found on this host'}",
             f"Seat failures: {json.dumps(result['seat_failures']) if result['seat_failures'] else 'none'}",
             "This is a rehearsal, not benchmark evidence.", ""]
    by_request = {seat: [] for seat in result["seats"]}
    for request in requests:
        target = request["path"] + (f"?{request['query']}" if request["query"] else "")
        by_request[request["seat"]].append(
            f"- #{request['seq']} t={request['t']:.3f}s {request['method']} {target} "
            f"-> {request['status'] if request['status'] is not None else 'no response'}")
    for seat, evidence in by_seat.items():
        info = result["seats"][seat]
        paths = (["- Not audited: the log has no recognized tool records."] if audit[seat] is None
                 else [f"- `{path}`" for path in audit[seat]] or ["- No outside paths found."])
        commands = seat_commands(out / "seats" / seat)
        started, ended = (f"{info[key]:.3f}" if info[key] is not None else "not started"
                          for key in ("started_sec", "ended_sec"))
        lines += [f"## {seat} ({info['mode']})", "",
                  f"Model: {info['model']}; harness: {info['harness']}",
                  (f"Started: {started}; ended: {ended}; exit: {info['exit_code']}; "
                   f"world calls: {info['world_calls']}; 4xx: {info['http_4xx']}; "
                   f"stopped after freeze: {info['stopped_after_freeze']}"), "",
                  *(evidence or ["- No seat events."]), "", "### World requests (arrival order)", "",
                  *(by_request[seat] or ["- No world calls."]), "", "### Shell commands (CLI order)", "",
                  *[f"- `{command}`" for command in commands],
                  *([] if commands else ["- No recognized shell command records."]), "", "### Outside-path audit", "",
                  "Candidate outside paths in the parsed shell commands and file-tool calls (possible reads or",
                  "listings, not an access trace). URL paths of the world are not listed.",
                  *paths, ""]
    (out / "summary.md").write_text("\n".join(lines))


def harness_name(template: str) -> str:
    args = shlex.split(template)
    for arg in args:
        if Path(arg).suffix in (".sh", ".py", ".zsh", ".bash"):
            return Path(arg).name
    return Path(args[0]).name


def seat_values(values: str | list[str] | None, seats: set[str], name: str, required: bool = False) -> dict:
    default = None
    overrides = {}
    for value in [values] if isinstance(values, str) else values or []:
        prefix, equals, rest = value.partition("=")
        if equals and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", prefix):
            if prefix not in seats:
                raise ValueError(f"{name}: unknown or non-external seat {prefix}")
            if prefix in overrides:
                raise ValueError(f"{name}: duplicate override for {prefix}")
            overrides[prefix] = rest
        else:
            if default is not None:
                raise ValueError(f"{name}: duplicate default")
            default = value
    resolved = {seat: overrides.get(seat, default) for seat in sorted(seats)}
    for seat, value in resolved.items():
        if (required and value is None) or (value is not None and not value.strip()):
            raise ValueError(f"{name}: missing value for {seat}")
    return resolved


def common_or_by_seat(values: dict) -> str | dict | None:
    distinct = set(values.values())
    return next(iter(distinct)) if len(distinct) == 1 else values if distinct else None


def seat_failures(info: dict) -> list[str]:
    failures = []
    if info["timed_out"]:
        failures.append("timed out")
    if info["error"]:
        failures.append(info["error"])
    if info["exit_code"] != 0 and not (info["stopped_after_freeze"] and info["started_sec"] is not None):
        failures.append(f"exit code {info['exit_code']}")
    if info["mode"] == "external" and info["world_calls"] == 0:
        failures.append("no world calls")
    return failures


def playtest(task_dir: Path, templates: str | list[str], selected: set[str] | None, timeout: float | None,
             max_parallel: int, out: Path, *, pass_env: tuple[str, ...] = (),
             profile: Path | None = None, model_label: str | list[str] | None = None,
             seat_timeout: float | None = None, stall_sec: float = 120, grace_sec: float = 20,
             scripts: Path = SEAT_SCRIPTS) -> dict:
    if qa.yaml is None:
        raise ValueError("PyYAML is required; use uvx --quiet --python 3.12 --with pyyaml python flotilla/playtest.py")
    task = qa.load_task(task_dir.resolve())
    if task.errors:
        raise ValueError("; ".join(f.detail for f in task.errors))
    config = tomllib.loads((task.path / "task.toml").read_text())
    after = dependencies(config["agents"])
    subjects = set(task.under_test)
    selected = subjects if selected is None else selected
    if selected - subjects:
        raise ValueError(f"unknown or scripted external seat(s): {sorted(selected - subjects)}")
    commands = seat_values(templates, selected, "--seat-cmd", required=True)
    models = seat_values(model_label, selected, "--model-label")
    timeout = float(config.get("agent", {}).get("timeout_sec", 900)) if timeout is None else timeout
    if not math.isfinite(timeout) or timeout <= 0 or max_parallel <= 0:
        raise ValueError("timeout and max-parallel must be positive")
    if any(not math.isfinite(value) or value <= 0 for value in (seat_timeout, stall_sec) if value is not None):
        raise ValueError("seat-timeout and stall-sec must be positive")
    if not math.isfinite(grace_sec) or grace_sec < 0:
        raise ValueError("grace-sec must be non-negative")
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in pass_env):
        raise ValueError("--pass-env requires environment variable names")
    profile = sandbox_profile(profile)
    scripts = scripts.resolve()
    if not scripts.is_dir():
        raise ValueError(f"seat scripts directory not found: {scripts}")
    python = find_python(profile, scripts)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError(f"output must be a new or empty directory: {out}")
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    tokens = {seat: secrets.token_hex(16) for seat in task.seats}
    with contextlib.ExitStack() as stack:
        root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="playtest-world-"))).resolve()
        seats = []
        try:
            with world_server(task, out, root, tokens, timeout) as (url, env):
                for seat in task.seats:
                    seat_root = Path(stack.enter_context(tempfile.TemporaryDirectory(
                        prefix=".playtest-seat-", dir=Path.home()))).resolve()
                    mode = "external" if seat in selected else "careful" if seat in subjects else "scripted"
                    seats.append(prepare_seat(task, seat, mode, seat_root, commands.get(seat, ""), out / "seats" / seat,
                                              after[seat], tokens[seat], url, pass_env, profile, scripts,
                                              models.get(seat), python))
                requests_stream = stack.enter_context((out / "requests.jsonl").open())
                responses_stream = stack.enter_context((out / "responses.jsonl").open())
                events_stream = stack.enter_context((out / "artifacts/logs/world/events.jsonl").open())
                watch = WaitWatch(requests_stream, responses_stream, events_stream,
                                  float((root / "clock").read_text()))
                timed_out, stop_reason = run_seats(seats, timeout, max_parallel, watch, seat_timeout, stall_sec, grace_sec)
                reward, score = verify(task, out, url, env, float(config.get("verifier", {}).get("timeout_sec", 120)))
        finally:
            archive_seats(seats)
        requests = read_requests(out)
        routes = frozenset(json.loads((root / "routes").read_text())) | {r["path"] for r in requests}
        audit = {seat.seat: audit_seat(seat.output, seat.workdir, seat.root, routes) for seat in seats}
        seat_results = {seat.seat: seat.result([r for r in requests if r["seat"] == seat.seat]) for seat in seats}
        failures = {name: errors for name, info in seat_results.items() if (errors := seat_failures(info))}
        reason = score.get("error") or (None if reward is not None else "verifier returned no reward")
        result = {"task": str(task.path), "reward": reward, "score": score, "timed_out": timed_out,
                  "valid": reason is None, "invalid_reason": reason, **world_causes(out),
                  "stop_reason": stop_reason,
                  "sandbox_profile": str(profile) if profile else None,
                  "python3": str(python.executable) if python else None, "model": common_or_by_seat(models),
                  "harness": common_or_by_seat({s.seat: s.harness for s in seats if s.mode == "external"}),
                  "seat_failures": failures, "seats": seat_results}
        (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        timeline(out, result, audit, requests)
        return result


def check_seat(profile: Path | None, scripts: Path = SEAT_SCRIPTS, deny_probes: tuple[Path, ...] = ()) -> dict:
    """Probe a seat shell that is set up and sandboxed like an external seat: no task, no assistant, no model."""
    profile, home = sandbox_profile(profile), Path.home().resolve()
    if not scripts.is_dir():
        raise ValueError(f"seat scripts directory not found: {scripts}")
    python = find_python(profile, scripts)
    token = secrets.token_hex(8)
    checks = []

    class Probe(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(token)))
            self.end_headers()
            self.wfile.write(token.encode())

        def log_message(self, format: str, *args: Any) -> None:
            pass

    with contextlib.ExitStack() as stack:
        root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix=".playtest-seat-", dir=home))).resolve()
        canary = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix=".playtest-canary-", dir=home)),
                      "canary.txt").resolve()
        canary.write_text(token)
        server = stack.enter_context(ThreadingHTTPServer(("127.0.0.1", 0), Probe))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        stack.callback(server.shutdown)
        workdir = root / "work"
        workdir.mkdir()
        env = ({name: os.environ[name] for name in ("USER",) if name in os.environ}
               | {"HOME": str(Path.home()), "PWD": str(workdir)} | seat_shell(root, python))
        login = CHECK_SHELLS[0][1]

        def shell(invocation: tuple[str, ...], command: str) -> tuple[int | None, str, str]:
            """Exit code (None: did not run to the end), stdout, and the last stderr line."""
            argv = [*invocation, command]
            if profile is not None:
                argv = sandbox_command(argv, profile, root, scripts, python)
            try:
                done = subprocess.run(argv, cwd=workdir, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                      text=True, timeout=30, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                return None, "", str(exc)
            return done.returncode, done.stdout.strip(), (done.stderr.strip().splitlines() or [""])[-1]

        def refused(path: Path) -> tuple[bool, str]:
            if not path.exists():
                return False, "not on this host, so the check proves nothing"
            code, out, err = shell(login, f"{'ls -A' if path.is_dir() else 'cat'} {shlex.quote(str(path))}")
            if code is None:
                return False, err
            return (True, err.rpartition(": ")[2]) if code != 0 and not out else (False, "the seat can read it")

        def add(name: str, ok: bool, detail: str) -> None:
            checks.append({"name": name, "ok": ok, "detail": detail})

        link = str(root / "bin" / "python3")
        for label, invocation in CHECK_SHELLS:
            code, out, err = shell(invocation,
                                   "command -v python3 && python3 -c 'import sys; print(sys.version.split()[0])'")
            lines = out.splitlines()
            ok = code == 0 and python is not None and len(lines) == 2 and lines[0] == link
            add(f"python3 -c ({label})", ok, f"Python {lines[1]}" if ok else "; ".join(
                part for part in (f"python3 is {lines[0]}" if lines else "python3 is not on PATH", err) if part))
        for label, invocation in CHECK_SHELLS:
            code, out, err = shell(invocation, "cat <<'EOF'\nhere-document ok\nEOF")
            add(f"here-document ({label})", code == 0 and out == "here-document ok", err if code != 0 else "")
        code, out, err = shell(login, f"curl -fsS -m 5 http://127.0.0.1:{server.server_address[1]}/")
        add("curl to a local port", code == 0 and out == token, err if code != 0 else "")
        code, out, err = shell(login, 'echo seat > check.txt && echo seat > "$TMPDIR/check.txt" '
                                      '&& cat check.txt "$TMPDIR/check.txt"')
        add("write inside the seat folder", code == 0 and out.split() == ["seat", "seat"], err if code != 0 else "")
        add("read of the user's home refused", *min(refused(home), refused(canary)))
        for probe in deny_probes:
            path = Path(os.path.abspath(probe.expanduser()))
            add(f"read refused: {path}", *refused(path))
    return {"sandbox_profile": str(profile) if profile else None,
            "python3": str(python.executable) if python else None, "checks": checks}


def print_seat_check(report: dict) -> bool:
    print(f"Seat check; sandbox profile: {report['sandbox_profile'] or 'none (host processes are not isolated)'}")
    print(f"Seat python3: {report['python3'] or 'none found on this host'}")
    for check in report["checks"]:
        detail = f": {check['detail']}" if check["detail"] else ""
        print(f"{'PASS' if check['ok'] else 'FAIL'}  {check['name']}{detail}")
    failed = sum(not check["ok"] for check in report["checks"])
    print(f"Seat check: {len(report['checks']) - failed} of {len(report['checks'])} passed")
    return not failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, epilog="Trusted host commands only; sandbox is optional.")
    parser.add_argument("--task", type=Path, help="built task folder")
    parser.add_argument("--seat-cmd", action="append",
                        help="default or SEAT=template; repeatable; {instruction}, {seat}, {workdir}, {log}")
    parser.add_argument("--seats", help="comma-separated external seats (default: all non-scripted seats)")
    parser.add_argument("--timeout-sec", type=float, help="total seat play time (default: built task timeout)")
    parser.add_argument("--seat-timeout", type=float, help="maximum seconds per seat, from its own start")
    parser.add_argument("--stall-sec", type=float, default=120, help="stop when all live seats wait after an exit")
    parser.add_argument("--grace-sec", type=float, default=20, help="seconds to allow seats to finish after freeze")
    parser.add_argument("--max-parallel", type=int, default=2, help="maximum concurrent external commands")
    parser.add_argument("--pass-env", action="append", default=[], metavar="NAME",
                        help="host variable to pass to a seat (repeatable; comma-separated names also accepted)")
    parser.add_argument("--sandbox-profile", type=Path, help="macOS sandbox-exec profile for external seats")
    parser.add_argument("--seat-scripts", type=Path, default=SEAT_SCRIPTS, help="readable seat scripts directory")
    parser.add_argument("--model-label", action="append", help="default or SEAT=model name; repeatable")
    parser.add_argument("--out", type=Path, help="new or empty output folder")
    parser.add_argument("--check-seat", action="store_true",
                        help="only probe a seat shell under the profile (no task, assistant or model), then exit")
    parser.add_argument("--deny-probe", type=Path, action="append", default=[], metavar="PATH",
                        help="with --check-seat: a path that the seat must not be able to read (repeatable)")
    args = parser.parse_args()
    if args.check_seat:
        try:
            report = check_seat(args.sandbox_profile, args.seat_scripts, tuple(args.deny_probe))
        except (ValueError, OSError) as exc:
            parser.exit(2, f"playtest: {exc}\n")
        raise SystemExit(0 if print_seat_check(report) else 1)
    required = (("--task", args.task), ("--seat-cmd", args.seat_cmd), ("--out", args.out))
    if missing := [flag for flag, value in required if value is None]:
        parser.error(f"the following arguments are required: {', '.join(missing)}")
    try:
        selected = {seat.strip() for seat in args.seats.split(",")} if args.seats is not None else None
        passed = tuple(name.strip() for group in args.pass_env for name in group.split(","))
        result = playtest(args.task, args.seat_cmd, selected, args.timeout_sec, args.max_parallel, args.out,
                          pass_env=passed, profile=args.sandbox_profile, model_label=args.model_label,
                          seat_timeout=args.seat_timeout, stall_sec=args.stall_sec, grace_sec=args.grace_sec,
                          scripts=args.seat_scripts)
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        parser.exit(2, f"playtest: {exc}\n")
    print(f"Reward: {result['reward']}; results: {args.out / 'result.json'}")
    print(f"Freeze reason: {result['freeze_reason']}")
    print(f"Budget events: {json.dumps(result['budget_events'])}")
    for name, info in result["seats"].items():
        print(f"{name}: exit={info['exit_code']}, world calls={info['world_calls']}, 4xx={info['http_4xx']}")
        if info["stopped_after_freeze"]:
            print(f"{name}: stopped after world froze")
    if result["stop_reason"]:
        print(f"playtest: stopped: {result['stop_reason']}", file=sys.stderr)
    if not result["valid"]:
        print(f"playtest: invalid: {result['invalid_reason']}", file=sys.stderr)
    if result["seat_failures"]:
        for name, failures in result["seat_failures"].items():
            print(f"playtest: {name}: {', '.join(failures)}", file=sys.stderr)
    if result["seat_failures"] or not result["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
