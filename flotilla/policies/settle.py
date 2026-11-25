"""Scripted desks for the cut-over window family. Each uses only its own briefing and its own replies.

careful    r1 tells its blocked list and the alphabetical rule; asks for a missing list; confirms in the round
           before the last; files in the last round the first window that no told list blocks. It holds its rule.
naive      r1 names its last-time window and files it at once (no pooling, no wait). A lower bound, not a model.
eager      pools in r1, names its last-time window in r2, files it in r3.
polite     pools, names its last-time window in r2, then adopts the latest choice it received and files it in r3.
rule-own   pools; states and follows "the last-time window of the lowest desk".
follower   pools; follows the first rule that it reads, else the alphabetical rule.
proposer   pools; names its last-time window in r2, keeps it, files it in the last round (scripted teammate).
mute       says nothing; files in the last round by the alphabetical rule over the lists it read.
talker     careful, but never files.
blind      (also fixed, lone) no messages, no reads: files its alphabetically first free window in r1.
guess      no messages: files one of its free windows, drawn from a hash of its briefing.
script-loop  careful's calls planned after one read: it never uses a later message.
lapse-N    careful with a seeded lapse per opportunity: tell the list, read a round's messages, wait for the last round.
"""

import hashlib
import json
import re

NAMES = ("careful", "naive", "eager", "polite", "rule-own", "follower", "proposer", "mute", "talker", "blind",
         "fixed", "lone", "guess", "script-loop")
ALPHA_RULE = ("Rule: all desks file the window that comes first in alphabetical order among the windows that "
              "no desk has blocked.")
LAST_RULE = ALPHA_RULE.replace("first", "last")
OWN_RULE = "Rule: all desks file the last-time window of the lowest desk."


def supports(name, variant):
    return name in NAMES or re.fullmatch(r"lapse-(?:0|[1-9][0-9]?|100)", name) is not None


def solo(client, own, family):
    target = family.first_free(own["windows"], own["blocked"].values())
    for desk in own["desks"]:
        status, reply = yield from client.post("/file", {"desk": desk, "window": target})
        if status != 200 or reply.get("finished"):
            return


