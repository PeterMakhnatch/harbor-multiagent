import argparse
from pathlib import Path
from uuid import UUID

from ..visibility import calendar_packet, calendar_protocol
from . import audit, team_source, team_views
from . import protocol as p

_SOURCE = (
    Path(__file__).resolve().parents[1] / "results/calendar-private-views-live.json"
)
SOURCE_SHA256 = "54e034e9590fbedd1c0d420c63028514289855aad7d9deca50c14131abeb299e"
_BINDING = ("trial_uuid", "result_sha256", "source")


def _exact_rows(report: audit.Row) -> dict[str, audit.Row]:
    audit.require(
        (report["schema"], report["mode"]) == (audit.PRIVATE_VIEWS_SCHEMA, "live"),
        "peer_payload_source_schema",
    )
    rows, uuids, hashes = {}, set(), set()
    for value in calendar_packet._list(report["cells"]):
        row = calendar_packet._object(value)
        if row["arm"] != "exact":
            continue
        cell = team_views.Cell(row["world"], "exact")
        audit.require(
            row["run"] == cell.run
            and row["control"] == cell.control
            and row["attempted"] is True
            and row["complete"] is True
            and row["error"] is None
            and isinstance(row["trial_uuid"], str),
            "peer_payload_source_row",
        )
        trial_uuid = UUID(row["trial_uuid"])
        result_sha256 = calendar_packet._check_digest(row["result_sha256"])
        source = calendar_packet._object(
            row["source"], "task_sha256", "semantics_sha256"
        )
        for sha256 in source.values():
            calendar_packet._check_digest(sha256)
        audit.require(
            trial_uuid.version == 4
            and str(trial_uuid) == row["trial_uuid"]
            and cell.world not in rows
            and trial_uuid not in uuids
            and result_sha256 not in hashes,
            "peer_payload_source_identity",
        )
        rows[cell.world] = row
        uuids.add(trial_uuid)
        hashes.add(result_sha256)
    audit.require(rows.keys() == set(team_views.WORLDS), "peer_payload_source_worlds")
    return rows


def _load_receipt(
    receipt: Path, expected_sha256: str
) -> tuple[str, dict[str, audit.Row]]:
    expected_sha256 = calendar_packet._check_digest(expected_sha256)
    data = audit.raw(receipt.expanduser().absolute())
    sha256 = p.digest(data)
    audit.require(sha256 == expected_sha256, "peer_payload_source_digest")
    return sha256, _exact_rows(audit._object(data))


def originals() -> dict[str, audit.Row]:
    return _load_receipt(_SOURCE, SOURCE_SHA256)[1]


def project(
    table: calendar_packet.Table, initial: object, opened: object, *, masked: bool
) -> str:
    initial_by_day: list[list[int | str]] = [[] for _ in calendar_protocol._DAYS]
    for slot in p._starts(initial):
        initial_by_day[p._day(slot)].append("?" if masked else slot)
    reports = calendar_packet._list(
        calendar_packet._object(opened, "reports")["reports"]
    )
    audit.require(len(reports) == 2, "peer_payload_opened_count")
    items = [calendar_packet._object(report, "day", "starts") for report in reports]
    days = p._picked_days([item["day"] for item in items])
    opened_by_day = []
    for day, item in zip(days, items, strict=True):
        starts = p._starts(calendar_packet._list(item["starts"]))
        audit.require(
            all(p._day(slot) == day for slot in starts), "peer_payload_opened_day"
        )
        opened_by_day.append(
            {"day": day, "starts": ["?" if masked else slot for slot in starts]}
        )
    peer = {
        "initial": [
            {"day": day, "starts": starts} for day, starts in enumerate(initial_by_day)
        ],
        "opened": opened_by_day,
    }
    return (
        team_views._view(table, "exact")
        + f"Other calendar reports: {p.wire(peer)}\n"
        + 'A "?" represents one listed start with its ID withheld. Each opened report '
        "replaces the initial list for that weekday. An empty list means no start was "
        "listed for that weekday; it is not proof none exists. For every other weekday, "
        "the initial list is your only information about the other calendar. "
        'Select one joint start. Return only JSON: {"slot":n}. Replace n with one slot ID.'
    )


