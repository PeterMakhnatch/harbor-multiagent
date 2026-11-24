import copy
import itertools
import json
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from typing import Any, NamedTuple

from .bracket import (
    BOUND_SCHEMA,
    HOST_SCHEMA,
    MAX_REVISION,
    WORLD_SCHEMA,
    SeatCutInvalid,
    analyze,
)

SCHEMA = "harbor.seat-cut.order-qualification/v1"
MAX_WORLD_EVENTS = 2
MAX_RPCS = 2
OUTCOMES = (
    "ok",
    "failed_marked",
    "failed_unmarked",
    "pending_marked",
    "pending_unmarked",
)
PEER_ENDS = (None, "clean", "error")
MAX_ORDER_EVENTS = 2 + MAX_WORLD_EVENTS + 3 * MAX_RPCS
MAX_SEARCH_NODES = 1 << 20
FAMILIES = ("free_full", "free_coarse", "restricted_full", "restricted_coarse")
SCORE_FREE = ("lower_seq", "upper_seq", "exact_ordinal", "peer_open_after_return")
TRIAL_ID = "00000000-0000-0000-0000-000000000001"
RUN_ID = "00000000-0000-4000-8000-000000000002"
TICKET = "sb-0000000000000001"

assert MAX_WORLD_EVENTS <= MAX_REVISION and MAX_RPCS >= 1
assert MAX_ORDER_EVENTS <= 16

Report = dict[str, Any]
Plan = tuple[str, ...]
Scores = tuple[int, ...]
Bounds = tuple[int, int]


class Event(NamedTuple):
    kind: str
    index: int


Order = tuple[Event, ...]
Poset = dict[Event, tuple[Event, ...]]
RETURN = Event("R", 0)
PEER_END = Event("P", 0)


class Sample(NamedTuple):
    before_target: str
    before_peer: str
    after_target: str | None
    after_peer: str | None
    ack: str


class Mark(NamedTuple):
    sample: int
    after_seq: int


class Observation(NamedTuple):
    world_events: int
    native_peer: str
    samples: tuple[Sample, ...]
    marks: tuple[Mark, ...]


class Truth(NamedTuple):
    prefix: int
    peer_after: bool


class Weight(NamedTuple):
    free: int
    restricted: int


Cells = dict[Truth, Weight]
Coarse = tuple[int, tuple[tuple[str, str | None, str], ...], tuple[Mark, ...]]
Family = list[tuple[set[int], Bounds]]
Mutation = Callable[[Report, Report], object]
Scanned = tuple[Observation, Truth, int]


class Group(NamedTuple):
    observation: Observation
    cells: Cells


class Population(NamedTuple):
    groups: dict[Observation, Cells]
    size: Counter[str]
    plans: Counter[str]


def require(condition: bool, message: str, subject: object = "") -> None:
    if not condition:
        raise AssertionError(f"{message}: {subject}")


def ack_of(outcome: str) -> str:
    assert outcome in OUTCOMES
    return outcome.partition("_")[0]


def is_marked(outcome: str) -> bool:
    assert outcome in OUTCOMES
    return not outcome.endswith("_unmarked")


def mark_id(index: int) -> str:
    assert 0 <= index < MAX_RPCS + 8
    return format(index + 1, "032x")


def rpc_plans() -> Iterator[Plan]:
    yield ()
    for outcome in OUTCOMES:
        yield (outcome,)
    for outcome in OUTCOMES:
        yield ("ok", outcome)


def poset(world_events: int, peer_end: str | None, plan: Plan) -> Poset:
    assert 0 <= world_events <= MAX_WORLD_EVENTS and len(plan) <= MAX_RPCS
    preds: Poset = {RETURN: ()}
    if peer_end is not None:
        preds[PEER_END] = ()
    for index in range(world_events):
        preds[Event("W", index)] = (Event("W", index - 1),) if index else ()
    for index, outcome in enumerate(plan):
        assert index == len(plan) - 1 or outcome == "ok"
        send = Event("B", index)
        preds[send] = (Event("A", index - 1),) if index else ()
        if is_marked(outcome):
            preds[Event("M", index)] = (send,)
        if outcome == "ok":
            preds[Event("A", index)] = (Event("M", index),)
        elif ack_of(outcome) == "failed":
            preds[Event("A", index)] = (send,)
    assert len(preds) <= MAX_ORDER_EVENTS
    assert all(set(before) <= set(preds) for before in preds.values())
    return preds


