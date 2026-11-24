from __future__ import annotations

import os
import stat
from collections import Counter, defaultdict

from .account_contract import MAX_SEAT_ROWS, validate
from .account_report import MAX_TOTAL_ATTEMPTS, _cost, _coverage, _seat_totals, _value
from .job_stream import MAX_ENTRIES, RESULT, _children, _present
from .native_account import MAX_TOTAL_BYTES, SOURCE, _object, project_result
from .seat_stream import Malformed, Record, _require

SCHEMA = "seatlab.displaced-trial-fragments/v1"
MAX_FRAGMENTS = 64
NONTRIAL_DIRS = frozenset({".sources"})
KINDS = (
    "result_copy",
    "other_in_result_slot",
    "sole_candidate",
    "competing_candidates",
)
DETACHED = ("other_in_result_slot", "sole_candidate", "competing_candidates")
ROLES = {
    True: (
        "final",
        "The job closed after all registered slots completed. These are its final results.",
    ),
    False: (
        "surviving",
        "The job did not close. These surviving results can change.",
    ),
    None: (
        "surviving",
        "The job has no proof of closure. These surviving results can change.",
    ),
}
CAVEAT = (
    "Displaced trial directories are joined to one validated native-job packet by job UUID, trial directory name "
    "and execution UUID; config, timestamps and labels are never evidence. Copies of one execution count once, and "
    "a copy of a packet result adds nothing. Detached executions are other executions in a slot with a packet "
    "result, or candidates in a slot without one. No order between executions and no displaced grade is inferred "
    "or reported. Seat USD and tokens are harness figures, not a bill and not team reward. A directory without a "
    "result is counted as a directory only; logs and trajectories are not read."
)


def _trial_dirs(path: str, identities: set[tuple[int, int]]) -> list[str]:
    try:
        info = os.lstat(path)
    except (OSError, ValueError) as error:
        raise Malformed("fragment directory is missing or unreadable") from error
    _require(stat.S_ISDIR(info.st_mode), "fragment is not a real non-symlink directory")
    identity = (info.st_dev, info.st_ino)
    _require(identity not in identities, "one fragment directory is supplied twice")
    identities.add(identity)
    _require(
        not _present(os.path.join(path, RESULT), "fragment job result"),
        "fragment directory holds a job result; supply displaced trial directories only",
    )
    return [name for name in _children(path) if name not in NONTRIAL_DIRS]


def _admit(
    row: Record,
    executions: dict[str, Record],
    held: dict[str, Record],
    held_slots: dict[str, str],
) -> None:
    trial, slot = row["trial"], row["slot"]
    known = executions.get(trial)
    if known is None and trial in held_slots:
        known = held[held_slots[trial]]
    if known is None:
        executions[trial] = row
        return
    _require(known["slot"] == slot, "one execution UUID appears in two trial slots")
    _require(known == row, "copies of one execution UUID project differently")
    executions[trial] = row


def _collect(
    packet: Record, fragment_dirs: list[str]
) -> tuple[dict[str, Record], dict[str, Counter[int]], list[Record]]:
    held = {a["slot"]: a for a in packet["attempts"]}
    held_slots = {a["trial"]: a["slot"] for a in packet["attempts"]}
    executions: dict[str, Record] = {}
    sources: dict[str, Counter[int]] = defaultdict(Counter)
    coverage: list[Record] = []
    identities: set[tuple[int, int]] = set()
    remaining, dirs, rows = MAX_TOTAL_BYTES, 0, 0
    for index, path in enumerate(fragment_dirs):
        names = _trial_dirs(path, identities)
        dirs += len(names)
        _require(
            dirs <= MAX_TOTAL_ATTEMPTS,
            f"fragments exceed {MAX_TOTAL_ATTEMPTS} trial directories in total",
        )
        missing = 0
        for name in names:
            result = os.path.join(path, name, RESULT)
            if not _present(result, "trial result"):
                missing += 1
                continue
            document, remaining = _object(result, "trial result", remaining)
            row = project_result(document, job_id=packet["scope"], slot_name=name)
            rows += len(row["seats"])
            _require(
                rows <= MAX_SEAT_ROWS,
                f"fragments exceed {MAX_SEAT_ROWS} seat rows in total",
            )
            _admit(row, executions, held, held_slots)
            sources[row["trial"]][index] += 1
        coverage.append(
            {
                "fragment": index,
                "trial_dirs": len(names),
                "results": len(names) - missing,
                "without_result": missing,
            }
        )
    return executions, sources, coverage


def _kinds(
    packet: Record, executions: dict[str, Record]
) -> tuple[dict[str, str], dict[str, list[str]]]:
    held = {a["slot"]: a["trial"] for a in packet["attempts"]}
    by_slot: dict[str, list[str]] = defaultdict(list)
    for trial, row in executions.items():
        by_slot[row["slot"]].append(trial)
    _require(
        len(set(held) | set(by_slot)) <= packet["registered"],
        "displaced executions name more trial slots than the job registered",
    )
    kinds: dict[str, str] = {}
    for slot, trials in by_slot.items():
        for trial in trials:
            if held.get(slot) == trial:
                kinds[trial] = "result_copy"
            elif slot in held:
                kinds[trial] = "other_in_result_slot"
            else:
                kinds[trial] = (
                    "sole_candidate" if len(trials) == 1 else "competing_candidates"
                )
    return kinds, by_slot