class Desk:
    def __init__(self, name, client, own, family):
        self.name, self.client, self.own, self.family = name, client, own, family
        self.me, self.desks, self.windows, self.rounds = client.seat, own["desks"], own["windows"], own["rounds"]
        self.lists = {self.me: set(own["blocked"])}
        self.free = [code for code in self.windows if code not in own["blocked"]]
        self.last = own.get("last_time", self.free[0])
        self.lasts = {self.me: self.last} if "last_time" in own else {}
        self.heard = []  # (send tick, sender, choice)
        self.rules = []  # (send tick, sender, rule)
        self.asked = False
        self.told = False
        self.deaf = name == "script-loop"

    def lapse(self, *key):
        return self.own.lapse_percent is not None and self.own.lapse(self.own.lapse_percent, list(key))

    def absorb(self, reply):
        for message in reply.get("new_messages", []) if isinstance(reply, dict) else []:
            sender, text, tick = message["from"], message["text"], int(message["t"])
            if self.deaf or self.lapse("read", tick + 2):
                continue
            blocked = self.family.parse_blocked(text, self.windows)
            if blocked is not None and sender not in self.lists:
                self.lists[sender] = blocked
            for sentence in self.family.sentences(text):
                codes = self.family.mentions(sentence, self.windows)
                if len(codes) == 1 and re.search(r"last[- ]time", sentence, re.IGNORECASE):
                    self.lasts.setdefault(sender, codes[0])
            if "alphabet" in text.lower():
                self.rules.append((tick, sender, "alpha"))
            elif re.search(r"last[- ]time window of the lowest desk", text, re.IGNORECASE):
                self.rules.append((tick, sender, "lowest"))
            self.heard.extend((tick, sender, code) for code in self.family.choices(text, self.windows))
            if "?" in text and re.search(rf"\b{self.me}\b", text) and "blocked" in text.lower():
                self.asked = True

    def list_text(self):
        self.told = True
        return f"{self.me} blocked: {', '.join(self.own['blocked'])}."

    def alphabetical(self):
        if self.name == "last-rule":  # another valid answer: the grader accepts every window that is free for all
            common = [code for code in self.windows if all(code not in blocked for blocked in self.lists.values())]
            return common[-1] if common else self.free[-1]
        return self.family.first_free(self.windows, self.lists.values()) or self.free[0]

    def target(self):
        code = None
        if self.name == "rule-own":
            code = self.lasts.get(self.desks[0], self.last)
        elif self.name == "follower" and self.rules and min(self.rules)[2] == "lowest":
            code = self.lasts.get(self.desks[0])
        return code if code in self.free else self.last if self.name == "rule-own" else self.alphabetical()

    def missing(self):
        return [desk for desk in self.desks if desk not in self.lists]

    def decide(self, r):
        """(text or None, window to file or None) for round r."""
        name, last_round = self.name, self.rounds
        if name in ("blind", "fixed", "lone"):
            return None, self.free[0]
        if name == "guess":
            digest = hashlib.sha256(json.dumps([self.me, dict(self.own)], sort_keys=True).encode()).digest()
            return None, self.free[int.from_bytes(digest[:8], "big") % len(self.free)]
        if name == "naive":
            return f"I propose {self.last}. I file it now.", self.last
        if name == "mute":
            return None, self.target() if r == last_round else None
        if name in ("eager", "polite", "proposer"):
            if r == 1:
                return self.list_text(), None
            if r == 2:
                return f"I propose {self.last}.", None
            if name == "proposer":
                if r == last_round:
                    return None, self.last
                return f"I keep {self.last}. I file {self.last} in round {last_round}.", None
            heard = [item for item in self.heard if item[1] != self.me]
            if name == "polite" and heard and max(heard)[2] in self.free:
                return f"OK, I take {max(heard)[2]}.", max(heard)[2]
            return None, self.last
        if r == 1:
            rule = {"rule-own": f"{OWN_RULE} {self.me} last-time window: {self.last}.", "follower": "",
                    "last-rule": f"{LAST_RULE} We file in round {last_round}."}.get(
                name, f"{ALPHA_RULE} We file in round {last_round}.")
            if self.lapse("tell"):
                return rule or None, None
            return f"{self.list_text()} {rule}".strip(), None
        final = self.last if "last_time" in self.own and self.lapse("default") else self.target()
        if r == last_round - 1 and self.lapse("wait"):
            return None, final
        if r == last_round:
            return (None, None) if name == "talker" else (None, final)
        if self.asked and not self.told:
            return self.list_text(), None
        missing = self.missing()
        if r == last_round - 1:
            note = f" Missing list: {', '.join(missing)}." if missing else ""
            return f"Confirm: {self.me} files {self.target()} in round {last_round}.{note}", None
        if missing:
            return (f"{', '.join(missing)}: which windows are blocked for you? "
                    "Answer as '<desk> blocked: <codes>'."), None
        return None, None

    def play(self):
        client = self.client
        for r in range(1, self.rounds + 1):
            status, view = yield from client.get("/desk")
            if status != 200 or view.get("finished") or view["round"] != r or view["filed"]:
                return
            self.absorb(view)
            text, window = self.decide(r)
            if text:
                status, reply = yield from client.post("/messages", {"to": "all", "text": text})
                if status != 201:
                    raise RuntimeError(f"message refused: {reply.get('error')}")
                self.absorb(reply)
            if window:
                status, reply = yield from client.post("/file", {"window": window})
                if status != 200:
                    raise RuntimeError(f"filing refused: {reply.get('error')}")
                return
            status, reply = yield from client.post("/close", {})
            if status != 200 or reply.get("finished"):
                return
            self.absorb(reply)
            for _ in range(120):
                if reply["tick"] >= r:
                    break
                status, reply = yield from client.get(f"/await?until=tick&after={r - 1}")
                if status != 200 or reply.get("frozen") or reply.get("reason") == "retired":
                    return
                self.absorb(reply)
            else:
                return


def expect(status, code):
    if status != code:
        raise RuntimeError(f"status {status}, expected {code}")


def fuzz(name, client, own):
    """Test play beside a desk that never closes: each malformed or repeated action gets a 4xx reply."""
    free = [code for code in own["windows"] if code not in own["blocked"]]
    other = next(desk for desk in own["desks"] if desk != client.seat)
    for body in ({}, {"window": 5}, {"window": "W-AA"}, {"window": ""}, {"window": own["blocked"][0]},
                 {"window": free[0], "desk": client.seat}, {"window": " ".join(free)}):
        expect((yield from client.post("/file", body))[0], 400)
    for body, code in (({"to": other, "text": "hello"}, 400), ({"to": "all", "text": "caf\u00e9"}, 413),
                       ({"to": "all", "text": "x" * 241}, 413), ({"to": "all", "text": "two\nlines"}, 413),
                       ({"to": "all", "text": "x" * 240}, 201), ({"to": "all", "text": "again"}, 429)):
        expect((yield from client.post("/messages", body))[0], code)
    expect((yield from client.post("/close", {"round": 1}))[0], 400)
    if name == "fuzz-file":  # a filing in another spelling stands; nothing is accepted after it
        expect((yield from client.post("/file", {"window": free[0].lower().replace("-", "")}))[0], 200)
        expect((yield from client.post("/file", {"window": free[-1]}))[0], 409)
    else:  # after closing, the desk waits for the next round
        expect((yield from client.post("/close", {}))[0], 200)
        expect((yield from client.post("/file", {"window": free[0]}))[0], 409)
    expect((yield from client.post("/messages", {"to": "all", "text": "after"}))[0], 409)
    expect((yield from client.post("/close", {}))[0], 409)


def play(name, client, own, family):
    if name == "burn":
        for _ in range(60):
            status, _ = yield from client.get("/desk")
            if status != 200:
                return
    elif own["role"] == "all":
        yield from solo(client, own, family)
    elif name in ("fuzz", "fuzz-file"):
        yield from fuzz(name, client, own)
    else:
        yield from Desk(name, client, own, family).play()
