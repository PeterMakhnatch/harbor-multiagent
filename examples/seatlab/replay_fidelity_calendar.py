from __future__ import annotations

from pathlib import Path
from typing import Any

from . import replay_fidelity_evidence as ev

NATIVE = ("calendar-visible-check-live.json", "664de8b392dbb3aae3789c1bf3e78d970b47d6897eac5376b9afee83b72cb4af")
REPLAY = ("calendar-visible-fixed-replay.json", "eae1e32b7ba236e639251c518fbe93be19271f0e060d58e0e87ae61f4c60c1cc")
COST = ("calendar-visible-fixed-replay-cost.json", "dcfdb8020c05a44bdc510e4cc03d9b779cc51d09b606962fb1786f67890c4e94")
WORLDS = tuple(range(24, 48))
LABELS = frozenset(f"w{world}" for world in WORLDS)
REPEATS = (0, 1)
ARM = "lists"
NATIVE_ROLE = "cal_decide"
REPLAY_ROLE = "decision_maker"
VERIFIED = ("attempted", "complete", "evidence_complete", "reward_valid", "reward_verified")
LIMITS = (
    "The private native exporter supplied the factual user-prompt link; the public native grade has no prompt digest.",
    "Replay grades join their charges only through the vjrec-wr-w<world>-r<repeat>-lists run-name grid.",
    "Served model strings come from the spend ledger; no per-call request alias is recorded.",
)


def _native(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    ev.check(doc.get("schema") == "seatlab.native-calendar-visible-check-results/v1", "unexpected native schema")
    ev.check(doc.get("mode") == "live" and doc.get("held_calls") == 0, "native study is not settled and live")
    ev.index(doc["cells"], "trial_uuid", "native trial")
    cells = ev.index([cell for cell in doc["cells"] if cell["arm"] == ARM], "world", "native lists world")
    ev.check(cells.keys() == LABELS, "native lists worlds differ from the registered 24..47")
    calls = ev.index(doc["all_call_costs"]["calls"], lambda call: (call["run"], call["role"]), "native ledger call")
    native: dict[str, dict[str, Any]] = {}
    for label, cell in cells.items():
        run = f"cvj-{label}-{ARM}"
        ev.check(cell["run"] == run, f"{label}: native run {cell['run']!r} is not {run}")
        ev.check(all(cell[flag] is True for flag in VERIFIED), f"{run}: native grade is not complete and verified")
        ev.check(cell["control"] == "live" and cell["error"] is None, f"{run}: native team is not a clean live run")
        reward = cell["reward"]
        ev.check(type(reward) is int and reward in (0, 1), f"{run}: native reward is not binary")
        stock = {"oracle": reward, "outer": reward, "verifier": reward}
        ev.check(cell["stock"] == stock, f"{run}: stock grades differ from the native reward")
        ev.check((run, NATIVE_ROLE) in calls, f"{run}: no {NATIVE_ROLE} receiver charge")
        decide = calls[run, NATIVE_ROLE]
        native[label] = {
            "trial": cell["trial_uuid"],
            "result": cell["result_sha256"],
            "draw": {"id": f"{run}:{NATIVE_ROLE}", "correct": reward, "served_model": ev.settled(decide, NATIVE_ROLE)},
        }
    return native


def _sources(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    ev.check(doc.get("schema") == "seatlab.calendar-visible-fixed-replay/v1", "unexpected replay schema")
    sources = ev.index(doc["sources"], "world", "replay source")
    ev.check(sources.keys() == LABELS, "replay sources differ from the registered 24..47")
    for label, source in sources.items():
        factual = source["factual_prompt_sha256"]
        ev.check(source["views"][ARM] == factual, f"{label}: lists view is not the factual receiver prompt")
    return sources


def _replays(doc: ev.Row, cost: ev.Row, sources: dict[str, Any]) -> dict[str, list[ev.Row]]:
    ev.check(cost.get("schema") == "seatlab.direct-flash-cost/v1", "unexpected replay cost schema")
    ev.check(cost.get("unsettled_calls") == 0, "replay cost ledger has unsettled calls")
    rows = [row for row in doc["observations"] if row["arm"] == ARM]
    draws = ev.index(rows, lambda row: (row["world"], row["repeat"]), "lists replay draw")
    ev.check(draws.keys() == {(label, repeat) for label in LABELS for repeat in REPEATS}, "lists replay grid incomplete")
    charges = ev.index(cost["calls"], "run", "replay charge")
    replays: dict[str, list[ev.Row]] = {label: [] for label in LABELS}
    for (label, repeat), row in sorted(draws.items()):
        run = f"vjrec-wr-{label}-r{repeat}-{ARM}"
        source = sources[label]
        ev.check(row["source_trial_uuid"] == source["trial_uuid"], f"{run}: replay source trial differs")
        ev.check(row["prompt_sha256"] == source["factual_prompt_sha256"], f"{run}: replay prompt digest differs")
        correct = row["correct"]
        ev.check(row["status"] == "scored" and type(correct) is int and correct in (0, 1), f"{run}: grade not scored")
        ev.check(run in charges, f"{run}: no settled replay charge")
        model = ev.settled(charges[run], REPLAY_ROLE)
        replays[label].append({"id": f"{run}:{REPLAY_ROLE}", "correct": correct, "served_model": model})
    return replays


def load(results: Path) -> tuple[list[ev.Row], dict[str, str]]:
    root = Path(results)
    native = _native(ev.read(root, *NATIVE))
    replay = ev.read(root, *REPLAY)
    sources = _sources(replay)
    replays = _replays(replay, ev.read(root, *COST), sources)
    worlds: list[ev.Row] = []
    for world in WORLDS:
        label = f"w{world}"
        own, source = native[label], sources[label]
        ev.check(own["trial"] == source["trial_uuid"], f"{label}: native trial differs from replay source")
        ev.check(own["result"] == source["result_sha256"], f"{label}: native result digest differs from replay source")
        worlds.append({
            "task": "calendar",
            "world": world,
            "units": 1,
            "prompt_sha256": source["factual_prompt_sha256"],
            "source_trial": source["trial_uuid"],
            "source_result_sha256": source["result_sha256"],
            "native": own["draw"],
            "replays": replays[label],
        })
    return worlds, {name: digest for name, digest in (NATIVE, REPLAY, COST)}
