"""Round barrier, final filings, receipts and event scoring for the cut-over window family."""

from typing import ClassVar

import family

SHAPES = ("premature", "crossed", "unpooled", "rule_clash", "late")


class Settle:
    routes: ClassVar = {"/desk": ("GET",), "/file": ("POST",), "/close": ("POST",)}

    def __init__(self, task, host):
        self.host, self.task, self.p = host, task, task["params"]
        self.seats = list(task["seats"])
        self.single = len(self.seats) == 1
        self.filed = {}
        self.closed, self.said, self.out = set(), set(), set()
        self.over = False
        self.log = []  # the family's own evidence, kept for a cut-off play

    @property
    def round(self):
        return self.host.tick + 1

    def note(self, kind, seat, data):
        self.log.append({"seq": len(self.log) + 1, "t": float(self.host.tick), "seat": seat, "kind": kind,
                         "data": data})

    def record(self, kind, seat, data):
        self.host.record(kind, seat, data)
        self.note(kind, seat, data)

    def refuse(self, seat, action, status, error):
        self.record("settle_refuse", seat, {"action": action, "status": status, "round": self.round})
        return status, {"error": error, "round": self.round, "tick": self.host.tick, "finished": self.over}

    def unfiled(self, seat):
        return [desk for desk in (self.p["desks"] if self.single else [seat]) if desk not in self.filed]

    # Core hooks.
    def pending(self, seat):
        return not self.over and seat not in self.out and bool(self.unfiled(seat))

    def on_deadline(self, stalled_seats):
        if getattr(self.host, "deadline_kind", "") == "simulation_stalled":
            return None  # a simulator limit is not a seat's omission
        self.record("settle_incomplete", None, {"stalled_seats": stalled_seats})
        return score(self.task, self.log, receipts=False)

    def on_budget(self, seat):
        self.record("settle_budget", seat, {"round": self.round})
        self.out.add(seat)
        self.barrier()

    def message_delay(self, message, recipient):
        return self.p["delay"]

    def allow_message(self, message):
        sender, text = message["from"], message["text"]
        if sender in self.filed:
            return 409, "you have filed: your part is over"
        if sender in self.closed:
            return 409, f"you closed round {self.round}: wait for the next round"
        if message["to"] != "all":
            return 400, 'a message goes to all desks: use "to": "all"'
        if sender in self.said:
            return 429, f"you already sent your message of round {self.round}"
        if len(text) > self.p["text_cap"] or any(not 32 <= ord(char) < 127 for char in text):
            return 413, f"a message is one line of plain ASCII text, at most {self.p['text_cap']} characters"
        return None

    def on_message(self, message):
        self.said.add(message["from"])
        self.note("send", message["from"], {"id": message["id"], "to": message["to"], "text": message["text"]})

    # Routes.
    def handle(self, seat, method, path, body, query):
        if path == "/desk":
            return self.desk(seat)
        if path == "/file":
            return self.file(seat, body)
        return self.close(seat, body)

    def desk(self, seat):
        p = self.p
        reply = {"round": self.round, "rounds": p["rounds"], "rounds_left": p["rounds"] - self.round,
                 "tick": self.host.tick, "windows": p["windows"], "finished": self.over}
        if self.single:
            reply |= {"desks": p["desks"], "blocked": p["blocked"], "last_time": p["last_time"],
                      "filed": {desk: self.filed.get(desk) for desk in p["desks"]}}
        else:
            reply |= {"desk": seat, "blocked": p["blocked"][seat], "filed": self.filed.get(seat),
                      "closed": seat in self.closed, "message_sent": seat in self.said}
            if seat in p["last_time"]:
                reply["last_time"] = p["last_time"][seat]
        item = f"desk:{seat}:{self.round}"
        self.record("settle_view", seat, {"round": self.round, "item": item})
        return 200, reply, [item]

    def file(self, seat, body):
        p = self.p
        if set(body) != ({"desk", "window"} if self.single else {"window"}):
            example = '{"desk": "<desk>", "window": "<code>"}' if self.single else '{"window": "<code>"}'
            return self.refuse(seat, "file", 400, f"send {example}")
        desk = body["desk"] if self.single else seat
        if not isinstance(desk, str) or desk not in p["desks"]:
            return self.refuse(seat, "file", 400, f"unknown desk; the desks are {', '.join(p['desks'])}")
        window = family.window_code(body["window"], p["windows"])
        if window is None:
            return self.refuse(seat, "file", 400, f"unknown window; the windows are {', '.join(p['windows'])}")
        if desk in self.filed:
            return self.refuse(seat, "file", 409, "this desk has filed already; a filing is final")
        if seat in self.closed:
            return self.refuse(seat, "file", 409, f"you closed round {self.round}: wait for the next round")
        if window in p["blocked"][desk]:
            return self.refuse(seat, "file", 400, f"desk {desk} cannot use {window}: it is on its blocked list")
        self.filed[desk] = {"window": window, "round": self.round}
        self.record("settle_file", seat, {"desk": desk, "window": window, "round": self.round})
        self.barrier()
        return 200, {"desk": desk, "filed": self.filed[desk], "tick": self.host.tick, "finished": self.over}

    def close(self, seat, body):
        if body:
            return self.refuse(seat, "close", 400, "send an empty object")
        if self.single:
            return self.refuse(seat, "close", 409, "there are no rounds to close: file for every desk")
        if seat in self.filed:
            return self.refuse(seat, "close", 409, "you have filed: your part is over")
        if seat in self.closed:
            return self.refuse(seat, "close", 409, f"you closed round {self.round} already: wait for the next round")
        closed = self.round
        self.closed.add(seat)
        self.record("settle_close", seat, {"round": closed})
        self.barrier()
        return 200, {"closed_round": closed, "round": self.round, "tick": self.host.tick, "finished": self.over}

    def barrier(self):
        """Logical time only: a round ends when every desk that can still act has closed it."""
        for _ in range(self.p["rounds"] + 1):
            if self.over:
                return
            if all(desk in self.filed for desk in self.p["desks"]):
                self.finish()
                return
            live = [seat for seat in self.seats if seat not in self.filed and seat not in self.out]
            if self.single or any(seat not in self.closed for seat in live):
                return
            self.record("settle_round", None, {"round": self.round})
            if self.round >= self.p["rounds"]:
                self.finish()
                return
            self.closed.clear()
            self.said.clear()
            self.host.advance()

    def finish(self):
        self.over = True
        self.record("settle_end", None, {"round": self.round})
        self.host.freeze()


