"""Replay scripted seats through the real world handlers with a seeded in-process scheduler."""

from __future__ import annotations

import argparse
import functools
import io
import json
import os
import random
import statistics
import sys
import tempfile
import time
import tomllib
import types
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parent
MAX_STEPS = 50_000
YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


@functools.lru_cache(maxsize=32)
def compiled(name: str, source: bytes) -> Any:
    return compile(source, name, "exec")


def load(path: Path, name: str) -> Any:
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compiled(path.name, path.read_bytes()), module.__dict__)  # noqa: S102 - local trusted task files
    return module


BUILD = load(ROOT / "build_tasks.py", "flotilla_sim_builder")
POLICY = load(ROOT / "world" / "policy.py", "flotilla_sim_policy")
SCORE = load(ROOT / "world" / "score.py", "flotilla_sim_score")


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def step(self) -> None:
        self.value += 0.001


class Connection:
    def __init__(self) -> None:
        self.timeout = None

    def gettimeout(self) -> float | None:
        return self.timeout

    def settimeout(self, seconds: float | None) -> None:
        self.timeout = seconds


def memory_handler(world: Any) -> type:
    class Handler(world.World):
        def __init__(self, token: str, request: Any) -> None:
            self.path = request.path
            self.command = request.method
            raw = json.dumps(request.body, allow_nan=False).encode() if request.body is not None else b""
            self.headers = {"Authorization": f"Bearer {token}", "Content-Length": str(len(raw))}
            self.rfile = io.BytesIO(raw)
            self.connection = Connection()
            self.client_address = ("127.0.0.2", 0)
            self.close_connection = False
            self.response = None
            self.pending_await = None

        def _await_wait(self, seat, until, after, deadline):
            if hasattr(self, "_wait_begin"):
                self._wait_begin(seat)
            if not self._await_poll(seat, until, after, deadline):
                self.pending_await = (seat, until, after, deadline)
            elif hasattr(self, "_wait_end"):
                self._wait_end(seat)

        def _write(self, status: int, data: bytes, content_type: str, headers: dict | None = None) -> bool:
            self.response = (status, json.loads(data) if content_type == "application/json" else data.decode())
            return self.command != "HEAD"

    return Handler


def environment(service: dict[str, Any]) -> dict[str, str]:
    return {key: str(value).replace("$$", "$") for key, value in service["environment"].items()}


class Simulation:
    def __init__(self, world: Any, clock: Clock, seed: int, schedule_seed: int) -> None:
        self.world, self.clock = world, clock
        self.random = random.Random(schedule_seed)
        ids = random.Random(seed)
        world.time = clock
        world.START = 0.0
        world.secrets = types.SimpleNamespace(token_hex=lambda size: ids.randbytes(size).hex())
        world.EVENT_SINK = self.record
        self.events: list[dict[str, Any]] = []
        self.handler = memory_handler(world)
        self.tokens = {seat: token for token, seat in world.TOKENS.items()} | {"admin": world.ADMIN}
        self.requests = dict.fromkeys(world.SEATS, 0)
        self.parked: dict[str, Any] = {}
        world.start_world()

    def record(self, event: dict[str, Any]) -> None:
        if event["seq"] != len(self.events) + 1 or (self.events and event["t"] < self.events[-1]["t"]):
            raise AssertionError("event order is invalid")
        if (event["seat"] is not None and event["seat"] not in self.tokens
                and (self.world.HOST is None or event["seat"] not in self.world.HOST.senders)):
            raise AssertionError("event has an unknown seat")
        if not isinstance(event["kind"], str) or not event["kind"] or not isinstance(event["data"], dict):
            raise AssertionError("invalid event shape")
        self.events.append(json.loads(json.dumps(event, sort_keys=True, allow_nan=False)))

    def request(self, seat: str, request: Any) -> tuple[int, Any] | None:
        self.clock.step()
        frozen = self.world.STATE["frozen"]
        event_start = len(self.events)
        accepted = len(self.world.STATE["submissions"])
        handler = self.handler(self.tokens[seat], request)
        handler._handle(request.method)
        if seat != "admin":
            self.requests[seat] += 1
        if handler.pending_await is not None:
            self.parked[seat] = handler
            return None
        if handler.response is None:
            raise AssertionError(f"no reply to {request.method} {request.path}")
        status, reply = handler.response
        if not 200 <= status < 500 and not (status == 500 and self.world.HOST and self.world.STATE["frozen"]):
            raise AssertionError(f"invalid reply status {status}")
        if (frozen and seat != "admin" and request.method == "POST" and status < 400
                and not any(e["kind"] == "retired_retry" for e in self.events[event_start:])):
            raise AssertionError("world accepted a move after freeze")
        if frozen and len(self.world.STATE["submissions"]) != accepted:
            raise AssertionError("world recorded a late submission")
        return status, reply

    def run(self, policies: dict[str, Any], start_after: dict[str, list[str]] | None = None) -> None:
        pending = dict(start_after or {})
        ended: set[str] = set()
        ready_at = dict.fromkeys(set(policies) - pending.keys(), 0.0)
        replies = dict.fromkeys(policies)
        for _ in range(MAX_STEPS):
            for seat, predecessors in list(pending.items()):
                if set(predecessors) <= ended:
                    ready_at[seat] = self.clock.value
                    del pending[seat]
            for seat, handler in list(self.parked.items()):
                if handler._await_poll(*handler.pending_await):
                    if hasattr(handler, "_wait_end"):
                        handler._wait_end(seat)
                    replies[seat] = handler.response
                    ready_at[seat] = self.clock.value
                    del self.parked[seat]
            if not ready_at and not self.parked:
                if pending:
                    raise ValueError("simulation start_after dependency cycle")
                return
            ready = sorted(s for s, at in ready_at.items() if at <= self.clock.value)
            if not ready:
                deadlines = [handler.pending_await[-1] for handler in self.parked.values()]
                self.clock.value = min([*ready_at.values(), *deadlines])
                continue
            seat = self.random.choice(ready)
            try:
                step = policies[seat].send(replies[seat])
            except StopIteration:
                del ready_at[seat]
                ended.add(seat)
                if self.world.HOST is not None and hasattr(self.world.HOST, "seat_end"):
                    self.world.HOST.seat_end(seat)
                continue
            if isinstance(step, POLICY.Request):
                replies[seat] = self.request(seat, step)
                if seat in self.parked:
                    del ready_at[seat]
            elif isinstance(step, POLICY.Sleep):
                replies[seat] = None
                ready_at[seat] = self.clock.value + step.seconds
            else:
                raise TypeError(f"invalid policy step {step!r}")
        if self.world.HOST is None:
            raise RuntimeError(f"simulation exceeded {MAX_STEPS} scheduler steps")
        if hasattr(self.world.HOST, "deadline"):
            self.world.HOST.deadline("simulation_stalled", {"steps": MAX_STEPS})
        else:
            self.world.record("simulation_stalled", None, {"steps": MAX_STEPS})

    def finish(self) -> dict[str, Any]:
        self.request("admin", POLICY.Request("POST", "/admin/freeze", {}))
        _, state = self.request("admin", POLICY.Request("GET", "/admin/state"))
        if not state["frozen"]:
            raise AssertionError("referee did not freeze")
        return state


