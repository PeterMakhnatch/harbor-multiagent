import json
from collections import Counter
from collections.abc import Callable
from itertools import combinations_with_replacement, pairwise
from typing import Any

from .work_meter import (
    GUARDS,
    KINDS,
    MAX_STEPS,
    MAX_TOKENS,
    PLACES,
    US_PER_MS,
    measure,
)
from .work_trace import Receipt, Step

SCHEMA = "harbor.post-return-work-meter-qualify/v1"
UNIT_US = 500_000
BASE_US = 3_000_000
SPAN = 5
MAX_SCHEDULE_STEPS = 2
RESIDUES = ((0, 0), (3, 1))
KEYS = ("steps", *KINDS)
BUCKETS = (*PLACES, "unplaced", "native", "bounds")
EARLY = -(1 << 20)
REQUIRED = (
    "sharp_checked",
    "equality_cases",
    "crossing_cases",
    "split_step_witnesses",
    "stamp_after_work_witnesses",
    "unplaced_tail_witnesses",
    "guard_widened",
)

Call = tuple[int, int, int, int]
Case = tuple[int, int, tuple[int, ...], tuple[int, int]]
World = tuple[int, int]


def _us(unit: int) -> int:
    return BASE_US + unit * UNIT_US


def _units(guard_ms: int) -> int:
    assert guard_ms * US_PER_MS % UNIT_US == 0
    return guard_ms * US_PER_MS // UNIT_US


def _tokens(index: int) -> tuple[int, int]:
    return 2 + 2 * index, 1 + index


def _receipt(case: Case) -> Receipt:
    exit_unit, initial, ends, residue = case
    tokens = [_tokens(index) for index in range(len(ends))]
    steps = tuple(
        Step(_us(end), prompt, completion)
        for end, (prompt, completion) in zip(ends, tokens, strict=True)
    )
    native = (
        sum(prompt for prompt, _ in tokens) + residue[0],
        sum(completion for _, completion in tokens) + residue[1],
    )
    return Receipt(_us(exit_unit), _us(0), _us(SPAN), _us(initial), steps, native)


def _cases() -> list[Case]:
    cases: list[Case] = []
    for count in range(MAX_SCHEDULE_STEPS + 1):
        for stamps in combinations_with_replacement(range(SPAN + 1), count + 1):
            for exit_unit in range(1, SPAN):
                for residue in RESIDUES:
                    cases.append((exit_unit, stamps[0], stamps[1:], residue))
    return cases


def _positions(low: int, high: int, ret: int) -> list[tuple[int, bool]]:
    found: list[tuple[int, bool]] = []
    for moment in range(low, high + 1):
        if moment < ret:
            found.append((2 * moment, False))
        elif moment > ret:
            found.append((2 * moment, True))
        else:
            found += [(2 * moment - 1, False), (2 * moment + 1, True)]
    assert found
    return found