def _context(native: Path, world: str, row: audit.Row) -> tuple[str, str]:
    cell = team_views.Cell(world, "exact")
    trial = native / "cells" / cell.run / "trials" / cell.run
    data = audit.raw(trial / "result.json")
    audit.require(p.digest(data) == row["result_sha256"], "peer_payload_result_digest")
    result = audit._object(data)
    audit.require(result["id"] == row["trial_uuid"], "peer_payload_result_uuid")
    source, tables = team_source._checked(Path(result["task_id"]["path"]), cell)
    audit.require(
        source.native_sha256 == row["source"]["task_sha256"] == result["task_checksum"]
        and source.semantics_sha256 == row["source"]["semantics_sha256"],
        "peer_payload_task_binding",
    )
    receiver = team_views.SEATS.index(cell.receiver)
    table = tables[receiver]
    seat = result["seat_results"][receiver]
    state = seat["agent_result"]["metadata"]["calendar_notice"]
    audit.require(
        seat["seat"] == state["seat"] == cell.receiver
        and state["run"] == cell.run
        and state["calendar_sha256"] == p.digest(p.wire(table)),
        "peer_payload_seat_binding",
    )
    received = state["received"]
    audit.require(
        [msg["id"] for msg in received] == ["m1", "m3"], "peer_payload_deliveries"
    )
    notice = audit._object(received[0]["text"])
    opened = audit._object(received[1]["text"])
    return (
        project(table, notice["initial"], opened, masked=False),
        project(table, notice["initial"], opened, masked=True),
    )


def _bound_receipt(
    native: Path, spend_dir: Path, receipt: Path, expected_sha256: str
) -> tuple[str, dict[str, audit.Row]]:
    sha256, rows = _load_receipt(receipt, expected_sha256)
    audited = _exact_rows(audit.report(native, spend_dir, private_views=True))
    for world, row in rows.items():
        audit.require(
            all(audited[world][key] == row[key] for key in _BINDING),
            "peer_payload_audit_binding",
        )
    return sha256, rows


def contexts_for_receipt(
    native: Path, spend_dir: Path, *, receipt: Path, expected_sha256: str
) -> dict[str, tuple[str, str]]:
    native = native.expanduser().absolute()
    _, rows = _bound_receipt(native, spend_dir, receipt, expected_sha256)
    return {world: _context(native, world, rows[world]) for world in team_views.WORLDS}


def contexts(native: Path, spend_dir: Path) -> dict[str, tuple[str, str]]:
    return contexts_for_receipt(
        native, spend_dir, receipt=_SOURCE, expected_sha256=SOURCE_SHA256
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Plan peer payload inputs from a verified native cohort.",
        allow_abbrev=False,
    )
    parser.add_argument("native", type=Path)
    parser.add_argument("--spend-dir", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--expect-sha256", required=True)
    parser.add_argument("--world", choices=team_views.WORLDS)
    args = parser.parse_args(argv)
    worlds = team_views.WORLDS if args.world is None else (args.world,)
    cells = []
    try:
        native = args.native.expanduser().absolute()
        sha256, rows = _bound_receipt(
            native, args.spend_dir, args.receipt, args.expect_sha256
        )
        for world in worlds:
            row = rows[world]
            shown, masked = _context(native, world, row)
            cells.append(
                {
                    "world": world,
                    "source_uuid": row["trial_uuid"],
                    "source_result_sha256": row["result_sha256"],
                    "shown_prompt_sha256": p.digest(shown),
                    "masked_prompt_sha256": p.digest(masked),
                }
            )
    except (OSError, ValueError, KeyError, TypeError, IndexError, RuntimeError):
        parser.exit(1, "peer payload: receipt or native cohort failed authentication\n")
    print(
        p.wire(
            {
                "schema": "seatlab.peer-input-plan/v1",
                "source_receipt_sha256": sha256,
                "cells": cells,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
