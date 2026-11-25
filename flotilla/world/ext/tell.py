"""Logical receiving rounds, irreversible carrier pickup, and evidence-only grading."""

import json
from typing import ClassVar

import family


class Tell:
    routes: ClassVar = {"/item": ("GET",), "/docket": ("GET",), "/board": ("GET",),
                        "/sheet": ("GET", "POST"), "/stock": ("GET",), "/record": ("POST",),
                        "/forward": ("POST",), "/wait": ("POST",)}

    def __init__(self, task, host):
        self.host = host
        self.params = task["params"]
        self.solo = len(task["seats"]) == 1
        self.batch = self.params["batch"]
        self.sheets = {desk: {r["order"]: dict(r) for r in rows} for desk, rows in self.params["boards"].items()}
        self.index = 0
        self.seen = {}
        self.acted = set()
        self.parked = set()
        self.arrivals = {desk: [] for desk in family.DESKS}
        self.forwards = set()
        self.omitted = set()
        self.carrier = False

    @property
    def round(self):
        return self.host.tick + 1

    def allow_message(self, message):
        return 403, "Use the document forwarding route; free text messages are disabled."

    def snapshot(self, desks):
        return {desk: list(self.sheets[desk].values()) for desk in desks}

    def current(self):
        if self.index == len(self.batch):
            return None, []
        item = self.batch[self.index]
        self.seen[item["item"]] = item
        return item, [item["item"]]

    def reply(self, seat, body=None, ids=None, status=200):
        return status, {"round": self.round, "acted": seat in self.acted, "carrier_left": self.carrier,
                        **(body or {})}, ids or []

    def post_document(self, seat, item, destination, automatic=False):
        message = self.host.post("receiving", destination, json.dumps(item, sort_keys=True), delay=1)
        self.arrivals[destination].append((self.round + 1, item))
        self.host.record("forward_document", seat, {"item": item["item"], "to": destination,
                                                     "message": message["id"], "automatic": automatic})

    def on_tick(self, tick):
        if self.solo:
            return
        for seat in family.DESKS:
            if self.host.status[seat] != "active":
                continue
            if any(due == self.round for due, _ in self.arrivals[seat]) or self.round == family.CARRIER:
                self.parked.discard(seat)
            if self.round == family.CARRIER:
                self.host.post("receiving", seat, "Carrier round", delay=0)

    def advance_if_ready(self):
        for _ in range(family.END):
            awake = {seat for seat, state in self.host.status.items() if state == "active"} - self.parked
            if awake - self.acted:
                return
            if self.round == family.CARRIER:
                self.carrier = True
                self.host.record("carrier", None, {"sheets": self.snapshot(family.DESKS),
                                                   "dispatch_acted": sorted(self.acted - {"p1"})})
                if not self.solo:
                    for seat in family.DESKS:
                        if self.host.status[seat] == "active":
                            self.host.retire(seat)
            if self.round == family.END or not any(v == "active" for v in self.host.status.values()):
                self.host.freeze()
                return
            self.acted.clear()
            self.host.advance()

    def on_budget(self, seat):
        self.omit(seat)

    def omit(self, seat):
        if self.host.status[seat] == "active":
            self.host.record("desk_omission", seat)
            self.omitted.add(seat)
            self.host.retire(seat)
            self.advance_if_ready()

    def view(self, seat, path):
        receiving = seat == "p1"
        if path in ("/item", "/board", "/docket") and not receiving:
            return self.reply(seat, {"error": "receiving desk only"}, status=403)
        if path in ("/sheet", "/stock") and receiving and not self.solo:
            return self.reply(seat, {"error": "dispatch desk only"}, status=403)
        desks = family.DESKS if self.solo else (seat,)
        if path == "/item":
            item, ids = self.current()
            return self.reply(seat, {"document": item}, ids)
        if path == "/docket":
            return self.reply(seat, {"docket": self.params["docket"]}, ["docket"])
        if path == "/board":
            return self.reply(seat, {"boards": self.params["boards"]}, ["board"])
        if path == "/sheet":
            return self.reply(seat, {"sheets": self.snapshot(desks)}, ["sheet"])
        return self.reply(seat, {"stock": {desk: self.params["stock"][desk] for desk in desks}}, ["stock"])

    def record_item(self, seat, body):
        if seat != "p1":
            return 403, "receiving desk only"
        if self.index == len(self.batch):
            return 409, "receiving log is complete"
        item = self.batch[self.index]
        status = body.get("status")
        if body.get("item") != item["item"] or item["item"] not in self.seen:
            return 409, "record the current document after viewing it"
        if (status not in ("accept", "reject", "file", "quarantine")
                or set(body) != ({"item", "status", "lot"} if status == "quarantine" else {"item", "status"})
                or (status == "quarantine" and not isinstance(body.get("lot"), str))):
            return 400, "send item, status, and the lot for quarantine"
        self.host.record("record_item", seat, body)
        self.index += 1
        if self.params["automatic"] and status == "quarantine":
            for desk in family.DESKS:
                self.post_document(seat, item, desk, automatic=True)
        return 200, "recorded"

    def forward(self, seat, body):
        if seat != "p1" or self.solo:
            return 403, "forwarding is available only at the receiving desk"
        item, destination = body.get("item"), body.get("to")
        if (set(body) != {"item", "to"} or not isinstance(item, str) or item not in self.seen
                or destination not in family.DESKS):
            return 400, "send a viewed item ID and a dispatch seat"
        if (item, destination) in self.forwards:
            return 409, "document already forwarded to this desk"
        if len(self.forwards) >= family.FORWARDS:
            self.host.record("forward_refused", seat)
            return 429, "manual forward budget exhausted"
        self.forwards.add((item, destination))
        self.post_document(seat, self.seen[item], destination)
        return 200, "forwarded"

    def edit_sheet(self, seat, body):
        if seat == "p1" and not self.solo:
            self.host.record("foreign_edit", seat)
            return 403, "only dispatch can change its sheet"
        order, lot = body.get("order"), body.get("lot")
        if set(body) != {"order", "lot"} or not isinstance(order, str) or not isinstance(lot, str):
            return 400, "send order and lot as strings"
        if self.carrier:
            return 409, "carrier has left"
        desk = next((d for d in (family.DESKS if self.solo else (seat,)) if order in self.sheets[d]), None)
        if desk is None:
            return 403, "order is not on your sheet"
        row = self.sheets[desk][order]
        documents = self.seen.values() if self.solo else (i for due, i in self.arrivals[desk] if due <= self.round)
        notices = [i for i in documents if i["kind"] == "quarantine" and i["product"] == row["product"]]
        banned = {i["lot"] for i in notices}
        if row["lot"] not in banned or lot in banned or lot not in self.params["stock"][desk][order]:
            return 400, "a received notice must quarantine the current lot; select another stock lot"
        self.host.record("sheet_change", seat, {"desk": desk, "order": order, "lot": lot,
                                               "notice": next(i["item"] for i in notices if i["lot"] == row["lot"])})
        row["lot"] = lot
        return 200, "updated"

    def handle(self, seat, method, path, body, query):
        if method == "GET":
            return self.view(seat, path)
        if seat in self.acted or seat in self.parked:
            return self.reply(seat, {"error": "wait for the next available round"}, status=409)
        if path == "/record":
            status, text = self.record_item(seat, body)
        elif path == "/forward":
            status, text = self.forward(seat, body)
        elif path == "/sheet":
            status, text = self.edit_sheet(seat, body)
        elif path == "/wait":
            if body not in ({}, {"until": "delivery"}) or (body and seat == "p1"):
                return self.reply(seat, {"error": "send {} or, at dispatch, {\"until\": \"delivery\"}"}, status=400)
            if body:
                self.parked.add(seat)
            status, text = 200, "waiting"
        else:
            return self.reply(seat, {"error": "unknown action"}, status=400)
        if status != 200:
            return self.reply(seat, {"error": text}, status=status)
        self.host.record("desk_action", seat, {"path": path})
        self.acted.add(seat)
        self.advance_if_ready()
        extra, ids = {}, []
        if path == "/record":
            extra["document"], ids = self.current()
        return self.reply(seat, {"result": text, **extra}, ids)


