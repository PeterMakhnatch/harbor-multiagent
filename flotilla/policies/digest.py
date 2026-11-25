"""Scripted workers use only the manual, amendments and delivered notes."""

import hashlib
from collections import Counter


def supports(name, variant):
    return (name in ("careful", "naive", "naive_reader", "lone", "lane_only", "hold_all", "uniform_random",
                     "blind", "random", "copy", "append", "newest_only", "guess-mode", "guess-position",
                     "renumber", "reorder", "no-usage",
                     "lapse-10", "lapse-20", "lapse-30", "lapse-50")
            or name.startswith("constant-"))


def wait_for_shift(client):
    status, me = yield from client.get("/await?until=active")
    return status == 200 and not me["frozen"] and me["active"] == "active"


def decode(family, own, reply, name):
    text = reply.get("note", "")
    if "manual" in own:
        home, rules = family.parse_manual(own["manual"])
    elif "record" in reply:
        record = reply["record"]
        home, rules = family.parse_manual(record["manual"])
        for entry in record["history"]:
            _, amendments = family.parse_manual(entry["amendment"])
            rules = family.merge_amendments(rules, amendments)
    else:
        try:
            home, rules = family.parse_note(text, own.get("format") == "card", own.get("ordered", False))
        except (ValueError, IndexError, KeyError, AttributeError, TypeError):
            home, rules = {}, []
    if "amendment" in own and (name not in ("copy", "append", "naive") or own.get("fullrecord")):
        _, amendments = family.parse_manual(own["amendment"])
        if name == "renumber" and not own.get("fullrecord"):
            ids = {r["id"] for r in rules}
            amendments = [r for r in amendments if r["id"] in ids]
        if name == "newest_only":
            rules = amendments
        elif name in ("careful", "no-usage") and not (
                own.get("revision") or own.get("fullrecord") or family.USAGE_LINE in text.splitlines()):
            changed = {r["id"] for r in amendments}
            rules = [r for r in rules if r["id"] not in changed] + amendments
        else:
            rules = family.merge_amendments(rules, amendments)
    return home, rules


def solve_job(family, client, job, home, rules, name, position):
    try:
        _, _, action = family.decision(rules, job)
    except (ValueError, AttributeError, TypeError):
        action = "hold"
    if name == "lane_only":
        action = home.get(job["product"], "hold")
    replies = {}
    guessing = name in ("lone", "uniform_random", "blind", "random", "guess-mode", "guess-position")
    if guessing or name.startswith("constant-"):
        for check in family.CHECKS:
            status, result = yield from client.post("/check", {"job": job["id"], "what": check})
            if status != 200:
                raise RuntimeError("check not accepted")
            replies[check] = result["reply"]
    if name == "guess-mode":
        counts = Counter(home.values())
        for rule in rules:
            for _, value in rule["clauses"]:
                outcomes = [item.split("=")[1] for item in value.split()[1:]] if value.startswith("check:") else [value]
                counts.update(outcome for outcome in outcomes if outcome in family.OUTCOMES)
        outcome = counts.most_common(1)[0][0] if counts else "hold"
    elif name == "guess-position":
        outcome = family.OUTCOMES[position % len(family.OUTCOMES)]
    elif guessing:
        number = int.from_bytes(hashlib.sha256(job["id"].encode()).digest()[:8], "big")
        outcome = family.OUTCOMES[number % len(family.OUTCOMES)]
    elif name.startswith("constant-"):
        outcome = name.removeprefix("constant-")
    elif name == "hold_all":
        outcome = "hold"
    else:
        if action.startswith("check:"):
            check = action.split()[0][6:]
            status, result = yield from client.post("/check", {"job": job["id"], "what": check})
            if status == 200:
                replies[check] = result["reply"]
        try:
            _, outcome = family.resolve(action, home, job, replies)
        except (ValueError, KeyError, IndexError):
            outcome = "hold"
    if outcome not in family.OUTCOMES:
        outcome = "hold"
    move = {"job": job["id"], "do": "hold" if outcome == "hold" else "send"}
    if outcome != "hold":
        move["to"] = outcome
    return move


def main_only(rules):
    return [r | {"clauses": r["clauses"][:1]} for r in rules]


def note_to_send(family, own, home, rules, text, cap, name):
    if name in ("lone", "blind", "random", "uniform_random"):
        return "No rules recorded."
    card = own.get("format") == "card"
    def render(home, rules):
        return family.card_text(home, rules, own.get("ordered", False)) if card else family.note_text(home, rules)
    if not card and not own.get("fullrecord"):
        if name == "renumber":
            rules = [rule | {"id": i} for i, rule in enumerate(rules, 1)]
        elif name == "reorder":
            rules = sorted(rules, key=lambda rule: rule["id"])
    if own["role"] == "relay" and not own.get("fullrecord"):
        if name == "copy":
            return text
        if name == "naive":
            return render(home, rules[1:])
        if name == "append":
            _, amendments = family.parse_manual(own["amendment"])
            addition = "\n".join(family.note_text(home, amendments).splitlines()[1:])
            combined = text + "\n" + addition
            return combined if len(combined) <= cap else text
    if card and (name == "naive" or own.lapse_percent is not None):
        full = render(home, rules)
        spacious = full.replace("=>", "                    =>                    ")
        if len(spacious) <= cap:
            return spacious
        if name == "naive":
            return render(home, main_only(rules))
    if own.lapse_percent is not None:
        home = {p: lane for p, lane in home.items() if not own.lapse(own.lapse_percent, ["home", p])}
        rules = [r for r in rules if not own.lapse(own.lapse_percent, ["keep-rule", r["id"]])]
    output = render(home, rules)
    if name == "careful" and not card and cap is not None and (
            own["role"] == "planner" or family.USAGE_LINE in text.splitlines()):
        with_usage = output + "\n" + family.USAGE_LINE
        if len(with_usage) <= cap:
            return with_usage
    return output


def play(name, client, own, family):
    if not (yield from wait_for_shift(client)):
        return
    text = ""
    home, rules = {}, []
    count = 0
    for page in range(100):
        status, reply = yield from client.get("/work")
        if status != 200:
            return
        if page == 0:
            text = reply.get("note", "")
            home, rules = decode(family, own, reply, name) if name != "lone" else ({}, [])
        if reply["done"]:
            break
        actions = []
        for job in reply["jobs"]:
            active_rules = rules
            if (own["role"] == "worker" and "manual" not in own and not own.get("fullrecord")
                    and not own.get("format") and count >= 12 and (name in ("naive", "naive_reader") or (
                        own.lapse_percent is not None and own.lapse(own.lapse_percent, ["read", job["id"]])))):
                active_rules = main_only(rules)
            actions.append((yield from solve_job(family, client, job, home, active_rules, name, count)))
            count += 1
        status, _ = yield from client.post("/act", {"actions": actions})
        if status != 200:
            raise RuntimeError("action not accepted")
    else:
        raise RuntimeError("too many pages")
    if own["role"] == "worker":
        return
    status, measure = yield from client.post("/note/check", {"text": "Measure note cap."})
    if status != 200:
        return
    output = note_to_send(family, own, home, rules, text, measure["limit"], name)
    if measure["limit"] is not None and len(output) > measure["limit"]:
        raise RuntimeError("policy note exceeds cap")
    status, _ = yield from client.post("/note/seal", {"text": output})
    if status != 201:
        raise RuntimeError("seal not accepted")