def _calls(case: Case) -> list[Call]:
    _, prior, ends, _ = case
    calls: list[Call] = []
    for index, end in enumerate(ends):
        prompt, completion = _tokens(index)
        calls.append((prior, end, prompt // 2, completion // 2))
        calls.append((prior, end, prompt - prompt // 2, completion - completion // 2))
        prior = end
    return calls


def _ordered(calls: list[Call], ret: int) -> set[World]:
    states = {(EARLY, 0, 0)}
    for low, high, prompt, completion in calls:
        options = _positions(low, high, ret)
        states = {
            (place, done_prompt + prompt * after, done_completion + completion * after)
            for last, done_prompt, done_completion in states
            for place, after in options
            if place >= last
        }
        assert states
    return {
        (done_prompt, done_completion) for _, done_prompt, done_completion in states
    }


def _oracle(case: Case, guard_units: int) -> set[World]:
    exit_unit, _, _, residue = case
    calls = _calls(case)
    worlds: set[World] = set()
    first = max(exit_unit - guard_units, -1)
    last = min(exit_unit + guard_units, SPAN + 1)
    for ret in range(first, last + 1):
        tail = {after for _, after in _positions(0, SPAN, ret)}
        for done_prompt, done_completion in _ordered(calls, ret):
            for prompt_after in tail:
                for completion_after in tail:
                    worlds.add(
                        (
                            done_prompt + residue[0] * prompt_after,
                            done_completion + residue[1] * completion_after,
                        )
                    )
    return worlds


def _shaped(result: dict[str, Any], steps: int) -> bool:
    if tuple(result) != KEYS or tuple(result["steps"]) != PLACES:
        return False
    if sum(result["steps"].values()) != steps:
        return False
    for kind in KINDS:
        cell = result[kind]
        if tuple(cell) != BUCKETS:
            return False
        if sum(cell[key] for key in (*PLACES, "unplaced")) != cell["native"]:
            return False
    return True


def _whole_sums(case: Case) -> set[int]:
    _, _, ends, residue = case
    sums = {0, residue[0]}
    for index in range(len(ends)):
        prompt, _ = _tokens(index)
        sums |= {value + prompt for value in sums}
    return sums


def _contained(result: dict[str, Any], worlds: set[World]) -> bool:
    for axis, kind in enumerate(KINDS):
        low, high = result[kind]["bounds"]
        if not all(low <= world[axis] <= high for world in worlds):
            return False
    return True


def _sharp(result: dict[str, Any], worlds: set[World]) -> bool:
    for axis, kind in enumerate(KINDS):
        values = [world[axis] for world in worlds]
        if result[kind]["bounds"] != [min(values), max(values)]:
            return False
    return True


def _equality(case: Case, guard_units: int) -> bool:
    exit_unit, prior, ends, _ = case
    for end in ends:
        if prior == exit_unit + guard_units or end == exit_unit - guard_units:
            return True
        prior = end
    return False


def _stamped(case: Case) -> int:
    exit_unit, _, ends, _ = case
    return sum(_tokens(index)[0] for index, end in enumerate(ends) if end > exit_unit)


def _tally_case(
    case: Case, guard_ms: int, result: dict[str, Any], tally: Counter[str]
) -> None:
    guard_units = _units(guard_ms)
    worlds = _oracle(case, guard_units)
    prompts = {world[0] for world in worlds}
    tally["measured"] += 1
    tally["failures"] += not _shaped(result, len(case[2]))
    tally["failures"] += not _contained(result, worlds)
    if guard_ms == 0 and case[3] == (0, 0):
        tally["sharp_checked"] += 1
        tally["failures"] += not _sharp(result, worlds)
    tally["equality_cases"] += _equality(case, guard_units)
    tally["crossing_cases"] += result["steps"]["crossing"] > 0
    tally["split_step_witnesses"] += not prompts <= _whole_sums(case)
    if guard_ms == 0:
        tally["stamp_after_work_witnesses"] += _stamped(case) > min(prompts)
    placed = result["prompt"]["crossing"] + result["prompt"]["after"]
    tally["unplaced_tail_witnesses"] += max(prompts) > placed


def _nested(results: list[dict[str, Any]]) -> bool:
    for narrow, wide in pairwise(results):
        if wide["steps"]["crossing"] < narrow["steps"]["crossing"]:
            return False
        for kind in KINDS:
            low, high = narrow[kind]["bounds"]
            wide_low, wide_high = wide[kind]["bounds"]
            if not wide_low <= low <= high <= wide_high:
                return False
    return True


def _enumerate(tally: Counter[str]) -> None:
    for case in _cases():
        results = [measure(_receipt(case), guard_ms) for guard_ms in GUARDS]
        for guard_ms, result in zip(GUARDS, results, strict=True):
            _tally_case(case, guard_ms, result, tally)
        tally["cases"] += 1
        tally["failures"] += not _nested(results)
        tally["guard_widened"] += results[0] != results[-1]


def _base(**change: Any) -> Receipt:
    fields: dict[str, Any] = {
        "exit_us": _us(3),
        "peer_start_us": _us(0),
        "peer_end_us": _us(SPAN),
        "initial_us": _us(0),
        "steps": (Step(_us(2), 2, 1), Step(_us(4), 4, 2)),
        "native": (6, 3),
    }
    fields.update(change)
    return Receipt(**fields)


def _refused(build: Callable[[], Any], guard_ms: Any) -> bool:
    try:
        measure(build(), guard_ms)
    except (ValueError, TypeError):
        return True
    return False


def _refusal_controls() -> list[tuple[str, Callable[[], Any], Any]]:
    tail = Step(_us(4), 4, 2)
    many = tuple(Step(_us(2), 0, 0) for _ in range(MAX_STEPS + 1))
    edge = Step(_us(SPAN) + 1, 4, 2)
    missing: Any = None
    return [
        ("null_native", lambda: _base(native=None), 0),
        ("null_native_part", lambda: _base(native=(None, 3)), 0),
        ("negative_residue", lambda: _base(native=(5, 3)), 0),
        ("native_arity", lambda: _base(native=(6, 3, 0)), 0),
        ("null_step_prompt", lambda: _base(steps=(Step(_us(2), missing, 1), tail)), 0),
        (
            "null_step_completion",
            lambda: _base(steps=(Step(_us(2), 2, missing), tail)),
            0,
        ),
        (
            "bool_tokens",
            lambda: _base(steps=(Step(_us(2), True, 1), tail), native=(5, 3)),
            0,
        ),
        (
            "negative_tokens",
            lambda: _base(steps=(Step(_us(2), -1, 1), tail), native=(3, 3)),
            0,
        ),
        ("token_cap", lambda: _base(native=(MAX_TOKENS + 1, 3)), 0),
        ("float_time", lambda: _base(exit_us=float(_us(3))), 0),
        ("null_time", lambda: _base(initial_us=None), 0),
        (
            "inverted_steps",
            lambda: _base(steps=(Step(_us(4), 2, 1), Step(_us(2), 4, 2))),
            0,
        ),
        ("initial_after_step", lambda: _base(initial_us=_us(3)), 0),
        ("before_peer", lambda: _base(initial_us=_us(0) - 1), 0),
        ("after_peer", lambda: _base(steps=(Step(_us(2), 2, 1), edge)), 0),
        ("exit_at_start", lambda: _base(exit_us=_us(0)), 0),
        ("exit_at_end", lambda: _base(exit_us=_us(SPAN)), 0),
        (
            "inverted_peer",
            lambda: _base(peer_start_us=_us(SPAN), peer_end_us=_us(0)),
            0,
        ),
        ("step_cap", lambda: _base(steps=many, native=(0, 0)), 0),
        ("raw_steps", lambda: _base(steps=((_us(2), 2, 1), tail)), 0),
        ("not_receipt", lambda: object(), 0),
        ("guard_between", _base, 500),
        ("guard_above", _base, 2000),
        ("guard_bool", _base, True),
        ("guard_float", _base, 1000.0),
        ("guard_missing", _base, None),
    ]


def _accepted() -> list[bool]:
    level = (Step(_us(3), 2, 1), Step(_us(3), 4, 2))
    builds: list[Callable[[], Any]] = [
        _base,
        lambda: _base(steps=(), native=(0, 0)),
        lambda: _base(steps=level),
    ]
    return [not _refused(build, guard_ms) for build in builds for guard_ms in GUARDS]


def _probe(case: Case, guard_ms: int) -> tuple[dict[str, Any], list[int]]:
    result = measure(_receipt(case), guard_ms)
    worlds = _oracle(case, _units(guard_ms))
    if not _contained(result, worlds):
        return result, [-1]
    return result, sorted({world[0] for world in worlds})


def _known() -> dict[str, bool]:
    stamp_case: Case = (3, 0, (2, 5), (0, 0))
    stamp, stamp_prompts = _probe(stamp_case, 0)
    tail, tail_prompts = _probe((4, 0, (1, 2), (3, 1)), 0)
    tie, tie_prompts = _probe((2, 0, (2, 4), (0, 0)), 0)
    split, split_prompts = _probe((2, 0, (4,), (0, 0)), 0)
    widen = [
        measure(_receipt((2, 0, (1, 3, 5), (0, 0))), guard_ms) for guard_ms in GUARDS
    ]
    return {
        "stamp_after_work": stamp["steps"] == {"before": 1, "crossing": 1, "after": 0}
        and stamp["prompt"]["bounds"] == [0, 4] == [stamp_prompts[0], stamp_prompts[-1]]
        and _stamped(stamp_case) == 4,
        "unplaced_tail": tail["prompt"]["unplaced"] == 3
        and tail["prompt"]["crossing"] + tail["prompt"]["after"] == 0
        and tail["prompt"]["bounds"] == [0, 3] == [tail_prompts[0], tail_prompts[-1]],
        "equal_stamps_cross": tie["steps"]["crossing"] == 2
        and tie["prompt"]["bounds"] == [0, 6] == [tie_prompts[0], tie_prompts[-1]],
        "split_step": split["steps"]["crossing"] == 1
        and split_prompts == [0, 1, 2]
        and split["prompt"]["bounds"] == [0, 2],
        "guard_widens": widen[0]["steps"] == {"before": 1, "crossing": 1, "after": 1}
        and widen[1]["steps"]["crossing"] == 3
        and widen[0]["prompt"]["bounds"] == [6, 10]
        and widen[1]["prompt"]["bounds"] == [0, 12]
        and _nested(widen),
    }


def main() -> int:
    tally: Counter[str] = Counter()
    _enumerate(tally)
    controls = _refusal_controls()
    unrefused = [
        name for name, build, guard_ms in controls if not _refused(build, guard_ms)
    ]
    accepted = _accepted()
    known = _known()
    ok = (
        tally["failures"] == 0
        and not unrefused
        and all(accepted)
        and all(known.values())
        and all(tally[key] > 0 for key in REQUIRED)
    )
    report = {
        "schema": SCHEMA,
        "guards_ms": list(GUARDS),
        **{key: tally[key] for key in ("cases", "measured", "failures", *REQUIRED)},
        "refusal_controls": len(controls),
        "unrefused": unrefused,
        "accepted_controls": [sum(accepted), len(accepted)],
        "known_witnesses": known,
        "ok": ok,
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
