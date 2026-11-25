from itertools import product
from typing import Any, NamedTuple

from ...native_study import require
from ..exhibit_wire import encode_request, render_turn
from ..physical_dispatch import policies
from ..physical_dispatch.certificate import Session, cli_view
from .world import MAPS, delivery

Map = tuple[str, str]
Record = dict[str, Any]
_WORLDS: tuple[Map, ...] = tuple(product(MAPS, MAPS))
_RULES = {
    "fixed_one": "file the current agenda record whose crate key sorts first",
    "earliest_bay": "file the current agenda record whose current bay sorts first",
}


class _Walk(NamedTuple):
    nomination: Record
    depot_view: Record
    filed_key: str
    packet: bytes
    first: bytes


def _choose(view: Record, rule: str) -> Record:
    policies._guard(view, "depot", "depot")
    agenda = view["agenda"]
    require(len(agenda) == 2 and agenda[0]["epoch"] == agenda[1]["epoch"], "agenda")
    epoch = agenda[0]["epoch"]
    records: list[Record] = []
    for row in agenda:
        card = policies._current(view["cards"], row["key"], epoch)
        if card is None:
            raise ValueError("missing current agenda record")
        records.append(card)
    require(len({record["id"] for record in records}) == 2, "duplicate agenda record")
    require(len({record["value"] for record in records}) == 2, "agenda bays collide")
    field = "key" if rule == "fixed_one" else "value"
    chosen = min(records, key=lambda record: record[field])
    return {"op": "file", "ids": [chosen["id"]]}


def _walk(case: Record, rule: str, instruction: str) -> _Walk:
    session = Session(case, "linked")
    start = cli_view(session.view("loader"))
    first = encode_request(render_turn(instruction, start, None))
    nomination = policies.nominate(start)
    session.apply("loader", nomination)
    session.apply("registrar", policies.registrar(cli_view(session.view("registrar"))))
    depot_view = cli_view(session.view("depot"))
    filing = _choose(depot_view, rule)
    session.apply("depot", filing)
    final = cli_view(session.view("loader"))
    require(final["phase"] == "dispatch", "loader is not at dispatch")
    require(len(final["registrar_file"]) == 2, "registrar filing count differs")
    filed = final["depot_file"]
    require([card["id"] for card in filed] == filing["ids"], "depot filing differs")
    delivered = cli_view(delivery(final, "neither"))
    require(
        all(
            set(card["value"]) == {"?"}
            for field in ("registrar_file", "depot_file")
            for card in delivered[field]
        ),
        "a filed value is visible",
    )
    packet = encode_request(render_turn(instruction, delivered, nomination))
    return _Walk(nomination, depot_view, filed[0]["key"], packet, first)


def _measure(rule: str, rows: dict[Map, _Walk], control: set[bytes]) -> Record:
    agenda = rows[("original", "original")].depot_view["agenda"]
    crates = {entry["key"] for entry in agenda}
    for key in _WORLDS:
        require(rows[key][1:] == rows[("original", key[1])][1:], "registry map visible")
    chosen = {row.filed_key for row in rows.values()}
    packets = {key: row.packet for key, row in rows.items()}
    classes = len(set(packets.values()))
    by_bay = all(
        (packets[left] == packets[right]) == (left[1] == right[1])
        for left, right in product(_WORLDS, repeat=2)
    )
    if rule == "fixed_one":
        require(chosen == {min(crates)} and classes == 1, "fixed-one control varies")
    else:
        require(chosen == set(crates) and classes == 2 and by_bay, "not bay-map split")
    return {
        "rule": _RULES[rule],
        "filed_depot_records": 1,
        "chosen_records": len(chosen),
        "byte_classes": classes,
        "split_by": "none" if classes == 1 else "bay_map",
        "request_bytes": sorted({len(packet) for packet in packets.values()}),
        "classes_shared_with_fixed_one": len(set(packets.values()) & control),
    }


def selection_check(selected: dict[Map, Record], instruction: str) -> Record:
    nominations: list[Record] = []
    arms: dict[str, Record] = {}
    control: set[bytes] = set()
    initial: set[bytes] = set()
    for rule in _RULES:
        rows = {key: _walk(case, rule, instruction) for key, case in selected.items()}
        nominations.extend(row.nomination for row in rows.values())
        initial.update(row.first for row in rows.values())
        if rule == "fixed_one":
            control = {row.packet for row in rows.values()}
        arms[rule] = _measure(rule, rows, control)
    require(all(row == nominations[0] for row in nominations), "nomination differs")
    require(len(initial) == 1, "initial loader packets differ")
    sizes = {size for arm in arms.values() for size in arm["request_bytes"]}
    return {
        "schema": "seatlab.joint-handoffs.filing-choice/v1",
        "measure": "aggregate_metadata_visibility",
        "channel": "neither",
        "worlds": len(_WORLDS),
        "model_calls": 0,
        "initial_loader_inputs_identical": True,
        "initial_request_bytes": len(initial.pop()),
        "focal_nomination_identical": True,
        "registrar_filed_records": 2,
        "request_bytes_equal_across_arms": len(sizes) == 1,
        "arms": arms,
        "scope": "Value-masked receiver bytes still expose which depot record was filed when the sender's choice depends on private bay values; this is metadata visibility only, not a dispatch reward or an attribution of intent.",
    }
