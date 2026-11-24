"""Project three frozen shipping control releases onto one decision row shape.

`collect()` reads only exact-byte pinned public samples under
`examples/ship_handover/samples` with no-follow reads, joins each release to its own
registrations by exact ticket and keeps releases separate. A registered ticket
without a released row is `missing`, never zero. No release recorded a citation.
A decision or pre-decision return that a release does not record stays
`unrecorded`; nothing is inferred from a seat policy, a prediction or another
release. Errors carry fixed literals only.
"""

import hashlib
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ...native_evidence import Incomplete, Invalid, read_bytes, strict_object
from ...ship_handover.study import spec_for, validate_registration

SAMPLES = Path(__file__).resolve().parents[2] / "ship_handover" / "samples"
MAX_SAMPLE_BYTES = 1 << 20
TWO_WORLD_DIR = "two-worlds"
DISCONNECTED_DIR = "disconnected-workers"
TWO_WORLD_REGISTRATIONS = (
    "stock-recovery.json",
    "stock-sender-only.json",
    "inflight-recovery.json",
    "inflight-sender-only.json",
)
PINS: dict[tuple[str, ...], str] = {
    ("control-outcomes.json",): (
        "42a99a7ca1885ed2d1ece29999067209d4beb32d6afbb671db1a6dc35d2513a9"
    ),
    ("control-registration.json",): (
        "3dae88b51aed78882fe11c9b5fab902a418a6b3bfcc95c29bc10f381269b25bc"
    ),
    (TWO_WORLD_DIR, "native-qualification.json"): (
        "c554d0d711292decd8c0662bf65f585c13fadd01f4a2b0c5949d5186d422ce84"
    ),
    (TWO_WORLD_DIR, "stock-recovery.json"): (
        "3f510417686868e18113c6c7e2eaeea4368aaf568d02961b241061420aaf18d9"
    ),
    (TWO_WORLD_DIR, "stock-sender-only.json"): (
        "588cb320bd3b8a91a11d0bf9e4f64ddc258805cae32dc7ab1b44794ab0004b5b"
    ),
    (TWO_WORLD_DIR, "inflight-recovery.json"): (
        "e89399570c6b31dc23276d8249836ae1dcbe894386be62c7f0f88147ec7deb88"
    ),
    (TWO_WORLD_DIR, "inflight-sender-only.json"): (
        "59042a4a73dcc58396d9149ec25660be565d882c4fd2dc7d5667d445ee2fb677"
    ),
    (DISCONNECTED_DIR, "qualification.json"): (
        "0c2b282838effb6932d6f4d283220ab8bb0ef406740ebd19b45f0df609f19b82"
    ),
    (DISCONNECTED_DIR, "stock.json"): (
        "355d45f8ac8acf588486b097a9b5a10b6599880aa96ae58a0de807fece97af76"
    ),
    (DISCONNECTED_DIR, "inflight.json"): (
        "27b0ca34ab6cbbe6210468ceb98b8267c7ac0534ce6599364b1bfbab6e256c36"
    ),
}
ROW_FIELDS = (
    "world",
    "axis_state",
    "decision",
    "inbox",
    "citation",
    "reward",
    "status",
)
UNRECORDED = "unrecorded"
ACCEPTED = "accepted"
ABSENT = "absent"
AXES = {"stock": "prior", "inflight": "fate"}
SERVED = {"none": "empty", "original": "original", "draft": "draft"}
RECEIPTS = {"none": UNRECORDED, "original": "original", "draft": "draft"}

Row = dict[str, object]
Cell = tuple[str, str]
Observed = tuple[object, object, object, str, str, object]


