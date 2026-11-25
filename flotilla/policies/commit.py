"""Read each opening reply, or keep the plan from the first view."""

import hashlib
import re

OUTAGE = re.compile(r"^(.+?) is out of service from round (\d+) to the end of the day\.")
POLICIES = ("careful", "naive", "lone", "primary", "backup", "random", "skim_2", "skim_4", "skim_8")


def supports(name, variant):
    return name in POLICIES


def key(item):
    return item["origin"], item["destination"], item["load"]


def read_outages(texts, outages):
    for text in texts:
        if match := OUTAGE.match(text):
            carrier, start = match.groups()
            outages[carrier] = int(start)


def consume(name, reply, r, outages, initial=False):
    if name == "careful" or (name == "naive" and initial):
        read_outages((m["text"] for m in reply.get("messages", [])), outages)
        read_outages((m["text"] for m in reply.get("new_messages", [])), outages)
    elif name.startswith("skim_") and (r - 1) % int(name.split("_")[1]) == 0:
        read_outages((m["text"] for m in reply.get("new_messages", [])), outages)


def carrier_desk(name, client, own):
    status, view = yield from client.get("/view")
    if status != 200:
        return
    if name == "careful" or name.startswith("skim_"):
        for text in own["outages"]:
            status, _ = yield from client.post("/messages", {"to": "p1", "text": text})
            if status != 201:
                raise RuntimeError("carrier notice was refused")
    status, _ = yield from client.post("/step", {"round": view["round"], "done": True})
    if status != 200:
        raise RuntimeError("carrier desk could not finish")


def select(name, shipment, lane, r, outages):
    primary = lane["primary"]
    if name in ("primary", "lone"):
        return primary
    if name == "backup":
        return lane["backup"]
    if name == "random":
        coin = hashlib.sha256(shipment["id"].encode()).digest()[0] & 1
        return lane["backup"] if coin else primary
    return lane["backup"] if outages.get(primary, r + 1) <= r else primary


def wait_next(client, view, name, outages):
    r = view["round"]
    for _ in range(30):
        status, reply = yield from client.get(f"/await?until=tick&after={r - 1}")
        if status != 200 or reply.get("frozen"):
            return None
        consume(name, reply, r + 1, outages)
        status, next_view = yield from client.get("/view")
        if status != 200:
            return None
        consume(name, next_view, next_view["round"], outages)
        if next_view["round"] > r:
            return next_view
    return None


def booking_desk(name, client, own):
    lanes = {key(lane): lane for lane in own["lanes"]}
    outages = {}
    if name not in ("lone", "primary", "backup", "random"):
        read_outages(own.get("outages", []), outages)
    status, view = yield from client.get("/view")
    if status != 200:
        return
    consume(name, view, view["round"], outages, initial=True)
    for _ in range(len(view["rounds"])):
        r = view["round"]
        if view["finished"]:
            return
        bookings = []
        for shipment in view["rounds"][str(r)]:
            if isinstance(shipment, str):
                status, shipment = yield from client.get(f"/shipment?id={shipment}")
                if status != 200:
                    raise RuntimeError("shipment lookup was refused")
                consume(name, shipment, r, outages)
            carrier = select(name, shipment, lanes[key(shipment)], r, outages)
            bookings.append({"shipment": shipment["id"], "carrier": carrier})
        status, view = yield from client.post("/step", {"round": r, "bookings": bookings})
        if status != 200:
            raise RuntimeError("booking step was refused")
        consume(name, view, view["round"], outages)
        if view["stepped"]:
            view = yield from wait_next(client, view, name, outages)
            if view is None:
                return


def play(name, client, own, family=None):
    if own["role"] == "carrier":
        yield from carrier_desk(name, client, own)
    else:
        yield from booking_desk(name, client, own)
