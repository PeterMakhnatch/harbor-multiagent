"""Policies using only the dispatch desk views and their own carried state."""

import hashlib


def supports(name, variant):
    return name in ("careful", "naive", *NO_NOTE, "reverse", "colon", "slash", "lines", "ref-first",
                    "json", "arrow", "dash", "ref-colon", "comma", "table", "ref-eq", "cleared", "paren",
                    "bare-paren", "json-list", "json-unknown", "candidates", "suspect-lots", "possible-bad",
                    "remaining", "bad-one-of", "ambiguous-pair", "ambiguous-lot", "exhaust", "stall", "extra",
                    "lapse-10", "lapse-20", "lapse-30", "lapse-50")


NO_NOTE = ("lone", "blind", "guess", "guess-fixed", "guess-each")



def check(code, reply):
    if code != 200:
        raise RuntimeError(f"desk call refused: {code} {reply.get('error', '')}")
    return reply


def record_state(log, lots, family):
    batches, refs, results = {}, {}, {}
    for call in log:
        reply = call["reply"]
        batches.update({b["batch"]: b["lots"] for b in reply.get("plan", []) + reply.get("open", [])})
        refs.update({r: b for b, r in reply.get("references", {}).items()})
        results.update({r["reference"]: r["status"] for r in reply.get("inbox", [])})
    possible = set(lots)
    for ref, status in results.items():
        if ref in refs and refs[ref] in batches:
            possible = family.narrow(possible, batches[refs[ref]], status)
    return {batch: ref for ref, batch in refs.items()}, possible


def shuffled(values, salt):
    return sorted(values, key=lambda value: hashlib.sha256(f"{salt}:{value}".encode()).digest())


def feasible_lots(possible, unknown_lots, statuses):
    returned = statuses.count("returned")
    delivered = len(statuses) - returned
    return {lot for lot in possible if sum(lot in inks for inks in unknown_lots) >= returned
            and sum(lot not in inks for inks in unknown_lots) >= delivered}


def work(name, client, own, memory, family):
    if name == "extra":
        for _ in range(3):
            check(*(yield from client.get("/desk")))
            status, _ = yield from client.post("/print", {"batch": "unknown"})
            if status != 409:
                raise RuntimeError("invalid print was not refused")
    desk = check(*(yield from client.get("/desk")))
    lots = desk["lots"]
    if memory is not None:
        bindings, possible = memory
    elif name in NO_NOTE:
        bindings, possible = {}, set(lots)
    else:
        bindings, possible, _ = family.parse_note(desk["note"], lots)
    local = set(lots)
    if memory is None and name not in NO_NOTE:
        record = check(*(yield from client.get("/log")))
        if any(call["shift"] < desk["shift"] for call in record["calls"]):
            bindings, possible = record_state(record["calls"], lots, family)
    available = {b["batch"]: b["lots"] for b in desk["plan"] + desk["open"]}
    planned = [b["batch"] for b in desk["plan"] if b["state"] == "planned"]
    if name == "reverse":
        planned.reverse()
    for batch in planned:
        check(*(yield from client.post("/print", {"batch": batch})))
    fresh = {}
    if planned:
        response = check(*(yield from client.post("/dispatch", {"batches": planned})))
        fresh = response["references"]
        bindings.update(fresh)
        desk = check(*(yield from client.get("/desk")))
    pending = {b["batch"] for b in desk["open"]}
    pending.update(b["batch"] for b in desk["plan"] if b["state"] == "dispatched")
    bindings = {b: r for b, r in bindings.items() if b in pending}
    by_ref = {r: b for b, r in bindings.items()}
    inbox = {r["reference"]: r["status"] for r in desk["inbox"]}
    items = []
    for ref, status in inbox.items():
        batch = by_ref.get(ref)
        if batch is not None:
            possible = family.narrow(possible, available[batch], status)
            local = family.narrow(local, available[batch], status)
            items.append(settlement(ref, batch, status))
    selected = None
    if name == "guess-each" or (name in ("guess", "guess-fixed") and desk["shift"] == desk["shifts"]):
        unknown_refs = sorted(set(inbox) - set(by_ref))
        unknown_batches = sorted(pending - set(bindings))
        salt = ":".join(sorted(fresh.values()) + unknown_refs)
        possible = feasible_lots(possible, [available[b] for b in unknown_batches],
                                 [inbox[r] for r in unknown_refs])
        if possible:
            selected = min(possible) if name == "guess-fixed" else shuffled(possible, "ticket:" + salt)[0]
            for status in ("returned", "delivered"):
                refs = [ref for ref in unknown_refs if inbox[ref] == status]
                eligible = [b for b in unknown_batches if (selected in available[b]) == (status == "returned")]
                ordered = eligible if name == "guess-fixed" else shuffled(eligible, status + ":" + salt)
                for ref, batch in zip(refs, ordered):
                    items.append(settlement(ref, batch, status))
    if name == "reverse":
        items.reverse()
    if items:
        check(*(yield from client.post("/settle", {"items": items})))
        for item in items:
            bindings.pop(item["batch"], None)
    if desk["ticket"] is None:
        if name in ("guess", "guess-fixed", "guess-each"):
            if selected is not None and desk["shift"] == desk["shifts"]:
                check(*(yield from client.post("/ticket", {"lot": selected})))
        elif len(possible) == 1:
            check(*(yield from client.post("/ticket", {"lot": next(iter(possible))})))
    check(*(yield from client.post("/close", {})))
    if own["continuous"]:
        keep, suspects = bindings, possible
    elif name == "naive":
        keep = {b: r for b, r in bindings.items() if b in fresh}
        suspects = local
        record = check(*(yield from client.get("/log")))
        if any(call["shift"] < desk["shift"] for call in record["calls"]):
            _, suspects = record_state(record["calls"], lots, family)
    elif name in NO_NOTE:
        keep, suspects = {}, set()
    else:
        keep, suspects = bindings, possible
    if own.lapse_percent is not None and not own["continuous"] and desk["shift"] < desk["shifts"]:
        percent = own.lapse_percent
        shift = desk["shift"]
        keep = {b: r for b, r in keep.items() if not own.lapse(percent, ["binding", shift, b])}
        suspects = suspects | {lot for lot in lots if lot not in suspects
                              and own.lapse(percent, ["clearance", shift, lot])}
    note = family.write_note(keep, suspects, name, lots)
    if own["continuous"] and len(note) > own["cap"]:
        note = ""
    check(*(yield from client.post("/handover", {"note": note})))
    return bindings, possible


def settlement(ref, batch, status):
    return {"reference": ref, "batch": batch, "action": "close" if status == "delivered" else "reissue"}


def play(name, client, own, family):
    if name == "stall":
        for _ in range(100_000):
            yield from client.sleep(0)
        return
    status, me = yield from client.get("/await?until=active")
    if status != 200 or me["active"] != "active" or me["frozen"]:
        return
    if name == "exhaust":
        for _ in range(family.CALL_CAP):
            yield from client.get("/desk")
        return
    if own["continuous"]:
        memory = None
        for _ in range(own["shifts"]):
            memory = yield from work(name, client, own, memory, family)
    else:
        yield from work(name, client, own, None, family)
