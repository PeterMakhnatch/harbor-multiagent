"""HTTP-portable desk policies; each uses only its own replies and delivered messages."""

import hashlib
import json

SEAMS = ("digest", "interface", "commit", "handoff")


def supports(name, variant):
    return name in ("careful", "naive", "random-valid", *(f"naive-{s}" for s in SEAMS),
                    "lapse-10", "lapse-20", "lapse-30", "lapse-50")


def failed(name, own, seam, key):
    if name in ("naive", f"naive-{seam}"):
        return True
    return name.startswith("lapse-") and hasattr(own, "lapse") and own.lapse(int(name.split("-")[1]), f"{seam}:{key}")


def interpret(reply, forms, amendments):
    for message in reply.get("new_messages", []):
        try:
            text = json.loads(message["text"])
        except (ValueError, TypeError):
            continue
        if not isinstance(text, dict):
            continue
        if isinstance(text.get("forms"), dict):
            forms.update(text["forms"])
        if isinstance(text.get("amend"), dict):
            amendments.update(text["amend"])


def from_note(note, forms, refs):
    for line in note.splitlines():
        if line.startswith("KEYS "):
            forms.update(item.split("=", 1) for item in line[5:].split(",") if "=" in item)
        elif len(parts := line.split()) == 2 and parts[0].startswith("K-") and parts[1].startswith("CR-"):
            refs[parts[1]] = parts[0]


def make_note(name, own, refs, forms):
    lines = ["KEYS " + ",".join(f"{c}={form}" for c, form in sorted(forms.items()))]
    pairs = sorted(refs.items())
    if name in ("naive", "naive-handoff"):
        pairs = pairs[:1]
        lines.append("Most recent work complete; outstanding callbacks remain.")
    else:
        pairs = [(ref, order) for ref, order in pairs if not failed(name, own, "handoff", ref)]
    lines.extend(f"{order} {ref}" for ref, order in pairs)
    note = "\n".join(lines)
    if len(note.encode()) > own["note_cap"] or len(lines) > own["note_lines"]:
        raise ValueError("reference notebook exceeds its cap")
    return note


def choose(values, key):
    index = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") % len(values)
    return values[index]


def account_actions(name, own, desk, body, family):
    if desk["round"] == 1:
        lines = desk["handbook"]
        if name.startswith("lapse-"):
            lines = [line for line in lines if not failed(name, own, "digest", line)]
        rules = family.read_handbook(lines, include_notes=name not in ("naive", "naive-digest"))
        body["card"] = family.encode_card(rules)
    board = {row["order"]: row["departure"] for row in desk["board"]}
    body["answers"] = [{"question": q["id"], "eta": board[q["order"]]} for q in desk["questions"]
                       if q["order"] in board]


def dispatch_actions(name, own, desk, body, family, forms, amendments, refs):
    round_no = desk["round"]
    bookings = []
    for order in desk["orders"]:
        oid = order["id"]
        early = failed(name, own, "commit", oid) or name == "random-valid"
        if round_no < 2 or not early and round_no <= order["amend_close"]:
            continue
        carriers = family.choices(order, desk["card"])
        if oid in amendments:
            carriers = [c for c in carriers if c == amendments[oid]]
        if not carriers:
            continue
        carrier = choose(carriers, oid + ":carrier") if name == "random-valid" else carriers[0]
        form = choose(family.FORMS, oid + ":form") if name == "random-valid" else forms.get(carrier, "C/Z")
        bookings.append({"order": oid, "carrier": carrier, "departure": order["departure"],
                         "rate_key": family.rate_key(carrier, order["zone"], form)})
    body["bookings"] = bookings
    reconciles = []
    for callback in desk["callbacks"]:
        ref = callback["carrier_ref"]
        if ref in refs:
            order = refs[ref]
        elif name == "random-valid" and refs:
            order = choose(sorted(refs.values()), ref + ":reconcile")
        else:
            continue
        reconciles.append({"carrier_ref": ref, "order": order,
                           "action": "close" if callback["status"] == "delivered" else "reissue"})
    body["reconciles"] = reconciles


def play(name, client, own, family):
    role = own["role"]
    single = role == "all"
    forms, amendments, refs = {}, {}, {}
    pending = []
    first = own["shift"] + 1 if role == "N2" else 1
    last = own["shift"] if role == "N1" else own["rounds"]
    if role == "N2":
        status, opened = yield from client.get("/await?until=active")
        if status != 200 or opened["active"] != "active":
            raise RuntimeError("late shift did not start")
        interpret(opened, forms, amendments)
    for round_no in range(first, last + 1):
        status, desk = yield from client.get("/desk")
        if status != 200 or desk["round"] != round_no:
            raise RuntimeError("desk did not open the required round")
        interpret(desk, forms, amendments)
        body = {"round": round_no}
        if single or role == "A":
            account_actions(name, own, desk, body, family)
            pending.extend(desk["amendments"])
            if single:
                amendments.update({m["order"]: m["carrier"] for m in pending})
                pending.clear()
            else:
                delayed = []
                for amend in pending:
                    if amend in desk["amendments"] and desk["questions"] and failed(name, own, "commit", amend["order"]):
                        delayed.append(amend)
                        continue
                    message = json.dumps({"amend": {amend["order"]: amend["carrier"]}})
                    status, reply = yield from client.post("/messages", {"to": amend["desk"], "text": message})
                    if status != 201:
                        raise RuntimeError("amendment relay failed")
                    interpret(reply, forms, amendments)
                pending = delayed
        if (single or role == "R") and (round_no - 1) % own["day_length"] == 0:
            forms = {carrier: supported[0] for carrier, supported in desk["ratefiles"].items()}
            body["rates"] = forms
            if not single:
                announced = {c: form for c, form in forms.items() if not failed(name, own, "interface", c)}
                if announced:
                    status, reply = yield from client.post(
                        "/messages", {"to": "all", "text": json.dumps({"forms": announced})}
                    )
                    if status != 201:
                        raise RuntimeError("rate form relay failed")
                    interpret(reply, forms, amendments)
        if single or role in ("N1", "N2", "S"):
            refs.update({r["carrier_ref"]: r["order"] for r in desk["receipts"]})
            if role == "N2" and round_no == first:
                from_note(desk["notebook"], forms, refs)
            dispatch_actions(name, own, desk, body, family, forms, amendments, refs)
            for reconcile in body["reconciles"]:
                refs.pop(reconcile["carrier_ref"], None)
            if role == "N1" and round_no == last:
                if body["bookings"]:
                    raise RuntimeError("generator must leave the last early round free of new bookings")
                body["notebook"] = make_note(name, own, refs, forms)
        status, reply = yield from client.post("/round", body)
        if status != 200 or any("error" in r for r in reply["results"]):
            raise RuntimeError(f"round actions rejected: {reply}")
        interpret(reply, forms, amendments)
        if round_no == last:
            return
        if reply["tick"] < round_no:
            status, opened = yield from client.get(f"/await?until=tick&after={round_no - 1}")
            if status != 200 or opened["tick"] != round_no:
                raise RuntimeError("round barrier did not open")
            interpret(opened, forms, amendments)
