"""Rehearse a built seat task over HTTP, using trusted local commands instead of containers.

    uvx --quiet --python 3.12 --with pyyaml python flotilla/playtest.py --help
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
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import qa

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
    process: subprocess.Popen | None = None
    started: float | None = None
    ended: float | None = None
    exit_code: int | None = None
    timed_out: bool = False
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
                "seconds": None if self.started is None else self.ended - self.started,
                "started_sec": self.started, "ended_sec": self.ended, "timed_out": self.timed_out,
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


def seat_env(task: qa.Task, seat: str, root: Path, pass_env: tuple[str, ...]) -> dict[str, str]:
    tmp = root / "tmp"
    tmp.mkdir(mode=0o700)
    names = ("PATH", "HOME", *pass_env)
    return ({name: os.environ[name] for name in names if name in os.environ}
            | {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(Path.home())}
            | task.env(seat) | {"TMPDIR": str(tmp)})


def sandbox_command(argv: list[str], profile: Path, root: Path) -> list[str]:
    return ["/usr/bin/sandbox-exec", "-f", str(profile), "-D", f"SEATROOT={root}",
            "-D", f"HOSTHOME={Path.home().resolve()}", "-D", f"HOSTTMP={Path(tempfile.gettempdir()).resolve()}",
            "-D", f"SEATSCRIPTS={Path(__file__).resolve().parent / 'playtest_seats'}", *argv]


def sandbox_profile(path: Path | None) -> Path | None:
    if path is None:
        return None
    if sys.platform != "darwin":
        print("playtest: --sandbox-profile skipped: sandbox-exec is only available on macOS", file=sys.stderr)
        return None
    if not path.is_file():
        raise ValueError(f"sandbox profile not found: {path}")
    return path.resolve()


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
                 pass_env: tuple[str, ...], profile: Path | None) -> SeatRun:
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
    env = seat_env(task, seat, root, pass_env) | {"SEAT_TOKEN": token, "PWD": str(workdir), "WORLD_URL": url}
    if mode == "external":
        values = {"instruction": str(prompt), "seat": seat, "workdir": str(workdir), "log": str(native_log)}
        command = PLACEHOLDER.sub(lambda m: shlex.quote(values[m[1]]), template)
        argv = ["/bin/bash", "-c", command]
        if profile is not None:
            argv = sandbox_command(argv, profile, root)
            env["PLAYTEST_SANDBOX"] = str(profile)
    else:
        argv = policy_command(task, seat, mode, root)
        command = shlex.join(argv)
    return SeatRun(seat, mode, root, output, workdir, env, command, argv, after, mapping)


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
              seat_timeout: float | None, stall_sec: float) -> tuple[bool, str | None]:
    origin = time.monotonic()
    pending = list(seats)
    running: list[SeatRun] = []
    ended: set[str] = set()
    last_exit = None
    ended_at = origin
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
                return False, None
            if now >= timeout:
                for seat in running + pending:
                    seat.finish(now, timeout=True)
                return True, "total timeout"
            watch.update()
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


def log_strings(text: str) -> list[str]:
    """Plain log lines as they are; JSON lines as their decoded string values, so escaped newlines split paths."""
    strings = []
    for line in text.splitlines():
        try:
            pending = [json.loads(line)]
        except ValueError:
            strings.append(line)
            continue
        while pending:
            value = pending.pop()
            if isinstance(value, str):
                strings.append(value)
            elif isinstance(value, dict):
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
    return strings


def outside_paths(text: str, workdir: Path, root: Path) -> list[str]:
    """Conservative log-text scan, not an access trace. Resolve relative mentions against the initial cwd."""
    paths = set()
    for chunk in log_strings(text):
        if ROOT_ACCESS.search(chunk):
            paths.add("/")
        for match in PATH_MENTION.finditer(chunk):
            raw = next(value for value in match.groups() if value is not None).rstrip(":")
            if raw.startswith("//") or raw in IGNORED_PATHS:
                continue
            path = Path(os.path.abspath(workdir / Path(raw).expanduser()))
            if not path.is_relative_to(root):
                paths.add(str(path))
    return sorted(paths)


def shell_commands(text: str) -> list[str]:
    commands = []
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
                items.append((item.get("id"), item.get("command")))
        elif event.get("type") == "assistant":
            message = event.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            for item in content if isinstance(content, list) else []:
                if isinstance(item, dict) and item.get("type") == "tool_use" and item.get("name") == "Bash":
                    inputs = item.get("input")
                    if isinstance(inputs, dict):
                        items.append((item.get("id"), inputs.get("command")))
        for item_id, command in items:
            if not isinstance(command, str):
                continue
            if isinstance(item_id, str):
                if item_id in seen:
                    continue
                seen.add(item_id)
            commands.append(command)
    return commands


def seat_commands(output: Path) -> list[str]:
    text = "\n".join((output / name).read_text(errors="replace") for name in ("seat.log", "command.log"))
    return shell_commands(text)


def archive_seats(seats: list[SeatRun]) -> dict[str, list[str]]:
    audit = {}
    for seat in seats:
        dest = seat.output
        shutil.copyfile(seat.root / "command.log", dest / "command.log")
        text = "\n".join((dest / name).read_text(errors="replace") for name in ("seat.log", "command.log"))
        audit[seat.seat] = outside_paths(text, seat.workdir, seat.root)
    return audit


def timeline(out: Path, result: dict, audit: dict[str, list[str]], requests: list[dict]) -> None:
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
             f"Seat failures: {json.dumps(result['seat_failures']) if result['seat_failures'] else 'none'}",
             "This is a rehearsal, not benchmark evidence.", ""]
    by_request = {seat: [] for seat in result["seats"]}
    for request in requests:
        by_request[request["seat"]].append(
            f"- #{request['seq']} t={request['t']:.3f}s {request['method']} {request['path']}")
    for seat, evidence in by_seat.items():
        info = result["seats"][seat]
        paths = [f"- `{path}`" for path in audit[seat]] or ["- No outside paths found."]
        commands = seat_commands(out / "seats" / seat)
        started, ended = (f"{info[key]:.3f}" if info[key] is not None else "not started"
                          for key in ("started_sec", "ended_sec"))
        lines += [f"## {seat} ({info['mode']})", "",
                  f"Started: {started}; ended: {ended}; exit: {info['exit_code']}; "
                  f"world calls: {info['world_calls']}; 4xx: {info['http_4xx']}", "",
                  *(evidence or ["- No seat events."]), "", "### World requests (arrival order)", "",
                  *(by_request[seat] or ["- No world calls."]), "", "### Shell commands (CLI order)", "",
                  *[f"- `{command}`" for command in commands],
                  *([] if commands else ["- No recognized shell command records."]), "", "### Outside-path audit", "",
                  "Candidate outside paths in the logs (possible reads or listings, not an access trace).",
                  *paths, ""]
    (out / "summary.md").write_text("\n".join(lines))


def harness_name(template: str) -> str:
    args = shlex.split(template)
    if Path(args[0]).name in ("bash", "sh", "zsh", "python", "python3", "python3.12") and len(args) > 1:
        return Path(args[1]).name
    return Path(args[0]).name


def seat_failures(info: dict) -> list[str]:
    failures = []
    if info["timed_out"]:
        failures.append("timed out")
    if info["error"]:
        failures.append(info["error"])
    if info["exit_code"] != 0:
        failures.append(f"exit code {info['exit_code']}")
    if info["mode"] == "external" and info["world_calls"] == 0:
        failures.append("no world calls")
    return failures


def playtest(task_dir: Path, template: str, selected: set[str] | None, timeout: float | None,
             max_parallel: int, out: Path, *, pass_env: tuple[str, ...] = (),
             profile: Path | None = None, model_label: str | None = None,
             seat_timeout: float | None = None, stall_sec: float = 120) -> dict:
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
    timeout = float(config.get("agent", {}).get("timeout_sec", 900)) if timeout is None else timeout
    if not math.isfinite(timeout) or timeout <= 0 or max_parallel <= 0 or not template.strip():
        raise ValueError("timeout and max-parallel must be positive; seat command must not be empty")
    if any(not math.isfinite(value) or value <= 0 for value in (seat_timeout, stall_sec) if value is not None):
        raise ValueError("seat-timeout and stall-sec must be positive")
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in pass_env):
        raise ValueError("--pass-env requires environment variable names")
    profile = sandbox_profile(profile)
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
                    seats.append(prepare_seat(task, seat, mode, seat_root, template, out / "seats" / seat,
                                              after[seat], tokens[seat], url, pass_env, profile))
                requests_stream = stack.enter_context((out / "requests.jsonl").open())
                responses_stream = stack.enter_context((out / "responses.jsonl").open())
                events_stream = stack.enter_context((out / "artifacts/logs/world/events.jsonl").open())
                watch = WaitWatch(requests_stream, responses_stream, events_stream,
                                  float((root / "clock").read_text()))
                timed_out, stop_reason = run_seats(seats, timeout, max_parallel, watch, seat_timeout, stall_sec)
                reward, score = verify(task, out, url, env, float(config.get("verifier", {}).get("timeout_sec", 120)))
        finally:
            audit = archive_seats(seats)
        requests = read_requests(out)
        seat_results = {seat.seat: seat.result([r for r in requests if r["seat"] == seat.seat]) for seat in seats}
        failures = {name: errors for name, info in seat_results.items() if (errors := seat_failures(info))}
        reason = score.get("error") or (None if reward is not None else "verifier returned no reward")
        result = {"task": str(task.path), "reward": reward, "score": score, "timed_out": timed_out,
                  "valid": reason is None, "invalid_reason": reason, **world_causes(out),
                  "stop_reason": stop_reason,
                  "sandbox_profile": str(profile) if profile else None, "model": model_label,
                  "harness": harness_name(template), "seat_failures": failures, "seats": seat_results}
        (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        timeline(out, result, audit, requests)
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, epilog="Trusted host commands only; sandbox is optional.")
    parser.add_argument("--task", type=Path, required=True, help="built task folder")
    parser.add_argument("--seat-cmd", required=True, help="shell template; {instruction}, {seat}, {workdir}, {log}")
    parser.add_argument("--seats", help="comma-separated external seats (default: all non-scripted seats)")
    parser.add_argument("--timeout-sec", type=float, help="total seat play time (default: built task timeout)")
    parser.add_argument("--seat-timeout", type=float, help="maximum seconds per seat, from its own start")
    parser.add_argument("--stall-sec", type=float, default=120, help="stop when all live seats wait after an exit")
    parser.add_argument("--max-parallel", type=int, default=2, help="maximum concurrent external commands")
    parser.add_argument("--pass-env", action="append", default=[], metavar="NAME",
                        help="host variable to pass to a seat (repeatable; comma-separated names also accepted)")
    parser.add_argument("--sandbox-profile", type=Path, help="macOS sandbox-exec profile for external seats")
    parser.add_argument("--model-label", help="model name to record in result.json")
    parser.add_argument("--out", type=Path, required=True, help="new or empty output folder")
    args = parser.parse_args()
    try:
        selected = {seat.strip() for seat in args.seats.split(",")} if args.seats is not None else None
        passed = tuple(name.strip() for group in args.pass_env for name in group.split(","))
        result = playtest(args.task, args.seat_cmd, selected, args.timeout_sec, args.max_parallel, args.out,
                          pass_env=passed, profile=args.sandbox_profile, model_label=args.model_label,
                          seat_timeout=args.seat_timeout, stall_sec=args.stall_sec)
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        parser.exit(2, f"playtest: {exc}\n")
    print(f"Reward: {result['reward']}; results: {args.out / 'result.json'}")
    for name, info in result["seats"].items():
        print(f"{name}: exit={info['exit_code']}, world calls={info['world_calls']}")
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
