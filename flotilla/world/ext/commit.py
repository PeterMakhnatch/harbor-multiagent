"""Logical freight rounds, staged mail delivery and event scoring."""

from typing import ClassVar


class Freight:
    routes: ClassVar = {"/view": ("GET",), "/shipment": ("GET",), "/step": ("POST",),
                        "/book": ("POST",), "/relay": ("POST",)}

    def __init__(self, task, host):
        self.host = host
        self.params = task["params"]
        self.task = task
        self.actions = []
        self.idle_waits = dict.fromkeys(task["seats"], 0)
        self.ended = False
        self.rounds = self.params["rounds"]
        self.shipments = {s["id"]: s for s in self.params["shipments"]}
        self.by_round = {r: [s["id"] for s in self.shipments.values() if s["round"] == r]
                         for r in range(1, self.rounds + 1)}
        self.primaries = {lane["primary"] for lane in self.params["lanes"]}
        self.carriers = self.primaries | {lane["backup"] for lane in self.params["lanes"]}
        self.report_ready = not self.params["live_report"]
        self.opening_acknowledged = not self.params["report_barrier"]
        self.stepped = False
        self.bookings = {}
        self.messages = {}
        self.fetched = set()
        self.queued = list(self.params["queued"])
        self.opened = False
        self.post_due(1)

    @property
    def round(self):
        return self.host.tick + 1

    def record_action(self, kind, seat, data):
        self.host.record(kind, seat, data)
        self.actions.append({"kind": kind, "seat": seat, "data": data})

    def on_tick(self, tick):
        self.actions.append({"kind": "tick", "seat": None, "data": {"tick": tick}})

    def finish(self):
        self.ended = True
        self.host.freeze()

    def pending(self, seat):
        if self.ended:
            return False
        if seat == "p2":
            return not self.report_ready
        waiting_for_report = self.stepped and self.opening_acknowledged and not self.report_ready
        return self.round <= self.rounds and not waiting_for_report

    def on_deadline(self, stalled_seats):
        for seat in stalled_seats:
            if self.pending(seat):
                self.record_action("commit_stall", seat, {"round": self.round, "reason": "unfinished"})
        result = score(self.task, self.actions)
        # The kit's deadline hook has no post-write receipt log. Do not invent deliveries.
        # The verifier's complete event log still supports offline replay of these measures.
        if any(m["from"] == "dispatch-mail" for m in self.messages.values()):
            result["delivered_outages"] = None
            if any(self.params["answer"][s] != self.params["primary"][s] for s in self.bookings):
                result.update(stale_after_delivery=None, affected_after_delivery=None, stale_rate=None)
        self.ended = True
        return result

    def augment_reply(self, seat, method, path):
        if self.ended or path != "/await":
            return None
        if seat == "p1" and self.stepped and not self.opening_acknowledged:
            self.opening_acknowledged = True
            self.record_action("commit_opening_ack", seat, {"round": self.round})
            self.advance()
            return {"round_ready": self.round > 1}
        if seat == "p1" and self.stepped and not self.report_ready:
            self.idle_waits[seat] += 1
            if self.idle_waits[seat] >= self.host.family.IDLE_WAITS:
                self.record_action("commit_stall", "p2", {"round": self.round, "reason": "report_waits"})
                self.finish()
                return {"duty_stalled": True}
            return None
        if not self.pending(seat):
            return None
        self.idle_waits[seat] += 1
        if self.idle_waits[seat] < self.host.family.IDLE_WAITS:
            return None
        self.stall(seat, "idle_waits")
        return {"duty_stalled": True}

    def allow_message(self, message):
        return 403, "notices are read-only"

    def post(self, sender, text):
        message = self.host.post(sender, "p1", text, delay=0)
        self.messages[message["id"]] = message
        return message["id"]

    def post_due(self, r):
        for post in self.params["posts"]:
            if post["round"] == r:
                self.post(post["sender"], post["text"])

    def open_round(self):
        self.opened = True
        self.host.record("commit_round_open", None, {"round": self.round})
        if self.round > 1:
            self.post_due(self.round)

    def view(self, seat):
        data = {"round": self.round, "stepped": self.stepped, "finished": self.round > self.rounds,
                "bookings": self.bookings.copy()}
        if seat == "p1":
            data["rounds"] = {str(r): ids for r, ids in self.by_round.items()}
            data["messages"] = [m for m in self.messages.values() if self.host.visible(m, seat)]
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
            if not isinstance(shipment, str) or shipment not in self.fetched:
                return "shipment must be inspected in the current round"
            if shipment in seen or shipment in self.bookings:
                return "repeated shipment"
            if not isinstance(carrier, str) or carrier not in self.carriers:
                return "unknown carrier"
            seen.add(shipment)
        return None

    def advance(self):
        if not self.stepped or not self.report_ready or not self.opening_acknowledged:
            return
        self.stepped = False
        self.idle_waits["p1"] = 0
        self.fetched.clear()
        self.host.advance()
        if self.round > self.rounds:
            self.finish()
        else:
            self.open_round()

    def step(self, seat, body, close):
        if seat != "p1":
            return self.refuse(seat, 403, "only the booking desk can book")
        if type(body.get("round")) is not int or body["round"] != self.round or self.stepped:
            return self.refuse(seat, 409, "wrong round or desk has already stepped")
        if set(body) != {"round", "bookings"}:
            return self.refuse(seat, 400, "send round and bookings")
        if self.fetched != set(self.by_round[self.round]):
            return self.refuse(seat, 409, "inspect every shipment of this round before booking")
        if error := self.validate_bookings(body["bookings"]):
            return self.refuse(seat, 400, error)
        for entry in body["bookings"]:
            self.bookings[entry["shipment"]] = entry["carrier"]
            self.record_action("commit_book", seat, {"round": self.round, **entry})
        if body["bookings"] or close:
            self.idle_waits[seat] = 0
        if close:
            self.stepped = True
            self.record_action("commit_step", seat, {"round": self.round})
            self.advance()
        return 200, self.view(seat)

    def relay(self, seat, body):
        if seat != "p2" or not self.params["live_report"]:
            return self.refuse(seat, 403, "only the carrier desk can submit a report")
        if self.report_ready or self.round != 1:
            return self.refuse(seat, 409, "report already closed")
        notices = body.get("notices")
        if set(body) != {"notices"} or not isinstance(notices, list) or len(notices) > len(self.primaries):
            return self.refuse(seat, 400, "invalid notice list")
        seen = set()
        for notice in notices:
            if not isinstance(notice, dict) or set(notice) != {"carrier", "start"}:
                return self.refuse(seat, 400, "each notice needs carrier and start")
            carrier, start = notice["carrier"], notice["start"]
            if (not isinstance(carrier, str) or carrier not in self.primaries or carrier in seen
                    or type(start) is not int or not 2 <= start <= self.rounds):
                return self.refuse(seat, 400, "invalid or repeated carrier or start")
            seen.add(carrier)
        indexes = {o["carrier"]: o["index"] for o in self.params["outages"]}
        for notice in notices:
            carrier, start = notice["carrier"], notice["start"]
            self.queued.append({**notice, "index": indexes.get(carrier, self.params["batch"]),
                                "text": self.host.family.outage_text(carrier, start)})
        self.record_action("commit_report", seat, {"notices": notices})
        self.report_ready = True
        self.host.retire(seat)
        self.advance()
        return 200, {"accepted": len(notices)}

    def shipment(self, seat, query):
        if seat != "p1":
            return self.refuse(seat, 403, "only the booking desk can inspect shipments")
        ids = query.get("id", [])
        if len(ids) != 1 or ids[0] not in self.by_round.get(self.round, []):
            return self.refuse(seat, 404, "unknown shipment in current round")
        id_ = ids[0]
        self.record_action("commit_lookup", seat, {"round": self.round, "shipment": id_})
        if id_ not in self.fetched:
            self.fetched.add(id_)
            self.idle_waits[seat] = 0
            index = len(self.fetched)
            for notice in sorted(self.queued, key=lambda n: n["carrier"]):
                if notice["start"] == self.round and notice["index"] == index:
                    message_id = self.post("dispatch-mail", notice["text"])
                    self.record_action("commit_notice_release", seat, {
                        "id": message_id, "round": self.round, "index": index,
                    })
        return 200, {k: v for k, v in self.shipments[id_].items() if k != "round"}

    def handle(self, seat, method, path, body, query):
        if not self.opened:
            self.open_round()
        if path == "/view":
            if seat == "p1":
                self.record_action("commit_view", seat, {"round": self.round})
            return 200, self.view(seat)
        if path == "/shipment":
            return self.shipment(seat, query)
        if path == "/relay":
            return self.relay(seat, body)
        return self.step(seat, body, close=path == "/step")

    def stall(self, seat, reason):
        self.record_action("commit_stall", seat, {"round": self.round, "reason": reason})
        if seat == "p2":
            self.report_ready = True
            self.host.retire(seat)
            self.advance()
        else:
            self.finish()

    def on_budget(self, seat):
        if self.pending(seat):
            self.stall(seat, "call_budget")


