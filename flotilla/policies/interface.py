"""Portable seat policies for the catalog convention seam."""

import hashlib


def supports(name, variant):
    return name in ("careful", "naive", "naive_read", "lone", "focal", "blind", "alternate", "ask")


def make_file(family, group, convention, kind, values):
    return {"group": group["name"], "kind": kind, "unit": convention[1],
            "rows": family.encode(group["rows"], values, convention, kind)}


def infer(family, group, catalog):
    keys = {row["key"] for row in catalog["rows"]}
    candidates = [c for c in family.CONVENTIONS if c[1] == catalog["unit"]
                  and {family.key_for(row, c) for row in group["rows"]} == keys]
    if len(candidates) != 1:
        raise ValueError("catalog does not identify one convention")
    return candidates[0]


def publish_catalogs(name, client, groups, family):
    choices = {}
    for group in groups:
        saved = tuple(group["saved"])
        convention = next(tuple(c) for c in group["support"] if tuple(c) != saved) if name == "alternate" else saved
        if name == "focal" and list(family.CONVENTIONS[0]) in group["support"]:
            convention = family.CONVENTIONS[0]
        choices[group["name"]] = convention
        body = make_file(family, group, convention, "catalog", [row["price"] for row in group["rows"]])
        status, _ = yield from client.post("/publish", body)
        if status != 201:
            raise ValueError("catalog upload refused")
    return choices


def wait_status(client, predicate):
    for _ in range(32):
        status, reply = yield from client.get("/staging")
        if status != 200 or predicate(reply):
            return reply
        status, waiting = yield from client.get(f"/await?until=tick&after={reply['tick']}")
        if status != 200 or waiting["reason"] in ("timeout", "retired", "frozen"):
            return reply
    raise ValueError("too many logical barrier transitions")


def desk_play(name, client, own, family):
    if name == "lone":
        return
    groups = own["groups"]
    choices = yield from publish_catalogs(name, client, groups, family)
    state = yield from wait_status(client, lambda reply: reply["prepared"])
    if not state.get("prepared"):
        return
    if own["messaging"] and name not in ("naive", "naive_read", "focal", "blind"):
        status, inbox = yield from client.get("/messages")
        if status != 200:
            return
        asked = {group for message in inbox["messages"] for group in family.questions(message, groups)}
        for group in groups:
            if group["name"] in asked:
                text = family.describe(group, choices[group["name"]])
                status, _ = yield from client.post("/messages", {"to": "p1", "text": text})
                if status != 201:
                    raise ValueError("catalog reply refused")
    yield from client.post("/done", {})


def read_work(client):
    counts = {}
    complete = set()
    for _ in range(100):
        status, reply = yield from client.post("/next", {})
        if status != 200:
            raise ValueError("cannot read work events")
        for event in reply["events"]:
            if event["kind"] == "complete":
                complete.add(event["group"])
            else:
                counts[event["group"], event["site"], event["item"]] = event["on_hand"], event["in_transit"]
        if reply["end"]:
            return counts, complete
    raise ValueError("work stream did not end")


def guess(name, own, group, family):
    if name == "focal":
        return family.CONVENTIONS[0]
    if name != "blind" and "saved" in own:
        return tuple(own["saved"])
    digest = hashlib.sha256(group["name"].encode()).digest()
    return family.CONVENTIONS[int.from_bytes(digest[:8], "big") % len(family.CONVENTIONS)]


def select(name, client, own, group, choices, state, messages, family):
    g = group["name"]
    if g in choices:
        return choices[g]
    if g in state.get("writer_settings", {}):
        return tuple(state["writer_settings"][g])
    fallback = guess(name, own, group, family)
    if name in ("naive", "lone", "focal", "blind"):
        return fallback
    if g in state.get("export_settings", {}):
        convention = tuple(state["export_settings"][g])
    elif group["hidden"] or name == "ask":
        candidates = [c for c in family.CONVENTIONS if any(message["from"] == group["desk"]
                      and family.announced(group, c, message["text"]) for message in messages)]
        convention = candidates[0] if len(candidates) == 1 else fallback
    else:
        status, reply = yield from client.get(f"/staging?group={g}")
        if status != 200:
            return fallback
        catalog = next((file for file in reply["files"] if file["kind"] == "catalog"), None)
        convention = infer(family, group, catalog) if catalog else fallback
    return fallback if name == "naive_read" else convention


def play(name, client, own, family):
    if own["role"] == "desk":
        yield from desk_play(name, client, own, family)
        return
    choices = {}
    if own["role"] == "all":
        choices = yield from publish_catalogs(name, client, own["desks"], family)
    counts, complete = yield from read_work(client)
    coordinate = own["messaging"] and not own.get("imports_settings") and name not in ("naive", "lone", "focal", "blind")
    if coordinate:
        for group in own["groups"]:
            if group["hidden"] or name == "ask":
                status, _ = yield from client.post("/messages", {"to": group["desk"],
                                                  "text": f"What key form and unit for {group['name']}?"})
                if status != 201:
                    raise ValueError("catalog question refused")
    status, _ = yield from client.post("/prepare", {})
    if status != 200:
        raise ValueError("preparation refused")
    desks = {g["desk"] for g in own["groups"]} - {"p1", "export"}
    state = yield from wait_status(client, lambda reply: desks <= set(reply["finished"]))
    messages = []
    if coordinate:
        _, inbox = yield from client.get("/messages")
        messages = inbox["messages"]
    for group in own["groups"]:
        g = group["name"]
        if g not in complete or g not in state.get("ready", []):
            continue
        if group["desk"] in desks and group["desk"] not in state["finished"]:
            continue
        convention = yield from select(name, client, own, group, choices, state, messages, family)
        values = [row["target"] - sum(counts[g, row["site"], row["item"]]) for row in group["rows"]]
        body = make_file(family, group, convention, "order", values)
        status, _ = yield from client.post("/publish", body)
        if status != 201:
            raise ValueError("order upload refused")
    yield from client.post("/done", {})
