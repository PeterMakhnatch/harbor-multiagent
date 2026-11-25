"""Desk permissions, round barrier and independent accounting over accepted actions."""

from collections import Counter
from typing import ClassVar

import family


class Week:
    routes: ClassVar = {"/desk": ("GET",), "/board": ("GET",), "/round": ("POST",),
                        "/card/check": ("POST",), "/rates/check": ("POST",)}

    def __init__(self, task, host):
        self.host = host
        self.p = task["params"]
        self.single = len(task["seats"]) == 1
        self.orders = {o["id"]: o for o in self.p["orders"]}
        self.questions = {q["id"]: q for q in self.p["questions"]}
        self.done = set()
        self.duties = {seat: set(family.duty_rounds(self.p, "all" if self.single else seat))
                       for seat in task["seats"]}
        self.task = task
        self.events = []
        self.messages = Counter()
        self.card = None
        self.card_live = {}
        self.rates = {}
        self.bookings = {}
        self.board = []
        self.reconciles = set()
        self.answers = set()
        self.note = ""

    def record(self, kind, seat, data=None):
        self.host.record(kind, seat, data)
        self.events.append({"kind": kind, "seat": seat, "data": data or {}})

    def pending(self, seat):
        return any(r > self.round or (r == self.round and seat not in self.done) for r in self.duties[seat])

    def work_open(self, seat):
        """True while the seat's own work round is open: `GET /await?until=work` then returns at once."""
        return self.round in self.duties[seat] and seat not in self.done

    def on_deadline(self, stalled_seats):
        self.record("week_incomplete", None, {"stalled_seats": stalled_seats})
        return score(self.task, self.events)

    @property
    def round(self):
        return self.host.tick + 1

    @property
    def day(self):
        return (self.round - 1) // self.p["day_length"] + 1

    def role(self, seat, role):
        return self.single or seat == role

    def owns(self, seat, order):
        return self.single or seat == family.desk_for(order["hub"], self.round, self.p["shift"])

    def message_delay(self, message, recipient):
        return 1 if self.host.status[recipient] == "active" else None

    def allow_message(self, message):
        sender, target = message["from"], message["to"]
        if sender not in ("a", "r") or (target != "all" and target not in ("n1", "n2", "s")):
            return 403, "only account or rates may send to dispatch"
        if sender in self.done or self.round not in self.duties[sender]:
            return 409, "round already closed or no scheduled duty"
        if target != "all" and self.host.status[target] != "active":
            return 403, "desk is not staffed"
        if self.messages[sender, self.day] >= 8 or len(message["text"]) > 400:
            return 429, "daily message limit"
        return None

    def on_message(self, message):
        self.messages[message["from"], self.day] += 1

    def on_budget(self, seat):
        self.record("week_budget", seat)
        self.host.freeze()

    def rates_valid(self, rates):
        return (isinstance(rates, dict) and set(rates) == set(family.CARRIERS)
                and all(isinstance(v, str) and v in family.FORMS for v in rates.values()))

    def receipts(self, seat):
        return [{"order": oid, "carrier_ref": self.orders[oid]["ref"]} for oid, book in self.bookings.items()
                if (self.single or book["desk"] == seat or (seat == "n2" and self.p["fullrecord"]
                    and book["desk"] == "n1")) and self.orders[oid]["ref"] not in self.reconciles]

    def active_callbacks(self, seat):
        return [{"carrier_ref": o["ref"], "status": o["status"]} for o in self.orders.values()
                if self.owns(seat, o) and o["id"] in self.bookings and o["callback"] is not None
                and o["callback"] <= self.round and o["ref"] not in self.reconciles]

    def desk(self, seat):
        reply = {"round": self.round, "day": self.day, "board": self.board}
        ids = []
        if self.role(seat, "a"):
            reply["handbook"] = family.handbook(self.p["rules"])
            reply["amendments"] = [{"order": o["id"], "carrier": o["amendment"], "desk":
                                     family.desk_for(o["hub"], self.round, self.p["shift"])}
                                    for o in self.orders.values() if o["amendment"] and o["amend_close"] == self.round]
            reply["questions"] = [q for q in self.questions.values() if q["round"] <= self.round
                                  and q["id"] not in self.answers]
            ids.extend(a["order"] + ":amendment" for a in reply["amendments"])
        if self.role(seat, "r"):
            reply["ratefiles"] = self.p["supports"]
            reply["published"] = self.rates
        if self.single or seat in ("n1", "n2", "s"):
            reply["orders"] = [family.public_order(o) for o in self.orders.values() if self.owns(seat, o)
                               and o["release"] <= self.round < o["departure"] and o["id"] not in self.bookings]
            reply["card"] = self.card_live
            reply["receipts"] = self.receipts(seat)
            reply["callbacks"] = self.active_callbacks(seat)
            ids.extend(o["id"] for o in reply["orders"])
            ids.extend(c["carrier_ref"] for c in reply["callbacks"])
            if seat == "n2":
                reply["notebook"] = self.note
                if self.p["fullrecord"]:
                    reply["prior_record"] = [{key: b[key] for key in ("order", "carrier", "ref")}
                                             for b in self.bookings.values() if b["desk"] == "n1"]
                ids.append("notebook")
        return 200, reply, ids

    def reject(self, seat, action, reason):
        self.record("week_rejected", seat, {"action": action, "reason": reason})
        return {"action": action, "error": reason}

    def seal_card(self, seat, text):
        card = family.parse_card(text, self.p["card_cap"])
        if not self.role(seat, "a") or self.round != 1 or self.card is not None or card is None:
            return self.reject(seat, "card", "card must be valid and sealed once in round 1 by the account desk")
        self.card = card
        self.record("week_card", seat, {"card": card})
        return {"action": "card", "ok": True}

    def publish_rates(self, seat, rates):
        if not self.role(seat, "r") or self.rates or not self.rates_valid(rates) or self.round != 1:
            return self.reject(seat, "rates", "publish all four forms once in round 1")
        self.rates = rates.copy()
        self.record("week_rates", seat, {"forms": rates, "day": self.day, "effective": self.round + 1})
        return {"action": "rates", "ok": True}

    def book(self, seat, item):
        oid, carrier, key = item.get("order"), item.get("carrier"), item.get("rate_key")
        order = self.orders.get(oid) if isinstance(oid, str) else None
        if (order is None or not self.owns(seat, order) or oid in self.bookings
                or not max(2, order["release"]) <= self.round < order["departure"]):
            return self.reject(seat, "book", "order unavailable")
        if (not isinstance(carrier, str) or carrier not in order["eligible"] or not isinstance(key, str)
                or len(key) > 32 or type(item.get("departure")) is not int or item["departure"] != order["departure"]):
            return self.reject(seat, "book", "use an eligible carrier, the requested departure and a rate key")
        if not self.single and carrier not in family.choices(order, self.card_live):
            return self.reject(seat, "book", "the installed card forbids this carrier")
        booking = {"order": oid, "carrier": carrier, "departure": order["departure"], "rate_key": key,
                   "ref": order["ref"], "desk": seat, "round": self.round}
        self.bookings[oid] = booking
        self.record("week_book", seat, booking)
        return {"action": "book", "order": oid, "carrier_ref": order["ref"]}

    def reconcile(self, seat, item):
        ref, oid, action = item.get("carrier_ref"), item.get("order"), item.get("action")
        pending = {c["carrier_ref"] for c in self.active_callbacks(seat)}
        if (not isinstance(ref, str) or ref not in pending or not isinstance(oid, str) or oid not in self.orders
                or not self.owns(seat, self.orders[oid]) or action not in ("close", "reissue")):
            return self.reject(seat, "reconcile", "callback, order or disposition is unavailable")
        self.reconciles.add(ref)
        self.record("week_reconcile", seat, {"ref": ref, "order": oid, "action": action})
        return {"action": "reconcile", "ok": True}

    def answer(self, seat, item):
        qid, eta = item.get("question"), item.get("eta")
        q = self.questions.get(qid) if isinstance(qid, str) else None
        if (not self.role(seat, "a") or q is None or qid in self.answers or q["round"] > self.round
                or type(eta) is not int):
            return self.reject(seat, "answer", "question is unavailable or ETA is not an integer round")
        self.answers.add(qid)
        self.record("week_eta", seat, {"question": qid, "eta": eta})
        return {"action": "answer", "ok": True}

    def seal_note(self, seat, note):
        if (seat != "n1" or self.single or self.round != self.p["shift"]
                or not family.fits_ascii(note, self.p["note_cap"]) or len(note.split("\n")) > self.p["note_lines"]):
            return self.reject(seat, "notebook", "seal within the byte and line cap at the end of the early shift")
        self.note = note
        self.record("week_notebook", seat, {"note": note})
        return {"action": "notebook", "ok": True}

    def close_round(self, seat):
        self.done.add(seat)
        self.record("week_ready", seat, {"round": self.round})
        for _ in range(self.p["rounds"] - self.round + 1):
            round_no = self.round
            active = {s for s, status in self.host.status.items() if status == "active"}
            required = {s for s in active if round_no in self.duties[s]}
            if not required <= self.done:
                return
            for idle in sorted(active - required):
                self.record("week_auto_ready", idle, {"round": round_no})
            self.done.clear()
            self.board = [{key: b[key] for key in ("order", "carrier", "departure")} for b in
                          sorted(self.bookings.values(), key=lambda b: b["order"])]
            self.card_live = self.card or {}
            self.record("week_closed", None, {"round": round_no})
            if round_no == self.p["rounds"]:
                self.host.freeze()
                return
            self.host.advance()
            if not self.single and round_no == self.p["shift"]:
                self.host.retire("n1")
                self.host.activate("n2")

    def handle(self, seat, method, path, body, query):
        if path == "/desk":
            if self.round not in self.duties[seat] or seat in self.done:
                return 409, {"error": "no open duty; wait for your next work round"}
            return self.desk(seat)
        if path == "/board":
            return 200, {"round": self.round, "board": self.board}
        if path == "/card/check":
            return (200, {"valid": family.parse_card(body.get("card"), self.p["card_cap"]) is not None}) if self.role(
                seat, "a") else (403, {"error": "account desk only"})
        if path == "/rates/check":
            return (200, {"valid": self.rates_valid(body.get("rates"))}) if self.role(seat, "r") else (
                403, {"error": "rates desk only"})
        if seat in self.done or self.round not in self.duties[seat]:
            return 409, {"error": "round already closed; wait for next tick"}
        if (type(body.get("round")) is not int or body["round"] != self.round or
                set(body) - {"round", "card", "rates", "bookings", "reconciles", "answers", "notebook"}):
            return 400, {"error": "supply the current round and named actions"}
        for key, limit in (("bookings", 6 if self.single else 3), ("reconciles", 60), ("answers", 30)):
            items = body.get(key, [])
            if not isinstance(items, list) or len(items) > limit or any(not isinstance(i, dict) for i in items):
                return 400, {"error": f"invalid {key} list"}
        if self.single and any(sum(self.orders.get(b.get("order"), {}).get("hub") == hub for b in body.get("bookings", [])
                                   if isinstance(b.get("order"), str)) > 3 for hub in ("North", "South")):
            return 400, {"error": "booking limit is three per hub"}
        results = []
        if "card" in body:
            results.append(self.seal_card(seat, body["card"]))
        if "rates" in body:
            results.append(self.publish_rates(seat, body["rates"]))
        for key, fn in (("bookings", self.book), ("reconciles", self.reconcile), ("answers", self.answer)):
            results.extend(fn(seat, item) for item in body.get(key, []))
        if "notebook" in body:
            results.append(self.seal_note(seat, body["notebook"]))
        round_no = self.round
        self.close_round(seat)
        return 200, {"round": round_no, "tick": self.host.tick, "results": results}


