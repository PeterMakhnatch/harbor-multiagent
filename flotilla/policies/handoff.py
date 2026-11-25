"""Policies using only the dispatch desk views and their own carried state."""

import hashlib
import re


def supports(name, variant):
    return name in ("careful", "naive", "lone", "blind", "reverse", "colon", "slash", "lines", "ref-first")


def parse_note(note, lots):
    bindings = dict(re.findall(r"\b(B\d{2})[=:/ ](R\d{2})\b", note))
    bindings.update((batch, ref) for ref, batch in re.findall(r"\b(R\d{2})>(B\d{2})\b", note))
    suspects = set(re.findall(r"\bL\d{2}\b", note))
    return bindings, suspects or set(lots)


def write_note(bindings, possible, style="careful"):
    order = sorted(bindings, reverse=style == "reverse")
    separator = {"colon": ":", "slash": "/", "lines": " "}.get(style, "=")
    pairs = [f"{bindings[b]}>{b}" if style == "ref-first" else f"{b}{separator}{bindings[b]}" for b in order]
    joiner = "\n" if style == "lines" else " "
    return "Pending " + joiner.join(pairs) + "; Suspects " + " ".join(sorted(possible))


def narrow(possible, lots, status):
    return possible & set(lots) if status == "returned" else possible - set(lots)


def check(code, reply):
    if code != 200:
        raise RuntimeError(f"desk call refused: {code} {reply.get('error', '')}")
    return reply


def record_candidates(log, lots):
    batches, refs, results = {}, {}, {}
    for call in log:
        reply = call["reply"]
        batches.update({b["batch"]: b["lots"] for b in reply.get("plan", []) + reply.get("open", [])})
        refs.update({r: b for b, r in reply.get("references", {}).items()})
        results.update({r["reference"]: r["status"] for r in reply.get("inbox", [])})
    possible = set(lots)
    for ref, status in results.items():
        if ref in refs and refs[ref] in batches:
            possible = narrow(possible, batches[refs[ref]], status)
    return possible


def shuffled(values, salt):
    return sorted(values, key=lambda value: hashlib.sha256(f"{salt}:{value}".encode()).digest())


def feasible_lots(possible, unknown_lots, statuses):
    returned = statuses.count("returned")
    delivered = len(statuses) - returned
    return {lot for lot in possible if sum(lot in inks for inks in unknown_lots) >= returned
            and sum(lot not in inks for inks in unknown_lots) >= delivered}


def work(name, client, own, memory):
    desk = check(*(yield from client.get("/desk")))
    lots = desk["lots"]
    if memory is not None:
        bindings, possible = memory
    elif name in ("lone", "blind"):
        bindings, possible = {}, set(lots)
    else:
        bindings, possible = parse_note(desk["note"], lots)
    local = set(lots)
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
            possible = narrow(possible, available[batch], status)
            local = narrow(local, available[batch], status)
            items.append(settlement(ref, batch, status))
    if name == "blind":
        unknown_refs = sorted(set(inbox) - set(by_ref))
        unknown_batches = sorted(pending - set(bindings))
        salt = ":".join(sorted(fresh.values()) + unknown_refs)
        possible = feasible_lots(possible, [available[b] for b in unknown_batches],
                                 [inbox[r] for r in unknown_refs])
        for ref, batch in zip(unknown_refs, shuffled(unknown_batches, salt)):
            items.append(settlement(ref, batch, inbox[ref]))
    if name == "reverse":
        items.reverse()
    if items:
        check(*(yield from client.post("/settle", {"items": items})))
        for item in items:
            bindings.pop(item["batch"], None)
    if desk["ticket"] is None:
        if name == "blind":
            if desk["shift"] == desk["shifts"] and possible:
                selected = shuffled(possible, "ticket:" + ":".join(sorted(inbox)))[0]
                check(*(yield from client.post("/ticket", {"lot": selected})))
        elif len(possible) == 1:
            check(*(yield from client.post("/ticket", {"lot": next(iter(possible))})))
    check(*(yield from client.post("/close", {})))
    if own["continuous"]:
        keep, suspects = bindings, possible
    elif name == "naive":
        keep = {b: r for b, r in bindings.items() if b in fresh}
        suspects = local
        if own["record_after_close"]:
            record = check(*(yield from client.get("/log")))
            suspects = record_candidates(record["calls"], lots)
    elif name in ("lone", "blind"):
        keep, suspects = {}, set()
    else:
        keep, suspects = bindings, possible
    note = write_note(keep, suspects, name)
    if own["continuous"] and len(note) > own["cap"]:
        note = ""
    check(*(yield from client.post("/handover", {"note": note})))
    return bindings, possible


def settlement(ref, batch, status):
    return {"reference": ref, "batch": batch, "action": "close" if status == "delivered" else "reissue"}


def play(name, client, own):
    for _ in range(300):
        _, me = yield from client.get("/whoami")
        if me["active"] == "active":
            break
        if me["frozen"]:
            return
        yield from client.sleep(0.2)
    else:
        return
    if own["continuous"]:
        memory = None
        for _ in range(own["shifts"]):
            memory = yield from work(name, client, own, memory)
    else:
        yield from work(name, client, own, None)