def select_policies(settings: dict[str, Any], scripted: list[str], policies: str | dict[str, str] | None) -> dict:
    names = dict(settings["seats"])
    subjects = set(names) - set(scripted)
    if isinstance(policies, str):
        names.update(dict.fromkeys(subjects, policies))
    elif policies is not None:
        if set(policies) - subjects:
            raise ValueError("policy overrides may name only seats under test")
        names.update(policies)
    return names


def score(task: dict, state: dict, events: list[dict], family: Any, env: Path) -> dict:
    if task.get("mode") == "extension":
        try:
            return SCORE.extension_score(task, events, env)
        except Exception as exc:  # noqa: BLE001 - scorer failures are invalid in both runtimes
            return {"invalid": f"extension verification failed: {type(exc).__name__}: {exc}"[:500]}
    else:
        submissions = state["submissions"]
        if probe := task.get("probe"):
            submissions = [s for s in submissions if s["seat"] == probe["subject"]]
        answer = submissions[-1]["answer"] if submissions else None
        reward = family.score(answer, task["answer_key"])["score"]
    if not 0 <= reward <= 1:
        raise AssertionError(f"invalid reward {reward}")
    return {"reward": float(reward)}


def simulate_built(task_dir: Path, policies: str | dict[str, str] | None, schedule_seed: int) -> dict[str, Any]:
    """Run a built task with its actual config, briefings, policy and source files. No sockets or subprocesses."""
    env, solution = task_dir / "environment", task_dir / "solution"
    task = json.loads((env / "task.json").read_text())
    if task.get("mode") == "rounds":
        raise ValueError("legacy wall-clock games are not supported by the logical simulator")
    settings_path = solution / "policy.json"
    expected_runner = (ROOT / "world" / "policy.py").read_bytes()
    if task.get("mode") == "extension":
        expected_runner = BUILD.marked(expected_runner)
    if settings_path.is_file() and (solution / "policy.py").read_bytes() != expected_runner:
        raise ValueError("built policy runner differs from this checkout; rebuild the task")
    settings = json.loads(settings_path.read_text()) if settings_path.is_file() else {
        "mode": task.get("mode", "answer"), "variant": task.get("variant", ""),
        "seats": dict.fromkeys(task["seats"], "careful"),
    }
    names = select_policies(settings, task.get("scripted_seats", []), policies)
    services = yaml.load((env / "docker-compose.yaml").read_text(), Loader=YAML_LOADER)["services"]
    family = load(solution / "family.py", "flotilla_sim_family")
    oracle = load(solution / "oracle.py", "flotilla_sim_oracle")
    custom_path = solution / "family_policy.py"
    custom = load(custom_path, "flotilla_sim_custom") if custom_path.is_file() else None
    with tempfile.TemporaryDirectory(prefix="flotilla-sim-logs-") as logs:
        settings_env = {k: v for k, v in os.environ.items() if not k.startswith("FLOTILLA_")}
        settings_env.update(environment(services["main"]))
        settings_env.update(FLOTILLA_LOGS=logs, FLOTILLA_TASK_JSON=str(env / "task.json"))
        with patch.dict(os.environ, settings_env, clear=True), patch.object(sys, "dont_write_bytecode", True):
            world = load(env / "world.py", "flotilla_sim_world")
            if not hasattr(world, "EVENT_SINK"):
                raise ValueError("built world lacks simulation support; rebuild the task")
            sim = Simulation(world, Clock(), task["seed"], schedule_seed)
            files_read = {seat: [] for seat in names}
            generators = {}
            for seat, name in names.items():
                own = json.loads(environment(services[seat])["SEAT_BRIEFING"])

                def read_file(filename: str, seat: str = seat) -> str:
                    files_read[seat].append(filename)
                    return (solution / filename if filename == "answer.json" else env / "player" / filename).read_text()

                client = POLICY.Client(seat, read_file)
                generators[seat] = POLICY.play(name, client, own, family, oracle, custom, settings["variant"],
                                               settings["mode"] == "extension", settings.get("seed"),
                                               settings.get("instance"))
            agents = tomllib.loads((task_dir / "task.toml").read_text())["agents"]
            sim.run(generators, {seat: row["start_after"] for seat, row in agents.items() if "start_after" in row})
            state = sim.finish()
            result = score(task, state, sim.events, family, env)
    if task.get("mode") == "extension" and "versions" in task:
        result["versions"] = task["versions"]
    return {"reward": result.get("reward"), "score": result, "invalid": result.get("invalid"),
            **({"versions": result["versions"]} if "versions" in result else {}),
            "stalled": result.get("invalid") is not None and any(e["kind"] == "simulation_stalled" for e in sim.events),
            "stalled_seats": result.get("stalled_seats", []),
            "events": sim.events, "state": state, "requests": sim.requests,
            "policies": names, "schedule_seed": schedule_seed, "files_read": files_read}


