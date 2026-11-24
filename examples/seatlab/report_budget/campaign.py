from __future__ import annotations

import argparse
import fcntl
import os
import subprocess
import sys
import threading
from collections import Counter
from pathlib import Path

from examples.seatlab.market_handoff import timing_seat
from examples.seatlab.market_handoff.timing_study import store
from examples.seatlab.report_budget import domain, study

WORKERS = 2
STUDY = ("-B", "-m", "examples.seatlab.report_budget.study", "run")
TURNS = (("sales", "sales"), ("finance", "finance-price"))
UNSENT_LIMIT = 2
LOGS = "campaign-logs"
LOCK = "campaign.lock"
HALT = "campaign-stop.json"
HALT_SCHEMA = "seatlab.report-budget-campaign-stop/v1"
REFUSED, UNSENT = "admission_refused", "unsent_limit"
STATES = ("sent", "unsent", "refused", "predecision", "error", "preexisting", "claimed")
LOCK_FLAGS = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC


class Refused(Exception):
    pass


def arms(world: int) -> tuple[int, int]:
    short, long = domain.TARGETS
    return (short, long) if world % 2 == 0 else (long, short)


def schedule(worker: int) -> list[tuple[int, int]]:
    if worker not in range(WORKERS):
        raise ValueError("unknown campaign worker")
    return [
        (world, target)
        for world in range(domain.WORLD_COUNT)
        if world % WORKERS == worker
        for target in arms(world)
    ]


def footprint(cell: Path) -> str:
    if (cell / "predecision.json").is_file():
        return "predecision"
    turns = [cell / "native" / "private" / seat / purpose for seat, purpose in TURNS]
    notes = [timing_seat.admission(turn) for turn in turns]
    if any(note is not None and note["outcome"] == "refused" for note in notes):
        return "refused"
    if any(os.path.lexists(turn / turn.name / "request.json") for turn in turns):
        return "sent"
    return "unsent"


def halted(root: Path) -> bool:
    return os.path.lexists(root / HALT)


def halt(root: Path, cause: str, cell: str) -> None:
    if cause not in (REFUSED, UNSENT):
        raise ValueError("unknown policy stop")
    try:
        store(root / HALT, {"schema": HALT_SCHEMA, "cause": cause, "cell": cell})
    except FileExistsError:
        pass


def acquire(root: Path) -> int:
    fd = os.open(root / LOCK, LOCK_FLAGS, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise Refused("another campaign controller holds this root") from None
    return fd


def preflight(root: Path) -> None:
    for world, target in study.slots():
        cell = study.cell(root, world, target)
        if cell.is_dir() and footprint(cell) == "refused":
            halt(root, REFUSED, cell.name)
    if halted(root):
        raise Refused(f"policy stop recorded in {HALT}; no new dispatch from this root")


class Campaign:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.lock = threading.Lock()
        self.stop: str | None = None
        self.unsent = 0
        self.counts = {target: Counter() for target in domain.TARGETS}
        self.logs = args.root / LOGS
        self.logs.mkdir(mode=0o700, exist_ok=True)

    def child(self, world: int, target: int) -> list[str]:
        a = self.args
        return [
            sys.executable,
            *STUDY,
            *("--stock", str(a.stock), "--root", str(a.root)),
            *("--world", str(world), "--target", str(target)),
            *("--native-python", str(a.native_python), "--slot", str(a.slot)),
            *("--client", str(a.client)),
        ]

    def launch(self, world: int, target: int, cell: Path) -> str:
        with (self.logs / f"{cell.name}.log").open("ab") as log:
            proc = subprocess.run(
                self.child(world, target),
                cwd=study.ROOT,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                check=False,
            )
        if proc.returncode == study.CLAIMED:
            return "claimed"
        state = footprint(cell) if cell.is_dir() else "unsent"
        if proc.returncode != 0 and state == "unsent":
            return "error"
        return state

    def policy(self, cause: str, cell: Path) -> None:
        halt(self.args.root, cause, cell.name)
        self.stop = self.stop or f"{cell.name}: {cause}"

    def settle(self, cell: Path, target: int, state: str) -> None:
        with self.lock:
            self.counts[target][state] += 1
            if state == "refused":
                self.policy(REFUSED, cell)
            elif state == "sent":
                self.unsent = 0
            elif state in ("unsent", "error"):
                self.unsent += 1
                if self.unsent >= UNSENT_LIMIT:
                    self.policy(UNSENT, cell)
            line = "; ".join(
                f"t{t:03d} "
                + " ".join(
                    f"{s}={self.counts[t][s]}" for s in STATES if self.counts[t][s]
                )
                for t in domain.TARGETS
            )
            print(f"{cell.name} {state} | {line}", flush=True)

    def dispatchable(self) -> bool:
        with self.lock:
            if self.stop is None and halted(self.args.root):
                self.stop = f"policy stop recorded in {HALT}"
            return self.stop is None

    def work(self, worker: int) -> None:
        for world, target in schedule(worker):
            if not self.dispatchable():
                return
            cell = study.cell(self.args.root, world, target)
            if cell.exists():
                self.settle(cell, target, "preexisting")
                continue
            try:
                state = self.launch(world, target, cell)
            except OSError:
                state = "error"
            self.settle(cell, target, state)

    def run(self) -> int:
        threads = [
            threading.Thread(target=self.work, args=(worker,), name=f"budget-{worker}")
            for worker in range(WORKERS)
        ]
        for thread in threads:
            thread.start()
        while any(thread.is_alive() for thread in threads):
            try:
                for thread in threads:
                    thread.join()
            except KeyboardInterrupt:
                with self.lock:
                    self.stop = self.stop or "operator interrupt"
        print(
            f"stopped: {self.stop}" if self.stop else "every registered slot reached",
            flush=True,
        )
        return 1 if self.stop else 0


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Issue every live report-budget slot once with two operators."
    )
    for name in ("stock", "root", "native-python", "client", "slot"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    args.stock = args.stock.resolve(strict=True)
    args.root = study.cell(args.root, 0, domain.TARGETS[0]).parent
    args.native_python = study.executable(args.native_python, "native Python")
    args.slot = study.executable(args.slot, "the Docker slot wrapper")
    args.client = study.executable(args.client, "trusted agy")
    return args


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    args = parse(argv)
    try:
        held = acquire(args.root)
        preflight(args.root)
    except Refused as exc:
        print(f"refusing to start: {exc}", file=sys.stderr)
        return 2
    try:
        return Campaign(args).run()
    finally:
        os.close(held)


if __name__ == "__main__":
    raise SystemExit(main())
