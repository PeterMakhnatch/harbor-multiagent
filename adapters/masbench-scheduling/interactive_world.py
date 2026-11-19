"""Relay bound queries and seat replies without deciding the meeting start."""

import argparse
import json
import os
import signal
import stat
import sys
import time
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from template.environment import calendar_world as base
else:
    import calendar_base as base

MODE = "cursor"
SCHEMA = "seatlab.calendar-interactive-world/v1"
MAX_ROUNDS = 720
MAX_MESSAGE_BYTES = 1024


def _stamp(metadata):
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


class InteractiveWorld:
    def __init__(self, case, mode, ports, artifact, *, seat_owner=None):
        if mode != MODE:
            raise ValueError(f"unknown interactive calendar mode: {mode!r}")
        self.case_id = case["case_id"]
        self.horizon = case["horizon"]
        if type(self.horizon) is not int or self.horizon < 1:
            raise ValueError("invalid calendar horizon")
        self.ports = ports
        self.artifact = artifact
        self.complete = False
        self._views = {}
        self._acknowledgments = {}
        self._sources = {role: {} for role in base.ROLES}
        self._rounds = []
        self._query = None
        self._query_sha256 = None
        self._replies = {}

        for role in base.ROLES:
            port = ports / role
            port.mkdir(exist_ok=True)
            if seat_owner is not None:
                os.chown(port, 0, 0)
            port.chmod(0o755)
            out = port / "out"
            out.mkdir(mode=0o700)
            if seat_owner is not None:
                os.chown(out, *seat_owner)
            out.chmod(0o700)
            view = base.role_view(case, mode, role)
            self._views[role] = base.digest(view)
            base.publish(port / "input.json", view)

    def _out(self, role, name):
        return self.ports / role / "out" / name

    def _is_tick(self, value, *, bound=0):
        return type(value) is int and bound <= value < self.horizon

    def _available(self, role, expected):
        sources = self._sources[role]
        available = set()
        retained = stages = 0
        with os.scandir(self.ports / role / "out") as entries:
            # One staging file may coexist with a linked, atomically published message.
            limit = len(sources) + len(expected) + 1
            for count, entry in enumerate(entries, 1):
                if count > limit:
                    raise ValueError(f"too many mailbox entries: {role}")
                name = entry.name
                if name in sources:
                    retained += 1
                elif name in expected:
                    available.add(name)
                elif name.startswith(".submit-") and stages == 0:
                    stages += 1
                else:
                    raise ValueError(f"unexpected or premature message: {role}/{name}")
        if retained != len(sources):
            raise ValueError(f"mailbox lost an accepted message: {role}")
        return available

    def _read(self, role, name):
        path = self._out(role, name)
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_MESSAGE_BYTES:
            raise ValueError(f"invalid submission file: {role}/{name}")
        if before.st_nlink == 2:
            # The actor still has a staging link. Its removal changes ctime.
            return None
        if before.st_nlink != 1:
            raise ValueError(f"invalid submission links: {role}/{name}")
        document = base._read_submission(path)
        stamp = _stamp(before)
        if document is None or _stamp(path.lstat()) != stamp:
            raise ValueError(f"message changed during acceptance: {role}/{name}")
        return document, stamp

    def _check_source(self, role, name, stamp=None):
        path = self._out(role, name)
        if stamp is None:
            stamp = self._sources[role][name]
        try:
            unchanged = _stamp(path.lstat()) == stamp
        except FileNotFoundError:
            unchanged = False
        if not unchanged:
            raise ValueError(f"message changed after acceptance: {role}/{name}")

    def _accept_query(self, number, name):
        if number > MAX_ROUNDS:
            raise ValueError("too many interactive calendar rounds")
        message = self._read("m1", name)
        if message is None:
            return
        submission, stamp = message
        previous = self._rounds[-1] if self._rounds else None
        prior_sha256 = previous["bundle_sha256"] if previous else self._views["m1"]
        if (
            submission.keys() != {"round", "bound", "view_sha256"}
            or type(submission["round"]) is not int
            or submission["round"] != number
            or submission["view_sha256"] != prior_sha256
        ):
            raise ValueError("query does not acknowledge the current coordinator view")
        bound = submission["bound"]
        if not self._is_tick(bound, bound=previous["bound"] + 1 if previous else 0):
            raise ValueError("query bound must increase within the calendar horizon")
        query = {"round": number, "bound": bound}
        self._sources["m1"][name] = stamp
        for role in base.ROLES:
            base.publish(self.ports / role / name, query)
        self._query = query
        self._query_sha256 = base.digest(query)

    def _accept_replies(self, query_name, reply_name, available):
        query = self._query
        if query is None:
            raise RuntimeError("cannot collect replies without an active query")
        self._check_source("m1", query_name)
        for role in base.ROLES:
            if role in self._replies:
                self._check_source(role, reply_name)
                continue
            if reply_name not in available[role]:
                continue
            message = self._read(role, reply_name)
            if message is None:
                continue
            reply, stamp = message
            if (
                reply.keys()
                != {"role", "round", "next", "initial_sha256", "query_sha256"}
                or reply["role"] != role
                or type(reply["round"]) is not int
                or reply["round"] != query["round"]
                or reply["initial_sha256"] != self._views[role]
                or reply["query_sha256"] != self._query_sha256
            ):
                raise ValueError(
                    f"reply does not bind its seat, input and query: {role}"
                )
            tick = reply["next"]
            if tick is not None and not self._is_tick(tick, bound=query["bound"]):
                raise ValueError(f"invalid reply tick: {role}")
            self._acknowledgments[role] = reply["initial_sha256"]
            self._sources[role][reply_name] = stamp
            self._replies[role] = tick

        if len(self._replies) == len(base.ROLES):
            self._check_source("m1", query_name)
            for role in base.ROLES:
                self._check_source(role, reply_name)
            self._available("m1", set())
            bundle = {**query, "replies": self._replies}
            number = query["round"]
            base.publish(self.ports / "m1" / f"round-{number:03d}.json", bundle)
            self._rounds.append(
                {
                    **bundle,
                    "query_sha256": self._query_sha256,
                    "bundle_sha256": base.digest(bundle),
                }
            )
            self._query = self._query_sha256 = None
            self._replies = {}

    def _finish(self, name):
        if not self._rounds:
            raise ValueError("choice arrived before the first round bundle")
        message = self._read("m1", name)
        if message is None:
            return False
        choice, stamp = message
        current = self._rounds[-1]
        if (
            choice.keys() != {"round", "start", "view_sha256"}
            or type(choice["round"]) is not int
            or choice["round"] != current["round"]
            or choice["view_sha256"] != current["bundle_sha256"]
        ):
            raise ValueError("choice does not acknowledge the current round bundle")
        start = choice["start"]
        if start is not None and not self._is_tick(start):
            raise ValueError("invalid choice tick")

        for role in base.ROLES:
            for accepted in self._sources[role]:
                self._check_source(role, accepted)
        self._available("m1", {name})
        self._check_source("m1", name, stamp)
        result = {
            "schema": SCHEMA,
            "case_id": self.case_id,
            "mode": MODE,
            "initial_view_sha256": self._acknowledgments,
            "rounds": self._rounds,
            "choice": start,
            "choice_view_sha256": choice["view_sha256"],
            "acknowledged": True,
        }
        self.artifact.parent.mkdir(parents=True, exist_ok=True)
        base.publish(self.artifact, result)
        for role in base.ROLES:
            base.publish(self.ports / role / "done.json", {"done": True})
        self.complete = True
        return True

    def advance(self):
        """Process one mailbox phase, without republishing completed transitions."""
        if self.complete:
            return True
        number = len(self._rounds) + 1
        query = f"query-{number:03d}.json"
        reply = f"reply-{number:03d}.json"
        choice = "choice.json"
        if self._query is not None:
            available = {role: self._available(role, {reply}) for role in base.ROLES}
            self._accept_replies(query, reply, available)
        else:
            actions = self._available("m1", {query, choice})
            for role in base.ROLES[1:]:
                self._available(role, set())
            if len(actions) > 1:
                raise ValueError("ambiguous coordinator actions: query and choice")
            if choice in actions:
                return self._finish(choice)
            if query in actions:
                self._accept_query(number, query)
        return False

    def run(self, timeout=base.DEADLINE_SECONDS):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.advance():
                return
            time.sleep(min(base.POLL_SECONDS, max(0.0, deadline - time.monotonic())))
        if self._query is None:
            raise TimeoutError("timed out waiting for a coordinator action")
        missing = ", ".join(role for role in base.ROLES if role not in self._replies)
        raise TimeoutError(
            f"timed out waiting for round {self._query['round']} replies: {missing}"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keepalive", action="store_true")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, base._terminate)
    status = 0
    try:
        config = json.loads(Path("/opt/calendar/case.json").read_text(encoding="utf-8"))
        world = InteractiveWorld(
            config["case"],
            config["mode"],
            Path("/ports"),
            Path("/logs/artifacts/world.calendar.json"),
            seat_owner=(10001, 10001),
        )
        world.run()
    except (OSError, ValueError, KeyError) as exc:
        print(f"interactive calendar world: {exc}", file=sys.stderr)
        status = 1
    if args.keepalive:
        Event().wait()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
