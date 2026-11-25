"""Test policies for the fixture; only the receiver's response to missing data differs."""


def supports(name, variant):
    return name in ("careful", "naive", "sender")


def play(name, client, own):
    role = own["role"]
    if role == "all":
        yield from client.post("/commit", {"total": own["left"] + own["right"]})
        return
    if role == "sender":
        for text in (f"handoff {own['left']}", "diagnostic"):
            status, _ = yield from client.post("/messages", {"to": "p2", "text": text})
            if status != 201:
                raise RuntimeError("sender could not post a message")
        yield from client.post("/handoff", {})
        return
    for _ in range(300):
        _, me = yield from client.get("/whoami")
        if me["active"] == "active":
            break
        if me["frozen"]:
            return
        yield from client.sleep(0.2)
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
        left = next((int(text.split()[1]) for text in messages if text.startswith("handoff ")), None)
        if left is not None or name == "naive":
            break
        yield from client.post("/advance", {})
    yield from client.post("/commit", {"total": own["right"] + (left or 0)})
