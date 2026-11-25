"""Portable seat policies for the catalog convention seam."""

import hashlib


def supports(name, variant):
    return (name in ("careful", "naive", "naive_read", "lone", "focal", "blind", "alternate", "ask", "script-loop", "eager")
            or name.startswith("lapse-") and name[6:] in ("10", "20", "30", "50"))


def lapses(own, opportunity, group):
    return own.lapse_percent is not None and own.lapse(own.lapse_percent, [opportunity, group["name"]])


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


def catalog_choices(name, groups, family):
    choices = {}
    for group in groups:
        saved = tuple(group["saved"])
        convention = next(tuple(c) for c in group["support"] if tuple(c) != saved) if name == "alternate" else saved
        if name == "focal" and list(family.CONVENTIONS[0]) in group["support"]:
            convention = family.CONVENTIONS[0]
        choices[group["name"]] = convention
    return choices


def publish_catalogs(name, client, groups, family):
    choices = catalog_choices(name, groups, family)
    for group in groups:
        body = make_file(family, group, choices[group["name"]], "catalog", [row["price"] for row in group["rows"]])
        status, _ = yield from client.post("/publish", body)
        if status != 201:
            raise ValueError("catalog upload refused")
    return choices


def wait_status(client, predicate):
    status, reply = yield from client.get("/staging")
    while status == 200 and not predicate(reply) and not reply.get("frozen"):
        status, reply = yield from client.get(f"/await?until=tick&after={reply['tick']}")
        if reply.get("reason") in ("retired", "frozen"):
            break
    return reply


def desk_replies(client, own, family, choices, reply):
    pending = list(reply.get("new_messages", []))
    groups = own["groups"]
    while pending:
        message = pending.pop(0)
        if message["from"] != "p1":
            continue
        asked = family.questions(message, groups)
        replies = [family.describe(group, choices[group["name"]]) for group in groups
                   if group["name"] in asked and not lapses(own, "answer", group)]
        if family.unparsed_question(message, own["directory"], client.seat):
            replies = ["Which group?"]
        elif not asked and message["to"] == client.seat:
            replies = ["Noted."]
        if not replies:
            continue
        status, reply = yield from client.post("/messages", {"to": "p1", "text": " ".join(replies)})
        if status == 429 or status == 409:
            return reply
        if status != 201:
            raise ValueError("catalog reply refused")
        pending.extend(reply.get("new_messages", []))
    return reply


def desk_play(name, client, own, family):
    if name == "lone":
        return
    groups = own["groups"]
    choices = catalog_choices(name, groups, family)
    coordinate = own["messaging"] and name not in ("naive", "naive_read", "focal", "blind")
    for group in groups:
        body = make_file(family, group, choices[group["name"]], "catalog", [row["price"] for row in group["rows"]])
        status, reply = yield from client.post("/publish", body)
        if status != 201:
            raise ValueError("catalog upload refused")
        if coordinate:
            reply = yield from desk_replies(client, own, family, choices, reply)
    while not reply.get("prepared") and "p1" not in reply.get("finished", []):
        if reply.get("reason") in ("retired", "frozen") or "tick" not in reply:
            return
        status, reply = yield from client.get(f"/await?until=tick&after={reply['tick']}")
        if status != 200 or reply.get("reason") in ("retired", "frozen"):
            return
        if coordinate:
            reply = yield from desk_replies(client, own, family, choices, reply)
    yield from client.post("/done", {})


def read_work(client):
    counts = {}
    complete = set()
    for _ in range(100):
        status, reply = yield from client.post("/next", {})
        if status != 200:
            raise ValueError(f"cannot read work events: {status} {reply.get('error')}")
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


def setting_from_messages(group, messages, family):
    candidates = {c for message in messages if message["from"] == group["desk"]
                  for c in family.announcement_settings(group, message["text"])}
    return next(iter(candidates)) if len(candidates) == 1 else None