def linear_extensions(preds: Poset) -> Iterator[Order]:
    events = sorted(preds)
    position = {event: index for index, event in enumerate(events)}
    need = [sum(1 << position[before] for before in preds[event]) for event in events]
    full = (1 << len(events)) - 1
    stack: list[tuple[int, Order]] = [(0, ())]
    for _ in range(MAX_SEARCH_NODES):
        if not stack:
            return
        require(len(stack) <= MAX_ORDER_EVENTS**2, "search stack exceeded its bound")
        placed, order = stack.pop()
        if placed == full:
            yield order
            continue
        for index in reversed(range(len(events))):
            bit = 1 << index
            if not placed & bit and need[index] & placed == need[index]:
                stack.append((placed | bit, order + (events[index],)))
    raise AssertionError("linear extension search exceeded its node bound")


def lifecycle(at: int, return_at: int, peer_at: int, peer: str) -> tuple[str, str]:
    return (
        "clean" if at > return_at else "running",
        peer if at > peer_at else "running",
    )


def scan(order: Order, plan: Plan, peer_end: str | None) -> Scanned:
    at = {event: position for position, event in enumerate(order)}
    assert len(at) == len(order)
    return_at = at[RETURN]
    peer_at = len(order) if peer_end is None else at[PEER_END]
    peer = peer_end or "running"
    worlds = [position for position, event in enumerate(order) if event.kind == "W"]
    samples: list[Sample] = []
    marks: list[Mark] = []
    for index, outcome in enumerate(plan):
        ack_at = at.get(Event("A", index))
        mark_at = at.get(Event("M", index))
        ack = ack_of(outcome)
        assert (ack_at is None) == (ack == "pending")
        assert (mark_at is None) != is_marked(outcome)
        send_at = at[Event("B", index)]
        before_target, before_peer = lifecycle(send_at, return_at, peer_at, peer)
        after_target, after_peer = (
            (None, None)
            if ack_at is None
            else lifecycle(ack_at, return_at, peer_at, peer)
        )
        sample = Sample(before_target, before_peer, after_target, after_peer, ack)
        samples.append(sample)
        if mark_at is not None:
            marks.append(Mark(index, sum(world < mark_at for world in worlds)))
    observation = Observation(len(worlds), peer, tuple(samples), tuple(marks))
    prefix = sum(world < return_at for world in worlds)
    for mark in observation.marks:
        sample = observation.samples[mark.sample]
        if sample.before_target == "clean":
            require(mark.after_seq >= prefix, "post-return send marked below truth")
        elif sample.ack == "ok" and sample.after_target == "running":
            require(mark.after_seq <= prefix, "open receipt marked above truth")
    restricted = 1
    for world in worlds:
        restricted *= (world < return_at) + (world < peer_at)
    return observation, Truth(prefix, peer_at > return_at), restricted


def observer_rule(observation: Observation) -> bool:
    samples = observation.samples
    return not (
        len(samples) == 2
        and samples[0].ack == "ok"
        and samples[0].before_target == "clean"
    )


def enumerate_orders() -> Population:
    groups: dict[Observation, Cells] = {}
    size: Counter[str] = Counter()
    plans: Counter[str] = Counter()
    for world_events in range(MAX_WORLD_EVENTS + 1):
        for peer_end in PEER_ENDS:
            for plan in rpc_plans():
                size["configurations"] += 1
                for order in linear_extensions(poset(world_events, peer_end, plan)):
                    observation, truth, restricted = scan(order, plan, peer_end)
                    rule = observer_rule(observation)
                    size["observer_rule" if rule else "relaxed_only"] += 1
                    size["free_orders"] += 1
                    size["restricted_actor_orders"] += restricted
                    size["max_order_events"] = max(size["max_order_events"], len(order))
                    plans["+".join(plan) or "none"] += 1
                    cells = groups.setdefault(observation, {})
                    held = cells.get(truth, Weight(0, 0))
                    cells[truth] = Weight(held.free + 1, held.restricted + restricted)
    return Population(groups, size, plans)


def snapshot(target: str | None, peer: str | None) -> dict[str, str | None] | None:
    return None if target is None else {"target": target, "peer": peer}


def sample_document(sample: Sample) -> Report:
    return {
        "before": snapshot(sample.before_target, sample.before_peer),
        "after": snapshot(sample.after_target, sample.after_peer),
        "ack": sample.ack,
    }


