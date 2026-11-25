"""Finishing desk referee; the reward is a macro-average over final-shift rule clauses."""

import re
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
        self.history = []
        self.work_seen = set()
        self.sealed = {}


    def pending(self, seat):
        return self.host.status[seat] == "active" and (
            not self.done(seat) or (seat != self.seats[-1] and seat not in self.sealed))

    def on_budget(self, seat):
        if self.pending(seat):
            self.host.record("shift_omission", seat, {"reason": "call_budget"})
            self.host.freeze()

    def on_seat_end(self, seat):
        if self.pending(seat):
            self.host.record("shift_omission", seat, {"reason": "seat_end"})
            self.host.freeze()


    def on_retired_call(self, seat, method, path, body, query):
        if method == "POST" and path == "/note/seal" and seat in self.sealed:
            text, reply = self.sealed[seat]
            if body == {"text": text}:
                return 201, reply
        return None

    def done(self, seat):
        return len(self.actions[seat]) == len(self.rows[seat])

    def note(self, seat):
        if seat not in self.notes:
            return {}, []
        text = self.notes[seat]
        note_id = f"note-{seat}"
        self.host.record("note_read", seat, {"chars": len(text), "id": note_id})
        reply = {"note": text}
        if self.params["fullrecord"]:
            reply["record"] = {"manual": self.params["manual"], "history": list(self.history)}
        return reply, [note_id]

    def work(self, seat):
        page = [j for j in self.order[seat] if j not in self.actions[seat]][:5]
        self.pages[seat] = page
        self.host.record("work", seat, {"jobs": page})
        reply = {"role": self.params["roles"][seat], "jobs": [self.rows[seat][j]["job"] for j in page],
                 "done": not page}
        ids = [f"job-{job}" for job in page]
        if seat not in self.work_seen:
            self.work_seen.add(seat)
            note, note_ids = self.note(seat)
            reply.update(note)
            ids.extend(note_ids)
        if not page and seat == self.seats[-1]:
            self.host.freeze()
        return 200, reply, ids

    def check(self, seat, body):
        job, what = body.get("job"), body.get("what")
        if not isinstance(job, str) or what not in CHECKS:
            return 400, {"error": "send a job ID and check name"}
        if job not in self.pages[seat] or job in self.actions[seat]:
            return 409, {"error": "job is not open on your page"}
        reply = self.rows[seat][job]["replies"][what]
        data = {"job": job, "what": what, "reply": reply}
        self.host.record("check", seat, data)
        return 200, data, [f"check-{job}-{what}"]

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
        if not text.strip():
            return 400, {"error": "note must contain non-whitespace text"}
        cap = self.params["cap"]
        if any(c != "\n" and not " " <= c <= "~" for c in text):
            return 400, {"error": "use printable ASCII and newlines only"}
        size = {"bytes": len(text), "limit": cap}
        if self.params["card"]:
            _, _, errors = self.host.family.card_parts(text, self.params.get("ordered", False))
            size["parse"] = "unparsed" if errors else "ok"
            size["bad_lines"] = errors
        if measure:
            self.host.record("note_check", seat, size)
            return 200, size
        if cap is not None and len(text) > cap:
            return 413, size
        if not self.done(seat):
            return 409, {"error": "finish your jobs before sealing"}
        self.sealed[seat] = (text, size)
        self.handover(seat, text, size)
        return 201, size

    def handover(self, seat, text, size):
        next_seat = self.seats[self.seats.index(seat) + 1]
        self.notes[next_seat] = text
        self.history.append({"seat": seat, "note": text, "amendment": self.params["amendments"].get(seat, "")})
        forwarded = seat in self.notes and self.notes[seat] == text
        self.host.record("note_seal", seat, size | {"copy_forward": forwarded, "id": f"note-{next_seat}", "text": text})
        self.host.advance()
        self.host.retire(seat)
        self.host.activate(next_seat)

    def handle(self, seat, method, path, body, query):
        if path == "/work":
            return self.work(seat)
        if path == "/note":
            note, ids = self.note(seat)
            return 200, note, ids
        if path == "/check":
            return self.check(seat, body)
        if path == "/act":
            return self.act(seat, body)
        if path in ("/note/check", "/note/seal"):
            return self.seal(seat, body, path == "/note/check")
        raise ValueError(f"unexpected route {path}")


def create(task, host):
    return Digest(task, host)


RULE_LABEL = re.compile(
    r"^\s*(?:[-*+]\s+)?(?:\*\*|__|`)?(?:(?:rule|r)\s*)?(\d+)(?!\d|\.\d)"
    r"(?:\*\*|__|`)?(?:\s*[:.)-]\s*|\s+|$)(.*)$", re.IGNORECASE)


