import json
from collections.abc import Iterator
from hashlib import sha256
from typing import Any

SEEDS = tuple(range(6401, 6407))
REPETITIONS = (0, 1)
EPISODES = 6
_ARMS = ("named", "count")
_ALIASES = tuple(f"Q{number}" for number in range(1, EPISODES + 1))
_BAYS = ("A", "B", "C", "D")
_SELECT_RULES = (
    "Choose 2 cases whose revision 2 reports would help you most to dispatch all 6 "
    "shipments. The goal is to choose the available bay with the highest utility "
    "in each case. Higher numbers mean greater utility. The change summary "
    "compares availability sets in the two submitted inventory reports. "
    'Reply with only a JSON object, for example {"open":["Q1","Q2"]}. '
    "Use exactly 2 distinct case aliases from the data."
)
_DECIDE_RULES = (
    "Choose the available bay with the highest utility in each case. Higher numbers "
    "mean greater utility. Use the latest inventory report shown for each case. "
    "Reply with only a JSON object of this form: "
    '{"choices":{"Q1":"A","Q2":"B","Q3":"C","Q4":"D","Q5":"A","Q6":"B"}}. '
    "Use each case alias exactly once and choose a bay A, B, C, or D for each case."
)


def grid() -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "cell_id": f"attn-s{seed}-r{rep}-{arm}",
            "seed": seed,
            "repetition": rep,
            "condition": "reordered",
            "pairing": "MM",
            "arm": arm,
        }
        for index, seed in enumerate(SEEDS)
        for rep in REPETITIONS
        for arm in (_ARMS if (index + rep) % 2 == 0 else _ARMS[::-1])
    )


def aliases(cases: list[dict[str, Any]], seed: int, rep: int) -> dict[str, int]:
    ids: list[int] = []
    for case in cases:
        native_id = case.get("id")
        if type(native_id) is not int:
            raise ValueError("Expected integer native case IDs")
        ids.append(native_id)
    if len(ids) != EPISODES or set(ids) != set(range(EPISODES)):
        raise ValueError("Expected all 6 registered native case IDs")
    ordered = sorted(
        ids,
        key=lambda native_id: sha256(
            f"seatlab-attn-v1:{seed}:{rep}:{native_id}".encode("utf-8")
        ).digest(),
    )
    return dict(zip(_ALIASES, ordered, strict=True))


def _dispatch_cases(
    cases: list[dict[str, Any]], seed: int, rep: int
) -> Iterator[tuple[str, dict[str, int], list[str], list[str]]]:
    mapping = aliases(cases, seed, rep)
    by_id = {case["id"]: case for case in cases}
    for alias, native_id in mapping.items():
        case = by_id[native_id]
        recent, previous = case["messages"]
        if (recent["revision"], previous["revision"]) != (2, 1):
            raise ValueError("Expected reordered inventory reports")
        yield alias, case["utilities"], previous["available"], recent["available"]


def changed_reports(cases: list[dict[str, Any]], seed: int, rep: int) -> frozenset[str]:
    return frozenset(
        alias
        for alias, _, previous, recent in _dispatch_cases(cases, seed, rep)
        if set(previous) != set(recent)
    )


def _prompt(rules: str, data: dict[str, Any]) -> str:
    return (
        rules
        + "\n"
        + json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)
    )


def select_prompt(cases: list[dict[str, Any]], seed: int, rep: int, arm: str) -> str:
    if arm not in _ARMS:
        raise ValueError("Unknown attention arm")
    rows = []
    changed = []
    for alias, utilities, previous, recent in _dispatch_cases(cases, seed, rep):
        rows.append({"alias": alias, "utilities": utilities, "revision_1": previous})
        if set(previous) != set(recent):
            changed.append(alias)
    data: dict[str, Any] = {"cases": rows, "changed_count": len(changed)}
    if arm == "named":
        data["changed_aliases"] = changed
    return _prompt(_SELECT_RULES, data)


def _open_pair(value: object) -> tuple[str, str]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        first, second = value
        if (
            isinstance(first, str)
            and isinstance(second, str)
            and first in _ALIASES
            and second in _ALIASES
            and first != second
        ):
            return first, second
    raise ValueError("Expected 2 distinct registered aliases")


def decide_prompt(
    cases: list[dict[str, Any]], seed: int, rep: int, opened: tuple[str, str]
) -> str:
    opened = _open_pair(opened)
    rows = []
    for alias, utilities, previous, recent in _dispatch_cases(cases, seed, rep):
        row = {"alias": alias, "utilities": utilities, "revision_1": previous}
        if alias in opened:
            row["revision_2"] = recent
        rows.append(row)
    return _prompt(_DECIDE_RULES, {"cases": rows})


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate JSON key")
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-JSON constant: {value}")


def _reply(text: str, field: str) -> Any:
    if not isinstance(text, str):
        raise ValueError("Expected a text reply")
    try:
        value = json.loads(
            text, object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
    except (ValueError, RecursionError):
        raise ValueError("Invalid JSON reply") from None
    if not isinstance(value, dict) or value.keys() != {field}:
        raise ValueError(f"Expected one JSON field: {field}")
    return value[field]


def select(text: str) -> tuple[str, str]:
    return _open_pair(_reply(text, "open"))


def decide(
    text: str, cases: list[dict[str, Any]], seed: int, rep: int
) -> dict[str, Any]:
    choices = _reply(text, "choices")
    mapping = aliases(cases, seed, rep)
    if not isinstance(choices, dict) or choices.keys() != mapping.keys():
        raise ValueError("Expected one choice for every registered case alias")
    if any(bay not in _BAYS for bay in choices.values()):
        raise ValueError("Expected a bay A, B, C, or D")
    rows = [
        {"id": native_id, "bay": choices[alias]} for alias, native_id in mapping.items()
    ]
    return {"rows": sorted(rows, key=lambda row: row["id"])}
