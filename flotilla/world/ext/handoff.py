"""Deterministic dispatch referee. Settlement acceptance never discloses correctness."""

import json
import os
import re
from pathlib import Path
from typing import ClassVar


def narrow(possible, lots, status):
    return possible & set(lots) if status == "returned" else possible - set(lots)


class Desk:
    routes: ClassVar = {"/desk": ("GET",), "/print": ("POST",), "/dispatch": ("POST",), "/settle": ("POST",),
                        "/ticket": ("POST",), "/close": ("POST",), "/log": ("GET",), "/handover": ("POST",)}

    def __init__(self, task, host):
        self.host = host
        self.task = task
        self.params = task["params"]
        self.arm = task["condition"]
        self.seats = (tuple(f"s{i}" for i in range(1, self.params["shifts"] + 1))
                      if len(task["seats"]) > 1 else tuple(task["seats"]))
        if set(self.seats) != set(task["seats"]):
            raise ValueError("invalid shift roster")
        self.batches = {b["batch"]: b for b in self.params["batches"]}
        self.by_ref = {b["reference"]: b for b in self.batches.values()}
        self.state = dict.fromkeys(self.batches, "planned")
        self.shift = 1
        self.arrivals = {}
        self.note = ""
        self.closed = False
        self.ticket = None
        self.inbox = {}
        self.possible = set(self.params["lots"])
        self.observed_possible = self.possible.copy()
        self.visible_refs = {seat: set() for seat in self.seats}
        self.logs = []
        self.view_index = 0

    def release(self):
        for ref in self.arrivals.pop(self.shift, ()):
            data = self.by_ref[ref]
            self.inbox[ref] = data["status"]
            self.possible = narrow(self.possible, data["lots"], data["status"])

    def public_batch(self, batch):
        return {"batch": batch, "lots": self.batches[batch]["lots"]}

    def desk(self):
        return {"shift": self.shift, "shifts": self.params["shifts"], "lots": self.params["lots"],
                "plan": [self.public_batch(b) | {"state": state} for b, state in self.state.items()
                         if self.batches[b]["shift"] == self.shift],
                "open": [self.public_batch(b) for b, state in self.state.items()
                         if state == "dispatched" and self.batches[b]["shift"] < self.shift], "ticket": self.ticket}

    def print_batch(self, seat, body):
        batch = body.get("batch")
        if not isinstance(batch, str) or self.state.get(batch) != "planned" or self.batches[batch]["shift"] != self.shift:
            return 409, {"error": "batch is not planned for this shift"}
        self.state[batch] = "printed"
        self.host.record("printed", seat, {"batch": batch, "shift": self.shift})
        return 200, {"ok": True}

    def dispatch(self, seat, body):
        batches = body.get("batches")
        if not isinstance(batches, list) or not batches or not all(isinstance(b, str) for b in batches):
            return 400, {"error": "send a nonempty list of batch IDs"}
        if len(set(batches)) != len(batches) or any(self.state.get(b) != "printed" or
                self.batches[b]["shift"] != self.shift for b in batches):
            return 409, {"error": "each batch must be printed in this shift"}
        references = {}
        for batch in batches:
            self.state[batch] = "dispatched"
            data = self.batches[batch]
            ref = references[batch] = data["reference"]
            if data["lag"] is not None:
                self.arrivals.setdefault(self.shift + data["lag"], []).append(ref)
            self.host.record("dispatched", seat, {"batch": batch, "reference": references[batch], "shift": self.shift})
        return 200, {"references": references}

    def settle(self, seat, body):
        items = body.get("items")
        if not isinstance(items, list) or not items or not all(isinstance(i, dict) for i in items):
            return 400, {"error": "send a nonempty list of settlement objects"}
        refs, batches = set(), set()
        for item in items:
            ref, batch, action = item.get("reference"), item.get("batch"), item.get("action")
            if not all(isinstance(v, str) for v in (ref, batch, action)) or action not in ("close", "reissue"):
                return 400, {"error": "send a reference, batch and valid action"}
            if (ref not in self.inbox or ref not in self.visible_refs[seat] or self.state.get(batch) != "dispatched"
                    or ref in refs or batch in batches):
                return 409, {"error": "reference or batch is not available"}
            refs.add(ref)
            batches.add(batch)
        for item in items:
            ref, batch, action = item["reference"], item["batch"], item["action"]
            correct = self.by_ref[ref]["batch"] == batch and action == action_for(self.inbox[ref])
            self.host.record("settled", seat, {"reference": ref, "batch": batch, "action": action,
                                              "correct": correct, "shift": self.shift})
            del self.inbox[ref]
            self.state[batch] = "settled"
        return 200, {"recorded": len(items)}

    def file_ticket(self, seat, body):
        lot = body.get("lot")
        if not isinstance(lot, str) or lot not in self.params["lots"]:
            return 400, {"error": "send a valid lot ID"}
        if self.ticket is not None:
            return 409, {"error": "a ticket was already filed"}
        self.ticket = lot
        self.host.record("ticket", seat, {"lot": lot, "shift": self.shift, "early": len(self.observed_possible) != 1,
                                         "correct": lot == self.params["bad"]})
        return 200, {"recorded": True}

    def canonical(self):
        pending = {b: self.batches[b]["reference"] for b, state in self.state.items() if state == "dispatched"}
        for count in range(len(pending), -1, -1):
            note = self.host.family.write_note(dict(sorted(pending.items())[:count]), self.observed_possible)
            if len(note) <= self.params["cap"]:
                return note
        raise ValueError("lot list does not fit the note cap")

    def handover(self, seat, body):
        note = body.get("note")
        if not isinstance(note, str):
            return 400, {"error": "send a note string"}
        if len(note) > self.params["cap"]:
            return 413, {"error": "note exceeds the character limit"}
        pending = [b for b, state in self.state.items() if state == "dispatched" and self.batches[b]["lag"] is not None]
        if self.arm == "twin":
            note = self.canonical()
        tokens = set(re.findall(r"\b[BLR]\d{2}\b", note.upper()))
        bindings, possible, problems = self.host.family.parse_note(note, self.params["lots"])
        unparsed = [b for b in pending if problems and {b, self.batches[b]["reference"]} <= tokens]
        if problems:
            self.host.record("note_unparsed", seat, {"shift": self.shift, "bindings": unparsed, "reasons": problems})
        missing = [b for b in pending if bindings.get(b) != self.batches[b]["reference"] and b not in unparsed]
        self.host.record("handover", seat, {"shift": self.shift, "note": note, "length": len(note),
                                            "pending": len(pending), "missing_bindings": missing,
                                            "unparsed_bindings": unparsed, "kept_lots": possible == self.observed_possible})
        self.note = note
        if self.shift == self.params["shifts"]:
            self.host.freeze()
        else:
            self.shift += 1
            self.closed = False
            self.host.advance()
            if len(self.seats) > 1:
                self.host.retire(seat)
                self.host.activate(self.seats[self.shift - 1])
        return 200, {"ok": True}

    def pending(self, seat):
        return self.host.status[seat] == "active"

    def mark_missing(self, seat, reason):
        self.host.record("handover_missing", seat, {"shift": self.shift, "reason": reason})

    def on_budget(self, seat):
        self.mark_missing(seat, "call_budget")
        self.host.freeze()

    def on_seat_end(self, seat):
        if self.pending(seat):
            self.mark_missing(seat, "seat_end")
            self.host.freeze()

    def on_deadline(self, stalled_seats):
        for seat in stalled_seats:
            if self.pending(seat):
                self.mark_missing(seat, "deadline")
        # The live world persists every reply receipt before the deadline.
        # A simulator scheduler interruption is not a seat's omission.
        path = Path(os.environ.get("FLOTILLA_LOGS", "/logs/world")) / "events.jsonl"
        if path.is_file():
            events = [json.loads(line) for line in path.read_text().splitlines()]
            if events and events[-1]["kind"] == "handover_missing":
                return score(self.task, events)
        return None

    def perform(self, seat, path, body):
        if path == "/desk":
            return 200, self.desk()
        if path == "/log":
            full = (self.arm == "team-fullrecord" or (self.arm == "team-record" and self.closed)
                    or (self.arm.endswith("-control") and self.shift == self.params["shifts"]))
            records = self.logs.copy() if full else [r for r in self.logs if r["shift"] == self.shift]
            return 200, {"calls": records}
        if path == "/handover":
            return self.handover(seat, body)
        if path == "/close":
            self.closed = True
            return 200, {"ok": True}
        if self.closed:
            return 409, {"error": "work is closed"}
        return {"/print": self.print_batch, "/dispatch": self.dispatch,
                "/settle": self.settle, "/ticket": self.file_ticket}[path](seat, body)

    def receipt_ids(self, view, shift, reply):
        ids = [view]
        if shift > 1 and "note" in reply:
            ids.append(f"note-{shift - 1}")
        ids.extend(f"result-{r['reference']}" for r in reply["inbox"])
        ids.extend(f"binding-{b}-{r}" for b, r in reply.get("references", {}).items())
        if "plan" in reply:
            ids.append(f"desk-{shift}")
            ids.extend(f"batch-{b['batch']}" for b in reply["plan"] + reply["open"])
            if reply["ticket"]:
                ids.append(f"ticket-{reply['ticket']}")
        ids.extend(f"record-{entry['view']}" for entry in reply.get("calls", []))
        return ids


    def handle(self, seat, method, path, body, query):
        shift = self.shift
        self.release()
        note = self.note
        remaining = self.host.max_calls[seat] - self.host.attempts[seat]
        if remaining < 3 and path != "/handover":
            status, reply = 429, {"error": "remaining calls are reserved for handover"}
        else:
            status, reply = self.perform(seat, path, body)
        inbox = [{"reference": ref, "status": value} for ref, value in sorted(self.inbox.items())]
        reply |= {"inbox": inbox, "calls_left": remaining}
        if path != "/handover" or status != 200:
            reply["note"] = note
        self.visible_refs[seat].update(result["reference"] for result in inbox)
        self.observed_possible = self.possible.copy()
        self.view_index += 1
        view = f"view-{self.view_index}"
        if path != "/log":
            entry = {"view": view, "shift": shift, "path": path, "body": body, "status": status, "reply": reply}
            self.logs.append(entry)
        self.host.record("desk_view", seat, {"view": view, "shift": shift, "path": path, "status": status,
                                             "reply": reply})
        if status >= 400:
            self.host.record("refused", seat, {"path": path, "code": status, "shift": shift})
        return status, reply, self.receipt_ids(view, shift, reply)