def note_measures(params, note):
    """Read numbered rule headings without requiring the scripted note syntax."""
    unknown = {"order_kept": None, "numbers_kept": None}
    if params["card"]:
        return unknown | {"note_measure_flag": "card_format"}
    if note is None:
        return unknown | {"note_measure_flag": "no_final_note"}
    heads = params.get("rule_heads")
    if not heads:
        return unknown | {"note_measure_flag": "no_reference"}
    expected = {field: number for number, field in heads}
    field_pattern = re.compile(r"\b(" + "|".join(expected) + r")\b", re.IGNORECASE)
    observed = {}
    numbers_match = True
    lines = [line for line in note.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        label = RULE_LABEL.match(line)
        if label is None:
            continue
        body = label[2].strip("*`_ ")
        if not body and index + 1 < len(lines) and not RULE_LABEL.match(lines[index + 1]):
            body = lines[index + 1]
        field = field_pattern.search(body)
        if field is None:
            return unknown | {"note_measure_flag": "unreadable_rule_heading"}
        name = field[1].lower()
        number = int(label[1])
        if name in observed and observed[name] != number:
            numbers_match = False
        observed[name] = number
        numbers_match &= number == expected[name]
    if not observed:
        return unknown | {"note_measure_flag": "unreadable_note"}
    return {"order_kept": list(observed) == [field for field in expected if field in observed],
            "numbers_kept": numbers_match and observed.keys() == expected.keys(),
            "note_measure_flag": None}


def score(task, events):
    checks, moves = {}, {}
    note_sizes, copies = [], 0
    final = task["seats"][-1]
    final_note = None
    for event in events:
        seat, kind, data = event["seat"], event["kind"], event["data"]
        if kind == "check":
            checks.setdefault((seat, data["job"]), set()).add(data["what"])
        elif kind == "act":
            key = (seat, data["job"])
            moves.setdefault(key, (data["outcome"], frozenset(checks.get(key, ()))))
        elif kind == "note_seal":
            if data.get("id") == f"note-{final}":
                final_note = data["text"]
            if data["limit"] is not None:
                note_sizes.append(data["bytes"] / data["limit"])
            copies += data["copy_forward"]
    omissions = extras = 0
    accuracy, totals, correct, points = {}, Counter(), Counter(), Counter()
    overlap = {}
    unfinished = {}
    for seat, rows in task["params"]["batches"].items():
        good = 0
        for row in rows:
            move, before = moves.get((seat, row["job"]["id"]), (None, ()))
            missing = set(row["checks"]) - set(before)
            ok = move == row["outcome"] and not missing
            good += ok
            if seat == final:
                totals[row["cell"]] += 1
                correct[row["cell"]] += ok
                omissions += bool(missing)
                points[row["cell"]] += 1 if ok else -task["params"]["wrong_send_cost"] if move not in (None, "hold") else 0
                extras += len(set(before) - set(row["checks"]))
                if row.get("overlap"):
                    overlap[row["job"]["id"]] = ok
        accuracy[seat] = good / len(rows) if rows else None
        unfinished[seat] = sum((seat, row["job"]["id"]) not in moves for row in rows)
    cells = {cell: correct[cell] / total for cell, total in totals.items()}
    depths = {depth: sum(v for c, v in cells.items() if c.endswith(f".{depth}")) /
              sum(c.endswith(f".{depth}") for c in cells) for depth in range(4)
              if any(c.endswith(f".{depth}") for c in cells)}
    rules = {rid: all(v == 1 for c, v in cells.items() if c.startswith(f"{rid}."))
             for rid in sorted({c.split(".")[0] for c in cells} - {"0"})}
    sealed = {e["seat"] for e in events if e["kind"] == "note_seal"}
    missing = [s for s in task["seats"][:-1] if s not in sealed]
    shift_omissions = {e["seat"]: e["data"]["reason"] for e in events if e["kind"] == "shift_omission"}
    for event in events:
        if event["kind"] == "play_incomplete":
            for seat in event["data"]["stalled_seats"]:
                if unfinished[seat] or seat in missing:
                    shift_omissions.setdefault(seat, "play_incomplete")
    reward = max(0.0, sum(points[c] / n for c, n in totals.items()) / len(totals))
    return {"reward": reward, "exact": all(v == 1 for v in cells.values()),
            "cells": cells, "depth_accuracy": depths, "rule_survival": rules, "check_omissions": omissions,
            "shift_omissions": shift_omissions,
            "extra_checks": extras, "notes_fraction": note_sizes, "handover_missing": missing,
            "unfinished_jobs": unfinished,
            "unparsed": sum(bool(e["data"].get("bad_lines")) for e in events if e["kind"] == "note_seal"),
            "copy_forward": copies, "shift_accuracy": accuracy, "job_accuracy": accuracy[final],
            "overlap_first_match": overlap,
            **note_measures(task["params"], final_note)}