def select(name, client, own, group, choices, state, messages, family):
    g = group["name"]
    if g in choices:
        return choices[g]
    if g in state.get("writer_settings", {}):
        return tuple(state["writer_settings"][g])
    fallback = guess(name, own, group, family)
    if name in ("naive", "lone", "focal", "blind"):
        return fallback
    if lapses(own, "obtain-setting", group):
        return fallback
    if g in state.get("export_settings", {}):
        convention = tuple(state["export_settings"][g])
    elif group["hidden"] or name == "ask":
        convention = setting_from_messages(group, messages, family) or fallback
    else:
        status, reply = yield from client.get(f"/staging?group={g}")
        if status != 200:
            return fallback
        catalog = next((file for file in reply["files"] if file["kind"] == "catalog"), None)
        convention = infer(family, group, catalog) if catalog else fallback
    return fallback if name == "naive_read" else convention


def submit_order(client, group, counts, convention, family):
    name = group["name"]
    values = [row["target"] - sum(counts[name, row["site"], row["item"]]) for row in group["rows"]]
    body = make_file(family, group, convention, "order", values)
    status, reply = yield from client.post("/publish", body)
    if status != 201:
        raise ValueError("order upload refused")
    return reply


def wait_play(name, client, own, family):
    target = next(g for g in own["groups"] if g["hidden"])
    other = [g for g in own["groups"] if not g["hidden"]]
    blind = name in ("blind", "lone", "focal")
    asked_at = None
    if not blind:
        status, reply = yield from client.post("/messages", {"to": target["desk"],
                                                "text": f"What key form and unit for {target['name']}?"})
        if status != 201:
            raise ValueError("catalog question refused")
        asked_at = reply["tick"]
    counts, complete = yield from read_work(client)
    status, _ = yield from client.post("/prepare", {})
    if status != 200:
        raise ValueError("preparation refused")
    desks = {g["desk"] for g in own["groups"]}
    state = yield from wait_status(client, lambda reply: desks <= set(reply["finished"]))
    if state.get("frozen"):
        return
    _, inbox = yield from client.get("/messages")
    setting = setting_from_messages(target, inbox["messages"], family)
    eager = name in ("naive", "eager", "naive_read") or lapses(own, "wait-for-answer", target)
    early = blind or (eager and setting is None and asked_at is not None
                      and inbox["tick"] - asked_at >= family.WAIT_EAGER_TICKS)
    if early:
        setting = guess(name, own, target, family)
        yield from submit_order(client, target, counts, setting, family)
    for group in other:
        if group["name"] in complete:
            convention = yield from select("careful", client, own, group, {}, state, [], family)
            yield from submit_order(client, group, counts, convention, family)
    if not early:
        _, inbox = yield from client.get("/messages")
        setting = setting_from_messages(target, inbox["messages"], family)
        while setting is None:
            status, reply = yield from client.get("/await?until=message")
            if status != 200 or reply.get("frozen"):
                return
            setting = setting_from_messages(target, reply.get("new_messages", []), family)
        yield from submit_order(client, target, counts, setting, family)
    yield from client.post("/done", {})


def play(name, client, own, family):
    if own["role"] == "desk":
        yield from desk_play(name, client, own, family)
        return
    if "wait" in own:
        yield from wait_play(name, client, own, family)
        return
    choices = {}
    if own["role"] == "all":
        choices = yield from publish_catalogs(name, client, own["desks"], family)
    counts, complete = yield from read_work(client)
    coordinate = own["messaging"] and not own.get("imports_settings") and name not in ("naive", "lone", "focal", "blind")
    if coordinate:
        for group in own["groups"]:
            if (group["hidden"] or name == "ask") and not lapses(own, "obtain-setting", group):
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
    orders = []
    for group in own["groups"]:
        g = group["name"]
        if g not in complete or g not in state.get("ready", []):
            continue
        if group["desk"] in desks and group["desk"] not in state["finished"]:
            continue
        convention = yield from select(name, client, own, group, choices, state, messages, family)
        values = [row["target"] - sum(counts[g, row["site"], row["item"]]) for row in group["rows"]]
        body = make_file(family, group, convention, "order", values)
        orders.append(body)
        if name != "script-loop":
            status, _ = yield from client.post("/publish", body)
            if status != 201:
                raise ValueError("order upload refused")
    if name == "script-loop":
        for body in orders:
            status, _ = yield from client.post("/publish", body)
            if status != 201:
                raise ValueError("order upload refused")
    yield from client.post("/done", {})
