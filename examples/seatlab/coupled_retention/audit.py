from fractions import Fraction

from .records import Cell
from .strategy import digest


def _indices(labels: tuple[str, ...]) -> dict[str, int]:
    if type(labels) is not tuple or not 1 <= len(labels) <= 16:
        raise ValueError("invalid action labels")
    for label in labels:
        if (
            type(label) is not str
            or not 1 <= len(label) <= 128
            or not label.isprintable()
            or label != label.strip()
        ):
            raise ValueError("invalid action label")
    indices = {label: index for index, label in enumerate(labels)}
    if len(indices) != len(labels):
        raise ValueError("duplicate action label")
    return indices


def _policy_indices(rules: dict[str, str], indices: dict[str, int]) -> dict[str, int]:
    if type(rules) is not dict or not 1 <= len(rules) <= 256:
        raise ValueError("invalid rule count")
    result: dict[str, int] = {}
    for key, label in rules.items():
        if type(key) is not str:
            raise ValueError("invalid observation digest")
        if type(label) is not str or label not in indices:
            raise ValueError("unknown rule action")
        result[key] = indices[label]
    return result


def _validate_cell(cell: Cell, height: int, width: int) -> None:
    if type(cell) is not Cell:
        raise ValueError("expected a Cell")
    if type(cell.weight) is not int or not 1 <= cell.weight <= 1_000_000:
        raise ValueError("invalid cell weight")
    for packet in (cell.east, cell.west):
        if type(packet) is not bytes or len(packet) > 8192:
            raise ValueError("invalid observation bytes")
    if type(cell.scores) is not tuple or len(cell.scores) != height:
        raise ValueError("invalid score row count")
    for row in cell.scores:
        if type(row) is not tuple or len(row) != width:
            raise ValueError("invalid score column count")
        if any(type(value) is not int or value not in (0, 1) for value in row):
            raise ValueError("scores must be binary integers")


def score(
    rows: list[Cell],
    east_labels: tuple[str, ...],
    west_labels: tuple[str, ...],
    east_rules: dict[str, str],
    west_rules: dict[str, str],
) -> dict[str, str | int]:
    if not 1 <= len(rows) <= 256:
        raise ValueError("invalid cell count")
    east_policy = _policy_indices(east_rules, _indices(east_labels))
    west_policy = _policy_indices(west_rules, _indices(west_labels))
    east_seen: set[str] = set()
    west_seen: set[str] = set()
    total = earned = 0
    for cell in rows:
        _validate_cell(cell, len(east_labels), len(west_labels))
        east_key, west_key = digest(cell.east), digest(cell.west)
        if east_key not in east_policy or west_key not in west_policy:
            raise ValueError("missing observation rule")
        east_seen.add(east_key)
        west_seen.add(west_key)
        earned += (
            cell.weight * cell.scores[east_policy[east_key]][west_policy[west_key]]
        )
        total += cell.weight
    if east_seen != east_policy.keys() or west_seen != west_policy.keys():
        raise ValueError("extra observation rule")
    return {
        "rows": len(rows),
        "total_weight": total,
        "earned": earned,
        "reward": str(Fraction(earned, total)),
    }
