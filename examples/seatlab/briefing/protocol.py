from collections.abc import Iterator
from typing import Any, cast

from examples.seatlab.attention.protocol import (
    EPISODES,
    _BAYS,
    _prompt,
    _reply,
    aliases,
    select,
)

SEEDS = tuple(range(6501, 6507))
REPETITIONS = (0, 1)
_ARMS = ("brief", "masked")
_BRIEF_RULES = (
    "For each case, choose a bay with the highest utility, ignoring availability. "
    "List the bay letters in Q1 to Q6 order. Reply with only a JSON object whose "
    'sole field "brief" is a string of exactly 6 letters A, B, C, or D, with no spaces.'
)
_PUSH_RULES = (
    "Choose 2 current reports (revision_2) to send so the dispatcher can improve "
    "its choices. For the other cases, the dispatcher uses revision_1. The "
    "dispatcher chooses the available bay with the highest utility. If present, "
    "the brief gives the preferred bay for each case in Q1 to Q6 order, ignoring "
    "availability. A null brief means no preferences were shared. Reply with only "
    'a JSON object whose sole field "open" is an array of exactly 2 distinct aliases.'
)


def grid() -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "cell_id": f"brf-s{seed}-r{rep}-{arm}",
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


def _ordered_cases(
    cases: list[dict[str, Any]], seed: int, rep: int
) -> Iterator[tuple[str, dict[str, Any]]]:
    if (
        not isinstance(cases, list)
        or len(cases) != EPISODES
        or any(not isinstance(case, dict) for case in cases)
    ):
        raise ValueError("Expected 6 native case rows")
    mapping = aliases(cases, seed, rep)
    by_id = {case["id"]: case for case in cases}
    for alias, native_id in mapping.items():
        yield alias, by_id[native_id]


def brief_prompt(dispatch_cases: list[dict[str, Any]], seed: int, rep: int) -> str:
    rows = []
    for alias, case in _ordered_cases(dispatch_cases, seed, rep):
        utilities = case.get("utilities")
        if (
            not isinstance(utilities, dict)
            or utilities.keys() != set(_BAYS)
            or any(type(rank) is not int for rank in utilities.values())
        ):
            raise ValueError("Expected one integer utility per bay")
        rows.append({"alias": alias, "utilities": utilities})
    return _prompt(_BRIEF_RULES, {"cases": rows})


def _checked_brief(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != EPISODES
        or any(bay not in _BAYS for bay in value)
    ):
        raise ValueError("Expected a 6-letter brief using only A, B, C, and D")
    return value


def parse_brief(text: str) -> str:
    return _checked_brief(_reply(text, "brief"))


def _report_sets(reports: object) -> dict[int, list[str]]:
    if not isinstance(reports, list) or len(reports) != 2:
        raise ValueError("Expected reports for revisions 1 and 2")
    by_revision: dict[int, list[str]] = {}
    for item in reports:
        if not isinstance(item, dict) or item.keys() != {"revision", "available"}:
            raise ValueError("Expected only revision and available in each report")
        report = cast(dict[str, Any], item)
        revision, bays = report["revision"], report["available"]
        if (
            type(revision) is not int
            or revision not in (1, 2)
            or revision in by_revision
        ):
            raise ValueError("Expected one report for each revision")
        if (
            not isinstance(bays, list)
            or len(bays) != 2
            or any(not isinstance(bay, str) or bay not in _BAYS for bay in bays)
            or bays[0] == bays[1]
        ):
            raise ValueError("Expected 2 distinct available bays")
        by_revision[revision] = sorted(bays)
    return by_revision


def push_prompt(
    accepted_inventory_rows: list[dict[str, Any]],
    seed: int,
    rep: int,
    brief: str | None,
) -> str:
    if brief is not None:
        _checked_brief(brief)
    rows = []
    for alias, case in _ordered_cases(accepted_inventory_rows, seed, rep):
        if case.keys() != {"id", "reports"}:
            raise ValueError("Expected only id and reports in each inventory row")
        reports = _report_sets(case["reports"])
        rows.append(
            {"alias": alias, "revision_1": reports[1], "revision_2": reports[2]}
        )
    return _prompt(_PUSH_RULES, {"cases": rows, "brief": brief})


def parse_push(text: str) -> tuple[str, str]:
    return select(text)
