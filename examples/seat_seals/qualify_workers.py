import copy
import hashlib
import itertools
import json
import os
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

from ..native_pair_bounds import summarize
from .contract import load_registration
from .worker_layout import assigned, digest, make_plan
from .worker_report import join

Record = dict[str, Any]
Pair = tuple[int | None, int | None]

SCHEMA = "harbor.seat-seal.worker-qualification/v1"
PACKET_SCHEMA = "harbor.seat-seal.worker-packet/v1"
HERE = Path(__file__).resolve()
REGISTRATION = HERE.with_name("samples") / "registration.json"
LAYOUTS = ("paired", "crossed")
MODES = ("continue", "seal")
GRADES = (0, 1)
VALUES: tuple[int | None, ...] = (None, 0, 1)
PAIR_VALUES: tuple[Pair, ...] = tuple(
    (first, second) for first in VALUES for second in VALUES
)
BLOCKS = 9
WORKERS = 3
ARMS = 2 * BLOCKS
WORKER_ARMS = ARMS // WORKERS
TWO_OF_THREE = {"paired": 6, "crossed": 3}
TWO_OF_THREE_WIDTH = 6
SHAPE = (
    "net_sum",
    "denominator",
    "known_arms",
    "missing_arms",
    "paired_finals",
    "joint",
)
UNSEEN: Record = {"grade": None, "joined": False, "gate": "unknown", "native_tag": None}
SYNTHETIC = b"seat-seal worker qualification control, not native evidence\0"
REFUSALS = (ValueError, TypeError)
COLD = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "from examples.seat_seals.worker_layout import digest, make_plan\n"
    "text = Path(sys.argv[1]).read_text(encoding='utf-8')\n"
    "found = json.loads(text, object_pairs_hook=lambda pairs: dict(reversed(pairs)))\n"
    "print(json.dumps([digest(make_plan(found, 3, layout)) for layout in ('paired', 'crossed')]))\n"
)
LITERAL: tuple[tuple[list[Pair], list[int], list[list[Any]]], ...] = (
    ([(None, None)], [-1, 1], [[0, [0, 1]], [1, [0]]]),
    ([(None, 1)], [0, 1], [[0, [0]], [1, [0]]]),
    ([(1, None)], [-1, 0], [[0, [0, 1]]]),
    (
        [(0, 1), (1, 0), (None, 0), (1, None), (None, None)],
        [-3, 1],
        [[1, [1, 2, 3, 4]], [2, [1, 2, 3]]],
    ),
)
MALFORMED: tuple[Any, ...] = (
    [(True, 0)],
    [(0, False)],
    [(None, True)],
    [(0, 1)] * 63 + [(1, True)],
    [(2, 0)],
    [(0,)],
    [],
    [(0, 1)] * 65,
)


class Ledger:
    def __init__(self) -> None:
        self.checks: Counter[str] = Counter()
        self.failed: dict[str, str] = {}

    def check(self, name: str, held: bool, case: object) -> None:
        self.checks[name] += 1
        if not held and name not in self.failed:
            self.failed[name] = str(case)[:300]

    def refused(self, name: str, case: object, action: Callable[[], object]) -> None:
        try:
            action()
        except REFUSALS:
            self.check(name, True, case)
            return
        except Exception as error:
            self.check(name, False, f"{case}: {type(error).__name__} {error}")
            return
        self.check(name, False, f"{case}: accepted")


def completions(arms: Sequence[Pair]) -> Iterator[list[tuple[int, int]]]:
    holes = sum(value is None for pair in arms for value in pair)
    assert holes <= ARMS
    for fill in itertools.product(GRADES, repeat=holes):
        values = iter(fill)
        yield [
            (
                next(values) if first is None else first,
                next(values) if second is None else second,
            )
            for first, second in arms
        ]


def oracle(arms: Sequence[Pair]) -> Record:
    points = {
        (sum(c < s for c, s in grid), sum(c > s for c, s in grid))
        for grid in completions(arms)
    }
    nets = [positive - negative for positive, negative in points]
    known = sum(value is not None for pair in arms for value in pair)
    return {
        "net_sum": [min(nets), max(nets)],
        "denominator": len(arms),
        "known_arms": known,
        "missing_arms": 2 * len(arms) - known,
        "paired_finals": sum(None not in pair for pair in arms),
        "joint": [
            [p, sorted(n for q, n in points if q == p)]
            for p in sorted({p for p, _ in points})
        ],
    }


def shape(report: object) -> Record | None:
    return {key: report.get(key) for key in SHAPE} if isinstance(report, dict) else None