def create(task, host):
    return Settle(task, host)


def failure_shape(desks, files, lists, told, named, rounds, agreed, feasible):
    """One label from events only: who filed in which round, what was named when, which lists were told."""
    never = rounds + 2

    def backed(desk):
        window, at = files[desk]["window"], files[desk]["round"]
        return all(named.get((other, window), never) < at for other in desks if other != desk)

    hasty = sorted(desk for desk in files if files[desk]["round"] < rounds and not backed(desk))
    for desk in files:
        window, at = files[desk]["window"], files[desk]["round"]
        for other in files:
            first = named.get((other, window), never)
            if (other != desk and files[other]["window"] != window and first < at
                    and named.get((desk, window), never) > first):
                return "crossed", hasty
    if hasty:
        return "premature", hasty
    if len(files) < len(desks):
        return "late", hasty
    for desk in files:
        window, at = files[desk]["window"], files[desk]["round"]
        if any(window in lists[other] and told.get(other, never) >= at for other in desks if other != desk):
            return "unpooled", hasty
    return ("unpooled" if agreed and not feasible else "rule_clash"), hasty


def score(task, events, receipts=True):
    p = task["params"]
    desks, windows, rounds = p["desks"], p["windows"], p["rounds"]
    lists = {desk: set(blocked) for desk, blocked in p["blocked"].items()}
    files = {}
    for event in events:
        if event["kind"] == "settle_file":
            data = event["data"]
            if data["desk"] in files or data["window"] not in windows or not 1 <= data["round"] <= rounds:
                return {"invalid": "filing evidence is inconsistent"}
            files[data["desk"]] = {"window": data["window"], "round": data["round"], "seq": event["seq"]}
    closed = [event["data"]["round"] for event in events if event["kind"] == "settle_round"]
    if closed != list(range(1, len(closed) + 1)) or len(closed) > rounds:
        return {"invalid": "round evidence is inconsistent"}
    not_filed = [desk for desk in desks if desk not in files]
    chosen = sorted({filing["window"] for filing in files.values()})
    agreed = not not_filed and len(chosen) == 1
    feasible = bool(files) and all(window not in lists[desk] for window in chosen for desk in desks)
    result = {
        "reward": float(agreed and feasible), "agreed": agreed, "feasible": feasible,
        "filed": {desk: {k: files[desk][k] for k in ("window", "round")} if desk in files else None
                  for desk in desks},
        "not_filed": not_filed,
        "rounds_used": min(rounds, len(closed) + 1),
        "budget_exhausted": sorted({event["seat"] for event in events if event["kind"] == "settle_budget"}),
        "refused": sum(event["kind"] == "settle_refuse" for event in events),
        "cut_off": any(event["kind"] == "settle_incomplete" for event in events),
    }
    if len(task["seats"]) == 1:
        return result
    sends = [{"seat": event["seat"], "round": int(event["t"]) + 1, "text": event["data"]["text"],
              "id": event["data"]["id"]} for event in events if event["kind"] == "send"]
    told, tell_id, unparsed, named = {}, {}, [], {}
    for send in sends:
        desk = send["seat"]
        if desk in lists and desk not in told and family.tells(send["text"], windows, lists[desk]):
            told[desk], tell_id[desk] = send["round"], send["id"]
            if family.parse_blocked(send["text"], windows) != lists[desk]:
                unparsed.append(desk)
        for window in family.choices(send["text"], windows):
            named.setdefault((desk, window), send["round"])
    shape, hasty = failure_shape(desks, files, lists, told, named, rounds, agreed, feasible)
    first_round = min((filing["round"] for filing in files.values()), default=rounds + 1)
    if receipts:
        first_seq = min((filing["seq"] for filing in files.values()), default=float("inf"))
        seen = {}
        for event in events:
            if event["kind"] == "deliver":
                for item in event["data"]["ids"]:
                    seen.setdefault((event["seat"], item), event["seq"])
        pooled = [desk for desk in told if all(seen.get((other, tell_id[desk]), float("inf")) < first_seq
                                               for other in desks if other != desk)]
    else:
        pooled = [desk for desk in told if told[desk] + p["delay"] < first_round]
    result |= {
        "shape": "none" if result["reward"] else shape, "premature_filing": hasty,
        "pooled": len(pooled) / len(desks), "pooled_basis": "receipts" if receipts else "send rounds",
        "told": {desk: told.get(desk) for desk in desks}, "unparsed": sorted(unparsed),
        "messages": len(sends), "questions": sum("?" in send["text"] for send in sends),
    }
    if subject := p["subject"]:
        mates = sorted({files[desk]["window"] for desk in files if desk != subject})
        own = files.get(subject)
        result["subject"] = {
            "seat": subject, "filed": own and own["window"], "round": own and own["round"],
            "matches_teammates": bool(own) and mates == [own["window"]],
            "filed_last_time": bool(own) and own["window"] == p["last_time"].get(subject),
            "premature": subject in hasty, "told_list": told.get(subject),
        }
    return result
