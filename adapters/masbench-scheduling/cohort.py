"""Admit at most one native calendar batch; reduce only retained study evidence."""

import argparse
import fcntl
import json
import os
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

STRATA = ((2, 40), (3, 60), (4, 60), (5, 40))
MODES = ("full", "first8")
STATUSES = ("unattempted", "incomplete", "failed", "invalid", "verified")
FIRST_NEW = 3
LAST_BATCH = max(count for _, count in STRATA)
POLL_SECONDS = 5


@dataclass(frozen=True)
class Reservation:
    minute: int = 15
    before: int = 120
    after: int = 150
    budget: int = 150

    def __post_init__(self):
        if not (
            0 <= self.minute < 60
            and min(self.before, self.after) >= 0
            and self.budget > 0
            and self.before + self.after + self.budget < 3600
        ):
            raise ValueError("invalid hourly reservation or batch duration")

    def wait(self, now):
        # UTC and the local canary clock have the same minute of the hour.
        first_blocked = self.minute * 60 - self.before - self.budget
        elapsed = (now - first_blocked) % 3600
        return max(0, self.before + self.budget + self.after - elapsed)


def batch_cases(index):
    return tuple(f"{stratum}/{index}" for stratum, count in STRATA if index <= count)


def _batch(root, index):
    return root / f"batch-{index:03d}"


def _claim(root, index):
    return _batch(root / ".claims", index)


def _present(path):
    return os.path.lexists(path)


def _directory(path, *, create=False):
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"expected a directory, not a symlink: {path}")
    return path.resolve(strict=True)


