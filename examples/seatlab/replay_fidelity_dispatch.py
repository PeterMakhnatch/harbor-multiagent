from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

from . import replay_fidelity_evidence as ev

Row = dict[str, Any]
WORLDS = tuple(range(6801, 6809))
UNITS = 6
PARTS = {"opened": 2, "unopened": 4}
LIVE = "live-confirmation.json"
SOURCES = "dispatch-peer-sources.json"
PANEL = "public-receiver-panel-native.json"
PAYLOAD = "dispatch-peer-payload-replay.json"
CONFLICTS = "dispatch-claim-conflicts.json"
CONFLICT_COST = "dispatch-claim-conflicts-cost.json"
PINS = {
    LIVE: "86eaf01d4a5306ed9961bd1d39c5a4e8feff113fdec6be795e23a6c28f217c40",
    SOURCES: "5e2759b8f56c52b056c1e33702cc7a321d6d3204826623cb3aeda08ff2fd79ab",
    PANEL: "ab00808412097f83ec2b05e396545eeaaca094e13f94cc7be78ac47a55130cb6",
    PAYLOAD: "ffe73d888a06248cdccf78ddf44a13c2342a0a88a33629d272c2bc50d1492522",
    CONFLICTS: "24c4d02041d23b1639bd647d4080804715142052523e8a769b34cc3c6069dea7",
    CONFLICT_COST: "24177d2e91b6ee81c7cd6f73f0e837e6b9441779a0c21a262cc641dc7c5e6f7d",
}
LIMITS = (
    "Dispatch native decide-prompt digests were checked against private trial folders by the payload exporter; public files carry that link only as the source manifest digest.",
    "Dispatch conflict replays bind to their source trial by result and prompt digest alone, and take served models from their cost ledger.",
    "Served model strings are literal provider response fields and do not identify a backend version.",
)


def _trial_ref(source: Row) -> str:
    return sha256(f"seatlab-confirmation:v1:{source['source_uuid']}".encode()).hexdigest()


def _optimal(score: Any, label: str) -> int:
    ev.check(isinstance(score, dict) and score.keys() == PARTS.keys(), f"unscored Dispatch reply: {label}")
    total = 0
    for part, shipments in PARTS.items():
        optimal = score[part]["optimal"]
        ev.check(type(optimal) is int and 0 <= optimal <= shipments, f"Dispatch {part} grade out of range: {label}")
        total += optimal
    return total


def _sources(root: Path) -> dict[int, Row]:
    manifest = ev.read(root, SOURCES, PINS[SOURCES])
    ev.check(manifest["schema"] == "seatlab.public-dispatch-peer-sources/v1"
             and manifest["source_receipt_sha256"] == PINS[LIVE], f"{SOURCES} does not name {LIVE}")
    sources = ev.index(manifest["worlds"], "world", "Dispatch source world")
    ev.check(tuple(sources) == WORLDS, "Dispatch sources are not worlds 6801-6808")
    ev.index(sources.values(), "source_uuid", "Dispatch source trial")
    return sources


def _native(root: Path, sources: dict[int, Row]) -> dict[int, Row]:
    live = ev.read(root, LIVE, PINS[LIVE])
    ev.check(live["schema"] == "seatlab.confirmation-native/v1", f"unexpected {LIVE} schema")
    named = ev.index((r for r in live["studies"]["notices"] if r["arm"] == "named"), "world", "named Dispatch trial")
    panel = ev.read(root, PANEL, PINS[PANEL])
    ev.check(panel["schema"] == "seatlab.public-receiver-panel/v1", f"unexpected {PANEL} schema")
    grades = ev.index((r for r in panel["rows"] if r["task"] == "dispatch"), "world", "native Dispatch grade")
    ev.check(named.keys() == grades.keys() == set(WORLDS), "native Dispatch trials do not cover worlds 6801-6808")
    native = {}
    for world, source in sources.items():
        trial, grade, run = named[world], grades[world], f"attnc-s{world}-r0-named-decide"
        ev.check(trial["complete"] is True and trial["behavior_confirmed"] is True
                 and trial["result_sha256"] == source["source_result_sha256"]
                 and trial["trial_ref"] == _trial_ref(source), f"native trial identity differs: {world}")
        ev.check(grade["state"] == "valid" and grade["units"] == UNITS
                 and grade["prompt_sha256"] == source["shown_prompt_sha256"]
                 and grade["trial_sha256"] == sha256(source["source_uuid"].encode()).hexdigest(),
                 f"native receiver grade identity differs: {world}")
        correct = grade["correct"]
        ev.check(type(correct) is int and 0 <= correct <= UNITS and trial["reward"] == correct / UNITS,
                 f"native grade differs from the Harbor reward: {world}")
        calls = ev.index(trial["calls"], "run", "native Dispatch run")
        ev.check(list(calls)[-1] == run, f"native receiver call is not the final call: {world}")
        call = calls[run]
        ev.check(call["role"] == "dispatch" and call["status"] == "settled"
                 and call["unsettled_hold_usd"] == "0.000000000" and call["served_model"] in ev.FLASH,
                 f"native receiver call is not a settled Flash call: {run}")
        native[world] = {"id": run, "correct": correct, "served_model": call["served_model"]}
    return native