def documents(observation: Observation) -> tuple[Report, Report]:
    host: Report = {
        "schema": HOST_SCHEMA,
        "trial_id": TRIAL_ID,
        "ticket": TICKET,
        "target": "planner",
        "peer": "dispatcher",
        "closed": all(sample.ack != "pending" for sample in observation.samples),
        "samples": [
            {"id": mark_id(index), **sample_document(sample)}
            for index, sample in enumerate(observation.samples)
        ],
    }
    marks = [
        {
            "id": mark_id(mark.sample),
            "after_seq": mark.after_seq,
            "revision": mark.after_seq,
        }
        for mark in observation.marks
    ]
    world: Report = {
        "schema": WORLD_SCHEMA,
        "run": RUN_ID,
        "ticket": TICKET,
        "marks": marks,
    }
    return host, world


def bound(
    host: Report, world: Report, observation: Observation, scores: Scores
) -> Report:
    return analyze(
        host,
        world,
        native_target="clean",
        native_peer=observation.native_peer,
        scores=list(scores),
        revisions=list(range(observation.world_events + 1)),
    )


def prefixes(cells: Cells, restricted: bool) -> set[int]:
    return {
        truth.prefix
        for truth, weight in cells.items()
        if weight.restricted or not restricted
    }


def coarse_key(observation: Observation) -> Coarse:
    samples = tuple(
        (sample.before_target, sample.after_target, sample.ack)
        for sample in observation.samples
    )
    return (observation.world_events, samples, observation.marks)


def describe(group: Group, result: Report, scores: Scores | None) -> Report:
    shown: tuple[str, ...] = ("lower_seq", "upper_seq", "exact_ordinal", "flags")
    if scores is not None:
        shown += ("min_grade", "max_grade", "grade_identified")
    reported = {name: result[name] for name in shown}
    if scores is None:
        reported["flags"] = [
            flag for flag in result["flags"] if flag != "no_score_identification"
        ]
    observation = group.observation
    return {
        "world_events": observation.world_events,
        "native_peer": observation.native_peer,
        "samples": [sample_document(sample) for sample in observation.samples],
        "marks": [mark._asdict() for mark in observation.marks],
        "scores": None if scores is None else list(scores),
        "true_prefixes_free": sorted(prefixes(group.cells, False)),
        "true_prefixes_restricted": sorted(prefixes(group.cells, True)),
        "reported": reported,
    }


class Ledger:
    def __init__(self) -> None:
        self.stats: Counter[str] = Counter()
        self.bounds: dict[Observation, Bounds] = {}
        self.witnesses: dict[str, tuple[tuple[int, int, str, Scores], Report]] = {}

    def offer(
        self, name: str, group: Group, result: Report, scores: Scores | None
    ) -> None:
        seen = group.observation
        rank = (-len(seen.marks), -len(seen.samples), json.dumps(seen), scores or ())
        held = self.witnesses.get(name)
        if held is None or rank < held[0]:
            self.witnesses[name] = (rank, describe(group, result, scores))


def check_scored(ledger: Ledger, group: Group, scores: Scores, result: Report) -> None:
    observation, cells = group
    lower, upper = result["lower_seq"], result["upper_seq"]
    low, high = result["min_grade"], result["max_grade"]
    subject = (observation, scores)
    bounded = result["schema"] == BOUND_SCHEMA and result["status"] == "bounded"
    cut = observation.world_events
    require(bounded, "clean return not bounded", subject)
    require(result["cut_length"] == cut, "cut length misreported", subject)
    require(result["final_grade"] == scores[cut], "final grade misreported", subject)
    for truth, weight in cells.items():
        grade = scores[truth.prefix]
        exact = lower == upper == truth.prefix
        identified = low == high == grade
        require(weight.free > 0, "empty truth cell", subject)
        require(lower <= truth.prefix <= upper, "ordinal bound misses truth", subject)
        require(low <= grade <= high, "grade bound misses truth", subject)
        require(exact or not result["exact_ordinal"], "exact ordinal wrong", subject)
        require(identified or not result["grade_identified"], "grade wrong", subject)
        peer_ok = truth.peer_after or not result["peer_open_after_return"]
        require(peer_ok, "peer open after a return it never outlived", subject)
    ledger.stats["checked_truth_cells"] += len(cells)
    reported = set(range(low, high + 1))
    restricted = {scores[prefix] for prefix in prefixes(cells, True)}
    free = {scores[prefix] for prefix in prefixes(cells, False)}
    ledger.stats["free_grade_sharp"] += reported == free
    ledger.stats["restricted_scored"] += bool(restricted)
    restricted_sharp = bool(restricted) and reported == restricted
    ledger.stats["restricted_grade_sharp"] += restricted_sharp
    if len(restricted) > 1:
        ledger.offer("shared_observation_distinct_grades", group, result, scores)


