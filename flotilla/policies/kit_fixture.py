"""Test policies for the fixture; only the receiver's response to missing data differs."""

import re


def supports(name, variant):
    return (name in ("careful", "naive", "sender", "exhaust", "stall", "fault", "wait-message",
                     "script-loop", "free-calls", "retry")
            or re.fullmatch(r"lapse-(?:0|[1-9][0-9]?|100)", name) is not None)


def play(name, client, own, family):
    if name == "exhaust":
        for _ in range(21):
            _, me = yield from client.get("/board")
            if me.get("frozen"):
                return
        return
    if name == "free-calls":
        for _ in range(25):
            yield from client.get("/whoami")
            yield from client.get("/await?until=active")
            yield from client.get("/health")
    if name == "stall":
        for _ in range(100_000):
            yield from client.sleep(0)
        return
    if name == "fault":
        yield from client.post("/fixture-fault", {"kind": "raise"})
        return
    role = own["role"]
    if role == "all":
        yield from client.post("/commit", {"total": own["left"] + own["right"]})
        return
    if role == "seal":
        _, reply = yield from client.post("/handoff", {"note": str(own["left"])})
        if name == "retry":
            status, repeated = yield from client.post("/handoff", {"note": str(own["left"])})
            if status != 200 or reply != repeated:
                raise RuntimeError("retired retry differs from the original reply")
        return
    if role == "read":
        _, me = yield from client.get("/await?until=active")
        if me["reason"] != "active":
            return
        status, board = yield from client.get("/board")
        if status == 200 and "note" in board:
            yield from client.post("/commit", {"total": own["right"] + int(board["note"])})
        return
    if role == "round_sender":
        yield from client.post("/messages", {"to": "p2", "text": family.encode(own["left"])})
        yield from client.post("/ready", {})
        return
    if role == "round_waiter":
        yield from client.post("/ready", {})
        return
    if role == "round_receiver":
        _, reply = yield from client.post("/ready", {})
        messages = reply.get("new_messages", [])
        if reply["tick"] == 0:
            route = "/await?until=message" if name == "wait-message" else "/await?until=tick&after=0"
            _, reply = yield from client.get(route)
            messages += reply.get("new_messages", [])
        left = next((family.decode(m["text"]) for m in messages if m["from"] == "p1"), 0)
        yield from client.post("/commit", {"total": own["right"] + left})
        return
    if role == "sender":
        for text in (family.encode(own["left"]), "diagnostic"):
            status, _ = yield from client.post("/messages", {"to": "p2", "text": text})
            if status != 201:
                raise RuntimeError("sender could not post a message")
        yield from client.post("/handoff", {})
        return
    for _ in range(2):
        _, me = yield from client.get("/await?until=active")
        if me["active"] == "active":
            break
        if me["frozen"]:
            return
        if me["reason"] in ("timeout", "retired"):
            return
    else:
        return
    yield from client.get("/board")
    left = None
    for _ in range(2):
        status, inbox = yield from client.get("/messages")
        if status != 200:
            return
        messages = [m["text"] for m in inbox["messages"] if m["from"] == "p1"]
        if "diagnostic" in messages:
            raise RuntimeError("the discarded message was delivered")
        left = next((family.decode(text) for text in messages if text.startswith("handoff ")), None)
        if left is not None or name == "naive":
            break
        if own.lapse_percent is not None and own.lapse(own.lapse_percent, "read-late-handoff"):
            break
        yield from client.post("/advance", {})
        if name == "script-loop":
            break
    yield from client.post("/commit", {"total": own["right"] + (left or 0)})
