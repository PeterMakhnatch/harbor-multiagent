"""Policies use the same document, board, and dispatch APIs as a live seat."""

import json
import re


def supports(name, variant):
    return (name in ("careful", "naive", "after", "late", "spray", "random", "fixed", "default",
                     "lone", "follow", "ignore", "product", "delayed-careful")
            or re.fullmatch(r"lapse-(?:0|[1-9][0-9]?|100)", name) is not None)


def wait_for(client, path):
    messages = []
    for _ in range(14):
        _, reply = yield from client.get(path)
        messages.extend(reply.get("new_messages", []))
        if reply.get("reason") != "timeout":
            break
    return reply | {"new_messages": messages}


def next_round(client, reply):
    if not reply.get("acted"):
        return True
    wait = yield from wait_for(client, f"/await?until=tick&after={reply['round'] - 1}")
    return not wait.get("frozen") and wait.get("active") != "retired" and wait["tick"] >= reply["round"]


def target(boards, document):
    return next(((desk, row) for desk, rows in boards.items() for row in rows
                 if row["lot"] == document["lot"] and row["product"] == document["product"]), (None, None))


def receive(name, client, own, family):
    solo = own["role"] == "both"
    _, docket = yield from client.get("/docket")
    stock = {}
    if solo:
        _, stock_reply = yield from client.get("/stock")
        stock = stock_reply["stock"]
    _, reply = yield from client.get("/item")
    used = 0
    shared_used = False
    late = []
    boards = None
    for _ in range(family.ITEMS):
        document = reply.get("document")
        if document is None or not (yield from next_round(client, reply)):
            return
        action = None
        if document["kind"] == "quarantine" and (solo or name not in ("naive", "lone")):
            if not solo and name == "fixed":
                if used < family.FORWARDS:
                    action = "/forward", {"item": document["item"], "to": "p2"}
            else:
                if boards is None:
                    _, board_reply = yield from client.get("/board")
                    boards = board_reply["boards"]
                if not solo and name in ("spray", "random", "default"):
                    choices = (family.guess_forward(boards, document, used, shared_used) if name != "spray"
                               else family.matching_desks(boards, document["product"]) if used < family.FORWARDS else [])
                    if choices:
                        destination = family.guess_destination(choices, document, fixed=name == "default")
                        shared_used |= len(choices) == 2
                        action = "/forward", {"item": document["item"], "to": destination}
                else:
                    desk, row = target(boards, document)
                    if solo and desk:
                        other = next(lot for lot in stock[desk][row["order"]] if lot != row["lot"])
                        action = "/sheet", {"order": row["order"], "lot": other}
                    elif desk and name == "late":
                        late.append((document, desk))
                    elif desk and (own.lapse_percent is None or not own.lapse(own.lapse_percent, document["item"])):
                        action = "/forward", {"item": document["item"], "to": desk}
            if action and name != "after":
                status, act = yield from client.post(*action)
                if status != 200 or not (yield from next_round(client, act)):
                    return
                used += 1
        status, reply = yield from client.post("/record", family.record_entry(document, docket["docket"]))
        if status != 200:
            return
        if action and name == "after":
            if not (yield from next_round(client, reply)):
                return
            status, act = yield from client.post(*action)
            if status != 200:
                return
            reply = reply | act
    for document, destination in late:
        if not (yield from next_round(client, reply)):
            return
        _, reply = yield from client.post("/forward", {"item": document["item"], "to": destination})


def notices(reply):
    for message in reply.get("new_messages", []):
        if message["text"] != "Carrier round":
            yield json.loads(message["text"])


def dispatch(name, client, own, family):
    seat = client.seat
    _, sheet_reply = yield from client.get("/sheet")
    rows = sheet_reply["sheets"][seat]
    _, reply = yield from client.get("/stock")
    stock = reply["stock"][seat]
    pending = list(notices(sheet_reply)) + list(notices(reply))
    for _ in range(family.END * 3):
        if reply.get("carrier_left") or reply.get("frozen") or reply.get("active") == "retired":
            return
        if reply.get("acted"):
            wait = yield from wait_for(client, f"/await?until=tick&after={reply['round'] - 1}")
            pending.extend(notices(wait))
            if wait.get("reason") == "timeout" or wait.get("active") == "retired" or wait.get("frozen"):
                return
        row = None
        while pending:
            document = pending.pop(0)
            if document["kind"] != "quarantine" or name in ("ignore", "lone"):
                continue
            row = next((r for r in rows if r["product"] == document["product"]
                        and (r["lot"] == document["lot"] or name == "product")), None)
            if row is not None:
                break
        if row is not None:
            other = next(lot for lot in stock[row["order"]] if lot != row["lot"])
            status, reply = yield from client.post("/sheet", {"order": row["order"], "lot": other})
            pending.extend(notices(reply))
            if status == 200:
                row["lot"] = other
                continue
        status, reply = yield from client.post("/wait", {"until": "delivery"})
        if status != 200:
            return
        pending.extend(notices(reply))
        if reply.get("carrier_left"):
            return
        if reply.get("new_messages") and not reply.get("acted"):
            continue
        reply = yield from wait_for(client, "/await?until=message")
        pending.extend(notices(reply))
        if reply.get("reason") == "timeout":
            return


def play(name, client, own, family):
    if name == "delayed-careful":
        yield from client.sleep(65)
        name = "careful"
    if own["role"] == "dispatch":
        yield from dispatch(name, client, own, family)
    else:
        yield from receive(name, client, own, family)
