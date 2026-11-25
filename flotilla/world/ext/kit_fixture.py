"""Test-only extension: state, route handling, logical time, delivery and the shift lifecycle."""

from typing import ClassVar


class Fixture:
    routes: ClassVar = {"/board": ("GET",), "/handoff": ("POST",), "/advance": ("POST",), "/commit": ("POST",)}

    def __init__(self, task, host):
        self.host = host
        self.params = task["params"]
        self.single = len(task["seats"]) == 1
        self.handed_off = False

    def message_delay(self, message, recipient):
        return self.params["delay"] if message["text"].startswith("handoff ") else None

    def handle(self, seat, method, path, body, query):
        if path == "/board":
            return 200, {"tick": self.host.tick, "handed_off": self.handed_off}
        if path == "/handoff":
            if self.single or seat != "p1" or self.handed_off or body:
                return 409, {"error": "handoff is not available"}
            self.handed_off = True
            self.host.record("handoff", seat)
            self.host.advance()
            self.host.retire("p1")
            self.host.activate("p2")
            return 200, {"tick": self.host.tick}
        if path == "/advance":
            if seat != "p2" or not self.handed_off or self.host.tick >= 2 or body:
                return 409, {"error": "cannot advance"}
            return 200, {"tick": self.host.advance()}
        if path == "/commit":
            if seat != ("p1" if self.single else "p2"):
                return 403, {"error": "only the final seat can commit"}
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
    return {"reward": float(correct), "handoffs": len(handoffs)}
