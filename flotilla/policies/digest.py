"""Scripted desk workers. Every decision uses the manual or the received note, not an answer key."""

import hashlib
import math


def supports(name, variant):
    return (name in ("careful", "naive", "naive_reader", "lone", "lane_only", "hold_all", "uniform_random") or
            name.startswith("constant-"))


def wait_for_shift(client):
    for _ in range(600):
        status, me = yield from client.get("/whoami")
        if status != 200 or me["frozen"] or me["active"] == "retired":
            return False
        if me["active"] == "active":
            return True
        yield from client.sleep(0.2)
    return False


def decode(family, own, text):
    if "manual" in own:
        return family.parse_manual(own["manual"])
    try:
        return family.parse_note(text, own.get("format") == "card")
    except (ValueError, IndexError, KeyError, AttributeError, TypeError):
        return {}, []


def decision(family, job, rules, home):
    try:
        rid, _, action = family.decision(rules, job)
    except (ValueError, AttributeError, TypeError):
        return 0, "hold"
    if action == "home" and job["product"] not in home:
        action = "hold"
    return rid, action


def solve_job(family, client, job, home, rules, name):
    rid, action = decision(family, job, rules, home)
    replies = {}
    checks = tuple(family.CHECKS) if name in ("lone", "uniform_random") or name.startswith("constant-") else ()
    if checks:
        for check in checks:
            status, result = yield from client.post("/check", {"job": job["id"], "what": check})
            if status != 200:
                raise RuntimeError("check not accepted")
            replies[check] = result["reply"]
    if name in ("lone", "uniform_random"):
        number = int.from_bytes(hashlib.sha256(job["id"].encode()).digest()[:8], "big")
        outcome = family.OUTCOMES[number % len(family.OUTCOMES)]
    elif name.startswith("constant-"):
        outcome = name.removeprefix("constant-")
    elif name == "hold_all":
        outcome = "hold"
    else:
        if name == "lane_only":
            action = home.get(job["product"], "hold")
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
    return rid, move


def main_only(rules):
    return [r | {"clauses": r["clauses"][:1]} for r in rules]


def note_to_send(family, own, home, rules, exercised, text, cap, name):
    if name == "lone":
        return ""
    card = own.get("format") == "card"
    render = family.card_text if card else family.note_text
    full = render(home, rules)
    if name != "naive":
        return text if own["role"] == "relay" else full
    # With room, a writer can retain every clause without condensing the card's layout.
    if card:
        spacious = "\n".join(line.replace("=>", "        =>        ") for line in full.splitlines())
        if len(spacious) <= cap:
            return spacious
    elif own["role"] == "planner" and len(own["manual"]) <= cap:
        return full
    unused = [r["id"] for r in rules if r["id"] not in exercised]
    remove = set(unused[len(unused) - math.ceil(len(unused) / 3):])
    return render(home, main_only([r for r in rules if r["id"] not in remove]))


def play(name, client, own, family):
    if not (yield from wait_for_shift(client)):
        return
    text = ""
    home, rules = {}, []
    count = 0
    exercised = set()
    for _ in range(100):
        status, reply = yield from client.get("/work")
        if status != 200:
            return
        if count == 0:
            text = reply.get("note", "")
            home, rules = decode(family, own, text) if name != "lone" else ({}, [])
        if reply["done"]:
            break
        actions = []
        for job in reply["jobs"]:
            cutoff = {12: 8, 40: 20, 60: 35}.get(own["jobs"], own["jobs"])
            forget = (name in ("naive", "naive_reader") and own["role"] == "worker" and "manual" not in own
                      and count >= cutoff and own.get("format") != "card")
            active_rules = main_only(rules) if forget else rules
            rid, move = yield from solve_job(family, client, job, home, active_rules, name)
            exercised.add(rid)
            actions.append(move)
            count += 1
        status, _ = yield from client.post("/act", {"actions": actions})
        if status != 200:
            raise RuntimeError("action not accepted")
    else:
        raise RuntimeError("too many pages")
    if own["role"] == "worker":
        return
    status, measure = yield from client.post("/note/check", {"text": ""})
    if status != 200:
        return
    output = note_to_send(family, own, home, rules, exercised, text, measure["limit"], name)
    if len(output) > measure["limit"]:
        raise RuntimeError("policy note exceeds cap")
    status, _ = yield from client.post("/note/seal", {"text": output})
    if status != 201:
        raise RuntimeError("seal not accepted")