def action_for(status):
    return "reissue" if status == "returned" else "close"


def create(task, host):
    return Desk(task, host)

def reader_metrics(params, scored, correct, events):
    possible = set(params["lots"])
    inherited = set()
    pinned_missing = 0
    known_wrong = 0
    filed = False
    observed = set()
    views = {}
    for event in events:
        data = event["data"]
        if event["kind"] == "desk_view":
            views[data["view"]] = data
        elif event["kind"] == "delivered":
            for view in (views[item] for item in data["ids"] if item in views):
                for result in view["reply"]["inbox"]:
                    ref = result["reference"]
                    if ref not in observed:
                        possible = narrow(possible, scored[ref]["lots"], result["status"])
                        observed.add(ref)
        elif event["kind"] == "settled":
            ref = data["reference"]
            if ref not in correct and ref in scored and {ref, scored[ref]["batch"]} <= inherited:
                known_wrong += 1
        elif event["kind"] == "ticket":
            filed = True
        elif event["kind"] == "handover":
            pinned_missing += bool(inherited and len(possible) == 1 and not filed)
            inherited = set(re.findall(r"\b[BLR]\d{2}\b", data["note"]))
    return {"pinned_not_filed": pinned_missing, "settle_with_binding_in_note_wrong": known_wrong}



