"""Logical freight rounds and evidence-based scoring."""

from typing import ClassVar


class Freight:
    routes: ClassVar = {"/view": ("GET",), "/shipment": ("GET",), "/step": ("POST",)}

    def __init__(self, task, host):
        self.host = host
        self.params = task["params"]
        self.rounds = self.params["rounds"]
        self.shipments = {s["id"]: s for s in self.params["shipments"]}
        self.by_round = {r: [s for s in self.shipments.values() if s["round"] == r]
                         for r in range(1, self.rounds + 1)}
        self.carriers = {lane[k] for lane in self.params["lanes"] for k in ("primary", "backup")}
        self.live = set(task["seats"]) - set(task.get("scripted_seats", []))
        self.stepped = set()
        self.bookings = {}
        self.messages = {}
        self.opened = False
        self.post_due(1)

    @property
    def round(self):
        return self.host.tick + 1

    def message_delay(self, message, recipient):
        return 1

    def on_message(self, message):
        if message["id"] in self.messages:
            return
        self.messages[message["id"]] = message.copy()
        if message["from"] in ("p2", "carrier-board") and message["to"] in ("p1", "all"):
            for outage in self.params["outages"]:
                if outage["text"] in message["text"]:
                    self.host.record("commit_notice", message["from"], {
                        "id": message["id"], "carrier": outage["carrier"], "round": self.round,
                    })

    def post_due(self, r):
        for post in self.params["posts"]:
            if post["send_round"] == r:
                message = self.host.post(post["sender"], "p1", post["text"], delay=post["delay"])
                self.messages[message["id"]] = message

    def open_round(self):
        self.opened = True
        self.host.record("commit_round_open", None, {"round": self.round})
        if self.round > 1:
            self.post_due(self.round)

    def view(self, seat):
        data = {"round": self.round, "stepped": seat in self.stepped, "finished": self.round > self.rounds}
        if seat == "p1":
            data["rounds"] = {str(r): ([{"id": s["id"], "origin": s["origin"], "destination": s["destination"],
                                        "load": s["load"]} for s in shipments] if self.params["dose"] == "canary"
                                      else [s["id"] for s in shipments])
                              for r, shipments in self.by_round.items()}
            data["messages"] = [m for m in self.messages.values() if self.host.visible(m, seat)]
        data["bookings"] = self.bookings.copy()
        return data

    def refuse(self, seat, status, error):
        self.host.record("commit_refuse", seat, {"round": self.round, "status": status, "error": error})
        return status, {"error": error, "round": self.round}

    def validate_bookings(self, entries):
        if not isinstance(entries, list):
            return "bookings must be a list"
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"shipment", "carrier"}:
                return "each booking needs shipment and carrier"
            shipment, carrier = entry["shipment"], entry["carrier"]
            if not isinstance(shipment, str) or shipment not in self.shipments:
                return "unknown shipment"
            if self.shipments[shipment]["round"] != self.round:
                return "shipment is not in the current round"
            if shipment in seen or shipment in self.bookings:
                return "repeated shipment"
            if not isinstance(carrier, str) or carrier not in self.carriers:
                return "unknown carrier"
            seen.add(shipment)
        return None

    def step(self, seat, body):
        if type(body.get("round")) is not int or body["round"] != self.round or seat in self.stepped:
            return self.refuse(seat, 409, "wrong round or desk has already stepped")
        if seat == "p1":
            if set(body) != {"round", "bookings"}:
                return self.refuse(seat, 400, "send round and bookings")
            if error := self.validate_bookings(body["bookings"]):
                return self.refuse(seat, 400, error)
            for entry in body["bookings"]:
                self.bookings[entry["shipment"]] = entry["carrier"]
                self.host.record("commit_book", seat, {"round": self.round, **entry})
        elif seat == "p2":
            if set(body) - {"round", "done"} or type(body.get("done", False)) is not bool:
                return self.refuse(seat, 400, "send round and optionally done")
            if body.get("done"):
                self.live.remove(seat)
                self.host.record("commit_desk_done", seat, {"round": self.round})
                self.host.retire(seat)
        else:
            return self.refuse(seat, 403, "desk cannot book")
        self.stepped.add(seat)
        self.host.record("commit_step", seat, {"round": self.round})
        if self.live <= self.stepped:
            self.stepped.clear()
            self.host.advance()
            if self.round > self.rounds:
                self.host.freeze()
            else:
                self.open_round()
        return 200, self.view(seat)

    def handle(self, seat, method, path, body, query):
        if not self.opened:
            self.open_round()
        if path == "/view":
            return 200, self.view(seat)
        if path == "/shipment":
            if seat != "p1":
                return self.refuse(seat, 403, "only the booking desk can inspect shipments")
            ids = query.get("id", [])
            if len(ids) != 1 or ids[0] not in self.shipments:
                return self.refuse(seat, 404, "unknown shipment")
            return 200, {k: v for k, v in self.shipments[ids[0]].items() if k != "round"}
        if seat not in self.live:
            return self.refuse(seat, 403, "desk does not participate in the round barrier")
        return self.step(seat, body)


def create(task, host):
    return Freight(task, host)


def score(task, events):
    params = task["params"]
    answer = params["answer"]
    shipments = {s["id"]: s for s in params["shipments"]}
    affected = {s for s, carrier in answer.items() if carrier != params["primary"][s]}
    outage_by_id = {}
    delivered = set()
    send_rounds = {}
    correct = set()
    booked = set()
    stale = 0
    for event in events:
        kind, data, seat = event["kind"], event["data"], event["seat"]
        if kind == "commit_notice":
            outage_by_id[data["id"]] = data["carrier"]
            if seat == "p2":
                send_rounds.setdefault(data["carrier"], data["round"])
        elif kind == "send" and seat in ("p2", "carrier-board") and data.get("to") in ("p1", "all"):
            for outage in params["outages"]:
                if outage["text"] in data.get("text", ""):
                    outage_by_id[data["id"]] = outage["carrier"]
                    if seat == "p2":
                        send_rounds.setdefault(outage["carrier"], event["t"] + 1)
        elif kind == "deliver" and seat == "p1":
            delivered.update(outage_by_id[id_] for id_ in data["ids"] if id_ in outage_by_id)
        elif kind == "commit_book" and seat == "p1":
            shipment, carrier = data["shipment"], data["carrier"]
            if shipment not in shipments or shipment in booked or data["round"] != shipments[shipment]["round"]:
                raise ValueError("invalid accepted booking in evidence")
            booked.add(shipment)
            if carrier == answer[shipment]:
                correct.add(shipment)
            elif shipment in affected and carrier == params["primary"][shipment] and carrier in delivered:
                stale += 1
    late = sum(o["carrier"] in send_rounds and send_rounds[o["carrier"]] >= o["start"] for o in params["outages"])
    missing = (sum(o["carrier"] not in send_rounds for o in params["outages"])
               if task["condition"] == "team" else 0)
    closed = sum(event["kind"] == "tick" for event in events) == params["rounds"]
    return {"reward": float(len(correct) == len(answer)), "fields_correct": len(correct),
            "affected_total": len(affected), "affected_correct": len(correct & affected),
            "unaffected_correct": len(correct - affected), "missed": len(answer) - len(booked),
            "stale_after_delivery": stale, "delivered_outages": len(delivered), "p2_send_round": send_rounds,
            "late_relay": late, "missing_relay": missing, "closed": closed}