def _drill(row: Record, kind: str, sources: Counter[int]) -> Record:
    seats = row["seats"]
    return {
        "trial": row["trial"],
        "slot": row["slot"],
        "kind": kind,
        "sources": sorted(sources),
        "copies": sum(sources.values()),
        "seat_records": len(seats),
        "harness_usd": _coverage(seats, "harness_usd", dollars=True),
        "input_tokens_including_cache": _coverage(seats, "input_tokens"),
        "cache_tokens": _coverage(seats, "cache_tokens"),
        "output_tokens": _coverage(seats, "output_tokens"),
    }


def _slot_counts(
    packet: Record, kinds: dict[str, str], by_slot: dict[str, list[str]]
) -> Record:
    held = {a["slot"] for a in packet["attempts"]}
    lacking = {slot: trials for slot, trials in by_slot.items() if slot not in held}
    absent = packet["registered"] - len(held)
    single = sum(len(trials) == 1 for trials in lacking.values())
    return {
        "registered": packet["registered"],
        "with_result": len(held),
        "with_result_and_other_execution": sum(
            any(kinds[t] == "other_in_result_slot" for t in by_slot[s])
            for s in held
            if s in by_slot
        ),
        "without_result": absent,
        "without_result_one_candidate": single,
        "without_result_competing_candidates": len(lacking) - single,
        "without_result_or_displaced_execution": absent - len(lacking),
    }


def _snapshot(packet: Record) -> Record:
    _require(
        "job_closed" in packet, "packet does not state whether the native job closed"
    )
    closed = packet["job_closed"]
    _require(
        closed is None or type(closed) is bool,
        "packet job_closed is not a boolean or null",
    )
    role, description = ROLES[closed]
    return {"job_closed": closed, "result_role": role, "description": description}


def recover(final_packet: Record, fragment_dirs: list[str]) -> Record:
    packet = validate(final_packet)
    _require(
        packet["source"] == SOURCE, "displaced fragments require a native-job packet"
    )
    _require(
        type(fragment_dirs) is list
        and 1 <= len(fragment_dirs) <= MAX_FRAGMENTS
        and all(type(path) is str for path in fragment_dirs),
        f"expected 1 to {MAX_FRAGMENTS} fragment directories",
    )
    snapshot = _snapshot(packet)
    executions, sources, coverage = _collect(packet, fragment_dirs)
    kinds, by_slot = _kinds(packet, executions)
    counts = Counter(kinds.values())
    detached = {
        kind: [s for t, r in executions.items() if kinds[t] == kind for s in r["seats"]]
        for kind in DETACHED
    }
    drill = [_drill(executions[t], kinds[t], sources[t]) for t in executions]
    return {
        "schema": SCHEMA,
        "scope": packet["scope"],
        "snapshot": snapshot,
        "fragments": coverage,
        "trial_dirs": sum(f["trial_dirs"] for f in coverage),
        "trial_dirs_without_result": sum(f["without_result"] for f in coverage),
        "result_copies": sum(sum(c.values()) for c in sources.values()),
        "executions": len(executions),
        "execution_kinds": {kind: counts[kind] for kind in KINDS},
        "slots": _slot_counts(packet, kinds, by_slot),
        "detached_seats": {
            "all": _seat_totals([s for kind in DETACHED for s in detached[kind]]),
            **{kind: _seat_totals(detached[kind]) for kind in DETACHED},
        },
        "drill": sorted(
            drill, key=lambda d: (KINDS.index(d["kind"]), d["slot"], d["trial"])
        ),
        "limits": {
            "fragments": MAX_FRAGMENTS,
            "entries_per_fragment": MAX_ENTRIES,
            "trial_dirs": MAX_TOTAL_ATTEMPTS,
            "result_bytes": MAX_TOTAL_BYTES,
            "seat_rows": MAX_SEAT_ROWS,
        },
        "caveat": CAVEAT,
    }


def _tokens(label: str, coverage: Record) -> str:
    return f"{label}: {_value(coverage['known_sum'])}; seats known {coverage['known_seats']}, unknown {coverage['unknown_seats']}"


def text(report: Record) -> str:
    kinds, slots, seats = (
        report["execution_kinds"],
        report["slots"],
        report["detached_seats"]["all"],
    )
    role = report["snapshot"]["result_role"]
    return "\n".join(
        [
            f"Packet results are {role}: {report['snapshot']['description']}",
            (
                f"Displaced fragments: {len(report['fragments'])}; trial directories {report['trial_dirs']}; "
                f"without a result {report['trial_dirs_without_result']} (directories, not executions)"
            ),
            (
                f"Displaced executions: {report['executions']} distinct from {report['result_copies']} result copies; "
                f"copies of a {role} result {kinds['result_copy']}"
            ),
            (
                f"Slots with a {role} result: {slots['with_result']}/{slots['registered']}; with another displaced execution "
                f"{slots['with_result_and_other_execution']} ({kinds['other_in_result_slot']} executions)"
            ),
            (
                f"Slots without a {role} result: {slots['without_result']}; "
                f"one displaced candidate {slots['without_result_one_candidate']}; "
                f"competing candidates {slots['without_result_competing_candidates']} ({kinds['competing_candidates']} executions); "
                f"no displaced execution {slots['without_result_or_displaced_execution']}"
            ),
            (
                f"Detached executions (not {role} results, not team reward): {sum(kinds[k] for k in DETACHED)}; "
                f"seat records {seats['seat_records']}"
            ),
            _cost("  Harness-reported subtotal", seats["harness_usd"]),
            _tokens(
                "  Input tokens including cache", seats["input_tokens_including_cache"]
            ),
            _tokens("  Output tokens", seats["output_tokens"]),
            report["caveat"],
        ]
    )
