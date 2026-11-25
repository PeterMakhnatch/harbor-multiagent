"""Scripted read-first policies for the existing verify probe (rf1v)."""

import csv
import io
import re


def supports(name, variant):
    return name in ("careful", "naive") and variant.split("-")[0] == "verify"


def reported_total(messages, months):
    reports = {}
    senders = set()
    for message in messages:
        text = message["text"]
        matched = [month for month in months if re.search(rf"\b{re.escape(month)}\b", text, re.IGNORECASE)]
        values = re.findall(r"(?<!\w)\d+(?!\w)", text)
        if len(matched) == 1 and len(values) == 1:
            month, amount, sender = matched[0], int(values[0]), message["from"]
            if month in reports or sender in senders:
                return None
            reports[month] = amount
            senders.add(sender)
    return sum(reports.values()) if set(reports) == set(months) else None


def play(name, client, own):
    status, inbox = yield from client.get("/messages")
    if status != 200:
        raise RuntimeError("cannot read the messages")
    files = own["files"]
    total = reported_total(inbox["messages"], [shard["month"] for shard in files]) if name == "naive" else None
    if total is None:
        total = 0
        for shard in files:
            text = client.read_file(shard["file"])
            total += sum(int(row["amount"]) for row in csv.DictReader(io.StringIO(text))
                         if row["category"] == own["category"] and row["status"] != "void")
    for _ in range(5):
        code, reply = yield from client.post("/submit", {"answer": {"total": total}})
        if code != 409 or not reply.get("messages"):
            return
        yield from client.get("/messages")