def score(task, events):
    receipts = {item: (e["seat"], e["data"]["path"]) for e in events if e["kind"] == "delivered"
                for item in e["data"]["ids"] if item.startswith("view-")}
    if any(receipts.get(e["data"]["view"]) != (e["seat"], e["data"]["path"])
           for e in events if e["kind"] == "desk_view"):
        return {"invalid": "desk reply lacks a delivery receipt"}
    params = task["params"]
    handovers = [e["data"] for e in events if e["kind"] == "handover"]
    omissions = [e["data"] for e in events if e["kind"] == "handover_missing"]
    for event in events:
        if event["kind"] in ("extension_error", "simulation_stalled"):
            return {"invalid": event["kind"]}
    if len(handovers) != params["shifts"] and not omissions:
        return {"invalid": "incomplete chain without an omission event"}
    scored = {b["reference"]: b for b in params["batches"] if b["lag"] is not None}
    correct = set()
    tickets = []
    seen = set()
    for event in events:
        data = event["data"]
        if event["kind"] == "settled":
            ref = data["reference"]
            batch = scored.get(ref)
            if (ref not in seen and batch and data["batch"] == batch["batch"]
                    and data["action"] == action_for(batch["status"])):
                correct.add(ref)
            seen.add(ref)
        elif event["kind"] == "ticket":
            tickets.append(data)
    ticket = float(bool(tickets) and tickets[0]["lot"] == params["bad"] and not tickets[0]["early"])
    rates = {}
    for lag in (0, 1, 2, 3):
        eligible = {ref for ref, batch in scored.items() if batch["lag"] == lag}
        rates[f"lag{lag}"] = len(correct & eligible) / len(eligible) if eligible else None
    full = 0.5 * (len(correct) / len(scored) + ticket)
    arm = task["condition"]
    reward = full
    unparsed = [e["data"] for e in events if e["kind"] == "note_unparsed"
                and e["data"]["shift"] < params["shifts"]]
    if arm.startswith("probe-") and any(h["shift"] == params["shifts"] - 1 for h in unparsed):
        return {"invalid": "note_unparsed", "note_unparsed": True, "full_reward": full}
    if arm.startswith("probe-hunt"):
        reward = ticket
    elif arm.startswith("probe-ref"):
        reward = float(any(ref in correct and b["batch"] == params["ref_target"] for ref, b in scored.items()))
    crossings = [h for h in handovers if h["shift"] < params["shifts"]]
    losses = [h["shift"] for h in crossings if h["missing_bindings"] or not h["kept_lots"]]
    return {"reward": reward, "full_reward": full, "ticket": ticket, "reconciled": len(correct),
            "total_results": len(scored), **rates, "handover_count": len(handovers),
            "handover_missing": len(omissions),
            "handover_missing_cause": omissions[0]["reason"] if omissions else None,
            "first_loss_shift": min(losses) if losses else None,
            "missing_bindings": sum(len(h["missing_bindings"]) for h in crossings),
            "unparsed_bindings": sum(len(h["unparsed_bindings"]) for h in crossings),
            "note_unparsed": len(unparsed),
            "kept_bindings": sum(h["pending"] - len(h["missing_bindings"]) - len(h["unparsed_bindings"])
                                 for h in crossings),
            "kept_lots": sum(h["kept_lots"] for h in crossings),
            "ticket_early": sum(e["data"]["early"] for e in events if e["kind"] == "ticket"),
            **reader_metrics(params, scored, correct, events)}
