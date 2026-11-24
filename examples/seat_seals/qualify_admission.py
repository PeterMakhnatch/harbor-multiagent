import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..native_study import require
from .admission_wire import make_request, read_message, validate_reply, wire_bytes
from .worker_layout import assigned, digest, load_plan

Record = dict[str, Any]
Served = tuple[int, str | None]

SCHEMA = "harbor.seat-seal.admission-qualification/v1"
MODULE = "examples.seat_seals.admission"
ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = "offline admission transport qualification, not native evidence"
GRANTED = "granted"
ISSUED = "already_issued"
REFUSED_EXIT = 2
RACERS = 8
SLOTS = 4
MIN_WORKERS = 2
TIMEOUT = 60
NONCE_BYTES = 16
DETAIL_BYTES = 300
PROBES = (
    "init_fresh_store",
    "concurrent_single_grant",
    "repeat_after_restart_denied",
    "lost_reply_then_denied",
    "wrong_plan_no_spend",
    "wrong_shard_no_spend",
    "refused_ticket_still_grantable",
    "independent_ticket_granted",
    "init_existing_refused",
    "status_stable_after_restart",
)
FAILURES = (
    ValueError,
    TypeError,
    KeyError,
    OSError,
    RuntimeError,
    subprocess.TimeoutExpired,
)
RACE_FAILURES = (OSError, subprocess.TimeoutExpired, threading.BrokenBarrierError)


def nonce() -> str:
    value = secrets.token_hex(NONCE_BYTES)
    assert len(value) == 2 * NONCE_BYTES and value == value.lower()
    return value


