"""Scripted policies that differ only in the use of the staged catalog convention."""

import hashlib

FORMS = ("SITE/ITEM", "ITEM/SITE", "SITE:ITEM", "ITEM:SITE", "SITE-ITEM", "ITEM-SITE")
CONVENTIONS = tuple((form, unit) for form in FORMS for unit in ("each", "case"))


def supports(name, variant):
    return name in ("careful", "naive", "naive_read", "lone", "focal", "blind", "alternate", "windowed-8", "windowed-30")


def key_for(row, convention):
    return convention[0].replace("SITE", row["site"]).replace("ITEM", row["item"])


def make_file(group, convention, kind, values):
    field = "price_cents" if kind == "catalog" else "qty"
    rows = []
    for row, value in zip(group["rows"], values, strict=True):
        if kind == "order" and value <= 0:
            continue
        if convention[1] == "case":
            value = value * row["case"] if kind == "catalog" else value // row["case"]
        rows.append({"key": key_for(row, convention), field: value})
    return {"group": group["name"], "kind": kind, "unit": convention[1], "rows": rows}


def infer(group, catalog):
    keys = {row["key"] for row in catalog["rows"]}
    candidates = [c for c in CONVENTIONS if c[1] == catalog["unit"]
                  and {key_for(row, c) for row in group["rows"]} == keys]
    if len(candidates) != 1:
        raise ValueError("catalog does not identify one convention")
    return candidates[0]


def publish_catalogs(name, client, groups, messaging):
    choices = {}
    for group in groups:
        saved = tuple(group["saved"])
        convention = next(tuple(c) for c in group["support"] if tuple(c) != saved) if name == "alternate" else saved
        choices[group["name"]] = convention
        body = make_file(group, convention, "catalog", [row["price"] for row in group["rows"]])
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


def select(name, client, own, group, choices, step, noticed):
    g = group["name"]
    if name == "blind":
        digest = hashlib.sha256(g.encode()).digest()
        return CONVENTIONS[int.from_bytes(digest[:8], "big") % len(CONVENTIONS)]
    if name == "focal":
        return CONVENTIONS[0]
    if name in ("careful", "alternate") and g in choices:
        return choices[g]
    read = name in ("careful", "alternate", "naive_read")
    if name.startswith("windowed-"):
        read = step - noticed[g] <= int(name.split("-")[1])
    if read:
        status, reply = yield from client.get(f"/staging?group={g}")
        if status != 200:
            raise ValueError("cannot read the staged catalog")
        catalog = next(file for file in reply["files"] if file["kind"] == "catalog")
        if name != "naive_read":
            return infer(group, catalog)
    return tuple(own["saved"])


def play(name, client, own):
    choices = {}
    if own["role"] in ("all", "desk"):
        groups = own["groups"] if own["role"] == "desk" else own["desks"]
        choices = yield from publish_catalogs(name, client, groups, own["messaging"])
    if own["role"] in ("all", "buyer"):
        counts, complete, noticed, step = yield from read_work(client)
        yield from wait_catalogs(client, own["groups"], step, noticed)
        for group in own["groups"]:
            g = group["name"]
            if g not in complete or g not in noticed:
                continue
            convention = yield from select(name, client, own, group, choices, step, noticed)
            values = [row["target"] - sum(counts[g, row["site"], row["item"]]) for row in group["rows"]]
            body = make_file(group, convention, "order", values)
            status, _ = yield from client.post("/publish", body)
            if status != 201:
                raise ValueError("order upload refused")
    yield from client.post("/done", {})