def bounds_section(ledger: Ledger) -> None:
    cases = [
        list(c)
        for size in range(1, 5)
        for c in itertools.product(PAIR_VALUES, repeat=size)
    ]
    cases += [list(c) for c in itertools.combinations_with_replacement(PAIR_VALUES, 5)]
    for arms in cases:
        want = oracle(arms)
        ledger.check("bounds.oracle", shape(summarize(arms)) == want, arms)
        ledger.check("bounds.order", shape(summarize(arms[::-1])) == want, arms)
    for arms, net, joint in LITERAL:
        found = summarize(arms)
        held = (
            [found["net_sum"], found["joint"]]
            == [net, joint]
            == [oracle(arms)["net_sum"], oracle(arms)["joint"]]
        )
        ledger.check("bounds.literal", held, arms)
    free: list[Pair] = [(None, None)] * 64
    half: list[Pair] = [(None, None), (1, 0)] * 32
    large: tuple[tuple[list[Pair], list[int], list[list[Any]]], ...] = (
        (free, [-64, 64], [[p, list(range(65 - p))] for p in range(65)]),
        (half, [-64, 0], [[p, list(range(32, 65 - p))] for p in range(33)]),
    )
    for arms, net, joint in large:
        found = summarize(arms)
        held = [found["net_sum"], found["joint"], found["denominator"]] == [
            net,
            joint,
            64,
        ]
        ledger.check("bounds.sixty_four", held, net)
    for arms in MALFORMED:
        ledger.refused("bounds.refusal", arms, lambda arms=arms: summarize(arms))


def registration_blocks(registration: Record) -> list[dict[str, str]]:
    grouped: dict[tuple[Any, ...], dict[str, str]] = {}
    for cell in registration["cells"]:
        key = (cell["team"], cell["schedule"], cell["seed"], cell["fixture"])
        grouped.setdefault(key, {})[cell["mode"]] = cell["ticket"]
    return sorted(grouped.values(), key=lambda block: min(block.values()))


def expected_owners(
    blocks: list[dict[str, str]], workers: int, layout: str
) -> list[list[str]]:
    owned: list[list[str]] = [[] for _ in range(workers)]
    for index, block in enumerate(blocks):
        first = index % workers
        owned[first].append(block["continue"])
        owned[first if layout == "paired" else (first + 1) % workers].append(
            block["seal"]
        )
    return [sorted(tickets) for tickets in owned]


