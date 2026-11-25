"""Freight policies that differ only in consumption of the dispatch mail."""

import hashlib
import itertools
import re

OUTAGE = re.compile(r"^(.+?) is out of service from round (\d+) to the end of the day\.")
POLICIES = ("careful", "naive", "lone", "primary", "backup", "blind", "random", "script-loop",
            "lapse-10", "lapse-20", "lapse-30", "lapse-50")


def supports(name, variant):
    return name in POLICIES


def key(item):
    return item["origin"], item["destination"], item["load"]


def read_outages(texts, outages):
    for text in texts:
        if match := OUTAGE.match(text):
            carrier, start = match.groups()
            outages[carrier] = int(start)


def consume(reply, outages):
    read_outages((m["text"] for m in reply.get("messages", [])), outages)
    read_outages((m["text"] for m in reply.get("new_messages", [])), outages)


def carrier_desk(name, client, own):
    notices = [] if name in ("naive", "lone") else own["outages"]
    if own.lapse_percent is not None:
        notices = [n for n in notices if not own.lapse(own.lapse_percent, ["tell", n["carrier"], n["start"]])]
    status, _ = yield from client.post("/relay", {"notices": notices})
    if status != 200:
        raise RuntimeError("carrier report was refused")


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


def wait_next(client, idle_waits):
    for _ in range(idle_waits):
        status, reply = yield from client.get("/await?until=tick&after=0")
        if status != 200 or reply.get("frozen") or reply.get("duty_stalled"):
            return None
        if reply["tick"] > 0:
            status, view = yield from client.get("/view")
            return view if status == 200 else None
    return None


def blind_plan(own, view):
    carriers = sorted({lane["primary"] for lane in own["lanes"]})
    k = own["max_outages"]
    combinations = list(itertools.combinations(carriers, k))
    starts = list(itertools.product(range(2, len(view["rounds"]) + 1), repeat=k))
    ids = "|".join(id_ for batch in view["rounds"].values() for id_ in batch)
    draw = int.from_bytes(hashlib.sha256(ids.encode()).digest(), "big")
    return dict(zip(combinations[draw % len(combinations)], starts[draw // len(combinations) % len(starts)],
                    strict=True))


def consume_shipment(name, shipment, outages, own):
    if name in ("naive", "script-loop", "blind"):
        return
    for message in shipment.get("new_messages", []):
        text = message["text"]
        if not OUTAGE.match(text):
            continue
        if own.lapse_percent is not None and own.lapse(own.lapse_percent, ["read", text]):
            continue
        read_outages([text], outages)


def booking_desk(name, client, own, family):
    lanes = {key(lane): lane for lane in own["lanes"]}
    outages = {}
    read_outages(own.get("outages", []), outages)
    status, view = yield from client.get("/view")
    if status != 200:
        return
    if name == "blind":
        outages = blind_plan(own, view)
    else:
        consume(view, outages)
    for _ in range(len(view["rounds"])):
        r = view["round"]
        if view["finished"]:
            return
        shipments = []
        for id_ in view["rounds"][str(r)]:
            status, shipment = yield from client.get(f"/shipment?id={id_}")
            if status != 200:
                raise RuntimeError("shipment lookup was refused")
            shipments.append(shipment)
            consume_shipment(name, shipment, outages, own)
        bookings = [{"shipment": s["id"], "carrier": select(name, s, lanes[key(s)], r, outages)}
                    for s in shipments]
        status, view = yield from client.post("/step", {"round": r, "bookings": bookings})
        if status != 200:
            raise RuntimeError("booking step was refused")
        if view["stepped"]:
            view = yield from wait_next(client, family.IDLE_WAITS)
            if view is None:
                return
        if name not in ("naive", "blind"):
            consume(view, outages)


def play(name, client, own, family=None):
    if own["role"] == "carrier":
        yield from carrier_desk(name, client, own)
    else:
        yield from booking_desk(name, client, own, family)
