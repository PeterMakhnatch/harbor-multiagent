"""Project saved `ship-decision-receipt-cut.json` files into current decision rows.

`collect` reads 1 to 256 user-supplied cut files with the bounded, no-follow plain-file
reader, regrades each one with `decision_verify.evaluate`, and returns one row per file
in input order. It never writes. The canonical `run` UUID only detects a file supplied
twice; a copied cut or two cuts that share a run reject the whole archive. Earlier
ship-inflight and ship-handover schemas are refused, never downgraded. Reasons are
fixed literals and never carry paths, runs, references or parser errors.
"""

import os
import stat
from pathlib import Path
from typing import cast

from ...native_evidence import (
    Incomplete,
    Invalid,
    canonical_uuid,
    read_bytes,
    require,
    same,
)
from . import decision_verify as verify

WORLD = "inflight"
OBSERVED = "observed"
FIELDS = frozenset(
    ("world", "axis_state", "decision", "inbox", "citation", "reward", "status")
)
INBOXES = ("absent", "empty", *verify.CITABLE, "unrecorded")
CITATIONS = {
    "no_decision": "not_applicable",
    "decided_without_returned_reference": "not_applicable",
    "returned_not_cited": "not_cited",
    "cited_at_decision": "at_decision",
}
EARLIER = (verify.inflight.CUT_SCHEMA, verify.stock.CUT_SCHEMA)
EMPTY = "none"
MAX_FILES = 256
MAX_TOTAL_BYTES = 1 << 20
UNUSABLE_NAMES = ("", ".", "..")

MISSING = "a cut file is missing"
UNREADABLE = "a cut file is not a plain file within the cut bound"
NOT_A_PATH = "the archive is not a list of 1 to 256 cut file paths"
OVER_TOTAL = "the archive exceeds its total byte bound"
FOREIGN = "a cut file uses an earlier schema without citations"
NOT_A_CUT = "a cut file is not a valid ship-decision-receipt cut"
INCONSISTENT = "a cut file replay is inconsistent with its citation status"
COPIED = "a cut file is supplied more than once"
CONFLICT = "two cut files share one run with different contents"

assert set(CITATIONS) == set(verify.STATUSES)
assert EMPTY in verify.stock.CHOICES["inbox"] and EMPTY not in verify.CITABLE
assert len(set(INBOXES)) == len(INBOXES) == 5
assert MAX_FILES * verify.stock.MAX_CUT_BYTES <= MAX_TOTAL_BYTES


def _read(path: Path) -> bytes:
    require(path.name not in UNUSABLE_NAMES, UNREADABLE)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise Invalid(MISSING) from None
    except OSError:
        raise Invalid(UNREADABLE) from None
    require(stat.S_ISREG(info.st_mode), UNREADABLE)
    try:
        data = read_bytes(path.parent, (path.name,), verify.stock.MAX_CUT_BYTES)
    except Incomplete:
        raise Invalid(MISSING) from None
    except (Invalid, OSError):
        raise Invalid(UNREADABLE) from None
    assert len(data) <= verify.stock.MAX_CUT_BYTES
    return data


def _regrade(data: bytes) -> tuple[dict[str, object], dict[str, object]]:
    try:
        cut = verify.stock.parse_cut(data)
    except verify.CutError:
        raise Invalid(NOT_A_CUT) from None
    require(type(cut) is dict, NOT_A_CUT)
    require(cast(dict[str, object], cut).get("schema") not in EARLIER, FOREIGN)
    try:
        observation = verify.evaluate(cut)
    except verify.CutError:
        raise Invalid(NOT_A_CUT) from None
    assert observation["schema"] == verify.OBSERVATION_SCHEMA
    return cast(dict[str, object], cut), observation


def _inbox(seen: dict[str, verify.Event]) -> str:
    resolve, inbox = seen.get("resolve"), seen.get("inbox")
    if resolve is None:
        return "unrecorded"
    if inbox is None:
        return "absent"
    assert inbox[0] != resolve[0]
    if inbox[0] > resolve[0]:
        return "absent"
    return "empty" if inbox[1] == EMPTY else inbox[1]


def project(observation: dict[str, object]) -> dict[str, object]:
    seen = verify._replayed_events(observation)
    decided = "resolve" in seen
    require(decided == (observation["relief_action"] != "absent"), INCONSISTENT)
    returned = verify.returned_before_decision(seen)
    cited = cast(str | None, observation[verify.CITATION])
    status = observation["citation_status"]
    require(status == verify.citation_status(decided, returned, cited), INCONSISTENT)
    inbox = _inbox(seen)
    require(returned == (inbox if inbox in verify.CITABLE else None), INCONSISTENT)
    require((inbox == "unrecorded") == (status == "no_decision"), INCONSISTENT)
    citation = CITATIONS[cast(str, status)]
    require((citation == "at_decision") == (cited is not None), INCONSISTENT)
    reward, fate = observation["reward"], observation["fate"]
    require(type(reward) is int and reward in (0, 1), INCONSISTENT)
    require(fate in verify.inflight.FATES, INCONSISTENT)
    row: dict[str, object] = {
        "world": WORLD,
        "axis_state": fate,
        "decision": "accepted" if decided else "absent",
        "inbox": inbox,
        "citation": citation,
        "reward": reward,
        "status": OBSERVED,
    }
    assert row.keys() == FIELDS and inbox in INBOXES
    return row


def collect(paths: list[Path]) -> list[dict[str, object]]:
    require(type(paths) is list and 0 < len(paths) <= MAX_FILES, NOT_A_PATH)
    require(all(isinstance(path, Path) for path in paths), NOT_A_PATH)
    runs: dict[str, dict[str, object]] = {}
    rows: list[dict[str, object]] = []
    total = 0
    for path in paths:
        data = _read(path)
        total += len(data)
        require(total <= MAX_TOTAL_BYTES, OVER_TOTAL)
        cut, observation = _regrade(data)
        run = observation["run"]
        assert canonical_uuid(run) and run == cut["run"]
        earlier = runs.setdefault(cast(str, run), cut)
        if earlier is not cut:
            raise Invalid(COPIED if same(earlier, cut) else CONFLICT)
        rows.append(project(observation))
    assert len(rows) == len(runs) == len(paths)
    return rows
