"""Schedule sequential native control and hosted panels for one fixed world."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

PLAN_SCHEMA = "seatlab.canary-plan/v1"
STATUS_SCHEMA = "seatlab.canary-status/v1"
ARMS = ("control", "hosted")
CONDITIONS = ("silent", "stale", "fresh", "reordered", "pooled")
DEFAULT_MODEL = "baidu/ernie-5.5-flash-preview:free"
DEFAULT_PROVIDER = "baidu"
LOCK = ".canary.lock"
# Harbor bounds build, seat setup, actor execution and verification. This outer
# limit also covers local preflight and a stuck artifact download or teardown.
PANEL_TIMEOUT = 900
COLLECT_TIMEOUT = 30
SLEEP_LIMIT = 30
ENV_KEYS = (
    "HOME",
    "LOGNAME",
    "PATH",
    "TMPDIR",
    "USER",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_RUNTIME_DIR",
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
)


@dataclass(frozen=True)
class Plan:
    schema: str
    start: str
    slots: int
    interval_seconds: int
    window_seconds: int
    seed: int
    condition: str
    episodes: int
    harbor: str
    runner: str
    runner_sha256: str
    hosted_route: dict[str, str]
    control_only: bool
    jobs: int = 1

    def slot_start(self, slot: int) -> datetime:
        return parse_utc(self.start) + timedelta(
            seconds=(slot - 1) * self.interval_seconds
        )


def parse_utc(value: str) -> datetime:
    if "T" not in value:
        raise ValueError("start must be an ISO datetime in UTC")
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("start must be an ISO datetime in UTC")
    return parsed.astimezone(UTC)


def stamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def now_utc() -> datetime:
    return datetime.now(UTC)


def slot_phase(
    now: datetime, start: datetime, window_seconds: int
) -> Literal["future", "due", "missed"]:
    if now < start:
        return "future"
    if now >= start + timedelta(seconds=window_seconds):
        return "missed"
    return "due"


def pending_arms(prior: dict[str, str], control_only: bool) -> tuple[str, ...]:
    """An attempt or a skip permanently consumes that arm in the slot."""
    return tuple(
        arm
        for arm in ARMS
        if arm not in prior and not (control_only and arm == "hosted")
    )


def _sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def _check_runner(plan: Plan) -> None:
    if _sha256(Path(plan.runner)) != plan.runner_sha256:
        raise ValueError("runner SHA-256 differs from the canary plan")


def _validate(plan: Plan) -> Plan:
    if plan.schema != PLAN_SCHEMA or type(plan.jobs) is not int or plan.jobs != 1:
        raise ValueError("invalid canary plan schema or job count")
    if type(plan.control_only) is not bool:
        raise ValueError("invalid control-only setting")
    if type(plan.slots) is not int or not 1 <= plan.slots <= 16:
        raise ValueError("use 1 to 16 slots")
    if (
        type(plan.window_seconds) is not int
        or type(plan.interval_seconds) is not int
        or not 1 <= plan.window_seconds <= min(900, plan.interval_seconds)
    ):
        raise ValueError("use a window of 1 to 900 seconds, at most the interval")
    if type(plan.seed) is not int or not -(2**63) <= plan.seed < 2**63:
        raise ValueError("seed must be a signed 64-bit integer")
    if plan.condition not in CONDITIONS:
        raise ValueError("unknown condition")
    if type(plan.episodes) is not int or not 1 <= plan.episodes <= 256:
        raise ValueError("use 1 to 256 episodes")
    if stamp(parse_utc(plan.start)) != plan.start:
        raise ValueError("the plan start must use canonical UTC")
    plan.slot_start(plan.slots) + timedelta(seconds=plan.window_seconds)
    if not Path(plan.runner).is_absolute() or not Path(plan.harbor).is_absolute():
        raise ValueError("the plan requires absolute executable paths")
    if not re.fullmatch(r"[a-f0-9]{64}", plan.runner_sha256):
        raise ValueError("invalid runner SHA-256")
    route = plan.hosted_route
    if (
        not isinstance(route, dict)
        or route.keys() != {"model", "provider"}
        or not isinstance(route["model"], str)
        or not isinstance(route["provider"], str)
        or not re.fullmatch(
            r"[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*:free", route["model"]
        )
        or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", route["provider"])
    ):
        raise ValueError(
            "hosted requires an author/model:free route and one provider slug"
        )
    return plan


def _requested_plan(args: argparse.Namespace, existing: Plan | None) -> Plan:
    if not args.harbor.is_absolute():
        raise ValueError("--harbor requires an absolute path")
    if not args.harbor.is_file() or not os.access(args.harbor, os.X_OK):
        raise ValueError("--harbor must be an executable file")
    runner = args.runner.resolve(strict=True)
    pinned = existing.hosted_route if existing else None
    route = pinned or {"model": DEFAULT_MODEL, "provider": DEFAULT_PROVIDER}
    if not args.control_only:
        route = {
            "model": os.environ.get("SEATLAB_FREE_MODEL", route["model"]),
            "provider": os.environ.get("SEATLAB_FREE_PROVIDER", route["provider"]),
        }
    return _validate(
        Plan(
            schema=PLAN_SCHEMA,
            start=stamp(parse_utc(args.start)),
            slots=args.slots,
            interval_seconds=args.interval_seconds,
            window_seconds=args.window_seconds,
            seed=args.seed,
            condition=args.condition,
            episodes=args.episodes,
            harbor=str(args.harbor.resolve()),
            runner=str(runner),
            runner_sha256=_sha256(runner),
            hosted_route=route,
            control_only=args.control_only,
        )
    )


def _sync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _mkdir(directory: Path) -> None:
    try:
        directory.mkdir(mode=0o700)
    except FileNotFoundError:
        _mkdir(directory.parent)
        _mkdir(directory)
    except FileExistsError:
        if not directory.is_dir() or directory.is_symlink():
            raise
    else:
        _sync_dir(directory.parent)


def _create(path: Path, value: object) -> None:
    """Durably publish a complete record without replacing an existing one."""
    payload = json.dumps(value, sort_keys=True, allow_nan=False) + "\n"
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=".canary-tmp-", delete=False, mode="w", encoding="utf-8"
    ) as file:
        temporary = Path(file.name)
        try:
            file.write(payload)
            file.flush()
            os.fchmod(file.fileno(), 0o400)
            os.fsync(file.fileno())
            os.link(temporary, path)
            _sync_dir(path.parent)
        finally:
            temporary.unlink(missing_ok=True)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return value


def _load_plan(out: Path) -> Plan | None:
    value = _read(out / "plan.json")
    return None if value is None else _validate(Plan(**value))


def _arm_dir(out: Path, slot: int, arm: str) -> Path:
    return out / f"slot-{slot:03d}" / arm


def _decision(path: Path) -> dict[str, Any] | None:
    value = _read(path / "decision.json")
    if value is not None and (
        value.get("state") not in ("attempted", "missed", "paused", "stopped")
        or not isinstance(value.get("at"), str)
    ):
        raise ValueError(f"invalid arm decision in {path}")
    return value


def _record_decision(path: Path, state: str) -> None:
    if os.path.lexists(path / "panel"):
        raise ValueError(f"refusing to adopt an existing panel at {path / 'panel'}")
    _mkdir(path)
    _create(path / "decision.json", {"state": state, "at": stamp(now_utc())})


def _sentinel(out: Path, name: str) -> bool:
    # A dangling symlink still acts as a stop signal.
    return os.path.lexists(out / name)


def _env(plan: Plan, arm: str) -> dict[str, str]:
    env = {name: os.environ[name] for name in ENV_KEYS if name in os.environ}
    if arm == "hosted":
        route = plan.hosted_route
        key = os.environ.get("OPENROUTER_API_KEY")
        if plan.control_only or not key:
            raise ValueError(
                "hosted requires OPENROUTER_API_KEY; set PAUSE_HOSTED for control only"
            )
        env.update(
            OPENROUTER_API_KEY=key,
            SEATLAB_FREE_MODEL=route["model"],
            SEATLAB_FREE_PROVIDER=route["provider"],
        )
    return env


def _panel_args(plan: Plan, path: Path, arm: str) -> list[str]:
    return [
        sys.executable,
        "-E",
        "-B",
        plan.runner,
        "--out",
        str(path / "panel"),
        "--harbor",
        plan.harbor,
        f"--seeds={plan.seed}",
        "--conditions",
        plan.condition,
        "--episodes",
        str(plan.episodes),
        "--jobs",
        str(plan.jobs),
        "--policy",
        "oracle" if arm == "control" else "hosted",
    ]


def _watch(out: Path, slot: int, arm: str, lock_fd: int) -> int:
    """Keep the lock and watchdog alive if the scheduler process exits."""
    plan = _load_plan(out)
    if (
        plan is None
        or not 1 <= slot <= plan.slots
        or arm not in ARMS
        or os.getpgrp() != os.getpid()
        or not os.path.samestat(os.fstat(lock_fd), (out / LOCK).stat())
    ):
        raise ValueError("invalid canary supervisor invocation")
    path = _arm_dir(out, slot, arm)
    decision = _decision(path)
    if decision is None or decision["state"] != "attempted":
        raise ValueError("supervisor requires a durable attempt decision")
    _check_runner(plan)
    command = _panel_args(plan, path, arm)
    _create(path / "supervisor.json", {"group": os.getpgrp(), "at": stamp(now_utc())})
    try:
        with (path / "panel.log").open("xb") as log:
            process = subprocess.Popen(
                command,
                env=_env(plan, arm),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                pass_fds=(lock_fd,),
            )
            code = process.wait(timeout=PANEL_TIMEOUT)
    except subprocess.TimeoutExpired:
        # Only this supervisor's own newly created session is a kill target.
        # A forced termination cannot establish that Docker completed teardown.
        try:
            _create(path / "process.json", {"state": "timeout", "returncode": None})
        finally:
            os.killpg(os.getpgrp(), signal.SIGKILL)
        return 1
    except OSError:
        _create(path / "process.json", {"state": "launch_error", "returncode": None})
    else:
        _create(path / "process.json", {"state": "exited", "returncode": code})
    return 0


def _launch(
    out: Path,
    plan: Plan,
    slot: int,
    arm: str,
    lock_fd: int,
    cutoff: datetime,
) -> bool:
    _check_runner(plan)
    env = _env(plan, arm)
    now = now_utc()
    if not plan.slot_start(slot) <= now < cutoff:
        return False
    path = _arm_dir(out, slot, arm)
    _record_decision(path, "attempted")
    command = [
        sys.executable,
        "-E",
        "-B",
        str(Path(__file__).resolve()),
        "_watch",
        str(out),
        str(slot),
        arm,
        str(lock_fd),
    ]
    try:
        supervisor = subprocess.Popen(
            command,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=sys.stderr,
            pass_fds=(lock_fd,),
            start_new_session=True,
        )
    except OSError:
        _create(path / "process.json", {"state": "launch_error", "returncode": None})
    else:
        # subprocess.run kills its child on KeyboardInterrupt. This supervisor
        # must keep running and keep the lock if the scheduler is interrupted.
        code = supervisor.wait()
        if code and not (path / "process.json").exists():
            _create(
                path / "process.json",
                {"state": "supervisor_error", "returncode": code},
            )
    return True


def _native_result(path: Path, plan: Plan) -> Path | None:
    native = path / "panel" / "cells" / f"seed-{plan.seed}--{plan.condition}" / "native"
    results = sorted(native.glob("seatlab-*/result.json"))
    if len(results) > 1:
        raise ValueError(f"multiple native results for one canary arm in {native}")
    return results[0] if results else None


def _collector_cell(path: Path, plan: Plan) -> dict[str, Any] | None:
    index = _read(path / "panel" / "runs.json")
    if index is None:
        return None
    cells = index.get("cells")
    if (
        index.get("schema") != "seatlab.panel-runs/v1"
        or not isinstance(cells, list)
        or len(cells) != 1
        or not isinstance(cells[0], dict)
        or cells[0].get("seed") != plan.seed
        or cells[0].get("condition") != plan.condition
    ):
        raise ValueError(f"unexpected collector index in {path}")
    return cells[0]


def _collect(path: Path, plan: Plan) -> None:
    _check_runner(plan)
    with (path / "collect.log").open("ab") as log:
        subprocess.run(
            [
                sys.executable,
                "-E",
                "-B",
                plan.runner,
                "--out",
                str(path / "panel"),
                "--collect",
            ],
            env=_env(plan, "control"),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=COLLECT_TIMEOUT,
            check=False,
        )


def _group_active(supervisor: dict[str, Any]) -> bool:
    group = supervisor.get("group")
    if type(group) is not int or group <= 1:
        raise ValueError("invalid canary supervisor group")
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _recover(path: Path, plan: Plan) -> None:
    """Close a prior attempt without executing a native trial again."""
    process = _read(path / "process.json")
    supervisor = _read(path / "supervisor.json")
    state = process.get("state") if process else None
    if supervisor is not None and _group_active(supervisor):
        raise ValueError(f"previous canary process group is still active: {path}")
    panel = path / "panel"
    if panel.is_symlink() or (panel.exists() and supervisor is None):
        raise ValueError(f"refusing to collect an unowned panel at {panel}")
    raw = _native_result(path, plan)
    cell = _collector_cell(path, plan)
    measured = raw is not None and cell is not None and cell.get("status") == "measured"
    if (
        raw is not None
        and not measured
        and (state != "exited" or cell is None or cell.get("error") == "not attempted")
    ):
        _collect(path, plan)
        cell = _collector_cell(path, plan)
        measured = cell is not None and cell.get("status") == "measured"
    unsafe = None
    if state == "timeout":
        unsafe = "watchdog stopped the process group; Docker teardown is unconfirmed"
    elif raw is None and panel.exists():
        unsafe = "native finalization is unconfirmed; inspect the panel before new work"
    elif state in ("launch_error", "supervisor_error") or (
        state == "exited" and raw is None
    ):
        unsafe = (
            "panel launch failed before producing a native result; inspect panel.log"
        )
    _create(
        path / "outcome.json",
        {
            "state": "completed" if measured else "unmeasured",
            "at": stamp(now_utc()),
            "unsafe": unsafe,
        },
    )


def _finish(path: Path, plan: Plan) -> bool:
    outcome = _read(path / "outcome.json")
    recovered = outcome is None
    if recovered:
        _recover(path, plan)
        outcome = _read(path / "outcome.json")
    if outcome and outcome.get("unsafe"):
        raise ValueError(f"{path}: {outcome['unsafe']}")
    return recovered


def _drive(out: Path, plan: Plan, lock_fd: int, once: bool) -> None:
    for slot in range(1, plan.slots + 1):
        start = plan.slot_start(slot)
        prior = {}
        recovered = False
        for arm in ARMS:
            path = _arm_dir(out, slot, arm)
            if decision := _decision(path):
                prior[arm] = decision["state"]
                if decision["state"] == "attempted":
                    recovered = _finish(path, plan) or recovered
        arms = pending_arms(prior, plan.control_only)
        if not arms:
            if (
                once
                and recovered
                and not _sentinel(out, "STOP")
                and slot_phase(now_utc(), start, plan.window_seconds) == "due"
            ):
                return
            continue

        while not _sentinel(out, "STOP"):
            now = now_utc()
            if slot_phase(now, start, plan.window_seconds) != "future":
                break
            time.sleep(min(SLEEP_LIMIT, (start - now).total_seconds()))

        phase = slot_phase(now_utc(), start, plan.window_seconds)
        processed = False
        cutoff = start + timedelta(seconds=plan.window_seconds)
        slot_end = start + timedelta(seconds=plan.interval_seconds)
        for arm in arms:
            path = _arm_dir(out, slot, arm)
            if phase == "missed":
                _record_decision(path, "missed")
            elif _sentinel(out, "STOP"):
                _record_decision(path, "stopped")
            elif phase == "future":
                # STOP disappeared or the clock moved back. Do not launch early.
                return
            elif now_utc() < start:
                return
            elif now_utc() >= cutoff:
                _record_decision(path, "missed")
            elif arm == "hosted" and _sentinel(out, "PAUSE_HOSTED"):
                _record_decision(path, "paused")
                processed = True
            else:
                if not _launch(out, plan, slot, arm, lock_fd, cutoff):
                    if now_utc() < start:
                        return
                    _record_decision(path, "missed")
                    continue
                _finish(path, plan)
                processed = True
                # An admitted slot may start its second arm after the cutoff,
                # but not after the next slot opens. Resume needs a fresh check.
                cutoff = slot_end
        if once and processed and not _sentinel(out, "STOP"):
            return


def _relative(path: Path, out: Path) -> str:
    return path.relative_to(out).as_posix()


def _status(out: Path, plan: Plan, now: datetime) -> dict[str, Any]:
    totals = {
        arm: dict.fromkeys(
            (
                "planned",
                "eligible",
                "attempted",
                "completed",
                "unmeasured",
                "missed",
                "skipped",
            ),
            0,
        )
        for arm in ARMS
    }
    fingerprints: set[str] = set()
    slots = []
    stopped = _sentinel(out, "STOP")
    for slot in range(1, plan.slots + 1):
        start = plan.slot_start(slot)
        phase = slot_phase(now, start, plan.window_seconds)
        arms = {}
        for arm in ARMS:
            path = _arm_dir(out, slot, arm)
            decision = _decision(path)
            attempted = decision is not None and decision["state"] == "attempted"
            info: dict[str, Any] = {}
            if decision is not None and attempted:
                supervisor = _read(path / "supervisor.json")
                result = _native_result(path, plan)
                cell = _collector_cell(path, plan)
                process = _read(path / "process.json")
                outcome = _read(path / "outcome.json")
                state = outcome["state"] if outcome else "attempted"
                evidence = cell.get("evidence") if cell else None
                fingerprint = (
                    evidence.get("fingerprint") if isinstance(evidence, dict) else None
                )
                if state == "completed" and isinstance(fingerprint, str):
                    fingerprints.add(fingerprint)
                info = {
                    "attempted_at": decision["at"],
                    "started_at": supervisor.get("at") if supervisor else None,
                    "panel": _relative(path / "panel", out),
                    "collector_index": _relative(path / "panel" / "runs.json", out),
                    "native_result": _relative(result, out) if result else None,
                    "process": process,
                    "collector_status": cell.get("status") if cell else None,
                    "collector_error": cell.get("error") if cell else None,
                    "fingerprint": fingerprint,
                    "unsafe": outcome.get("unsafe") if outcome else None,
                }
            elif decision is not None:
                state = decision["state"]
                info["decided_at"] = decision["at"]
            elif arm == "hosted" and plan.control_only:
                state = "disabled"
                info["reason"] = "control-only plan"
            elif phase == "missed":
                state = "missed"
            elif stopped:
                state = "stopped"
            else:
                state = "pending"
            skipped = state in ("disabled", "paused", "stopped")
            counts = totals[arm]
            counts["planned"] += 1
            counts["eligible"] += not skipped
            counts["attempted"] += attempted
            for name in ("completed", "unmeasured", "missed"):
                counts[name] += state == name
            counts["skipped"] += skipped
            arms[arm] = {"state": state, **info}
        slots.append(
            {
                "slot": slot,
                "start": stamp(start),
                "cutoff": stamp(start + timedelta(seconds=plan.window_seconds)),
                "arms": arms,
            }
        )
    return {
        "schema": STATUS_SCHEMA,
        "as_of": stamp(now),
        "stop": stopped,
        "pause_hosted": _sentinel(out, "PAUSE_HOSTED"),
        "plan": asdict(plan),
        "totals": totals,
        "fingerprints_match": len(fingerprints) == 1 if fingerprints else None,
        "slots": slots,
    }


def _run(args: argparse.Namespace, out: Path) -> Plan:
    _mkdir(out)
    fd = os.open(out / LOCK, os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("another canary invocation owns this output") from error
        existing = _load_plan(out)
        plan = _requested_plan(args, existing)
        if existing is None:
            allowed = {LOCK, "STOP", "PAUSE_HOSTED"}
            if any(
                entry.name not in allowed and not entry.name.startswith(".canary-tmp-")
                for entry in out.iterdir()
            ):
                raise ValueError(
                    "refusing to register a canary in an unrelated directory"
                )
            _create(out / "plan.json", asdict(plan))
        elif existing != plan:
            raise ValueError(
                "requested configuration differs from the immutable canary plan"
            )
        _drive(out, plan, fd, args.once)
        return plan
    finally:
        # Close, do not unlock: the supervisor may still hold the same lock.
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    values = sys.argv[1:] if argv is None else argv
    try:
        if values[:1] == ["_watch"] and len(values) == 5:
            return _watch(Path(values[1]), int(values[2]), values[3], int(values[4]))
        parser = argparse.ArgumentParser(
            description=__doc__,
            epilog=(
                "Create OUT/STOP to stop future work, or OUT/PAUSE_HOSTED to skip "
                "hosted arms while controls continue. Neither interrupts an active "
                "attempt. The canary never removes a sentinel. Create PAUSE_HOSTED "
                "if a fixed contract error invalidates the hosted arm. Create STOP "
                "if the zero-price route cannot be preserved."
            ),
        )
        commands = parser.add_subparsers(dest="command", required=True)
        run = commands.add_parser("run", help="register or resume a schedule")
        status = commands.add_parser(
            "status", help="read the schedule without launching jobs"
        )
        for command in (run, status):
            command.add_argument("--out", required=True, type=Path)
        for name in ("harbor", "runner"):
            run.add_argument(f"--{name}", required=True, type=Path)
        run.add_argument(
            "--start", required=True, help="first slot, ISO datetime in UTC"
        )
        for name in ("slots", "interval-seconds", "window-seconds", "seed", "episodes"):
            run.add_argument(f"--{name}", required=True, type=int)
        run.add_argument("--condition", required=True, choices=CONDITIONS)
        run.add_argument("--control-only", action="store_true")
        run.add_argument(
            "--once", action="store_true", help="finish only the next eligible slot"
        )
        args = parser.parse_args(values)
        out = args.out.expanduser().resolve()
        plan = _run(args, out) if args.command == "run" else _load_plan(out)
        if plan is None:
            raise ValueError(f"no canary plan at {out}")
        print(
            json.dumps(_status(out, plan, now_utc()), sort_keys=True, allow_nan=False)
        )
    except KeyboardInterrupt:
        print(
            "canary: interrupted; an active attempt can finish independently",
            file=sys.stderr,
        )
        return 130
    except (
        OSError,
        TypeError,
        ValueError,
        OverflowError,
        subprocess.TimeoutExpired,
    ) as error:
        print(f"canary: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