def simulate(family: str, arm: str, instance: int, policies: str | dict[str, str], seed: int, schedule_seed: int,
             *, variant: str = "", n_seats: int | None = None, difficulty: str = "easy", **build_options: Any) -> dict:
    """Build one deterministic instance, then schedule its seat policies without Docker or a model."""
    if "." in family:
        family, suffix = family.split(".", 1)
        if variant and variant != suffix:
            raise ValueError("conflicting variant names")
        variant = suffix
    module = BUILD.load_family(family)
    variant = variant or getattr(module, "DEFAULT_VARIANT", "")
    if n_seats is None:
        n_seats = BUILD.default_seats(module, difficulty)
    with tempfile.TemporaryDirectory(prefix="flotilla-sim-") as root:
        (path,) = BUILD.build(family, [instance], n_seats, difficulty, seed, (arm,), Path(root), variant=variant,
                              policy="careful", **build_options)
        return simulate_built(path, policies, schedule_seed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", required=True)
    parser.add_argument("--arms", required=True, help="comma-separated arm names")
    parser.add_argument("--policies", default="careful,naive,nop")
    parser.add_argument("--instances", default="0-9")
    parser.add_argument("--schedule-seeds", default="0-9")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--variant", default="")
    parser.add_argument("--seats", type=int)
    parser.add_argument("--difficulty", default="easy")
    parser.add_argument("--read-first", action="store_true")
    args = parser.parse_args()
    started, runs = time.monotonic(), 0
    for arm in args.arms.split(","):
        for name in args.policies.split(","):
            rewards = [simulate(args.family, arm, instance, name, args.seed, schedule_seed, variant=args.variant,
                                n_seats=args.seats, difficulty=args.difficulty,
                                read_first_subject=args.read_first)["reward"]
                       for instance in BUILD.parse_instances(args.instances)
                       for schedule_seed in BUILD.parse_instances(args.schedule_seeds)]
            runs += len(rewards)
            valid = [reward for reward in rewards if reward is not None]
            mean = f"{statistics.mean(valid):.4f}" if valid else "n/a"
            print(f"{arm:20} {name:10} n={len(rewards):5d} invalid={len(rewards) - len(valid)} mean={mean}")
    print(f"{runs} simulations in {time.monotonic() - started:.3f}s")


if __name__ == "__main__":
    main()
