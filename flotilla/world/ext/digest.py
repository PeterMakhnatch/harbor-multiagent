"""Finishing desk referee; the reward is a macro-average over final-shift rule clauses."""

from collections import Counter
from typing import ClassVar

OUTCOMES = ("N", "E", "S", "W", "P", "C", "X", "F", "Q")
CHECKS = ("credit", "press", "freight", "ink")


class Digest:
    routes: ClassVar = {"/work": ("GET",), "/note": ("GET",), "/check": ("POST",), "/act": ("POST",),
                        "/note/check": ("POST",), "/note/seal": ("POST",)}

    def __init__(self, task, host):
        self.host = host
        self.seats = task["seats"]
        self.params = task["params"]
        self.rows = {s: {row["job"]["id"]: row for row in rows} for s, rows in self.params["batches"].items()}
        self.order = {s: [row["job"]["id"] for row in rows] for s, rows in self.params["batches"].items()}
        self.pages = {s: [] for s in self.seats}
        self.actions = {s: set() for s in self.seats}
        self.notes = {}
        self.read = set()

    def done(self, seat):
        return len(self.actions[seat]) == len(self.rows[seat])

    def note(self, seat):
        if seat not in self.notes:
            return {}
        text = self.notes[seat]
        self.host.record("note_read", seat, {"chars": len(text)})
        if seat not in self.read:
            self.read.add(seat)
            self.host.record("note_delivered", seat, {"chars": len(text), "id": f"note-{seat}"})
        return {"note": text}

    def work(self, seat):
        page = [j for j in self.order[seat] if j not in self.actions[seat]][:5]
        self.pages[seat] = page
        self.host.record("work", seat, {"jobs": page})
        reply = {"role": self.params["roles"][seat], "jobs": [self.rows[seat][j]["job"] for j in page],
                 "done": not page}
        if seat not in self.read:
            reply.update(self.note(seat))
        if not page and seat == self.seats[-1]:
            self.host.freeze()
        return 200, reply

    def check(self, seat, body):
        job, what = body.get("job"), body.get("what")
        if not isinstance(job, str) or what not in CHECKS:
            return 400, {"error": "send a job ID and check name"}
        if job not in self.pages[seat] or job in self.actions[seat]:
            return 409, {"error": "job is not open on your page"}
        reply = self.rows[seat][job]["replies"][what]
        data = {"job": job, "what": what, "reply": reply}
        self.host.record("check", seat, data)
        return 200, data

    def act(self, seat, body):
        actions = body.get("actions")
        if not isinstance(actions, list) or not 1 <= len(actions) <= 5:
            return 400, {"error": "send 1 to 5 actions"}
        moves = {}
        for action in actions:
            if not isinstance(action, dict) or not isinstance(action.get("job"), str):
                return 400, {"error": "each action needs a job ID"}
            job, kind = action["job"], action.get("do")
            if kind == "send" and action.get("to") in OUTCOMES:
                outcome = action["to"]
            elif kind == "hold" and "to" not in action:
                outcome = "hold"
            else:
                return 400, {"error": "use send with a destination, or hold without a destination"}
            if job in moves or job in self.actions[seat] or job not in self.pages[seat]:
                return 409, {"error": "job is not open on your page or appears twice"}
            moves[job] = outcome
        for job, outcome in moves.items():
            self.actions[seat].add(job)
            self.host.record("act", seat, {"job": job, "outcome": outcome})
        return 200, {"accepted": list(moves)}

    def seal(self, seat, body, measure):
        if seat == self.seats[-1]:
            return 403, {"error": "no next shift"}
        text = body.get("text")
        if not isinstance(text, str):
            return 400, {"error": "send a text string"}
        cap = self.params["cap"]
        size = {"chars": len(text), "limit": cap}
        if measure:
            self.host.record("note_check", seat, size)
            return 200, size
        if len(text) > cap:
            return 413, size
        if not self.done(seat):
            return 409, {"error": "finish your jobs before sealing"}
        next_seat = self.seats[self.seats.index(seat) + 1]
        self.notes[next_seat] = text
        forwarded = seat in self.notes and self.notes[seat] == text
        self.host.record("note_seal", seat, size | {"copy_forward": forwarded, "id": f"note-{next_seat}"})
        self.host.advance()
        self.host.retire(seat)
        self.host.activate(next_seat)
        return 201, size

    def handle(self, seat, method, path, body, query):
        if path == "/work":
            return self.work(seat)
        if path == "/note":
            return 200, self.note(seat)
        if path == "/check":
            return self.check(seat, body)
        if path == "/act":
            return self.act(seat, body)
        if path in ("/note/check", "/note/seal"):
            return self.seal(seat, body, path == "/note/check")
        raise ValueError(f"unexpected route {path}")


def create(task, host):
    return Digest(task, host)


def score(task, events):
    checks, moves = {}, {}
    read_before, note_sizes, copies = set(), [], 0
    for event in events:
        seat, kind, data = event["seat"], event["kind"], event["data"]
        if kind == "check":
            checks.setdefault((seat, data["job"]), set()).add(data["what"])
        elif kind == "act":
            key = (seat, data["job"])
            moves.setdefault(key, (data["outcome"], frozenset(checks.get(key, ())), seat in read_before))
        elif kind == "note_read":
            read_before.add(seat)
        elif kind == "note_seal":
            note_sizes.append(data["chars"] / data["limit"])
            copies += data["copy_forward"]
    accuracy, totals, correct = {}, Counter(), Counter()
    omissions = exception_reads = 0
    final = task["seats"][-1]
    for seat, rows in task["params"]["batches"].items():
        good = 0
        for row in rows:
            move, before, read = moves.get((seat, row["job"]["id"]), (None, (), False))
            missing = set(row["checks"]) - set(before)
            ok = move == row["outcome"] and not missing
            good += ok
            if seat == final:
                totals[row["cell"]] += 1
                correct[row["cell"]] += ok
                omissions += bool(missing)
                exception_reads += read and not row["cell"].endswith(".0")
        accuracy[seat] = good / len(rows) if rows else None
    cells = {cell: correct[cell] / total for cell, total in totals.items()}
    depths = {depth: sum(v for c, v in cells.items() if c.endswith(f".{depth}")) /
              sum(c.endswith(f".{depth}") for c in cells) for depth in range(4)
              if any(c.endswith(f".{depth}") for c in cells)}
    rules = {rid: all(v == 1 for c, v in cells.items() if c.startswith(f"{rid}."))
             for rid in {c.split(".")[0] for c in cells} - {"0"}}
    return {"reward": sum(cells.values()) / len(cells), "exact": all(v == 1 for v in cells.values()),
            "cells": cells, "depth_accuracy": depths, "rule_survival": rules, "check_omissions": omissions,
            "exception_jobs_with_note_read": exception_reads, "notes_fraction": note_sizes,
            "copy_forward": copies, "shift_accuracy": accuracy}