def check_evidence(ledger: Ledger, group: Group, result: Report) -> None:
    observation, cells = group
    lower, upper = result["lower_seq"], result["upper_seq"]
    receipts: set[int] = {0}
    posts: set[int] = set()
    acked_posts: set[int] = set()
    for mark in observation.marks:
        sample = observation.samples[mark.sample]
        if sample.ack == "ok" and sample.after_target == "running":
            receipts.add(mark.after_seq)
        if sample.before_target == "clean":
            posts.add(mark.after_seq)
            if sample.ack == "ok":
                acked_posts.add(mark.after_seq)
    cut = observation.world_events
    require(lower in receipts, "lower bound has no open receipt", observation)
    require(upper in posts | {cut}, "upper bound has no post-return mark", observation)
    require(upper <= min(posts, default=cut), "post-return mark unused", observation)
    free_low = min(prefixes(cells, False))
    restricted = prefixes(cells, True)
    for mark in observation.marks:
        sample, seq = observation.samples[mark.sample], mark.after_seq
        if sample.ack == "ok":
            continue
        if sample.before_target == "running":
            trapped = free_low < seq and lower >= seq
            require(not trapped, "lost ack became a lower bound", observation)
            if restricted and min(restricted) < seq:
                ledger.offer("lost_ack_lower_trap", group, result, None)
        elif upper == seq and all(post > seq for post in acked_posts):
            ledger.offer("post_return_lost_ack_upper", group, result, None)


def examine(ledger: Ledger, group: Group) -> None:
    observation = group.observation
    host, world = documents(observation)
    first: Report | None = None
    for scores in itertools.product((0, 1), repeat=observation.world_events + 1):
        try:
            result = bound(host, world, observation, scores)
        except SeatCutInvalid as error:
            raise AssertionError(
                f"generated observation rejected: {error}: {observation}"
            ) from error
        ledger.stats["analyzer_calls"] += 1
        check_scored(ledger, group, scores, result)
        first = result if first is None else first
        for name in SCORE_FREE:
            require(
                result[name] == first[name], f"{name} depends on scores", observation
            )
    assert first is not None
    check_evidence(ledger, group, first)
    ledger.bounds[observation] = (first["lower_seq"], first["upper_seq"])


def sharpness(family: Iterable[tuple[set[int], Bounds]]) -> Report:
    names = ("observations", "sharp", "lower_slack", "upper_slack", "interior_gaps")
    tally = dict.fromkeys(names + ("max_slack",), 0)
    for admissible, (lower, upper) in family:
        least, most = min(admissible), max(admissible)
        require(lower <= least and most <= upper, "bounds exclude a true prefix")
        tally["observations"] += 1
        tally["sharp"] += admissible == set(range(lower, upper + 1))
        tally["lower_slack"] += least > lower
        tally["upper_slack"] += most < upper
        tally["interior_gaps"] += admissible != set(range(least, most + 1))
        tally["max_slack"] = max(tally["max_slack"], least - lower + upper - most)
    return {**tally, "every_observation_sharp": tally["sharp"] == tally["observations"]}


def sharpness_report(groups: dict[Observation, Cells], ledger: Ledger) -> Report:
    families: dict[str, Family] = {name: [] for name in FAMILIES}
    merged: dict[Coarse, tuple[Bounds, set[int], set[int]]] = {}
    for observation, cells in groups.items():
        bounds = ledger.bounds[observation]
        free, restricted = prefixes(cells, False), prefixes(cells, True)
        families["free_full"].append((free, bounds))
        if restricted:
            families["restricted_full"].append((restricted, bounds))
        held = merged.setdefault(coarse_key(observation), (bounds, set(), set()))
        require(held[0] == bounds, "bounds depend on peer detail", observation)
        held[1].update(free)
        held[2].update(restricted)
    for bounds, free, restricted in merged.values():
        families["free_coarse"].append((free, bounds))
        if restricted:
            families["restricted_coarse"].append((restricted, bounds))
    stats = ledger.stats
    return {
        "ordinal": {name: sharpness(families[name]) for name in FAMILIES},
        "grade_full_observations": {
            "free_scored": stats["analyzer_calls"],
            "free_sharp": stats["free_grade_sharp"],
            "restricted_scored": stats["restricted_scored"],
            "restricted_sharp": stats["restricted_grade_sharp"],
        },
        "scope": "coarse keeps target clean/open per snapshot, acks and marks; "
        "claims cover only the generated families",
    }