def create(task, host):
    return Week(task, host)


def score(task, events):
    p = task["params"]
    v_ref = p["v_ref"]
    closed = [e["data"]["round"] for e in events if e["kind"] == "week_closed"]
    if closed != list(range(1, len(closed) + 1)) or len(closed) > p["rounds"]:
        return {"invalid": "round evidence inconsistent"}
    budget_seats = sorted({e["seat"] for e in events if e["kind"] == "week_budget"})
    stalled = sorted({seat for e in events if e["kind"] == "week_incomplete"
                      for seat in e["data"]["stalled_seats"]} | set(budget_seats))
    flags = []
    if len(closed) != p["rounds"]:
        flags.append("unfinished_week")
    if budget_seats:
        flags.append("budget_exhausted")
    if any(e["kind"] == "week_incomplete" for e in events):
        flags.append("seat_omission")
    books = {e["data"]["order"]: e for e in events if e["kind"] == "week_book"}
    reconciles = {e["data"]["ref"]: e["data"] for e in events if e["kind"] == "week_reconcile"}
    etas = {e["data"]["question"]: e["data"]["eta"] for e in events if e["kind"] == "week_eta"}
    tables = [e["data"] for e in events if e["kind"] == "week_rates"]
    card = next((e["data"]["card"] for e in events if e["kind"] == "week_card"), {})
    if len(task["seats"]) > 1 and not any(e["kind"] == "week_notebook" for e in events):
        flags.append("notebook_missing")
    truth = {r["key"]: r["carrier"] for r in p["rules"]}
    ledger = []
    revenue = 0
    fees = 0
    opportunities = Counter()
    unobserved = Counter()
    failed = {s: set() for s in family.SEAMS[:-1]}
    missing_seams = {}

    def charge(subject, seam, reason, fee=0, lost=0):
        nonlocal fees
        fees += fee
        ledger.append({"subject": subject, "seam": seam, "reason": reason, "fee": fee, "lost_revenue": lost})
        if seam in failed:
            failed[seam].add(subject)

    for order in p["orders"]:
        oid, ref = order["id"], order["ref"]
        book = books.get(oid)
        is_crossing = (len(task["seats"]) > 1 and order["hub"] == "North"
                       and order["departure"] <= p["shift"] < (order["callback"] or 0))
        applicable = ["interface"]
        if family.rule_key(order) in truth:
            applicable.append("digest")
        if order["amendment"]:
            applicable.append("commit")
        if is_crossing:
            applicable.append("handoff")
        opportunities.update(applicable)
        if book is None:
            carriers = family.choices(order, card)
            overstrict = (not set(carriers).intersection(family.choices(order, truth))
                          or (order["amendment"] is not None and order["amendment"] not in carriers))
            seam = "digest" if overstrict else "own"
            if overstrict and "digest" not in applicable:
                opportunities["digest"] += 1
            missing_seams[oid] = seam
            charge(oid, seam, "card_overstrict" if overstrict else "unbooked", lost=100)
            unobserved.update(s for s in applicable if s != seam)
            continue
        b = book["data"]
        valid = True
        if b["carrier"] not in family.choices(order, truth):
            charge(oid, "digest", "card_missing", 150, 100)
            valid = False
        if order["amendment"] and b["carrier"] != order["amendment"]:
            reason = "booked_in_window" if b["round"] <= order["amend_close"] else "amendment_conflict"
            charge(oid, "commit", reason, 150, 100 if valid else 0)
            valid = False
        table = next((t["forms"] for t in reversed(tables) if t["effective"] <= b["round"]), {})
        form = table.get(b["carrier"])
        if (form not in p["supports"][b["carrier"]]
                or b["rate_key"] != family.rate_key(b["carrier"], order["zone"], form)):
            missing = b["rate_key"] == ""
            charge(oid, "interface", "key_missing" if missing else "key_wrong", 40 if missing else 200)
        if order["callback"] is not None:
            correct = {"ref": ref, "order": oid, "action": "close" if order["status"] == "delivered" else "reissue"}
            if reconciles.get(ref) != correct:
                crossing = b["desk"] == "n1" and order["callback"] > p["shift"]
                lost = order["status"] != "delivered"
                wrong = ref in reconciles
                reason = "wrong_reconciliation" if wrong else "missing_reconciliation"
                charge(oid, "handoff" if crossing else "own", reason,
                       (120 if lost else 20) + (300 if wrong else 0), 100 if lost and valid else 0)
                valid = valid and not lost
        if valid:
            revenue += 100
    orders = {o["id"]: o for o in p["orders"]}
    for q in p["questions"]:
        if etas.get(q["id"]) == orders[q["order"]]["departure"] and q["order"] in books:
            revenue += 10
        else:
            seam = missing_seams.get(q["order"], "own")
            charge(q["id"], seam, "wrong_or_missing_eta", lost=10)
            if seam in failed:
                failed[seam].discard(q["id"])
    for index, event in enumerate(e for e in events if e["kind"] == "week_rejected"):
        charge(f"rejection-{index}", "own", "rejected_action", 1)
    costs = {seam: sum(row["fee"] + row["lost_revenue"] for row in ledger if row["seam"] == seam)
             for seam in family.SEAMS}
    value = revenue - fees
    if v_ref - value != sum(costs.values()):
        return {"invalid": "ledger identity failed"}
    return {"reward": max(0.0, min(1.0, value / v_ref)), "V": value, "V_ref": v_ref,
            "revenue": revenue, "fees": fees, "costs": costs, "ledger": ledger, "rounds_closed": len(closed),
            "flags": flags, "stalled_seats": stalled,
            "seams": {s: {"failed": len(failed[s]), "opportunities": opportunities[s],
                          "unobserved": unobserved[s]} for s in family.SEAMS[:-1]}}