def create(task, host):
    return Freight(task, host)


def notice_activity(rounds, events):
    activity = {str(r): [] for r in range(1, rounds + 1)}
    pending = []
    for event in events:
        if event["seat"] != "p1":
            continue
        kind, data = event["kind"], event["data"]
        if kind == "commit_notice_release":
            pending.append({"release_index": data["index"], "lookups_before_booking": 0, "view_before_booking": False})
        elif kind == "commit_lookup":
            for entry in pending:
                entry["lookups_before_booking"] += 1
        elif kind == "commit_view":
            for entry in pending:
                entry["view_before_booking"] = True
        elif kind == "commit_book" and pending:
            activity[str(data["round"])].extend(pending)
            pending = []
        elif kind == "commit_step" and pending:
            activity[str(data["round"])].extend(
                dict(entry, lookups_before_booking=None, view_before_booking=None) for entry in pending)
            pending = []
    if pending:
        # A release without a subsequent booking has no completed interval.
        round_ = next(e["data"]["round"] for e in reversed(events) if e["kind"] == "commit_notice_release")
        activity[str(round_)].extend(
            dict(entry, lookups_before_booking=None, view_before_booking=None) for entry in pending)
    return activity


def score(task, events):
    params = task["params"]
    answer = params["answer"]
    shipments = {s["id"]: s for s in params["shipments"]}
    affected = {s for s, carrier in answer.items() if carrier != params["primary"][s]}
    notice_text = {o["text"]: o["carrier"] for o in params["outages"]}
    expected_report = {(o["carrier"], o["start"]) for o in params["outages"]}
    report = set()
    outage_by_id = {}
    delivered = set()
    correct = set()
    booked = set()
    stale = exposed = 0
    stalls = set()
    for event in events:
        kind, data, seat = event["kind"], event["data"], event["seat"]
        if kind == "commit_report":
            report = {(n["carrier"], n["start"]) for n in data["notices"]}
        elif kind == "send" and seat == "dispatch-mail" and data["text"] in notice_text:
            outage_by_id[data["id"]] = notice_text[data["text"]]
        elif kind == "deliver" and seat == "p1":
            delivered.update(outage_by_id[id_] for id_ in data["ids"] if id_ in outage_by_id)
        elif kind == "commit_book" and seat == "p1":
            shipment, carrier = data["shipment"], data["carrier"]
            if shipment not in shipments or shipment in booked or data["round"] != shipments[shipment]["round"]:
                raise ValueError("invalid accepted booking in evidence")
            booked.add(shipment)
            if carrier == answer[shipment]:
                correct.add(shipment)
            if shipment in affected and params["primary"][shipment] in delivered:
                exposed += 1
                stale += carrier == params["primary"][shipment]
        elif kind == "commit_stall":
            stalls.add(seat)
    missing = len(expected_report - report) if params["live_report"] else 0
    report_ok = not params["live_report"] or report == expected_report
    closed = sum(event["kind"] == "tick" for event in events) == params["rounds"]
    return {"reward": float(len(correct) == len(answer) and report_ok and closed and not stalls),
            "fields_correct": len(correct),
            "affected_total": len(affected), "affected_correct": len(correct & affected),
            "unaffected_correct": len(correct - affected), "missed": len(answer) - len(booked),
            "stale_after_delivery": stale, "affected_after_delivery": exposed,
            "stale_rate": stale / exposed if exposed else None,
            "notice_activity": notice_activity(params["rounds"], events),
            "delivered_outages": len(delivered), "missing_relay": missing, "report_ok": report_ok,
            "p1_stalled": int("p1" in stalls),
            "p2_stalled": int("p2" in stalls), "closed": closed}
