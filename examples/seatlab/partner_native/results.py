from collections import Counter
from collections.abc import Iterable
from fractions import Fraction
from itertools import chain
from pathlib import Path
from typing import Any

from ...joint_reopen import collect
from ...native_evidence import same

SCHEMA = "seatlab.partner-native-matrix/v1"
CASES_PER_PAIR = 6
SCOPE = (
    "Registered native Joint Reopen cases only; original and mixed ordered "
    "partnerships have equal weight within each group. Bounds allow every "
    "unusable case either binary reward. No population inference."
)

Record = dict[str, Any]


def _groups(
    pairs: list[list[str]], registration: Record, rows: list[Record]
) -> dict[str, list[Record]]:
    groups: dict[str, list[Record]] = {pair: [] for pair in chain.from_iterable(pairs)}
    cells = registration["cells"]
    expected = {cell["ticket"]: cell for cell in cells}
    wanted = CASES_PER_PAIR * len(groups)
    if len(expected) != wanted or len(cells) != wanted:
        raise ValueError("registration must contain six unique tickets per pair")
    if set(registration["teams"]) != set(groups):
        raise ValueError("registration teams do not match the ordered pairs")
    if type(rows) is not list or len(rows) != wanted:
        raise ValueError("collector must return one row per registered ticket")

    for row in rows:
        if type(row) is not dict or type(row.get("ticket")) is not str:
            raise ValueError("collector row has no valid ticket")
        cell = expected.pop(row["ticket"], None)
        if cell is None or not all(
            same(row.get(field), cell[field]) for field in collect.CELL_FIELDS
        ):
            raise ValueError("collector row repeats or differs from a registered cell")
        if cell["team"] not in groups:
            raise ValueError("registration cell names an unknown pair")
        status = row.get("status")
        if status not in collect.STATUSES:
            raise ValueError("collector row has an unknown status")
        if status == "usable":
            observation = row.get("observation")
            reward = observation.get("reward") if type(observation) is dict else None
            if type(reward) is not int or reward not in (0, 1):
                raise ValueError(
                    "usable collector row has no binary observation reward"
                )
        groups[cell["team"]].append(row)

    if expected:
        raise ValueError("collector omitted a registered cell")
    for group in groups.values():
        cases = {(row["seed"], row["fixture"], row["channel"]) for row in group}
        if len(group) != CASES_PER_PAIR or len(cases) != CASES_PER_PAIR:
            raise ValueError("registration must contain six distinct cases per pair")
    return groups


def _totals(rows: Iterable[Record]) -> Record:
    statuses = Counter({status: 0 for status in collect.STATUSES})
    wins = 0
    for row in rows:
        status = row["status"]
        statuses[status] += 1
        if status == "usable":
            wins += row["observation"]["reward"]

    registered = sum(statuses.values())
    usable = statuses["usable"]
    lower = Fraction(wins, registered)
    upper = Fraction(wins + registered - usable, registered)
    return {
        "registered": registered,
        "usable": usable,
        "wins": wins,
        "statuses": dict(statuses),
        "reward": str(lower) if usable == registered else None,
        "bounds": [str(lower), str(upper)],
    }


def from_rows(manifest: Record, registration: Record, rows: list[Record]) -> Record:
    names = list(manifest["names"])
    n = len(names)
    pairs = [[f"p{i:02}-{j:02}" for j in range(n)] for i in range(n)]
    groups = _groups(pairs, registration, rows)
    matrix = [[_totals(groups[pair]) for pair in row] for row in pairs]
    home = _totals(chain.from_iterable(groups[pairs[i][i]] for i in range(n)))
    mixed = _totals(
        chain.from_iterable(
            groups[pairs[i][j]] for i in range(n) for j in range(n) if i != j
        )
    )
    complete = home["reward"] is not None and mixed["reward"] is not None
    home_lo, home_hi = (Fraction(value) for value in home["bounds"])
    mixed_lo, mixed_hi = (Fraction(value) for value in mixed["bounds"])
    gap_lo = str(home_lo - mixed_hi)
    gap_hi = str(home_hi - mixed_lo)
    return {
        "schema": SCHEMA,
        "names": names,
        "mode": manifest["mode"],
        "registration_sha256": manifest["registration_sha256"],
        "matrix": matrix,
        "home": home,
        "mixed": mixed,
        "home_minus_mixed": {
            "reward": gap_lo if complete else None,
            "bounds": [gap_lo, gap_hi],
        },
        "complete": complete,
        "scope": SCOPE,
    }


def summarize(manifest: Record, registration: Record, roots: list[Path]) -> Record:
    collected = collect.collect_registered(registration, roots)
    return from_rows(manifest, registration, collected["cells"])
