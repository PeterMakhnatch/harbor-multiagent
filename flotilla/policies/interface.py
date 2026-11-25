"""Scripted policies that differ only in the use of the staged catalog convention."""

import hashlib


def supports(name, variant):
    return name in ("careful", "naive", "naive_read", "lone", "focal", "blind", "alternate",
                    "windowed-8", "windowed-30", "ask")


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


def publish_catalogs(name, client, groups, messaging, family):
    choices = {}
    for group in groups:
        saved = tuple(group["saved"])
        convention = next(tuple(c) for c in group["support"] if tuple(c) != saved) if name == "alternate" else saved
        choices[group["name"]] = convention
        body = make_file(family, group, convention, "catalog", [row["price"] for row in group["rows"]])
        status, _ = yield from client.post("/publish", body)
        if status != 201:
            raise ValueError("catalog upload refused")
        if messaging and name in ("careful", "alternate"):
            row = group["rows"][0]
            text = (f"{group['name']}: key for site {row['site']} item {row['item']} is "
                    f"`{body['rows'][0]['key']}`, unit {body['unit']}.")
            status, _ = yield from client.post("/messages", {"to": "p1", "text": text})
            if status != 201:
                raise ValueError("catalog announcement refused")
    return choices


def observe(reply, step, noticed):
    for file in reply.get("staging", []):
        if file["kind"] == "catalog":
            noticed.setdefault(file["group"], step)


def read_work(client):
    counts = {}
    complete = set()
    noticed = {}
    for _ in range(100):
        status, reply = yield from client.post("/next", {})
        if status != 200:
            raise ValueError("cannot read work events")
        step = reply["step"]
        observe(reply, step, noticed)
        for event in reply["events"]:
            if event["kind"] == "complete":
                complete.add(event["group"])
            else:
                counts[event["group"], event["site"], event["item"]] = event["on_hand"], event["in_transit"]
        if reply["end"]:
            return counts, complete, noticed, step
    raise ValueError("work stream did not end")


def wait_catalogs(client, groups, step, noticed):
    names = {group["name"] for group in groups}
    for _ in range(200):
        if names <= noticed.keys():
            return
        status, reply = yield from client.get("/staging")
        if status != 200:
            return
        observe(reply, step, noticed)
        if names <= noticed.keys():
            return
        yield from client.sleep(0.2)


def select(name, client, own, group, choices, step, noticed, family):
    g = group["name"]
    if name == "blind":
        digest = hashlib.sha256(g.encode()).digest()
        return family.CONVENTIONS[int.from_bytes(digest[:8], "big") % len(family.CONVENTIONS)]
    if name == "focal":
        return family.CONVENTIONS[0]
    if name in ("careful", "alternate") and g in choices:
        return choices[g]
    if name == "ask":
        status, reply = yield from client.post("/messages", {"to": group["desk"], "text": f"What is the convention for {g}?"})
        if status != 201:
            raise ValueError("catalog question refused")
        lines = [line for message in reply.get("new_messages", []) if message["from"] == group["desk"]
                 for line in message["text"].splitlines() if line.startswith(g + ":")]
        row = group["rows"][0]
        for c in family.CONVENTIONS:
            if any(f"`{family.key_for(row, c)}`, unit {c[1]}." in line for line in lines):
                return c
        raise ValueError("desk did not answer the question")
    read = name in ("careful", "alternate", "naive_read")
    if name.startswith("windowed-"):
        read = step - noticed[g] <= int(name.split("-")[1])
    if read:
        status, reply = yield from client.get(f"/staging?group={g}")
        if status != 200:
            raise ValueError("cannot read the staged catalog")
        catalog = next(file for file in reply["files"] if file["kind"] == "catalog")
        if name != "naive_read":
            return infer(family, group, catalog)
    return tuple(own["saved"])


def play(name, client, own, family):
    choices = {}
    if own["role"] in ("all", "desk"):
        groups = own["groups"] if own["role"] == "desk" else own["desks"]
        choices = yield from publish_catalogs(name, client, groups, own["messaging"], family)
    if own["role"] in ("all", "buyer"):
        counts, complete, noticed, step = yield from read_work(client)
        yield from wait_catalogs(client, own["groups"], step, noticed)
        for group in own["groups"]:
            g = group["name"]
            if g not in complete or g not in noticed:
                continue
            convention = yield from select(name, client, own, group, choices, step, noticed, family)
            values = [row["target"] - sum(counts[g, row["site"], row["item"]]) for row in group["rows"]]
            body = make_file(family, group, convention, "order", values)
            status, _ = yield from client.post("/publish", body)
            if status != 201:
                raise ValueError("order upload refused")
    yield from client.post("/done", {})