def layout_section(ledger: Ledger, registration: Record) -> dict[str, Record]:
    blocks = registration_blocks(registration)
    ledger.check("layout.registration", len(blocks) == BLOCKS, len(blocks))
    keyed = json.loads(
        json.dumps(registration), object_pairs_hook=lambda pairs: dict(reversed(pairs))
    )
    moved = copy.deepcopy(registration)
    moved["cells"].reverse()
    cells = registration["cells"]
    private = {
        cell[field] for cell in cells for field in ("team", "schedule", "fixture")
    }
    plans: dict[str, Record] = {}
    for layout, workers in itertools.product(LAYOUTS, range(1, len(blocks) + 1)):
        case = f"{layout}/{workers}"
        plan = make_plan(copy.deepcopy(registration), workers, layout)
        owned = [assigned(plan, worker) for worker in range(workers)]
        held = plan["blocks"] == blocks and owned == expected_owners(
            blocks, workers, layout
        )
        ledger.check("layout.assignment", held, case)
        every = sorted(ticket for tickets in owned for ticket in tickets)
        ledger.check(
            "layout.partition",
            every == sorted(c["ticket"] for c in cells) and all(owned),
            case,
        )
        ledger.check(
            "layout.outcome_free",
            not any(word in json.dumps(plan) for word in private),
            case,
        )
        same = digest(make_plan(copy.deepcopy(keyed), workers, layout)) == digest(plan)
        ledger.check("layout.key_order", same, case)
        shuffled = make_plan(copy.deepcopy(moved), workers, layout)
        same = [assigned(shuffled, worker) for worker in range(workers)] == owned
        ledger.check(
            "layout.cell_order_membership",
            shuffled["blocks"] == plan["blocks"] and same,
            case,
        )
        if workers == WORKERS:
            plans[layout] = plan
    done = subprocess.run(
        [sys.executable, "-BS", "-c", COLD, str(REGISTRATION)],
        cwd=HERE.parents[2],
        env={"PATH": os.environ.get("PATH", ""), "PYTHONHASHSEED": "1"},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    want = [digest(plans[layout]) for layout in LAYOUTS]
    ledger.check(
        "layout.cold_process",
        done.returncode == 0 and json.loads(done.stdout) == want,
        done.stderr[-300:],
    )
    return plans


def graded_world(plan: Record) -> Record:
    rows: dict[str, Record] = {}
    for index, block in enumerate(plan["blocks"]):
        for mode in MODES:
            ticket = block[mode]
            tag = (
                "sha256:"
                + hashlib.sha256(SYNTHETIC + ticket.encode("ascii")).hexdigest()
            )
            grade = index % 2 if mode == "continue" else (index // 2) % 2
            rows[ticket] = {
                "ticket": ticket,
                "status": "graded",
                "grade": grade,
                "joined": True,
                "gate": "complete",
                "native_tag": tag,
            }
    return {
        "rows": rows,
        "claims": {ticket: {row["native_tag"]} for ticket, row in rows.items()},
    }


def changed(
    world: Record, ticket: str, claims: tuple[str, ...] | None = None, **fields: Any
) -> Record:
    rows = {key: dict(row) for key, row in world["rows"].items()}
    owned = {key: set(tags) for key, tags in world["claims"].items()}
    rows[ticket].update(fields)
    if claims is not None:
        owned[ticket] = set(claims)
    return {"rows": rows, "claims": owned}


def packet(plan: Record, worker: int, world: Record) -> Record:
    tickets = assigned(plan, worker)
    claims = sorted(
        [ticket, tag] for ticket in tickets for tag in world["claims"][ticket]
    )
    rows = [dict(world["rows"][ticket]) for ticket in tickets]
    return {
        "schema": PACKET_SCHEMA,
        "plan": digest(plan),
        "worker": worker,
        "claims": claims,
        "rows": rows,
    }


def expected(
    plan: Record, world: Record, received: tuple[int, ...]
) -> tuple[list[Pair], int, int]:
    held = {
        ticket: world["rows"][ticket]
        for worker in received
        for ticket in assigned(plan, worker)
    }
    owners: dict[str, set[str]] = {}
    for ticket in held:
        for tag in world["claims"][ticket]:
            owners.setdefault(tag, set()).add(ticket)
    shared = {
        ticket for tickets in owners.values() if len(tickets) > 1 for ticket in tickets
    }
    known = {
        t for t, row in held.items() if row["status"] == "graded" and t not in shared
    }
    arms: list[Pair] = []
    linked = scheduled = 0
    for block in plan["blocks"]:
        first, second = (
            held[block[mode]]["grade"] if block[mode] in known else None
            for mode in MODES
        )
        arms.append((first, second))
        joined = all(
            block[mode] in known and held[block[mode]]["joined"] for mode in MODES
        )
        linked += int(joined)
        scheduled += int(
            joined
            and all(
                held[block[mode]]["gate"] in ("complete", "natural") for mode in MODES
            )
        )
    return arms, linked, scheduled


def verify(
    ledger: Ledger, name: str, plan: Record, world: Record, received: tuple[int, ...]
) -> Record:
    packets = [packet(plan, worker, world) for worker in received]
    report = join(plan, copy.deepcopy(packets))
    arms, linked, scheduled = expected(plan, world, received)
    case = f"{plan['layout']} {name} {list(received)}"
    ledger.check("join.bounds", shape(report["bounds"]) == oracle(arms), case)
    found = [report["paired_interventions"], report["paired_schedules"]]
    ledger.check(
        "join.grade_join_gate", found == [linked, scheduled], f"{case} {found}"
    )
    for order in itertools.permutations(packets):
        ledger.check(
            "join.receipt_order", join(plan, copy.deepcopy(list(order))) == report, case
        )
    return report


def subset_section(ledger: Ledger, plans: dict[str, Record]) -> Record:
    table: Record = {}
    for layout, plan in plans.items():
        world, rows = graded_world(plan), []
        for size in range(WORKERS + 1):
            for received in itertools.combinations(range(WORKERS), size):
                report = verify(ledger, "withdrawal", plan, world, received)
                bounds, linked = report["bounds"], report["paired_interventions"]
                net = bounds["net_sum"]
                ledger.check(
                    "join.blocks_retained", bounds["denominator"] == BLOCKS, received
                )
                if size == WORKERS - 1:
                    held = (
                        linked == TWO_OF_THREE[layout]
                        and bounds["missing_arms"]
                        == net[1] - net[0]
                        == TWO_OF_THREE_WIDTH
                    )
                    ledger.check(
                        "join.two_of_three", held, [layout, received, linked, net]
                    )
                rows.append(
                    [
                        list(received),
                        linked,
                        report["paired_schedules"],
                        bounds["known_arms"],
                        net,
                    ]
                )
        table[layout] = rows
    return table


def controls(
    plan: Record,
) -> list[tuple[str, Record, tuple[int, ...], list[int | None]]]:
    base = graded_world(plan)
    a, b = assigned(plan, 0)[0], assigned(plan, 1)[0]
    tag = base["rows"][a]["native_tag"]
    every, pair = tuple(range(WORKERS)), 2 * WORKER_ARMS
    across = changed(base, b, (tag,), native_tag=tag)
    weak = changed(base, b, (tag,), status="incomplete", **UNSEEN)
    return [
        (
            "absent_ticket",
            changed(base, a, (), status="missing", **UNSEEN),
            every,
            [ARMS - 1, BLOCKS - 1, BLOCKS - 1],
        ),
        (
            "unsupported_join",
            changed(base, a, joined=False),
            every,
            [ARMS, BLOCKS - 1, BLOCKS - 1],
        ),
        (
            "gate_incomplete",
            changed(base, a, gate="incomplete"),
            every,
            [ARMS, BLOCKS, BLOCKS - 1],
        ),
        ("collision_across_workers", across, every, [ARMS - 2, None, None]),
        ("collision_partner_unreceived", across, (0, 2), [pair, None, None]),
        ("weak_partial_collision", weak, every, [ARMS - 2, None, None]),
        ("weak_partner_unreceived", weak, (0, 2), [pair, None, None]),
    ]


def control_section(ledger: Ledger, plans: dict[str, Record]) -> list[list[Any]]:
    table: list[list[Any]] = []
    for layout, plan in plans.items():
        for name, world, received, want in controls(plan):
            report = verify(ledger, name, plan, world, received)
            got = [
                report["bounds"]["known_arms"],
                report["paired_interventions"],
                report["paired_schedules"],
            ]
            held = all(w is None or w == g for w, g in zip(want, got, strict=True))
            ledger.check(f"control.{name}", held, [layout, want, got])
            table.append([layout, name, *got, report["bounds"]["net_sum"]])
    return table


MUTATIONS: tuple[tuple[str, Callable[[Record], object]], ...] = (
    ("schema", lambda p: p.update(schema=PACKET_SCHEMA + "-other")),
    ("plan_digest", lambda p: p.update(plan="sha256:" + "0" * 64)),
    ("worker_bool", lambda p: p.update(worker=bool(p["worker"]))),
    ("row_missing", lambda p: p["rows"].pop()),
    ("rows_unsorted", lambda p: p["rows"].reverse()),
    ("grade_bool", lambda p: p["rows"][0].update(grade=True)),
    ("joined_int", lambda p: p["rows"][0].update(joined=1)),
    ("graded_unknown_gate", lambda p: p["rows"][0].update(gate="unknown")),
    ("claims_unsorted", lambda p: p["claims"].reverse()),
    ("witness_unclaimed", lambda p: p["claims"].pop(0)),
)


def transfer_section(ledger: Ledger, plans: dict[str, Record]) -> None:
    plan = plans["paired"]
    good = [packet(plan, worker, graded_world(plan)) for worker in range(WORKERS)]
    ledger.refused(
        "transfer.refusal",
        "duplicate_worker",
        lambda: join(plan, copy.deepcopy([good[0], good[0]])),
    )
    foreign = packet(plans["crossed"], 0, graded_world(plans["crossed"]))
    ledger.refused(
        "transfer.refusal",
        "mixed_plan",
        lambda: join(plan, [foreign, copy.deepcopy(good[1])]),
    )
    for name, mutate in MUTATIONS:
        bad = copy.deepcopy(good[0])
        mutate(bad)
        ledger.refused(
            "transfer.refusal",
            name,
            lambda bad=bad: join(plan, [bad, copy.deepcopy(good[1])]),
        )


def qualify() -> Record:
    ledger = Ledger()
    found: Record = {}
    sections: tuple[tuple[str, Callable[[], object]], ...] = (
        ("bounds", lambda: bounds_section(ledger)),
        (
            "layout",
            lambda: found.update(
                plans=layout_section(ledger, load_registration(REGISTRATION))
            ),
        ),
        (
            "withdrawal",
            lambda: found.update(field=subset_section(ledger, found["plans"])),
        ),
        (
            "controls",
            lambda: found.update(controls=control_section(ledger, found["plans"])),
        ),
        ("transfer", lambda: transfer_section(ledger, found["plans"])),
    )
    for name, section in sections:
        try:
            section()
        except Exception as error:
            ledger.check(f"{name}.completed", False, f"{type(error).__name__}: {error}")
    return {
        "schema": SCHEMA,
        "ok": not ledger.failed,
        "checks": dict(sorted(ledger.checks.items())),
        "failed": dict(sorted(ledger.failed.items())),
        "field": found.get("field"),
        "controls": found.get("controls"),
        "packets": "synthetic controls, not native evidence",
    }


def main() -> int:
    if sys.argv[1:]:
        sys.stderr.write("usage: python3 -BS -m examples.seat_seals.qualify_workers\n")
        return 2
    report = qualify()
    sys.stdout.write(json.dumps(report, separators=(",", ":"), sort_keys=True) + "\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
