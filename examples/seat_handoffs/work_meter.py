from typing import Any, cast

from .schedule_exit_clock import _micros
from .work_trace import MAX_STEPS, MAX_TOKENS, Receipt, Step

GUARDS = (0, 1000, 5000)
US_PER_MS = 1000
MAX_US = _micros("9999-12-31T23:59:59.999999Z")
KINDS = ("prompt", "completion")
PLACES = ("before", "crossing", "after")
REASONS = frozenset(
    {
        "unsupported_guard",
        "not_a_receipt",
        "bad_time",
        "bad_tokens",
        "bad_steps",
        "bad_native",
        "step_cap",
        "missing_step_metric",
        "missing_native_metric",
        "exit_not_inside_peer",
        "endpoints_outside_peer",
        "endpoints_inverted",
        "negative_residue",
    }
)

Span = tuple[int, int, int, int]


class MeterRefusal(ValueError):
    def __init__(self, reason: str) -> None:
        assert reason in REASONS
        super().__init__(reason)
        self.reason = reason


def _margin(guard_ms: object) -> int:
    if type(guard_ms) is not int or guard_ms not in GUARDS:
        raise MeterRefusal("unsupported_guard")
    return US_PER_MS * guard_ms


def _time(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_US:
        raise MeterRefusal("bad_time")
    return value


def _count(value: object, missing: str) -> int:
    if value is None:
        raise MeterRefusal(missing)
    if type(value) is not int or not 0 <= value <= MAX_TOKENS:
        raise MeterRefusal("bad_tokens")
    return value


def _native(value: object) -> tuple[int, int]:
    if value is None:
        raise MeterRefusal("missing_native_metric")
    if not isinstance(value, tuple) or len(value) != len(KINDS):
        raise MeterRefusal("bad_native")
    prompt = _count(value[0], "missing_native_metric")
    completion = _count(value[1], "missing_native_metric")
    return prompt, completion


def _steps(value: object) -> tuple[Step, ...]:
    if not isinstance(value, tuple | list):
        raise MeterRefusal("bad_steps")
    if len(value) > MAX_STEPS:
        raise MeterRefusal("step_cap")
    if not all(isinstance(step, Step) for step in value):
        raise MeterRefusal("bad_steps")
    return cast(tuple[Step, ...], tuple(value))


def _spans(receipt: Receipt) -> tuple[int, list[Span]]:
    start = _time(receipt.peer_start_us)
    finish = _time(receipt.peer_end_us)
    exit_us = _time(receipt.exit_us)
    if not start < exit_us < finish:
        raise MeterRefusal("exit_not_inside_peer")
    prior = _time(receipt.initial_us)
    if prior < start:
        raise MeterRefusal("endpoints_outside_peer")
    spans: list[Span] = []
    for step in _steps(receipt.steps):
        stamp = _time(step.end_us)
        if stamp < prior:
            raise MeterRefusal("endpoints_inverted")
        prompt = _count(step.prompt, "missing_step_metric")
        completion = _count(step.completion, "missing_step_metric")
        spans.append((prior, stamp, prompt, completion))
        prior = stamp
    if prior > finish:
        raise MeterRefusal("endpoints_outside_peer")
    assert len(spans) <= MAX_STEPS
    return exit_us, spans


def _place(prior: int, stamp: int, exit_us: int, margin: int) -> str:
    assert prior <= stamp and margin >= 0
    if prior > exit_us + margin:
        return "after"
    if stamp < exit_us - margin:
        return "before"
    return "crossing"


def _kind(placed: dict[str, int], total: int) -> dict[str, Any]:
    residue = total - sum(placed.values())
    if residue < 0:
        raise MeterRefusal("negative_residue")
    low = placed["after"]
    high = total - placed["before"]
    assert 0 <= low <= high <= total
    assert high - low == placed["crossing"] + residue
    return {**placed, "unplaced": residue, "native": total, "bounds": [low, high]}


def measure(receipt: Receipt, guard_ms: int) -> dict[str, Any]:
    margin = _margin(guard_ms)
    if not isinstance(receipt, Receipt):
        raise MeterRefusal("not_a_receipt")
    exit_us, spans = _spans(receipt)
    native = _native(receipt.native)
    steps = dict.fromkeys(PLACES, 0)
    sums = {kind: dict.fromkeys(PLACES, 0) for kind in KINDS}
    for prior, stamp, prompt, completion in spans:
        place = _place(prior, stamp, exit_us, margin)
        steps[place] += 1
        sums["prompt"][place] += prompt
        sums["completion"][place] += completion
    assert sum(steps.values()) == len(spans)
    result: dict[str, Any] = {"steps": steps}
    for kind, total in zip(KINDS, native, strict=True):
        result[kind] = _kind(sums[kind], total)
    return result