class ArchiveError(ValueError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ArchiveError(message)


def _read(parts: tuple[str, ...]) -> dict[str, Any]:
    _need(parts in PINS, "sample is not pinned")
    try:
        data = read_bytes(SAMPLES, parts, MAX_SAMPLE_BYTES)
    except (Incomplete, Invalid):
        raise ArchiveError("sample is not a readable plain file") from None
    _need(
        hashlib.sha256(data).hexdigest() == PINS[parts],
        "sample digest differs from its pin",
    )
    try:
        return strict_object(data, "sample")
    except Invalid:
        raise ArchiveError("sample is not strict JSON") from None


def _symbol(table: Mapping[str, str], value: object) -> str:
    _need(isinstance(value, str) and value in table, "returned symbol is unknown")
    assert isinstance(value, str)
    return table[value]


def registered(*registrations: object) -> dict[str, Cell]:
    cells: dict[str, Cell] = {}
    for value in registrations:
        try:
            registration = validate_registration(value)
        except (ValueError, OSError):
            raise ArchiveError("registration is invalid") from None
        spec = spec_for(registration)
        for cell in registration["cells"]:
            _need(cell["ticket"] not in cells, "registrations repeat a ticket")
            cells[cell["ticket"]] = (spec.world, cell[spec.axis])
    return cells


def _row(cell: Cell, decision: str, inbox: str, reward: object) -> Row:
    _need(
        reward is None or (type(reward) is int and reward in (0, 1)),
        "reward is not a bit",
    )
    return {
        "world": cell[0],
        "axis_state": cell[1],
        "decision": decision,
        "inbox": inbox,
        "citation": UNRECORDED,
        "reward": reward,
        "status": "missing" if reward is None else "observed",
    }


def _join(cells: Mapping[str, Cell], observed: Iterable[Observed]) -> list[Row]:
    rows: dict[str, Row] = {}
    for ticket, world, state, decision, inbox, reward in observed:
        _need(
            isinstance(ticket, str) and ticket in cells, "row ticket is not registered"
        )
        assert isinstance(ticket, str)
        _need(ticket not in rows, "row repeats a ticket")
        _need(cells[ticket] == (world, state), "row differs from its registration")
        _need(reward is not None, "released row has no reward")
        rows[ticket] = _row(cells[ticket], decision, inbox, reward)
    return [
        rows[ticket] if ticket in rows else _row(cell, UNRECORDED, UNRECORDED, None)
        for ticket, cell in cells.items()
    ]


def _stock_cell(cell: dict[str, Any]) -> Observed:
    observation = cell["observation"]
    if observation["relief_action"] == ABSENT:
        inbox = ABSENT if "inbox" not in observation["timeline"] else UNRECORDED
        decision = ABSENT
    elif observation["inbox_before_resolve"] is True:
        decision, inbox = (
            ACCEPTED,
            _symbol(SERVED, observation["reference_before_resolve"]),
        )
    else:
        _need(observation["inbox_before_resolve"] is False, "inbox order is unknown")
        decision, inbox = ACCEPTED, ABSENT
    return cell["ticket"], "stock", cell["prior"], decision, inbox, cell["grade"]


def stock_rows(outcomes: dict[str, Any], registration: object) -> list[Row]:
    return _join(registered(registration), map(_stock_cell, outcomes["cells"]))


def _two_world(row: dict[str, Any]) -> Observed:
    served = row["served_before_resolve"]
    if served is None:
        decision, inbox = UNRECORDED, UNRECORDED
    else:
        decision, inbox = ACCEPTED, _symbol(SERVED, served)
    state = row.get(AXES.get(row["world"], ""))
    return row["ticket"], row["world"], state, decision, inbox, row["reward"]


def two_world_rows(
    qualification: dict[str, Any], registrations: list[object]
) -> list[Row]:
    return _join(registered(*registrations), map(_two_world, qualification["rows"]))


def _disconnected(row: dict[str, Any]) -> Observed:
    _need(isinstance(row["relief_action"], str), "relief action is unrecorded")
    receipt = _symbol(RECEIPTS, row["receipt_before_decision"])
    state = row.get(AXES.get(row["world"], ""))
    return row["ticket"], row["world"], state, ACCEPTED, receipt, row["grade"]


def disconnected_rows(
    qualification: dict[str, Any], stock: object, inflight: object
) -> list[Row]:
    cells = registered(stock, inflight)
    rows = _join(cells, map(_disconnected, qualification["native_rows"]))
    missing = [t for t, row in zip(cells, rows) if row["status"] == "missing"]
    unknown = qualification["native_firsts"]["no_known_grade"]
    _need(sorted(missing) == sorted(unknown), "missing tickets differ from the release")
    return rows


def collect() -> dict[str, list[Row]]:
    return {
        "stock-controls": stock_rows(
            _read(("control-outcomes.json",)), _read(("control-registration.json",))
        ),
        "two-worlds": two_world_rows(
            _read((TWO_WORLD_DIR, "native-qualification.json")),
            [_read((TWO_WORLD_DIR, name)) for name in TWO_WORLD_REGISTRATIONS],
        ),
        "disconnected": disconnected_rows(
            _read((DISCONNECTED_DIR, "qualification.json")),
            _read((DISCONNECTED_DIR, "stock.json")),
            _read((DISCONNECTED_DIR, "inflight.json")),
        ),
    }
