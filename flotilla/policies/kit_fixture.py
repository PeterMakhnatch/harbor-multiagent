"""Test policies for the fixture; only the receiver's response to missing data differs."""


def supports(name, variant):
    return name in ("careful", "naive", "sender")


def play(name, client, own, family):
    role = own["role"]
    if role == "all":
        yield from client.post("/commit", {"total": own["left"] + own["right"]})
        return
    if role == "seal":
        yield from client.post("/handoff", {"note": str(own["left"])})
        return
    if role == "read":
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
            _, reply = yield from client.get("/await?until=tick&after=0")
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
        yield from client.post("/advance", {})
    yield from client.post("/commit", {"total": own["right"] + (left or 0)})