def _environment():
    # Pass only the settings needed by the local tools, not model credentials
    # or Python overrides. Do not put bytecode in the frozen source capsule.
    keys = ("HOME", "PATH", "TMPDIR", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE")
    return {
        **{key: os.environ[key] for key in keys if key in os.environ},
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _command(runner, action, path):
    return [sys.executable, "-B", str(runner), action, "--out", str(path)]


@contextmanager
def _try_lock(path):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("--lock must be a regular file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield None
        else:
            yield fd
    finally:
        # The study child retains this open description if the controller dies.
        # An explicit LOCK_UN would release its admission prematurely.
        os.close(fd)


def _hold(root, lock, reservation):
    if _present(root / "STOP"):
        return "STOP", 0
    if _present(root / "PAUSE"):
        return "PAUSE", POLL_SECONDS
    if _present(root / "PRIORITY") or _present(lock.parent / "PRIORITY"):
        return "PRIORITY", POLL_SECONDS
    wait = reservation.wait(time.time())
    if wait:
        return "hourly canary reservation", min(wait, POLL_SECONDS)
    return None, 0


@contextmanager
def _admission(root, lock, reservation, batch_name):
    last_hold = None
    while True:
        reason, wait = _hold(root, lock, reservation)
        if reason is None:
            with _try_lock(lock) as fd:
                if fd is None:
                    reason, wait = "host admission busy", POLL_SECONDS
                else:
                    reason, wait = _hold(root, lock, reservation)
                    if reason is None:
                        yield fd
                        return
        if reason == "STOP":
            print(f"STOP: no new admission at {batch_name}", file=sys.stderr)
            yield None
            return
        if reason != last_hold:
            print(f"{batch_name}: waiting for {reason}", file=sys.stderr, flush=True)
            last_hold = reason
        time.sleep(wait)


def _identities(index):
    return [(case_id, mode) for case_id in batch_cases(index) for mode in MODES]


def _native_id(value):
    identifier = UUID(value)
    if identifier.version != 4:
        raise ValueError("native trial UUID is not version 4")
    return str(identifier)


def _registered_rows(runner, path, index):
    result = subprocess.run(
        _command(runner, "collect", path),
        cwd=runner.parent,
        env=_environment(),
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError("frozen study collect rejected the registration")
    document = json.loads(result.stdout)
    if document["schema"] != "seatlab.calendar-study-result/v1":
        raise ValueError("unexpected study result schema")
    indexed = {}
    for row in document["cells"]:
        cell = row["case_id"], row["mode"]
        if cell in indexed:
            raise ValueError("duplicate case and mode in study result")
        if row["status"] not in STATUSES:
            raise ValueError("unexpected study cell status")
        indexed[cell] = dict(row)
        if "native_id" in row:
            indexed[cell]["native_id"] = _native_id(row["native_id"])
        if row["status"] == "verified":
            observed = row["observed"]
            choice = observed["choice"]
            expected_choice = row["source_expected"]["choice"]
            feasible = observed["physically_feasible"]
            if not (
                "native_id" in row
                and type(observed["protocol_match"]) is bool
                and type(observed["official_exact_match"]) is bool
                and (feasible is None or type(feasible) is bool)
                and (choice is None or type(choice) is int)
                and (expected_choice is None or type(expected_choice) is int)
            ):
                raise ValueError("unexpected verified study score")
    expected = _identities(index)
    if set(indexed) != set(expected):
        raise ValueError("study result does not match the exact batch cases and modes")
    return [indexed[cell] for cell in expected]


def _missing(index, status, reason):
    return [
        {"case_id": case_id, "mode": mode, "status": status, "reason": reason}
        for case_id, mode in _identities(index)
    ]


def _batch_rows(runner, path, claim, index):
    if _present(claim) and (claim.is_symlink() or not claim.is_dir()):
        return _missing(index, "invalid", "claim path is not a plain directory")
    if not _present(path):
        if _present(claim):
            return _missing(index, "incomplete", "claim has no study directory")
        return _missing(index, "unattempted", "no study directory")
    if path.is_symlink() or not path.is_dir():
        return _missing(index, "invalid", "batch path is not a plain directory")
    if not all(_present(path / name) for name in ("plan.json", "source.json")):
        return _missing(index, "incomplete", "study registration is incomplete")
    try:
        return _registered_rows(runner, path, index)
    except (AttributeError, KeyError, OSError, TypeError, ValueError) as exc:
        reason = str(exc) if type(exc) is ValueError else "malformed study result"
        return _missing(index, "invalid", reason)


def _launch(runner, dataset, harbor, out, claim, index, fd):
    claim.mkdir(mode=0o700)
    command = [
        *_command(runner, "run", out),
        "--dataset",
        str(dataset),
        "--harbor",
        str(harbor),
        "--cases",
        ",".join(batch_cases(index)),
        "--modes",
        ",".join(MODES),
    ]
    with (
        (claim / "run.log").open("xb") as log,
        subprocess.Popen(
            command,
            cwd=runner.parent,
            env=_environment(),
            stdout=log,
            stderr=subprocess.STDOUT,
            pass_fds=(fd,),
            start_new_session=True,
        ) as process,
    ):
        status = process.wait()
    with (claim / "returncode").open("x") as receipt:
        receipt.write(f"{status}\n")
    return status


def _successful(rows):
    return all(
        row["status"] == "verified"
        and row["observed"]["protocol_match"]
        and row["observed"]["choice"] == row["source_expected"]["choice"]
        for row in rows
    )


def _lock_path(path, root, protected):
    lock = path.parent.resolve(strict=True) / path.name
    if any(lock.is_relative_to(directory) for directory in protected):
        raise ValueError("--lock must not write into the dataset or frozen runner")
    if lock.is_relative_to(root):
        entry = lock.relative_to(root).parts
        if (
            not entry
            or entry[0] in {".claims", "STOP", "PAUSE", "PRIORITY"}
            or entry[0].startswith("batch-")
        ):
            raise ValueError("--lock overlaps the cohort output or controls")
    if lock.name == "PRIORITY":
        raise ValueError("--lock overlaps the host PRIORITY marker")
    return lock


def run(args):
    reservation = Reservation(
        args.reserve_hourly_minute,
        args.reserve_before_seconds,
        args.reserve_after_seconds,
        args.max_batch_seconds,
    )
    if not FIRST_NEW <= args.start_index <= args.end_index <= LAST_BATCH:
        raise ValueError(f"run range must be within {FIRST_NEW}–{LAST_BATCH}")
    dataset = _directory(args.dataset.resolve(strict=True))
    harbor = args.harbor.resolve(strict=True)
    if not harbor.is_file() or not os.access(harbor, os.X_OK):
        raise ValueError("--harbor must be an executable file")
    protected = (dataset, args.runner.parent)
    if any(args.root.resolve().is_relative_to(directory) for directory in protected):
        raise ValueError("--root must not write into the dataset or frozen runner")
    root = _directory(args.root, create=True)
    lock = _lock_path(args.lock, root, protected)
    if _present(root / ".claims"):
        _directory(root / ".claims")

    failed = False
    for index in range(args.start_index, args.end_index + 1):
        out, claim = _batch(root, index), _claim(root, index)
        with _admission(root, lock, reservation, out.name) as fd:
            if fd is None:
                return int(failed)
            if _present(out) or _present(claim):
                receipt = claim / "returncode"
                rows = _batch_rows(args.runner, out, claim, index)
                good = _successful(rows) and (
                    not _present(receipt)
                    or (
                        not receipt.is_symlink()
                        and receipt.is_file()
                        and receipt.read_text() == "0\n"
                    )
                )
                status = "verified" if good else "not successful"
                print(f"{out.name}: retained; {status}", file=sys.stderr)
            else:
                _directory(claim.parent, create=True)
                print(f"{out.name}: starting", file=sys.stderr, flush=True)
                started = time.monotonic()
                status = _launch(args.runner, dataset, harbor, out, claim, index, fd)
                good = status == 0
                if time.monotonic() - started > reservation.budget:
                    print(
                        f"{out.name}: exceeded the batch estimate; stopping admission",
                        file=sys.stderr,
                    )
                    return 1
        if not good:
            failed = True
            print(
                f"{out.name}: inspect retained evidence before continuing",
                file=sys.stderr,
            )
            if not args.continue_on_failure:
                return 1
    return int(failed)


def _select_input(root, index, external):
    local = _batch(root, index)
    if external is None:
        return local
    external = _directory(external)
    if _present(local) and (local.is_symlink() or local != external):
        raise ValueError(f"both local and external inputs supplied for {local.name}")
    return external


def _mode_summary(rows):
    statuses = {
        status: sum(row["status"] == status for row in rows) for status in STATUSES
    }
    verified = [row for row in rows if row["status"] == "verified"]
    scores = [row["observed"] for row in verified]
    choices = [score for score in scores if score["choice"] is not None]
    return {
        "planned": len(rows),
        **statuses,
        "missing": len(rows) - len(verified),
        "choice_agreement": {
            "passes": sum(
                row["observed"]["choice"] == row["source_expected"]["choice"]
                for row in verified
            ),
            "denominator": len(verified),
        },
        "protocol_matches": sum(score["protocol_match"] for score in scores),
        "official_exact_match": {
            "passes": sum(score["official_exact_match"] for score in scores),
            "denominator": len(verified),
        },
        "scheduled_official_exact_match": {
            "passes": sum(score["official_exact_match"] for score in choices),
            "denominator": len(choices),
        },
        "physically_feasible": {
            "passes": sum(score["physically_feasible"] is True for score in choices),
            "denominator": len(choices),
        },
        "no_choice": len(verified) - len(choices),
    }


def collect(runner, root, smoke, batch_two=None):
    root = _directory(root)
    if _present(root / ".claims"):
        _directory(root / ".claims")
    first = _select_input(root, 1, smoke)
    second = _select_input(root, 2, batch_two)
    rows, owners, warnings = [], {}, []
    for index in range(1, LAST_BATCH + 1):
        path = first if index == 1 else second if index == 2 else _batch(root, index)
        batch_rows = _batch_rows(runner, path, _claim(root, index), index)
        for row in batch_rows:
            row["batch"] = _batch(root, index).name
            rows.append(row)
            if "native_id" in row:
                owners.setdefault(row["native_id"], []).append(row)
    for native_id, duplicates in owners.items():
        if len(duplicates) > 1:
            warnings.append(
                {"reason": "duplicate native trial UUID", "native_id": native_id}
            )
            for row in duplicates:
                row.pop("observed", None)
                row.update(status="invalid", reason="duplicate native trial UUID")
    for row in rows:
        if row["status"] == "verified" and (
            not row["observed"]["protocol_match"]
            or row["observed"]["choice"] != row["source_expected"]["choice"]
        ):
            warnings.append(
                {
                    "reason": "native decision or reports differ from source",
                    "case_id": row["case_id"],
                    "mode": row["mode"],
                }
            )
    missing = {
        status: sum(row["status"] == status for row in rows)
        for status in STATUSES
        if status != "verified"
    }
    return {
        "schema": "seatlab.calendar-cohort-result/v1",
        "planned": len(rows),
        "summary": {
            mode: _mode_summary([row for row in rows if row["mode"] == mode])
            for mode in MODES
        },
        "missing": {"total": sum(missing.values()), **missing},
        "warnings": warnings,
        "cells": rows,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    launch = commands.add_parser("run", help="admit only fresh batch directories")
    reduce = commands.add_parser("collect", help="check retained native evidence")
    for command in (launch, reduce):
        command.add_argument("--runner", required=True, type=Path)
        command.add_argument("--root", required=True, type=Path)
    launch.add_argument("--dataset", required=True, type=Path)
    launch.add_argument("--harbor", required=True, type=Path)
    launch.add_argument(
        "--lock", required=True, type=Path, help="shared host admission lock"
    )
    launch.add_argument("--start-index", type=int, default=FIRST_NEW)
    launch.add_argument("--end-index", type=int, default=LAST_BATCH)
    launch.add_argument("--reserve-hourly-minute", type=int, default=15)
    launch.add_argument("--reserve-before-seconds", type=int, default=120)
    launch.add_argument("--reserve-after-seconds", type=int, default=150)
    launch.add_argument("--max-batch-seconds", type=int, default=150)
    launch.add_argument(
        "--continue-on-failure",
        action="store_true",
        help="admit later batches, never retry",
    )
    reduce.add_argument(
        "--smoke", required=True, type=Path, help="private batch-001 study directory"
    )
    reduce.add_argument(
        "--batch-two", type=Path, help="private batch-002 directory if outside --root"
    )
    args = parser.parse_args(argv)
    try:
        args.runner = args.runner.resolve(strict=True)
        if not args.runner.is_file():
            raise ValueError("--runner must be the frozen study script")
        if args.action == "run":
            return run(args)
        report = collect(args.runner, args.root, args.smoke, args.batch_two)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"cohort: {exc}\n")
    if report["warnings"]:
        print(
            f"cohort: {len(report['warnings'])} UUID or source warnings in the report",
            file=sys.stderr,
        )
    print(json.dumps(report, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