MALFORMED: tuple[tuple[str, Mutation], ...] = (
    ("duplicate_mark", lambda h, w: w["marks"].append(dict(w["marks"][0]))),
    ("out_of_order_marks", lambda h, w: w["marks"].reverse()),
    ("revision_mismatch", lambda h, w: w["marks"][0].update(revision=0)),
    ("ordinal_beyond_cut", lambda h, w: w["marks"][1].update(after_seq=3)),
    (
        "non_monotone_ordinals",
        lambda h, w: (
            w["marks"][0].update(after_seq=2, revision=2),
            w["marks"][1].update(after_seq=1, revision=1),
        ),
    ),
    ("unknown_mark", lambda h, w: w["marks"][0].update(id=mark_id(7))),
    ("acked_without_mark", lambda h, w: w["marks"].pop(1)),
    ("ticket_mismatch", lambda h, w: w.update(ticket="sb-00000000000000ff")),
    ("world_run_not_uuid4", lambda h, w: w.update(run=TRIAL_ID)),
    ("duplicate_sample_id", lambda h, w: h["samples"][1].update(id=mark_id(0))),
    (
        "pending_not_last",
        lambda h, w: h["samples"][0].update(ack="pending", after=None),
    ),
    (
        "lifecycle_revert",
        lambda h, w: h["samples"][1]["after"].update(target="running"),
    ),
)


def malformed_cases() -> Report:
    samples = (
        Sample("running", "running", "running", "running", "ok"),
        Sample("clean", "running", "clean", "clean", "ok"),
    )
    observation = Observation(2, "clean", samples, (Mark(0, 1), Mark(1, 2)))
    scores = (1, 0, 1)
    host, world = documents(observation)
    base = bound(host, world, observation, scores)
    require(base["lower_seq"] == 1 and base["upper_seq"] == 2, "malformed base misread")
    reasons: dict[str, str] = {}
    for name, mutate in MALFORMED:
        bad_host, bad_world = copy.deepcopy(host), copy.deepcopy(world)
        mutate(bad_host, bad_world)
        try:
            bound(bad_host, bad_world, observation, scores)
        except SeatCutInvalid as error:
            reasons[name] = str(error)
            continue
        raise AssertionError(f"malformed case {name} was accepted")
    return {
        "base_accepted": True,
        "cases": len(MALFORMED),
        "rejected": len(reasons),
        "reasons": reasons,
    }


LIMITS = [
    "Bounds are small: at most 2 world events, 2 RPCs, binary scores. Larger schedules are not enumerated.",
    "No clocks, intervals, max_samples, journal write failures, cancellation, or error/incomplete target returns are modeled.",
    "World marks and snapshots are atomic events; any real non-atomic snapshot or mark outside the world lock is outside this model.",
    "Sharpness is computed only for these generated families. Restricted-family slack follows from attribution rules the analyzer cannot see and is not a defect claim.",
    "The simulator imports bracket.analyze and only tests it as a black box; malformed coverage is the listed deterministic mutations of one valid observation.",
]


def size_report(population: Population, ledger: Ledger) -> Report:
    groups, size, plans = population
    calls = ledger.stats["analyzer_calls"]
    return {
        "world_events": [0, MAX_WORLD_EVENTS],
        "rpcs": [0, MAX_RPCS],
        "final_rpc_outcomes": list(OUTCOMES),
        "peer_ends": ["absent" if end is None else end for end in PEER_ENDS],
        "configurations": size["configurations"],
        "max_order_events": size["max_order_events"],
        "free_orders": size["free_orders"],
        "restricted_actor_orders": size["restricted_actor_orders"],
        "observer_rule_free_orders": size["observer_rule"],
        "relaxed_only_free_orders": size["relaxed_only"],
        "free_orders_by_rpc_plan": dict(sorted(plans.items())),
        "distinct_observations": len(groups),
        "distinct_coarse_observations": len({coarse_key(key) for key in groups}),
        "free_only_observations": sum(not prefixes(c, True) for c in groups.values()),
        "score_paths": sum(2 ** (n + 1) for n in range(MAX_WORLD_EVENTS + 1)),
        "scored_observations": calls,
        "analyzer_calls": calls,
        "checked_truth_cells": ledger.stats["checked_truth_cells"],
    }


def qualify() -> Report:
    population = enumerate_orders()
    ledger = Ledger()
    for observation, cells in population.groups.items():
        examine(ledger, Group(observation, cells))
    return {
        "schema": SCHEMA,
        "size": size_report(population, ledger),
        "sharpness": sharpness_report(population.groups, ledger),
        "witnesses": {name: held[1] for name, held in sorted(ledger.witnesses.items())},
        "malformed": malformed_cases(),
        "limits": LIMITS,
    }


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        sys.stderr.write(
            "usage: python3 -BS -m examples.seat_checkpoints.qualify_order\n"
        )
        return 2
    sys.stdout.write(json.dumps(qualify(), indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