def finish(proc: subprocess.Popen[bytes], data: bytes) -> tuple[bytes, bytes]:
    try:
        out, err = proc.communicate(data, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise
    assert proc.returncode is not None
    return out or b"", err or b""


def strict_pairs(pairs: list[tuple[str, Any]]) -> Record:
    found = dict(pairs)
    require(len(found) == len(pairs), "status JSON repeats a key")
    return found


def reject_constant(name: str) -> None:
    raise ValueError(f"status JSON holds non-finite {name}")


class Runner:
    def __init__(self, ledger: Path) -> None:
        assert ledger.is_absolute() and not ledger.exists()
        self.ledger = ledger
        self.env = {"PATH": os.environ.get("PATH", os.defpath)}
        self.processes = 0
        self.replies: Counter[str] = Counter()

    def start(self, args: tuple[str, ...], stdout: int) -> subprocess.Popen[bytes]:
        assert args and len(args) <= 5
        self.processes += 1
        return subprocess.Popen(
            [sys.executable, "-BS", "-m", MODULE, *args],
            cwd=ROOT,
            env=self.env,
            stdin=subprocess.PIPE,
            stdout=stdout,
            stderr=subprocess.PIPE,
        )

    def call(
        self, *args: str, data: bytes = b"", stdout: int = subprocess.PIPE
    ) -> tuple[int, bytes, bytes]:
        proc = self.start(args, stdout)
        out, err = finish(proc, data)
        return proc.returncode, out, err

    def tally(self, code: int, status: str | None) -> Served:
        if code != 0:
            self.replies["exit_refused" if code == REFUSED_EXIT else "exit_other"] += 1
        self.replies[status or "no_valid_reply"] += 1
        return code, status

    def reply(self, out: bytes, request: Record) -> str | None:
        if not out:
            return None
        try:
            return validate_reply(read_message(out), request)
        except (ValueError, TypeError, KeyError):
            return None

    def serve(self, request: Record, *, lost: bool = False) -> Served:
        stdout = subprocess.DEVNULL if lost else subprocess.PIPE
        code, out, _ = self.call(
            "serve-once", str(self.ledger), data=wire_bytes(request), stdout=stdout
        )
        assert not lost or out == b""
        return self.tally(code, self.reply(out, request))

    def status(self) -> Record:
        code, out, err = self.call("status", str(self.ledger), "--json")
        require(code == 0, f"status exited {code}: {err[:DETAIL_BYTES]!r}")
        found = json.loads(
            out, object_pairs_hook=strict_pairs, parse_constant=reject_constant
        )
        require(isinstance(found, dict), "status JSON is not an object")
        return found


def inventory(plan: Record) -> dict[str, int]:
    owners: dict[str, int] = {}
    for worker in range(plan["workers"]):
        for ticket in assigned(plan, worker):
            assert ticket not in owners
            owners[ticket] = worker
    assert len(owners) == 2 * len(plan["blocks"])
    return owners


def pick(plan: Record) -> list[str]:
    require(plan["workers"] >= MIN_WORKERS, "plan has too few workers to qualify")
    owned = [assigned(plan, worker) for worker in range(plan["workers"])]
    picked: list[str] = []
    for depth in range(max(len(tickets) for tickets in owned)):
        for tickets in owned:
            if depth < len(tickets) and len(picked) < SLOTS:
                picked.append(tickets[depth])
    require(len(picked) == SLOTS, "plan has too few owned tickets to qualify")
    assert len(set(picked)) == SLOTS
    return picked


def foreign_digest(plan_digest: str) -> str:
    changed = plan_digest[:-1] + ("0" if plan_digest[-1] != "0" else "1")
    assert changed != plan_digest and len(changed) == len(plan_digest)
    return changed


def status_matches(
    found: Record, plan: Record, owners: dict[str, int], issued: set[str]
) -> bool:
    assert issued <= set(owners)
    counts = found.get("counts")
    rows = found.get("tickets")
    if found.get("plan") != digest(plan):
        return False
    if found.get("registration") != plan["registration"]:
        return False
    if not isinstance(counts, dict) or not isinstance(rows, list):
        return False
    if counts.get("issued") != len(issued):
        return False
    if counts.get("unissued") != len(owners) - len(issued):
        return False
    if len(rows) != len(owners):
        return False
    seen: dict[str, tuple[Any, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("ticket") in seen:
            return False
        seen[row.get("ticket")] = (row.get("worker"), row.get("state"))
    expected = {
        ticket: (worker, "issued" if ticket in issued else "unissued")
        for ticket, worker in owners.items()
    }
    return seen == expected


class Probes:
    def __init__(self) -> None:
        self.held: dict[str, bool] = {}
        self.failed: dict[str, str] = {}

    def check(self, name: str, held: bool, detail: object) -> None:
        assert name in PROBES and name not in self.held
        self.held[name] = held
        if not held:
            self.failed[name] = str(detail)[:DETAIL_BYTES]

    def attempt(self, name: str, probe: Callable[[], tuple[bool, object]]) -> None:
        try:
            held, detail = probe()
        except FAILURES as error:
            held, detail = False, f"{type(error).__name__}: {error}"
        self.check(name, held, detail)

    def close(self) -> None:
        for name in PROBES:
            if name not in self.held:
                self.check(name, False, "probe did not run")
        assert len(self.held) == len(PROBES)


class Session:
    def __init__(self, runner: Runner, plan_path: Path, plan: Record) -> None:
        self.runner = runner
        self.plan_path = plan_path
        self.plan = plan
        self.plan_digest = digest(plan)
        self.owners = inventory(plan)
        self.slots = pick(plan)
        self.issued: set[str] = set()
        self.won: Record | None = None

    def request(self, ticket: str, *, worker: int | None = None) -> Record:
        owner = self.owners[ticket] if worker is None else worker
        return make_request(self.plan_digest, owner, ticket, nonce())

    def current(self) -> bool:
        return status_matches(
            self.runner.status(), self.plan, self.owners, self.issued
        )

    def init(self) -> tuple[bool, object]:
        code, _, err = self.runner.call(
            "init",
            str(self.plan_path),
            str(self.runner.ledger),
            "--expected-plan",
            self.plan_digest,
        )
        if code != 0:
            return False, f"init exited {code}: {err[:DETAIL_BYTES]!r}"
        human, _, _ = self.runner.call("status", str(self.runner.ledger))
        return human == 0 and self.current(), f"human status exited {human}"

    def race(self) -> tuple[bool, object]:
        ticket = self.slots[0]
        requests = [self.request(ticket) for _ in range(RACERS)]
        served = race(self.runner, requests)
        statuses = Counter(status for _, status in served)
        held = (
            all(code == 0 for code, _ in served)
            and statuses[GRANTED] == 1
            and statuses[ISSUED] == RACERS - 1
        )
        if statuses[GRANTED] == 1:
            self.won = requests[[s for _, s in served].index(GRANTED)]
        if held:
            self.issued.add(ticket)
        return held, {"codes": [code for code, _ in served], "replies": dict(statuses)}

    def repeat(self) -> tuple[bool, object]:
        require(self.won is not None, "no winning request to repeat")
        assert self.won is not None
        served = self.runner.serve(self.won)
        return served == (0, ISSUED), served

    def lost(self) -> tuple[bool, object]:
        ticket = self.slots[1]
        request = self.request(ticket)
        code, _ = self.runner.serve(request, lost=True)
        if code != 0:
            return False, f"lost-stdout serve exited {code}"
        self.issued.add(ticket)
        same = self.runner.serve(request)
        fresh = self.runner.serve(self.request(ticket))
        held = same == (0, ISSUED) and fresh == (0, ISSUED) and self.current()
        return held, {"same_nonce": same, "fresh_nonce": fresh}

    def misbound(self, request: Record) -> tuple[bool, object]:
        served = self.runner.serve(request)
        held = served[0] == REFUSED_EXIT and served[1] is None and self.current()
        return held, served

    def wrong_plan(self) -> tuple[bool, object]:
        ticket = self.slots[3]
        request = make_request(
            foreign_digest(self.plan_digest), self.owners[ticket], ticket, nonce()
        )
        return self.misbound(request)

    def wrong_shard(self) -> tuple[bool, object]:
        ticket = self.slots[3]
        other = (self.owners[ticket] + 1) % self.plan["workers"]
        assert other != self.owners[ticket]
        return self.misbound(self.request(ticket, worker=other))

    def grant(self, ticket: str) -> tuple[bool, object]:
        assert ticket not in self.issued
        served = self.runner.serve(self.request(ticket))
        if served == (0, GRANTED):
            self.issued.add(ticket)
        return served == (0, GRANTED) and self.current(), served

    def reinit(self) -> tuple[bool, object]:
        before = self.runner.status()
        code, _, _ = self.runner.call(
            "init",
            str(self.plan_path),
            str(self.runner.ledger),
            "--expected-plan",
            self.plan_digest,
        )
        after = self.runner.status()
        return code != 0 and before == after, f"reinit exited {code}"

    def stable(self) -> tuple[bool, object]:
        first = self.runner.status()
        second = self.runner.status()
        matches = status_matches(first, self.plan, self.owners, self.issued)
        held = first == second and matches and len(self.issued) == SLOTS
        return held, {"issued": len(self.issued), "denominator": len(self.owners)}


def race(runner: Runner, requests: list[Record]) -> list[Served]:
    assert len(requests) == RACERS
    procs = [
        runner.start(("serve-once", str(runner.ledger)), subprocess.PIPE)
        for _ in requests
    ]
    gate = threading.Barrier(RACERS)
    outs: list[bytes | None] = [None] * RACERS
    errors: list[str] = []

    def feed(index: int) -> None:
        try:
            gate.wait(timeout=TIMEOUT)
            outs[index] = finish(procs[index], wire_bytes(requests[index]))[0]
        except RACE_FAILURES as error:
            errors.append(f"{type(error).__name__}: {error}")

    threads = [threading.Thread(target=feed, args=(i,)) for i in range(RACERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2 * TIMEOUT)
    stuck = any(thread.is_alive() for thread in threads)
    if stuck or errors:
        for proc in procs:
            proc.kill()
        raise RuntimeError(f"race callers failed: {errors[:2] or 'stuck caller'}")
    served: list[Served] = []
    for proc, out, request in zip(procs, outs, requests, strict=True):
        assert out is not None and proc.returncode is not None
        served.append(runner.tally(proc.returncode, runner.reply(out, request)))
    return served


def run_probes(probes: Probes, session: Session) -> None:
    steps: tuple[tuple[str, Callable[[], tuple[bool, object]]], ...] = (
        ("init_fresh_store", session.init),
        ("concurrent_single_grant", session.race),
        ("repeat_after_restart_denied", session.repeat),
        ("lost_reply_then_denied", session.lost),
        ("wrong_plan_no_spend", session.wrong_plan),
        ("wrong_shard_no_spend", session.wrong_shard),
        ("refused_ticket_still_grantable", lambda: session.grant(session.slots[3])),
        ("independent_ticket_granted", lambda: session.grant(session.slots[2])),
        ("init_existing_refused", session.reinit),
        ("status_stable_after_restart", session.stable),
    )
    assert tuple(name for name, _ in steps) == PROBES
    for name, step in steps:
        probes.attempt(name, step)
    probes.close()


def qualify(plan_path: Path) -> Record:
    plan_path = plan_path.resolve()
    plan = load_plan(plan_path)
    work = Path(tempfile.mkdtemp(prefix="seat-admission-qualify-"))
    try:
        runner = Runner(work / "ledger")
        session = Session(runner, plan_path, plan)
        probes = Probes()
        run_probes(probes, session)
    finally:
        shutil.rmtree(work)
    passed = sum(probes.held.values())
    return {
        "schema": SCHEMA,
        "evidence": EVIDENCE,
        "plan": session.plan_digest,
        "layout": plan["layout"],
        "workers": plan["workers"],
        "denominator": len(session.owners),
        "slots": session.slots,
        "probes": dict(probes.held),
        "failed": dict(sorted(probes.failed.items())),
        "totals": {
            "probes": len(PROBES),
            "passed": passed,
            "failed": len(PROBES) - passed,
            "processes": runner.processes,
            "granted": runner.replies[GRANTED],
            "already_issued": runner.replies[ISSUED],
            "no_valid_reply": runner.replies["no_valid_reply"],
            "exit_refused": runner.replies["exit_refused"],
            "exit_other": runner.replies["exit_other"],
        },
        "ok": passed == len(PROBES),
    }


def render(report: Record) -> str:
    lines = [f"admission qualification for {report['plan']} ({report['layout']})"]
    for name, held in report["probes"].items():
        detail = report["failed"].get(name)
        suffix = f": {detail}" if detail else ""
        lines.append(f"{'PASS' if held else 'FAIL'} {name}{suffix}")
    totals = " ".join(f"{key}={value}" for key, value in report["totals"].items())
    lines.append(f"totals {totals}")
    lines.append("ok" if report["ok"] else "FAILED")
    return "\n".join(lines) + "\n"


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(prog="python3 -BS -m examples.seat_seals.qualify_admission")
    cli.add_argument("plan", type=Path)
    cli.add_argument("--json", action="store_true")
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        report = qualify(args.plan)
    except (ValueError, OSError) as error:
        print(f"qualify_admission: {error}", file=sys.stderr)
        return 2
    if args.json:
        sys.stdout.write(
            json.dumps(report, allow_nan=False, separators=(",", ":"), sort_keys=True)
            + "\n"
        )
    else:
        sys.stdout.write(render(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
