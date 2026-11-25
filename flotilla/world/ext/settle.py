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
                 "tick": self.host.tick, "windows": p.get("orders", {}).get(seat, p["windows"]), "finished": self.over}
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
            order = p.get("orders", {}).get(seat, p["windows"])
            return self.refuse(seat, "file", 400, f"unknown window; the windows are {', '.join(order)}")
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


def failure_shape(desks, files, lists, told, named, rounds, agreed, feasible, hasty):
    """One label from events only. A rule can suffice without a named proposal."""
    never = rounds + 2
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


def filing_evidence(params, desk, filing, visible):
    """Use only the desk's own list and messages available before its filing."""
    windows = params["windows"]
    reports = {desk: set(params["blocked"][desk])}
    rules = {}
    for send in sorted(visible, key=lambda msg: (msg["round"], msg["seat"])):
        sender = send["seat"]
        if sender != desk and send["blocked"] is not None:
            reports[sender] = send["blocked"]
        if send["rule"]:
            rules[sender] = send
    if set(reports) != set(params["desks"]):
        return False, None
    common = [code for code in windows if all(code not in blocked for blocked in reports.values())]
    if not common:
        return False, None
    candidates = set()
    own_order = params.get("orders", {}).get(desk, windows)
    for send in rules.values():
        rule = send["rule"]
        if rule == "alpha":
            candidates.add(min(common))
        elif rule == "alpha-last":
            candidates.add(max(common))
        elif rule == "list-order":
            order = family.mentions(" ".join(sentence for sentence in family.sentences(send["text"])
                                              if family.proposed_rule(sentence) == "list-order"), windows)
            if set(order) >= set(common):
                candidates.add(next(code for code in order if code in common))
            elif "orders" not in params:
                candidates.add(common[0])
            else:
                candidates.add(None)
        else:
            candidates.add(None)
    if len(common) == 1:
        target = common[0]
    elif len(candidates) == 1 and None not in candidates:
        target = next(iter(candidates))
    else:
        # A single current, feasible named choice may settle the choice even without a shared rule.
        received = [send for send in visible if send["seat"] != desk and send["choices"]]
        latest = max((send["round"] for send in received), default=0)
        names = {code for send in visible if send["round"] == latest for code in send["choices"] if code in common}
        target = next(iter(names)) if received and len(names) == 1 else None
    rule_round = min((send["round"] for send in visible if send["rule"]), default=params["rounds"] + 1)
    rule_target = target
    if any(send["rule"] == "list-order" for send in rules.values()) and target is None:
        rule_target = next(code for code in own_order if code in common)
    named_later = any(send["seat"] != desk and send["round"] > rule_round
                      and filing["window"] in send["choices"] for send in visible)
    on_rule = bool(rules) and filing["window"] == rule_target and not named_later
    return on_rule, target


def message_evidence(params, events, files, lists, receipts):
    sends, seen, told, named, readable, intent = [], {}, {}, {}, set(), set()
    for event in events:
        kind, desk, data = event["kind"], event["seat"], event["data"]
        if kind == "deliver":
            for item in data["ids"]:
                seen.setdefault((desk, item), event["seq"])
        elif kind == "send":
            text, round_ = data["text"], int(event["t"]) + 1
            blocked = family.parse_blocked(text, params["windows"], desk)
            if blocked is not None:
                readable.add(desk)
                if blocked == lists[desk]:
                    told.setdefault(desk, round_)
            elif family.report_intent(text, params["windows"]):
                intent.add(desk)
            choices = family.choices(text, params["windows"])
            for window in choices:
                named.setdefault((desk, window), round_)
            sends.append({"seat": desk, "round": round_, "text": text, "id": data["id"], "seq": event["seq"],
                          "blocked": blocked, "choices": choices, "rule": family.proposed_rule(text)})

    def reached(desk, send):
        if desk not in files:
            return (desk, send["id"]) in seen if receipts else send["round"] + params["delay"] <= params["rounds"]
        if desk == send["seat"]:
            return send["seq"] < files[desk]["seq"]
        if receipts:
            at = seen.get((desk, send["id"]))
            return at is not None and at < files[desk]["seq"]
        return send["round"] + params["delay"] <= files[desk]["round"]

    complete, filed_on_rule, divergent = {}, [], []
    for desk in params["desks"]:
        visible = [send for send in sends if reached(desk, send)]
        latest = {send["seat"]: send["blocked"] for send in sorted(visible, key=lambda msg: msg["round"])
                  if send["blocked"] is not None}
        complete[desk] = {sender for sender, blocked in latest.items() if blocked == lists[sender]} | {desk}
        if desk in files:
            on_rule, target = filing_evidence(params, desk, files[desk], visible)
            if on_rule:
                filed_on_rule.append(desk)
            if target is not None and files[desk]["window"] != target:
                divergent.append(desk)
    first_round = min((filing["round"] for filing in files.values()), default=params["rounds"] + 1)
    filers = [desk for desk in files if files[desk]["round"] == first_round] or params["desks"]
    pooled = set(told).intersection(*(complete[desk] for desk in filers))
    hasty = sorted(desk for desk in files if complete[desk] != set(params["desks"]))
    first_divergent = min(divergent, key=lambda desk: (files[desk]["round"], desk), default=None)
    return {"told": told, "named": named, "hasty": hasty, "pooled": len(pooled) / len(params["desks"]),
            "unparsed": sorted(intent - readable), "filed_on_rule": sorted(filed_on_rule), "first_divergent": first_divergent,
            "messages": len(sends), "questions": sum("?" in send["text"] for send in sends)}


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
    evidence = message_evidence(p, events, files, lists, receipts)
    told, hasty = evidence["told"], evidence["hasty"]
    shape, _ = failure_shape(desks, files, lists, told, evidence["named"], rounds, agreed, feasible, hasty)
    result |= {
        "shape": "none" if result["reward"] else shape, "premature_filing": hasty,
        "filed_on_rule": evidence["filed_on_rule"],
        "first_divergent": None if result["reward"] else evidence["first_divergent"],
        "pooled": evidence["pooled"], "pooled_basis": "receipts" if receipts else "send rounds",
        "told": {desk: told.get(desk) for desk in desks}, "unparsed": evidence["unparsed"],
        "messages": evidence["messages"], "questions": evidence["questions"],
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
