"""Reference actor. Read only this seat's exchange and write its own submissions."""

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

ROLES = ("m1", "m2", "m3", "m4", "m5")
DEADLINE_SECONDS = 120
POLL_SECONDS = 0.025


def _canonical(document):
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def _digest(document):
    return hashlib.sha256(_canonical(document)).hexdigest()


def report_starts(view):
    busy = set(view["busy"])
    duration = view["duration"]
    slots_per_day = view["slots_per_day"]
    starts = []
    for start in range(view["horizon"] - duration + 1):
        if start % slots_per_day + duration > slots_per_day:
            continue
        if any(tick in busy for tick in range(start, start + duration)):
            continue
        starts.append(start)
        if view["mode"] == "first8" and len(starts) == 8:
            break
    return starts


def choose_start(reports):
    common = set(reports["m1"])
    for role in ROLES[1:]:
        common.intersection_update(reports[role])
    return min(common, default=None)


def _wait_for(path, deadline):
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"timed out waiting for {path.name}")
        try:
            return json.loads(path.read_bytes())
        except FileNotFoundError:
            time.sleep(min(POLL_SECONDS, remaining))


def _submit(path, document):
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_canonical(document))
        os.link(name, path)
    finally:
        Path(name).unlink()


def main():
    role = os.environ.get("CALENDAR_ROLE")
    if role not in ROLES:
        raise SystemExit("CALENDAR_ROLE must name an active seat (m1 through m5)")
    exchange = Path("/exchange")
    deadline = time.monotonic() + DEADLINE_SECONDS
    view = _wait_for(exchange / "input.json", deadline)
    if view["role"] != role or view["mode"] not in ("full", "first8"):
        raise ValueError("input is not for this seat or is in an unknown mode")
    starts = report_starts(view)
    _submit(
        exchange / "out" / "report.json",
        {"role": role, "view_sha256": _digest(view), "starts": starts},
    )
    if role == "m1":
        proposals = _wait_for(exchange / "proposals.json", deadline)
        if (
            proposals["role"] != role
            or proposals["mode"] != view["mode"]
            or proposals["reports"][role] != starts
        ):
            raise ValueError("proposals do not include this seat's report and mode")
        _submit(
            exchange / "out" / "choice.json",
            {
                "view_sha256": _digest(proposals),
                "start": choose_start(proposals["reports"]),
            },
        )
    if _wait_for(exchange / "done.json", deadline) != {"done": True}:
        raise ValueError("invalid end-of-play message")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, OSError, ValueError) as exc:
        raise SystemExit(f"calendar actor: {exc}") from exc
