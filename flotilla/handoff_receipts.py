"""Offline handoff receipt assay: does a batch pointer in a note resolve from what its writer saw, and at what cost?

A shadow of a memory aid, not an intervention: it reads finished world logs (`events.jsonl`, one per argument) and
never changes a task, a reply or a reward. For each non-final note (a `handover` or `handover_missing` event with
shift < shifts) it writes one JSONL row; each log also gets one `run` row, first. Final-shift handovers, filed or
missing, are excluded and only counted in `final_notes`. Condition `twin` is refused with an error row before any
note is read, because the world replaces its notes with canonical ones the seat did not write.

Handles. A handle is a literal whole batch ID in the exact note: `B` or `b`, then exactly 2 ASCII digits, with no
ASCII letter, digit or underscore on either side ("B95's", "B82/B72", "(b19," match; "B950", "AB95", "B9", "B 95"
do not). Handles are upper-cased and kept once, in order of first appearance. Lot and reference IDs are ignored.

Evidence. A handle resolves only from a reply the sending seat itself received in the shift that filed the note:
a `desk_view` with status 200, the same seat and the same shift (also in solo arms, where one seat works every
shift with a fresh session), and a top-level `plan` or `open` entry for that batch, confirmed by a later
`delivered` event of the same seat and path whose ids hold both the view id and `batch-<handle>`, with both events
before the note's `handover` event. "Delivered" means the reply was written to the seat's socket, not that the
seat read it. Resolution attaches lot memberships only; it does not check the note's reference/batch pairs or its
returned/delivered claims. Only the `note`, `shift`, `seat` and `seq` of a `handover` event are read; its
measures, `settled.correct`, `dispatched`, the verifier, `result.json`, task parameters and the family's note
parser are never read. `/log` records (`calls`) are an archive and not used, and nothing is followed through
earlier notes: the assay is one hop. The shift count comes from the `shifts` field of the seat-visible desk
replies; a log without desk replies is refused, never resolved from hidden parameters.

An unresolved handle gets the first true reason: `conflicting` (confirmed views disagree on its lots),
`unconfirmed` (shown to the writer before filing without a matching delivery receipt), `after_filing` (shown to
the writer only after filing), `elsewhere` (shown only to another seat or shift), `not_seen`.

The receipt maps each resolved handle to its sorted lots. `receipt_bytes` is the UTF-8 length of its compact
canonical JSON (sorted keys, no spaces, so an empty receipt is `{}`, 2 bytes). `state` is `attached`,
`empty_no_handles`, `empty_unresolved` or `missing_note`; a missing note has a null receipt and null counts.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HANDLE = re.compile(r"(?<![A-Za-z0-9_])[Bb]([0-9]{2})(?![A-Za-z0-9_])")


@dataclass(frozen=True)
class Sighting:
    batch: str
    lots: tuple[str, ...]
    seat: str
    shift: int
    view_seq: int
    confirmed_seq: int | None


def handles(note: str) -> list[str]:
    return list(dict.fromkeys(f"B{match.group(1)}" for match in HANDLE.finditer(note)))


def canonical(receipt: dict[str, list[str]]) -> str:
    return json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def read_events(path: Path) -> list[dict[str, Any]]:
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for event in events:
        if (not isinstance(event, dict) or not isinstance(event.get("kind"), str)
                or not isinstance(event.get("seq"), int) or not isinstance(event.get("data"), dict)):
            raise TypeError("event lacks kind, seq or data")
    seqs = [event["seq"] for event in events]
    if len(set(seqs)) != len(seqs):
        raise ValueError("duplicate event seq")
    return sorted(events, key=lambda event: event["seq"])


def entries(reply: dict[str, Any]) -> list[tuple[str, tuple[str, ...]]]:
    found = []
    for key in ("plan", "open"):
        items = reply.get(key, [])
        if not isinstance(items, list):
            raise TypeError(f"desk reply {key} is not a list")
        for item in items:
            if not isinstance(item, dict):
                raise TypeError(f"desk reply {key} entry is not an object")
            batch, lots = item.get("batch"), item.get("lots")
            if not isinstance(batch, str) or not isinstance(lots, list) or not all(isinstance(lot, str) for lot in lots):
                raise ValueError(f"desk reply {key} entry lacks a batch or lot list")
            found.append((batch, tuple(sorted(lots))))
    return found


def sightings(events: list[dict[str, Any]]) -> tuple[list[Sighting], set[int]]:
    views = {}
    for event in events:
        if event["kind"] != "desk_view":
            continue
        data = event["data"]
        if not isinstance(data.get("view"), str) or not isinstance(data.get("reply"), dict):
            raise TypeError("desk_view lacks its view id or reply payload")
        if data["view"] in views:
            raise ValueError(f"duplicate desk view {data['view']}")
        views[data["view"]] = event
    if not views:
        raise ValueError("no seat-visible desk_view payloads; refusing to resolve from hidden parameters")
    receipts = {}
    for event in events:
        ids = event["data"].get("ids") if event["kind"] == "delivered" else None
        if not isinstance(ids, list):
            continue
        for view_id in ids:
            view = views.get(view_id)
            if (view_id in receipts or view is None or view["seq"] >= event["seq"] or view["seat"] != event["seat"]
                    or view["data"].get("path") != event["data"].get("path")):
                continue
            receipts[view_id] = (event["seq"], set(ids))
    found, shifts = [], set()
    for view_id, view in views.items():
        data = view["data"]
        if data.get("status") != 200:
            continue
        if not isinstance(data.get("shift"), int):
            raise TypeError(f"desk view {view_id} lacks its shift")
        if "shifts" in data["reply"]:
            shifts.add(data["reply"]["shifts"])
        delivered_seq, ids = receipts.get(view_id, (None, set()))
        for batch, lots in entries(data["reply"]):
            confirmed = delivered_seq if f"batch-{batch}" in ids else None
            found.append(Sighting(batch, lots, view["seat"], data["shift"], view["seq"], confirmed))
    return found, shifts


def resolve(handle: str, seat: str, shift: int, filed: int, seen: list[Sighting]) -> tuple[str, tuple[str, ...] | None]:
    named = [s for s in seen if s.batch == handle]
    own = [s for s in named if s.seat == seat and s.shift == shift]
    confirmed = {s.lots for s in own if s.confirmed_seq is not None and s.confirmed_seq < filed}
    if len(confirmed) == 1:
        return "resolved", next(iter(confirmed))
    if confirmed:
        return "conflicting", None
    if any(s.view_seq < filed for s in own):
        return "unconfirmed", None
    if own:
        return "after_filing", None
    return ("elsewhere" if named else "not_seen"), None


def note_row(event: dict[str, Any], seen: list[Sighting]) -> dict[str, Any]:
    data = event["data"]
    row = {"kind": "note", "seat": event["seat"], "shift": data["shift"], "seq": event["seq"]}
    if event["kind"] == "handover_missing":
        return row | {"state": "missing_note", "note": None, "note_bytes": None, "handles": [], "resolved": [],
                      "unresolved": {}, "receipt": None, "receipt_bytes": None, "receipt_batches": None,
                      "receipt_lots": None}
    note = data.get("note")
    if not isinstance(note, str):
        raise TypeError(f"handover at seq {event['seq']} lacks its note text")
    named = handles(note)
    receipt, unresolved = {}, {}
    for handle in named:
        reason, lots = resolve(handle, event["seat"], data["shift"], event["seq"], seen)
        if lots is None:
            unresolved[handle] = reason
        else:
            receipt[handle] = list(lots)
    state = "attached" if receipt else "empty_unresolved" if named else "empty_no_handles"
    return row | {"state": state, "note": note, "note_bytes": len(note.encode("utf-8")), "handles": named,
                  "resolved": [h for h in named if h in receipt], "unresolved": unresolved, "receipt": receipt,
                  "receipt_bytes": len(canonical(receipt).encode("utf-8")), "receipt_batches": len(receipt),
                  "receipt_lots": sum(len(lots) for lots in receipt.values())}


def assay(events: list[dict[str, Any]], log: str) -> list[dict[str, Any]]:
    start = next((event["data"] for event in events if event["kind"] == "start"), {})
    run = {"kind": "run", "log": log, "condition": start.get("condition"), "shifts": None, "notes": 0,
           "final_notes": 0, "error": None}
    try:
        if run["condition"] == "twin":
            raise ValueError("condition twin: the world writes its canonical notes, the seat does not")
        seen, shifts = sightings(events)
        if len(shifts) != 1 or not isinstance(next(iter(shifts)), int):
            raise ValueError(f"desk replies state {len(shifts)} shift counts, need exactly 1")
        run["shifts"] = next(iter(shifts))
        filed = [event for event in events if event["kind"] in ("handover", "handover_missing")]
        if not all(isinstance(event["data"].get("shift"), int) and isinstance(event.get("seat"), str) for event in filed):
            raise ValueError("handover event lacks its seat or shift")
        rows = [note_row(event, seen) for event in filed if event["data"]["shift"] < run["shifts"]]
    except (ValueError, TypeError) as exc:
        return [run | {"error": str(exc)}]
    run |= {"notes": len(rows), "final_notes": len(filed) - len(rows)}
    keep = {"log": log, "condition": run["condition"], "shifts": run["shifts"]}
    return [run, *({**keep, **row} for row in rows)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logs", nargs="+", type=Path, metavar="EVENTS_JSONL")
    parser.add_argument("--out", type=Path, help="write JSONL here instead of stdout")
    args = parser.parse_args(argv)
    rows, failed = [], False
    for path in args.logs:
        try:
            result = assay(read_events(path), str(path))
        except (OSError, UnicodeDecodeError, ValueError, TypeError) as exc:
            result = [{"kind": "run", "log": str(path), "condition": None, "shifts": None, "notes": 0,
                       "final_notes": 0, "error": str(exc)}]
        failed |= result[0]["error"] is not None
        rows.extend(result)
    text = "".join(json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n" for row in rows)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