def _payload(root: Path, sources: dict[int, Row]) -> dict[int, list[Row]]:
    receipt = ev.read(root, PAYLOAD, PINS[PAYLOAD])
    ev.check(receipt["schema"] == "seatlab.dispatch-payload/v1"
             and receipt["original_receipt_sha256"] == PINS[LIVE], f"{PAYLOAD} does not name {LIVE}")
    cells = ev.index(receipt["cells"], "run", "payload replay run")
    calls = ev.index(receipt["meter"]["calls"], "run", "payload meter run")
    ev.check(sum(cell["arm"] == "shown" for cell in cells.values()) == 2 * len(WORLDS), "payload shown grid changed")
    draws: dict[int, list[Row]] = {}
    for world, source in sources.items():
        draws[world] = []
        for repeat in (0, 1):
            run = f"dpp-s{world}-r{repeat}-shown"
            ev.check(run in cells and run in calls, f"missing payload replay: {run}")
            cell = cells[run]
            ev.check(cell["arm"] == "shown" and cell["world"] == world and cell["repeat"] == repeat
                     and cell["started"] is True and cell["status"] == "accepted"
                     and cell["source_ref"] == _trial_ref(source)
                     and cell["source_result_sha256"] == source["source_result_sha256"]
                     and cell["prompt_sha256"] == source["shown_prompt_sha256"], f"payload replay identity differs: {run}")
            model = ev.settled(calls[run], "dispatch")
            ev.check(cell["served_model"] == model, f"payload reply and meter models differ: {run}")
            draws[world].append({"id": run, "correct": _optimal(cell["scores"], run), "served_model": model})
    return draws


def _conflicts(root: Path, sources: dict[int, Row]) -> dict[int, list[Row]]:
    receipt = ev.read(root, CONFLICTS, PINS[CONFLICTS])
    cost = ev.read(root, CONFLICT_COST, PINS[CONFLICT_COST])
    ev.check(receipt["schema"] == "seatlab.dispatch-claim-conflicts/v1"
             and cost["schema"] == "seatlab.dispatch-claim-conflicts-cost/v1", "unexpected Dispatch conflict schema")
    cells = ev.index(receipt["cells"], "id", "conflict replay run")
    calls = ev.index(cost["ledger"]["calls"], "run", "conflict ledger run")
    ev.check(sum(cell["arm"] == "shown" for cell in cells.values()) == 4 * len(WORLDS), "conflict shown grid changed")
    draws: dict[int, list[Row]] = {}
    for world, source in sources.items():
        draws[world] = []
        for repeat in range(4):
            run = f"dcf-s{world}-r{repeat}-shown"
            ev.check(run in cells and run in calls, f"missing conflict replay: {run}")
            cell = cells[run]
            ev.check(cell["arm"] == "shown" and cell["world"] == world and cell["repeat"] == repeat
                     and cell["status"] == "complete"
                     and cell["source_result_sha256"] == source["source_result_sha256"]
                     and cell["prompt_sha256"] == source["shown_prompt_sha256"], f"conflict replay identity differs: {run}")
            model = ev.settled(calls[run], "dispatch")
            draws[world].append({"id": run, "correct": _optimal(cell["score"], run), "served_model": model})
    return draws


def load(results: Path) -> tuple[list[Row], dict[str, str]]:
    sources = _sources(results)
    native = _native(results, sources)
    payload = _payload(results, sources)
    conflicts = _conflicts(results, sources)
    worlds = []
    for world, source in sources.items():
        replays = payload[world] + conflicts[world]
        ev.index([native[world], *replays], "id", "Dispatch draw")
        worlds.append({"task": "dispatch", "world": world, "units": UNITS,
                       "prompt_sha256": source["shown_prompt_sha256"], "source_trial": source["source_uuid"],
                       "source_result_sha256": source["source_result_sha256"],
                       "native": native[world], "replays": replays})
    return worlds, dict(PINS)
