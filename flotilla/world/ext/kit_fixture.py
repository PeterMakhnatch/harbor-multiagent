"""Test-only extension: state, route handling, logical time, delivery and the shift lifecycle."""

from typing import ClassVar

import family


class Fixture:
    routes: ClassVar = {"/board": ("GET",), "/handoff": ("POST",), "/advance": ("POST",),
                        "/commit": ("POST",), "/ready": ("POST",), "/fixture-fault": ("POST",)}

    def __init__(self, task, host):
        self.host = host
        self.params = task["params"]
        self.single = len(task["seats"]) == 1
        self.handed_off = False
        self.protocol = self.params.get("protocol", "shift")
        self.ready: set[str] = set()
        self.note = ""
        if self.protocol == "team":
            host.post("board", "p2", "Round opens", delay=0)

    def allow_message(self, message):
        if self.protocol == "team" and (message["from"] != "p1" or message["to"] != "p2"):
            return 403, "only the sender can post to the receiver"
        return None

    def on_message(self, message):
        if self.protocol == "team":
            self.host.post("board", message["from"], "Message accepted", delay=0)

    def on_tick(self, tick):
        if self.protocol == "team":
            self.host.post("board", "p2", f"Round {tick}", delay=0)

    def on_budget(self, seat):
        self.host.record("fixture_budget", seat)
        self.host.freeze()

    def on_seat_end(self, seat):
        if self.protocol == "staged" and seat == "p1" and not self.handed_off:
            self.host.record("handover_missing", seat)

    def message_delay(self, message, recipient):
        return self.params["delay"] if message["text"].startswith("handoff ") else None

    def handle(self, seat, method, path, body, query):
        if path == "/fixture-fault":
            kind = body.get("kind")
            if kind == "status":
                return "200", {}
            if kind == "list":
                return 200, []
            if kind == "nan":
                return 200, {"value": float("nan")}
            raise RuntimeError("fixture failure")
        if path == "/board":
            reply = {"tick": self.host.tick, "handed_off": self.handed_off}
            if self.protocol == "staged" and seat == "p2" and self.handed_off:
                return 200, reply | {"note": self.note}, ["note-1"]
            return 200, reply
        if path == "/ready":
            if self.protocol != "team" or seat in self.ready or body:
                return 409, {"error": "cannot record readiness"}
            self.ready.add(seat)
            self.host.record("ready", seat)
            if self.ready == set(self.host.status):
                self.handed_off = True
                self.host.record("handoff", "p1")
                self.host.advance()
            return 200, {"tick": self.host.tick}
        if path == "/handoff":
            if self.single or seat != "p1" or self.handed_off or self.protocol == "team":
                return 409, {"error": "handoff is not available"}
            if self.protocol == "staged":
                note = body.get("note")
                if not isinstance(note, str) or not note.isdecimal():
                    return 400, {"error": "note must be a decimal string"}
                if len(note) > family.NOTE_CAP:
                    return 413, {"error": "note too long"}
                self.note = note
                self.host.record("sealed_note", seat, {"note": note})
            elif body:
                return 400, {"error": "send an empty object"}
            self.handed_off = True
            self.host.record("handoff", seat)
            self.host.advance()
            self.host.retire("p1")
            self.host.activate("p2")
            return 200, {"tick": self.host.tick}
        if path == "/advance":
            if self.protocol != "shift" or seat != "p2" or not self.handed_off or self.host.tick >= 2 or body:
                return 409, {"error": "cannot advance"}
            return 200, {"tick": self.host.advance()}
        if path == "/commit":
            if seat != ("p1" if self.single else "p2"):
                return 403, {"error": "only the final seat can commit"}
            if self.protocol == "team" and not self.handed_off:
                return 409, {"error": "round is still open"}
            total = body.get("total")
            if type(total) is not int:
                return 400, {"error": "send a whole number as total"}
            self.host.record("commit_value", seat, {"total": total})
            self.host.freeze()
            return 200, {"accepted": True}
        raise ValueError(f"unexpected route {path}")


def create(task, host):
    return Fixture(task, host)


def score(task, events):
    single = len(task["seats"]) == 1
    commits = [event for event in events if event["kind"] == "commit_value"]
    handoffs = [event for event in events if event["kind"] == "handoff" and event["seat"] == "p1"]
    correct = (len(commits) == 1 and commits[0]["seat"] == ("p1" if single else "p2")
               and commits[0]["data"]["total"] == task["params"]["total"] and (single or len(handoffs) == 1))
    if any(event["kind"] == "fixture_budget" for event in events):
        return {"invalid": "fixture budget exhausted"}
    if task["params"].get("protocol") == "staged":
        notes = [event for event in events if event["kind"] == "sealed_note"]
        receipts = [event for event in events if event["kind"] == "delivered" and event["seat"] == "p2"]
        correct = correct and len(notes) == 1 and bool(receipts)
    result = {"reward": float(correct), "handoffs": len(handoffs)}
    if task["params"].get("protocol") == "staged":
        result["handover_missing"] = any(event["kind"] == "handover_missing" for event in events)
    return result
