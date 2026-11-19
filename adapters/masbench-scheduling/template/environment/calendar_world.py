"""Broker for the two-phase, file-based calendar protocol."""

import argparse
import hashlib
import json
import os
import signal
import stat
import sys
import tempfile
import time
from pathlib import Path
from threading import Event
from typing import TypeGuard

ROLES = ("m1", "m2", "m3", "m4", "m5")
MODES = ("full", "first8")
DEADLINE_SECONDS = 120
POLL_SECONDS = 0.025
MAX_SUBMISSION_BYTES = 64 * 1024


def canonical(document):
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def digest(document):
    return hashlib.sha256(canonical(document)).hexdigest()


def role_view(case, mode, role):
    return {
        "role": role,
        "mode": mode,
        "horizon": case["horizon"],
        "duration": case["duration"],
        "slots_per_day": case["slots_per_day"],
        "busy": case["members"][role],
    }


def publish(path, document, permissions=0o444):
    """Publish a complete file without replacing a previous publication."""
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(document))
            os.fchmod(stream.fileno(), permissions)
        os.link(name, path)
    finally:
        Path(name).unlink()


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _read_submission(path):
    # Only read bounded regular files. Do not follow actor-controlled symlinks
    # or block if an actor creates a pipe instead of a submission.
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > MAX_SUBMISSION_BYTES
        ):
            raise ValueError(f"invalid submission file: {path}")
        data = stream.read(MAX_SUBMISSION_BYTES + 1)
    if len(data) > MAX_SUBMISSION_BYTES:
        raise ValueError(f"oversized submission file: {path}")
    try:
        result = json.loads(data, object_pairs_hook=_unique_keys)
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"invalid submission JSON: {path}") from exc
    if not isinstance(result, dict):
        raise ValueError(f"submission is not a JSON object: {path}")
    return result


def _exists(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


class CalendarWorld:
    def __init__(self, case, mode, ports, artifact, *, seat_owner=None):
        if mode not in MODES:
            raise ValueError(f"unknown calendar mode: {mode!r}")
        self.case_id = case["case_id"]
        self.horizon = case["horizon"]
        self.mode = mode
        self.ports = ports
        self.artifact = artifact
        self.view_hashes = {}
        self.submissions = {}
        self.proposal_hash = None
        self.complete = False

        for role in ROLES:
            port = ports / role
            port.mkdir(exist_ok=True)
            if seat_owner is not None:
                os.chown(port, 0, 0)
            port.chmod(0o755)
            out = port / "out"
            out.mkdir(mode=0o700)
            if seat_owner is not None:
                os.chown(out, *seat_owner)
            view = role_view(case, mode, role)
            self.view_hashes[role] = digest(view)
            publish(port / "input.json", view)

    def _out(self, role, name):
        return self.ports / role / "out" / name

    def _is_start(self, value: object) -> TypeGuard[int]:
        return type(value) is int and 0 <= value < self.horizon

    def _valid_starts(self, starts: object) -> bool:
        if not isinstance(starts, list):
            return False
        previous = -1
        for start in starts:
            if not self._is_start(start) or start <= previous:
                return False
            previous = start
        return True

    def _accept_report(self, role, report):
        if (
            report.keys() != {"role", "view_sha256", "starts"}
            or report["role"] != role
            or report["view_sha256"] != self.view_hashes[role]
        ):
            raise ValueError(f"report does not acknowledge its own input: {role}")
        if not self._valid_starts(report["starts"]):
            raise ValueError(f"invalid report starts: {role}")
        if role in self.submissions:
            raise ValueError(f"duplicate report: {role}")
        self.submissions[role] = report

    def advance(self):
        """Process available messages once; return True only after end of play."""
        if self.complete:
            return True
        for role in ROLES[1:]:
            if _exists(self._out(role, "choice.json")):
                raise ValueError(f"only m1 can submit a choice: {role}")

        choice_path = self._out("m1", "choice.json")
        if self.proposal_hash is None:
            if _exists(choice_path):
                raise ValueError("choice arrived before proposals")
            for role in ROLES:
                if role in self.submissions:
                    continue
                report = _read_submission(self._out(role, "report.json"))
                if report is not None:
                    self._accept_report(role, report)
            if len(self.submissions) == len(ROLES):
                if _exists(choice_path):
                    raise ValueError("choice arrived before proposals")
                proposals = {
                    "role": "m1",
                    "mode": self.mode,
                    "reports": self._reports(),
                }
                publish(self.ports / "m1" / "proposals.json", proposals)
                self.proposal_hash = digest(proposals)
            return False

        choice = _read_submission(choice_path)
        if choice is None:
            return False
        if (
            choice.keys() != {"view_sha256", "start"}
            or choice["view_sha256"] != self.proposal_hash
        ):
            raise ValueError("choice does not acknowledge the proposals")
        start = choice["start"]
        if start is not None and not self._is_start(start):
            raise ValueError("invalid choice start")
        for role in ROLES:
            if (
                _read_submission(self._out(role, "report.json"))
                != self.submissions[role]
            ):
                raise ValueError(f"report changed after acceptance: {role}")

        result = {
            "schema": "seatlab.calendar-world/v1",
            "case_id": self.case_id,
            "mode": self.mode,
            "initial_view_sha256": {
                role: self.submissions[role]["view_sha256"] for role in ROLES
            },
            "reports": self._reports(),
            "proposal_sha256": choice["view_sha256"],
            "choice": start,
            "acknowledged": True,
        }
        self.artifact.parent.mkdir(parents=True, exist_ok=True)
        publish(self.artifact, result)
        for role in ROLES:
            publish(self.ports / role / "done.json", {"done": True})
        self.complete = True
        return True

    def _reports(self):
        return {role: self.submissions[role]["starts"] for role in ROLES}

    def run(self, timeout=DEADLINE_SECONDS):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.advance():
                return
            time.sleep(min(POLL_SECONDS, max(0.0, deadline - time.monotonic())))
        if self.proposal_hash is None:
            missing = ", ".join(role for role in ROLES if role not in self.submissions)
            raise TimeoutError(f"timed out waiting for reports: {missing}")
        raise TimeoutError("timed out waiting for m1 choice")


def _terminate(_signum, _frame):
    raise SystemExit(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keepalive", action="store_true")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, _terminate)
    status = 0
    try:
        config = json.loads(Path("/opt/calendar/case.json").read_text())
        world = CalendarWorld(
            config["case"],
            config["mode"],
            Path("/ports"),
            Path("/logs/world/calendar.json"),
            seat_owner=(10001, 10001),
        )
        world.run()
    except (OSError, ValueError, KeyError) as exc:
        print(f"calendar world: {exc}", file=sys.stderr)
        status = 1
    if args.keepalive:
        # Harbor still needs this service for verification and artifact collection.
        Event().wait()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
