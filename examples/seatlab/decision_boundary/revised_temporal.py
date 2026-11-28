import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from runpy import run_path
from types import SimpleNamespace
from typing import Any

from ...native_study import require
from . import temporal
from .core import World

TASK = Path(__file__).resolve().parents[2] / "tasks" / "revised-dispatch"
TASK_NAME = "harbor/revised-dispatch"
SCHEMA = "seatlab.revised-temporal/v1"
LABEL = b"seatlab.revised-temporal.normalized-handler-transcript/v1\n"
VARIANTS = ("confirm", "replace")
TIMINGS = ("early", "late")
SLOTS = ("A", "B", "C")
PHASES = ("initial", "revision")
SCALE = 1
MAX_SEED = 1_000_000
DECIDER = "booker"
INBOX = (DECIDER, "GET", "/inbox")
REPORT_FIELDS = frozenset({"phase", "slot", "receipt"})
EXPECTED_GRADES = len(VARIANTS) * len(TIMINGS) * len(SLOTS)
OBSERVATION = (
    "label + booker instruction bytes + the booker's own handler calls in order: "
    "role, method, path, request body, status and reply; booker /inbox receipt "
    "values become first-seen aliases; reporter calls run but are not observed; "
    "not HTTP transport bytes and not a replay of saved trials"
)
SCHEDULES = {
    "early": (
        "reporter reads its brief and sends the initial report; booker reads its "
        "inbox and drafts the reported slot; reporter reads its brief and sends the "
        "revision; booker reads its inbox; booker commits; booker reads its inbox"
    ),
    "late": (
        "reporter reads its brief and sends the initial report; booker reads its "
        "inbox and drafts the reported slot; booker reads its inbox; booker commits; "
        "reporter reads its brief and sends the revision; booker reads its inbox"
    ),
}
DIFFERENCES = (
    (
        "dispatch-recall sets timing with the world setting RECALL_TIMING "
        "(immediate, deferred, withheld) inside a seated trial; here the unmodified "
        "revised-dispatch World has no timing setting and the driver orders legal "
        "handler calls instead"
    ),
    (
        "revised-dispatch opens the revision only after the booker drafts, so both "
        "schedules share the initial report and draft"
    ),
    (
        "there is no withheld arm: the late revision is always sent and read after the "
        "irrevocable commit"
    ),
    (
        "worlds are the confirm and replace variants of one seed, not stay and move "
        "bays; actions are the three slots"
    ),
    (
        "the reporter is a scripted truthful sender and the booker a scripted reader; "
        "no free seat, model, container or HTTP server runs"
    ),
    (
        "grades come from revised-dispatch tests/verify.py grade on the frozen log and "
        "state, not from the dispatch-recall verifier"
    ),
)

type Entry = tuple[str, str, str, dict[str, Any] | None, int, dict[str, Any]]


@dataclass(frozen=True)
class Launch:
    entries: tuple[Entry, ...]
    decision: int
    reward: int
    sensitive: frozenset[str]


def native() -> SimpleNamespace:
    world = run_path(str(TASK / "environment" / "world.py"))
    verify = run_path(str(TASK / "tests" / "verify.py"))
    return SimpleNamespace(
        World=world["World"],
        grade=verify["grade"],
        Cut=verify["Cut"],
        Fixture=verify["Fixture"],
        Refused=verify["WorldEvidenceError"],
    )


def _phases(reply: dict[str, Any]) -> list[str]:
    return [report["phase"] for report in reply["reports"]]


