"""Handoff receipt assay: one-hop resolution from the writer's own delivered replies, and its fail-closed cases."""

import copy
import json

from conftest import FLOTILLA, load

RECEIPTS = load(FLOTILLA / "handoff_receipts.py")
SIM = load(FLOTILLA / "sim.py")
SEED = 7236499753218553
HIDDEN = ("lots_exact", "lots_kept", "lots_pointer", "missing_bindings", "note_unparsed", "pairs_kept", "pending",
          "unparsed_bindings", "length")


def played(policies="careful"):
    return SIM.simulate("handoff", "team", 0, policies, SEED, 4, difficulty="canary")["events"]


def notes(rows):
    return {row["shift"]: row for row in rows if row["kind"] == "note"}


def seen_by(events, seat):
    return {item["batch"] for e in events if e["kind"] == "desk_view" and e["seat"] == seat
            for item in e["data"]["reply"].get("plan", []) + e["data"]["reply"].get("open", [])}


def handover(events, shift):
    return next(e for e in events if e["kind"] == "handover" and e["data"]["shift"] == shift)


def test_handles_are_literal_whole_batch_ids():
    note = "B95's lots, b19 (B950) xB12 B07/B08 B9 B 33 B_44 L22 R10 B95"
    assert RECEIPTS.handles(note) == ["B95", "B19", "B07", "B08"]
    assert RECEIPTS.handles("") == []


def test_careful_notes_resolve_from_own_deliveries():
    events = played()
    rows = RECEIPTS.assay(events, "sim")
    run, by_shift = rows[0], notes(rows)
    assert run == {"kind": "run", "log": "sim", "condition": "team", "shifts": 3, "notes": 2, "final_notes": 1,
                   "error": None}
    assert sorted(by_shift) == [1, 2]
    lots = {item["batch"]: sorted(item["lots"]) for e in events if e["kind"] == "desk_view"
            for item in e["data"]["reply"].get("plan", []) + e["data"]["reply"].get("open", [])}
    for shift, row in by_shift.items():
        assert row["note"] == handover(events, shift)["data"]["note"]
        assert row["handles"] and row["resolved"] == row["handles"] and row["unresolved"] == {}
        assert row["state"] == "attached" and row["receipt"] == {h: lots[h] for h in row["handles"]}
        assert row["receipt_bytes"] == len(RECEIPTS.canonical(row["receipt"]).encode("utf-8"))
        assert row["receipt_lots"] == sum(map(len, row["receipt"].values()))
    assert RECEIPTS.assay(copy.deepcopy(events), "sim") == rows


def test_hidden_fields_and_world_records_do_not_matter():
    events = played()
    blind = [e for e in copy.deepcopy(events) if e["kind"] not in ("settled", "dispatched", "ticket", "printed")]
    for event in blind:
        if event["kind"] == "handover":
            for key in HIDDEN:
                event["data"].pop(key, None)
    assert RECEIPTS.assay(blind, "sim") == RECEIPTS.assay(events, "sim")


def test_other_seat_and_unknown_handles_stay_unresolved():
    events = copy.deepcopy(played())
    only_first = sorted(seen_by(events, "s1") - seen_by(events, "s2"))
    unknown = next(f"B{i:02d}" for i in range(100) if f"B{i:02d}" not in seen_by(events, "s1") | seen_by(events, "s2")
                   | seen_by(events, "s3"))
    note = handover(events, 2)["data"]["note"] + f" see {only_first[0]} and {unknown.lower()}"
    handover(events, 2)["data"]["note"] = note
    row = notes(RECEIPTS.assay(events, "sim"))[2]
    assert row["note"] == note
    assert row["unresolved"] == {only_first[0]: "elsewhere", unknown: "not_seen"}
    assert only_first[0] not in row["receipt"] and unknown not in row["receipt"]


def test_unconfirmed_future_and_conflicting_views_fail_closed():
    events = played()
    first = notes(RECEIPTS.assay(events, "sim"))[1]
    target = first["handles"][0]

    unconfirmed = copy.deepcopy(events)
    for event in unconfirmed:
        if event["kind"] == "delivered" and event["seat"] == "s1":
            event["data"]["ids"] = [i for i in event["data"]["ids"] if i != f"batch-{target}"]
    assert notes(RECEIPTS.assay(unconfirmed, "sim"))[1]["unresolved"] == {target: "unconfirmed"}

    future = copy.deepcopy(events)
    handover(future, 1)["seq"] = 0
    row = notes(RECEIPTS.assay(future, "sim"))[1]
    assert set(row["unresolved"].values()) == {"after_filing"} and row["state"] == "empty_unresolved"
    assert row["receipt"] == {} and row["receipt_bytes"] == 2 and row["receipt_batches"] == 0

    conflict = copy.deepcopy(events)
    view = next(e for e in conflict if e["kind"] == "desk_view" and e["seat"] == "s1"
                and any(i["batch"] == target for i in e["data"]["reply"].get("plan", [])))
    forged = copy.deepcopy(view)
    forged["seq"], forged["data"]["view"] = handover(conflict, 1)["seq"] - 0.5, "view-forged"
    for item in forged["data"]["reply"]["plan"]:
        item["lots"] = item["lots"][1:]
    receipt = {"kind": "delivered", "seat": "s1", "seq": forged["seq"] + 0.25, "t": 0.0,
               "data": {"ids": ["view-forged", f"batch-{target}"], "path": forged["data"]["path"]}}
    assert notes(RECEIPTS.assay([*conflict, forged, receipt], "sim"))[1]["unresolved"][target] == "conflicting"


def test_missing_note_and_payload_free_logs():
    rows = RECEIPTS.assay(played({"s2": "nop"}), "sim")
    assert rows[0]["notes"] == 2 and rows[0]["error"] is None
    missing = notes(rows)[2]
    assert missing["state"] == "missing_note" and missing["receipt"] is None and missing["receipt_bytes"] is None
    stripped = [e for e in played() if e["kind"] != "desk_view"]
    assert "hidden parameters" in RECEIPTS.assay(stripped, "sim")[0]["error"]


def test_twin_condition_is_refused_before_notes(tmp_path):
    events = copy.deepcopy(played())
    start = next(e for e in events if e["kind"] == "start")
    start["data"]["condition"] = "twin"
    rows = RECEIPTS.assay(events, "sim")
    assert len(rows) == 1 and rows[0]["kind"] == "run" and rows[0]["condition"] == "twin"
    assert rows[0]["notes"] == 0 and rows[0]["shifts"] is None and "twin" in rows[0]["error"]
    log = tmp_path / "events.jsonl"
    log.write_text("".join(json.dumps(e) + "\n" for e in events))
    assert RECEIPTS.main([str(log), "--out", str(tmp_path / "out.jsonl")]) == 1


def test_cli_writes_stable_jsonl(tmp_path):
    log = tmp_path / "events.jsonl"
    log.write_text("".join(json.dumps(e) + "\n" for e in played()))
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json\n")
    first, second = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    assert RECEIPTS.main([str(log), "--out", str(first)]) == 0
    assert RECEIPTS.main([str(log), "--out", str(second)]) == 0
    assert first.read_bytes() == second.read_bytes()
    assert [json.loads(line)["kind"] for line in first.read_text().splitlines()] == ["run", "note", "note"]
    assert RECEIPTS.main([str(log), str(bad), "--out", str(first)]) == 1
    rows = [json.loads(line) for line in first.read_text().splitlines()]
    assert rows[-1]["log"] == str(bad) and rows[-1]["error"]
