from collections import Counter
from collections.abc import Iterable, Mapping
from collections.abc import Set as AbstractSet
from itertools import combinations
from typing import Any

_DAYS = range(5)
_STARTS_PER_DAY = 7
_SLOT_COUNT = len(_DAYS) * _STARTS_PER_DAY


def _id(value: object, limit: int) -> int:
    if type(value) is not int or not 0 <= value < limit:
        raise ValueError("invalid calendar ID")
    return value


def _ids(values: Iterable[int], limit: int, *, ascending: bool) -> set[int]:
    if isinstance(values, (str, bytes, bytearray, Mapping)):
        raise ValueError("expected a collection of calendar IDs, not text or a mapping")  # noqa: TRY004
    try:
        items = iter(values)
    except TypeError as exc:
        raise ValueError("expected an iterable of calendar IDs") from exc
    ordered = ascending and not isinstance(values, AbstractSet)
    result: set[int] = set()
    previous = -1
    for raw in items:
        item = _id(raw, limit)
        if item in result or (ordered and item <= previous):
            raise ValueError("invalid order or duplicate calendar ID")
        result.add(item)
        previous = item
    return result


def measure(
    own: Iterable[int],
    initial: Iterable[int],
    revised: Iterable[int],
    opened: Iterable[int],
    choice: int,
    target: int,
) -> dict[str, int]:
    own_slots = _ids(own, _SLOT_COUNT, ascending=True)
    _id(choice, _SLOT_COUNT)
    _id(target, _SLOT_COUNT)
    if target not in own_slots:
        raise ValueError("target must be legal for the receiver")
    days = _ids(opened, len(_DAYS), ascending=False)
    if len(days) != 2:
        raise ValueError("expected two distinct weekdays")
    actual_first, actual_second = sorted(days)

    provisional = own_slots & _ids(initial, _SLOT_COUNT, ascending=True)
    counts = Counter(slot // _STARTS_PER_DAY for slot in provisional)
    ranked_days = sorted(_DAYS, key=lambda day: (-counts[day], day))[:2]
    ranked_first, ranked_second = sorted(ranked_days)

    updated = own_slots & _ids(revised, _SLOT_COUNT, ascending=True)
    candidates = {
        pair: {slot for slot in provisional if slot // _STARTS_PER_DAY not in pair}
        | {slot for slot in updated if slot // _STARTS_PER_DAY in pair}
        for pair in combinations(_DAYS, 2)
    }
    actual = candidates[actual_first, actual_second]
    ranked = candidates[ranked_first, ranked_second]
    target_only = {target}
    return {
        "visible_pairs": sum(target in slots for slots in candidates.values()),
        "unique_correct_pairs": sum(
            slots == target_only for slots in candidates.values()
        ),
        "actual_visible": int(target in actual),
        "actual_unique_correct": int(actual == target_only),
        "choice_correct": int(choice == target),
        "unique_taken": int(actual == target_only and choice == target),
        "ranked_unique_correct": int(ranked == target_only),
        "ranked_earliest_correct": int(min(ranked, default=None) == target),
        "ranked_empty": int(not ranked),
    }


def totals(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    summed = (
        "actual_visible",
        "actual_unique_correct",
        "choice_correct",
        "unique_taken",
        "ranked_unique_correct",
        "ranked_earliest_correct",
        "ranked_empty",
    )
    counts = {"teams": 0, "ever_visible": 0, "ever_unique_correct": 0}
    counts.update(dict.fromkeys(summed, 0))
    for row in rows:
        counts["teams"] += 1
        counts["ever_visible"] += row["visible_pairs"] > 0
        counts["ever_unique_correct"] += row["unique_correct_pairs"] > 0
        for key in summed:
            counts[key] += row[key]
    return counts