def _play(world: Any, timing: str, slot: str) -> tuple[list[Entry], int]:
    entries: list[Entry] = []

    def call(
        role: str, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        raw = None if body is None else json.dumps(body).encode("ascii")
        status, reply = world.handle(method, path, f"Bearer {world.tokens[role]}", raw)
        require(status == 200, f"{role} {method} {path} was refused")
        entries.append((role, method, path, body, status, reply))
        return reply

    def report(phase: str) -> None:
        brief = call("reporter", "GET", "/brief")
        require(brief["phase"] == phase, f"the {phase} report is not open")
        call("reporter", "POST", "/report", {"phase": phase, "slot": brief["slot"]})

    report("initial")
    first = call(*INBOX)
    require(_phases(first) == ["initial"], "booker did not receive the initial report")
    call(DECIDER, "POST", "/draft", {"slot": first["reports"][0]["slot"]})
    if timing == "early":
        report("revision")
    before = call(*INBOX)
    seen = ["initial", "revision"] if timing == "early" else ["initial"]
    require(_phases(before) == seen, "pre-commit inbox differs from the schedule")
    decision = len(entries)
    call(DECIDER, "POST", "/commit", {"slot": slot})
    if timing == "late":
        report("revision")
    after = call(*INBOX)
    require(_phases(after) == list(PHASES), "post-commit inbox lacks the revision")
    return entries, decision


def launch(
    harbor: SimpleNamespace, variant: str, seed: int, timing: str, slot: str
) -> Launch:
    require(variant in VARIANTS and timing in TIMINGS and slot in SLOTS, "bad cell")
    with tempfile.TemporaryDirectory(prefix="revised-temporal-") as scratch:
        world = harbor.World(Path(scratch), variant, seed)
        try:
            entries, decision = _play(world, timing, slot)
            cut = world.freeze()
            log, state = world.log_path.read_bytes(), world.state_path.read_bytes()
        finally:
            world.close()
    try:
        grade = harbor.grade(
            log,
            state,
            harbor.Cut(cut["run"], cut["events"]),
            harbor.Fixture(variant, seed),
        )
    except harbor.Refused as error:
        raise ValueError(f"native grade refused the launch: {error}") from None
    require(grade.reward in (0, 1) and grade.decision["run"] == cut["run"], "grade")
    receipts = {
        report["receipt"]
        for role, method, path, _, _, reply in entries
        if (role, method, path) == INBOX
        for report in reply["reports"]
    }
    sensitive = frozenset({cut["run"], *world.tokens.values(), *receipts})
    return Launch(tuple(entries), decision, grade.reward, sensitive)


def _alias(
    report: Any,
    aliases: dict[str, str],
    phase_of: dict[str, str],
    receipt_of: dict[str, str],
) -> dict[str, Any]:
    require(
        type(report) is dict and set(report) == REPORT_FIELDS, "inbox report fields"
    )
    phase, receipt = report["phase"], report["receipt"]
    require(phase in PHASES and type(receipt) is str and receipt != "", "inbox report")
    require(
        phase_of.setdefault(receipt, phase) == phase, "receipt reused across phases"
    )
    require(
        receipt_of.setdefault(phase, receipt) == receipt,
        "receipt changed between reads",
    )
    alias = aliases.setdefault(receipt, f"receipt-{len(aliases) + 1}")
    return {**report, "receipt": alias}


def _scrubbed(entry: Entry) -> str:
    role, method, path, body, status, reply = entry
    if (role, method, path) == INBOX:
        reply = {"reports": [{**r, "receipt": None} for r in reply["reports"]]}
    return json.dumps([role, method, path, body, status, reply], sort_keys=True)


def normalize(entries: tuple[Entry, ...]) -> tuple[Entry, ...]:
    aliases: dict[str, str] = {}
    phase_of: dict[str, str] = {}
    receipt_of: dict[str, str] = {}
    normalized: list[Entry] = []
    for role, method, path, body, status, reply in entries:
        if (role, method, path) == INBOX:
            require(type(reply) is dict and set(reply) == {"reports"}, "inbox reply")
            reports = [
                _alias(r, aliases, phase_of, receipt_of) for r in reply["reports"]
            ]
            reply = {"reports": reports}
        normalized.append((role, method, path, body, status, reply))
    rest = "".join(_scrubbed(entry) for entry in entries)
    require(
        not any(receipt in rest for receipt in aliases),
        "a receipt appears outside a booker inbox receipt field",
    )
    require(not any(alias in rest for alias in aliases.values()), "alias collision")
    return tuple(normalized)


def _line(entry: Entry) -> bytes:
    text = json.dumps(list(entry), sort_keys=True, separators=(",", ":"))
    return text.encode("ascii") + b"\n"


def observation(instruction: bytes, entries: tuple[Entry, ...], end: int) -> bytes:
    require(0 <= end <= len(entries), "observation end outside the transcript")
    lines = b"".join(_line(entry) for entry in entries[:end] if entry[0] == DECIDER)
    return LABEL + len(instruction).to_bytes(4, "big") + instruction + lines


def _study_slot(entries: tuple[Entry, ...]) -> str:
    first = next(
        reply
        for role, method, path, _, _, reply in entries
        if (role, method, path) == INBOX
    )
    return first["reports"][0]["slot"]


def _cell(
    instruction: bytes, variant: str, runs: dict[str, Launch]
) -> tuple[World, World]:
    require(tuple(runs) == SLOTS, "a cell needs one launch per slot")
    normalized = {slot: normalize(run.entries) for slot, run in runs.items()}
    decision = runs[SLOTS[0]].decision
    prefix = normalized[SLOTS[0]][:decision]
    require(
        all(
            run.decision == decision and normalized[slot][:decision] == prefix
            for slot, run in runs.items()
        ),
        "physical actions disagree on the normalized decision prefix",
    )
    scores = {slot: run.reward for slot, run in runs.items()}
    study = normalized[_study_slot(prefix)]
    commit = World(variant, 1, observation(instruction, prefix, decision), scores)
    close = World(variant, 1, observation(instruction, study, len(study)), dict(scores))
    return commit, close


def launches(seed: int) -> dict[tuple[str, str, str], Launch]:
    require(type(seed) is int and 0 <= seed <= MAX_SEED, "invalid seed")
    harbor = native()
    return {
        (variant, timing, slot): launch(harbor, variant, seed, timing, slot)
        for variant in VARIANTS
        for timing in TIMINGS
        for slot in SLOTS
    }


def report(seed: int, runs: dict[tuple[str, str, str], Launch]) -> dict[str, Any]:
    require(
        len(runs) == EXPECTED_GRADES, "expected one launch per variant, timing and slot"
    )
    instruction = (TASK / "instructions" / f"{DECIDER}.md").read_bytes()
    arms: dict[str, list[World]] = {arm: [] for arm in temporal.ARMS}
    for variant in VARIANTS:
        for timing in TIMINGS:
            cell = {slot: runs[(variant, timing, slot)] for slot in SLOTS}
            commit, close = _cell(instruction, variant, cell)
            arms[f"{timing}_commit"].append(commit)
            arms[f"{timing}_close"].append(close)
    qualified = temporal.qualify(arms, SCALE)
    return {
        "schema": SCHEMA,
        "task": TASK_NAME,
        "task_sources": [
            "environment/world.py",
            "tests/verify.py",
            "instructions/booker.md",
        ],
        "seed": seed,
        "native_grades": len(runs),
        "rewarded_slots": {
            f"{variant}/{timing}": sum(runs[(variant, timing, s)].reward for s in SLOTS)
            for variant in VARIANTS
            for timing in TIMINGS
        },
        "schedules": SCHEDULES,
        "observation": OBSERVATION,
        "hindsight_action": "the slot of the initial report, in every arm",
        "differences_from_dispatch_recall": list(DIFFERENCES),
        "timing_only_pattern": qualified["timing_only_pattern"],
        "temporal": qualified,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Qualify early and late revisions on the unmodified revised-dispatch world."
    )
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        output = report(args.seed, launches(args.seed))
    except (ValueError, OSError, RuntimeError, KeyError) as error:
        print(f"revised-temporal: {error}", file=sys.stderr)
        return 2
    print(json.dumps(output, sort_keys=True, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
