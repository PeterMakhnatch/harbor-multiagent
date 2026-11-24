"""Pure Joint Reopen design support: frozen layouts, balanced private seeds, safe audit.

`audit` re-validates the registration with `study.validate_registration` and raises
`ValueError` on any invalid shape. It certifies `balanced_paired` only when the task
identity equals `TASK` and, for every registered team and every public incumbent the
team uses, each channel holds all 60 ordered hidden triples x 4 veto masks with one
equal positive multiplicity, and both channels hold the same seed/mask multiset.
Mixed incumbents form separate strata; a team without cells, or with one channel,
is not certified. Live phase barriers and elapsed time stay in both channels.
"""

import hashlib
import itertools
from collections import Counter
from typing import Any

from .study import CHANNELS, FIXTURES, MAX_SEED, validate_registration

SCHEMA = "harbor.joint-reopen.design-audit/v1"
OPTIONS = ("A", "B", "C", "D", "E", "F")
PERMUTATION_PREFIX = "joint-reopen/v1"
TASK: dict[str, str] = {
    "name": "harbor/joint-reopen",
    "digest": "sha256:ec969b116cab9718db6f8a1e35337529c0e5c60e1cc86160746ce8497b8fdf44",
    "checksum": "d39605c284edbf87a396d75d273bb096f0b17ceca2ef09f9fb57a671acc2dfd8",
}
HIDDEN = 3
TRIPLES = 60
STRATUM_KEYS = TRIPLES * len(FIXTURES)
SEED_SPACE = MAX_SEED + 1
MAX_CANDIDATES = 65_536

assert SEED_SPACE == 1 << 31
assert FIXTURES == ("none", "east", "west", "both")
assert CHANNELS == ("deliver", "withhold")

Triple = tuple[str, str, str]


def _check_seed(value: object, label: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SEED:
        raise ValueError(f"{label} must be an integer in 0..{MAX_SEED}")
    return value


def labels(seed: int) -> tuple[str, ...]:
    _check_seed(seed, "seed")
    keyed = sorted(
        (
            hashlib.sha256(
                f"{PERMUTATION_PREFIX}:{seed}:{slot}".encode("ascii")
            ).digest(),
            slot,
        )
        for slot in OPTIONS
    )
    order = tuple(slot for _, slot in keyed)
    assert sorted(order) == list(OPTIONS)
    return order


def layout(seed: int) -> tuple[str, str, str, str]:
    order = labels(seed)
    return order[0], order[1], order[2], order[3]


def triples(incumbent: str) -> tuple[Triple, ...]:
    if type(incumbent) is not str or incumbent not in OPTIONS:
        raise ValueError(f"incumbent must be one of {list(OPTIONS)}")
    others = [slot for slot in OPTIONS if slot != incumbent]
    found = tuple(itertools.permutations(others, HIDDEN))
    assert len(found) == TRIPLES and len(set(found)) == TRIPLES
    return found


def balanced_seeds(incumbent: str, start: int, stride: int) -> list[int]:
    wanted = triples(incumbent)
    _check_seed(start, "start")
    if type(stride) is not int or not 0 < stride <= MAX_SEED or stride % 2 != 1:
        raise ValueError(f"stride must be an odd integer in 1..{MAX_SEED}")
    found: dict[Triple, int] = {}
    for index in range(MAX_CANDIDATES):
        seed = (start + index * stride) % SEED_SPACE
        head, shared, east_only, west_only = layout(seed)
        if head == incumbent:
            found.setdefault((shared, east_only, west_only), seed)
            if len(found) == TRIPLES:
                break
    if len(found) != TRIPLES:
        raise ValueError(
            f"{MAX_CANDIDATES} candidates cover {len(found)}/{TRIPLES} triples"
        )
    seeds = [found[triple] for triple in wanted]
    assert len(set(seeds)) == TRIPLES
    assert all(layout(s) == (incumbent, *t) for s, t in zip(seeds, wanted, strict=True))
    return seeds


def _multiplicity(units: list[tuple[int, str]], incumbent: str) -> int:
    counts = Counter((layout(seed)[1:], mask) for seed, mask in units)
    expected = set(itertools.product(triples(incumbent), FIXTURES))
    assert len(expected) == STRATUM_KEYS
    if set(counts) != expected or len(set(counts.values())) != 1:
        return 0
    multiplicity = next(iter(counts.values()))
    assert multiplicity > 0 and multiplicity * STRATUM_KEYS == len(units)
    return multiplicity


def _blocks(
    cells: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, list[tuple[int, str]]]]:
    blocks: dict[tuple[str, str], dict[str, list[tuple[int, str]]]] = {}
    for cell in cells:
        seed = _check_seed(cell["seed"], "cell seed")
        arms = blocks.setdefault((cell["team"], layout(seed)[0]), {})
        arms.setdefault(cell["channel"], []).append((seed, cell["fixture"]))
    assert sum(
        len(units) for arms in blocks.values() for units in arms.values()
    ) == len(cells)
    return blocks


def audit(registration: dict[str, Any]) -> dict[str, Any]:
    checked = validate_registration(registration)
    registered = dict(checked["task"]) == TASK
    teams: dict[str, Any] = checked["teams"]
    cells: list[dict[str, Any]] = checked["cells"]
    blocks = _blocks(cells)
    certified = {team for team, _ in blocks}
    assert certified <= set(teams)
    strata = unbalanced = paired = 0
    repeats: set[int] = set()
    for (team, incumbent), arms in blocks.items():
        assert set(arms) <= set(CHANNELS) and arms
        multiplicities = [_multiplicity(arms[channel], incumbent) for channel in arms]
        strata += len(multiplicities)
        unbalanced += multiplicities.count(0)
        repeats.update(m for m in multiplicities if m > 0)
        matched = (
            set(arms) == set(CHANNELS)
            and all(m > 0 for m in multiplicities)
            and Counter(arms[CHANNELS[0]]) == Counter(arms[CHANNELS[1]])
        )
        paired += matched
        if not matched:
            certified.discard(team)
    balanced_paired = registered and certified == set(teams)
    assert not balanced_paired or (unbalanced == 0 and paired == len(blocks))
    return {
        "schema": SCHEMA,
        "task_registered": registered,
        "rows": len(cells),
        "teams": len(teams),
        "teams_certified": len(certified) if registered else 0,
        "incumbents": sorted({incumbent for _, incumbent in blocks}),
        "strata": strata,
        "unbalanced_strata": unbalanced,
        "blocks": len(blocks),
        "paired_strata": paired,
        "unpaired_strata": len(blocks) - paired,
        "repeats": sorted(repeats),
        "balanced_paired": balanced_paired,
        "live_timing_removed": False,
    }