def create(task, host):
    return Tell(task, host)


def score(task, events):
    params = task["params"]
    hit = params["batch"][params["hit"]]["item"]
    receiver = params["receiver"]
    seen = None
    sent = []
    reached = []
    changes = []
    records = []
    carrier = None
    board_read = False
    received_ids = set()
    for event in events:
        kind, seat, data = event["kind"], event["seat"], event["data"]
        round_no = int(event["t"]) + 1
        if kind == "delivered" and seat == "p1":
            if hit in data["ids"] and seen is None:
                seen = round_no
            if seen is not None and "board" in data["ids"] and round_no < family.CARRIER:
                board_read = True
        elif kind == "forward_document" and data["item"] == hit and data["to"] == receiver and seen is not None:
            sent.append((data["message"], round_no))
        elif kind == "deliver" and seat == receiver:
            received_ids.update(data["ids"])
            if carrier is None and round_no <= family.CARRIER:
                reached.extend(round_no for msg, _ in sent if msg in data["ids"])
        elif kind == "record_item":
            records.append(data)
        elif kind == "sheet_change":
            if data["notice"] == hit and carrier is None and (len(task["seats"]) == 1 and seen is not None
                                                                or any(msg in received_ids for msg, _ in sent)):
                changes.append(data)
        elif kind == "carrier":
            carrier = data
    expected = [family.record_entry(item, params["docket"]) for item in params["batch"]]
    own_ok = records == expected
    banned = {item["lot"] for item in params["batch"] if item["kind"] == "quarantine"}
    sheets = {desk: [row | {"lot": next(lot for lot in params["stock"][desk][row["order"]] if lot not in banned)}
                     if row["lot"] in banned else row for row in rows] for desk, rows in params["boards"].items()}
    solo = len(task["seats"]) == 1
    affected = any(row["lot"] in banned for rows in params["boards"].values() for row in rows)
    ship_ok = (carrier is not None and carrier["sheets"] == sheets and (not affected or bool(changes))
               and (solo or carrier["dispatch_acted"] == list(family.DESKS)))
    told = bool(reached) if affected and not solo and seen is not None else None
    return {"reward": float(own_ok and ship_ok), "own_ok": own_ok, "ship_ok": ship_ok,
            "hit_seen_round": seen, "board_read_after_hit": board_read, "told_in_time": told,
            "forwarded_hit_round": sent[0][1] if sent else None, "told_late": bool(sent) and not reached,
            "receiver_applied": bool(changes), "n_forwards": sum(e["kind"] == "forward_document" and not
                                                                 e["data"]["automatic"] for e in events),
            "forward_refused": sum(e["kind"] == "forward_refused" for e in events),
            "foreign_edit": sum(e["kind"] == "foreign_edit" for e in events),
            "receiving_incomplete": len(records) != len(expected), "carrier_missing": carrier is None,
            "desk_omissions": [e["seat"] for e in events if e["kind"] == "desk_omission"],
            "rounds_used": max((int(e["t"]) + 1 for e in events), default=0)}
