import argparse
import os
import stat
import sys
from pathlib import Path
from typing import Any, NoReturn, override

from ..visibility import calendar_packet as packet
from ..wait_peer import protocol as peer
from ..wait_peer import receipt as base
from ..wait_peer.receipt import HEX64, Bad, canonical, need, present
from . import evidence as ev
from . import external_evidence as ext
from . import external_owner, protocol

DESCRIPTION = (
    "Bind the nominated model seats of one audited external fan-in cell to the "
    "operator's independently held owner records and print one redacted canonical "
    "provenance row. Exit 2 with a fixed category when any record contradicts another."
)
SCHEMA = "seatlab.external-provenance/v1"
SEATS = ext.SEATS
ARMS = ext.ARMS
STATUSES = ("bound", "skipped", "unverified")
EMPTY_RECORD_MODE = 0o700
ROW_KEYS = {
    "schema",
    "world",
    "arm",
    "trial_id",
    "external_row_sha256",
    "seats",
    "attested_sessions",
    "attested_tokens",
    "fully_accounted",
    "served_model",
}
SEAT_KEYS = {"seat", "status", "session_sha256", "client_tokens"}
Row = dict[str, Any]
Turn = tuple[str | None, str | None]


def selection(model_seats: list[str] | tuple[str, ...]) -> frozenset[str]:
    need(
        type(model_seats) in (list, tuple)
        and 1 <= len(model_seats) <= len(SEATS)
        and len(set(model_seats)) == len(model_seats)
        and all(seat in SEATS for seat in model_seats),
        "model_seat_selection",
    )
    return frozenset(model_seats)


def owner_directory(cell: Path, owner_dir: Path) -> None:
    need(
        isinstance(owner_dir, Path)
        and owner_dir.is_absolute()
        and owner_dir.resolve(strict=True) == owner_dir
        and cell.resolve(strict=True) == cell
        and not owner_dir.is_relative_to(cell)
        and not cell.is_relative_to(owner_dir),
        "owner_dir_binding",
    )
    info = owner_dir.lstat()
    need(
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) == 0o700,
        "owner_dir_binding",
    )


def wire_turn(connector: Path | None, seat: str, entry: Row) -> Turn:
    record = ext.owner_record(connector, seat)
    if record is None:
        need(not entry["request"] and not entry["reply"], "wire_record_binding")
        return None, None
    request, reply = record
    need(
        entry["request"] is True
        and entry["reply"] is (reply is not None)
        and entry["request_sha256"] == packet._digest(request)
        and entry["reply_sha256"] == (None if reply is None else packet._digest(reply)),
        "wire_record_binding",
    )
    prompt = ext.request_prompt(request)
    need(request == external_owner.envelope(prompt), "owner_prompt_binding")
    return prompt, None if reply is None else ext.reply_text(reply)


def empty_record(directory: Path, text: str | None) -> bool:
    if ev.entries(directory):
        return False
    need(
        stat.S_IMODE(directory.lstat().st_mode) == EMPTY_RECORD_MODE,
        "owner_custody",
    )
    need(text is None, "not_admitted_with_reply")
    return True


def seat_out(seat: str, status: str, session: str | None, tokens: int | None) -> Row:
    assert status in STATUSES
    assert (status == "bound") == (session is not None)
    assert status != "unverified" or tokens is None
    return {
        "seat": seat,
        "status": status,
        "session_sha256": session,
        "client_tokens": tokens,
    }


def owner_side(owner_dir: Path, seat: str, prompt: str, text: str | None) -> Row:
    turn = (
        None
        if text is None
        else {"prompt_sha256": peer.sha(prompt), "reply_sha256": peer.sha(text)}
    )
    owned = base.owner_turn(owner_dir, seat, prompt, turn)
    session = owned["receipt"]["session_sha256"] if "receipt" in owned else None
    if owned["status"] != "bound":
        need(
            text is None and owned["status"] in ("unanswered", "answered_unused"),
            "owner_turn_binding",
        )
        return {"out": seat_out(seat, "unverified", None, None), "session": session}
    receipt = owned["receipt"]
    tokens = receipt["usage"]["total_tokens"]
    need(
        owned["text"] == text
        and receipt["server_model"] is None
        and isinstance(session, str)
        and HEX64.fullmatch(session)
        and type(tokens) is int
        and tokens > 0,
        "owner_receipt_binding",
    )
    return {"out": seat_out(seat, "bound", session, tokens), "session": session}


def seat_provenance(owner_dir: Path, seat: str, entry: Row, turn: Turn) -> Row:
    prompt, text = turn
    directory = owner_dir / seat
    recorded = present(directory)
    if entry["action"] == "skip":
        need(
            prompt is None
            and text is None
            and not entry["request"]
            and not entry["reply"]
            and not recorded,
            "skipped_owner_recorded",
        )
        return {"out": seat_out(seat, "skipped", None, 0), "session": None}
    if prompt is None:
        need(text is None and not recorded, "owner_without_request")
        return {"out": seat_out(seat, "unverified", None, None), "session": None}
    if not recorded or empty_record(directory, text):
        return {"out": seat_out(seat, "unverified", None, None), "session": None}
    return owner_side(owner_dir, seat, prompt, text)


def summary(row: Row, seats: list[Row]) -> Row:
    bound = [seat for seat in seats if seat["status"] == "bound"]
    result = {
        "schema": SCHEMA,
        "world": row["world"],
        "arm": row["arm"],
        "trial_id": row["trial_id"],
        "external_row_sha256": packet._digest(canonical(row) + b"\n"),
        "seats": seats,
        "attested_sessions": len(bound),
        "attested_tokens": sum(seat["client_tokens"] for seat in bound),
        "fully_accounted": all(seat["status"] != "unverified" for seat in seats),
        "served_model": None,
    }
    assert result.keys() == ROW_KEYS
    assert all(seat.keys() == SEAT_KEYS for seat in seats)
    assert len({seat["seat"] for seat in seats}) == len(seats)
    assert result["attested_tokens"] >= result["attested_sessions"]
    return result


def audit(
    cell: Path,
    world: int,
    arm: str,
    owner_dir: Path,
    model_seats: list[str] | tuple[str, ...],
) -> Row:
    chosen = selection(model_seats)
    row = ext.audit(cell, world, arm)
    owner_directory(cell, owner_dir)
    connector = ext.owner_root(cell)
    entries = {entry["seat"]: entry for entry in row["seats"]}
    need(
        len(row["seats"]) == len(SEATS) and entries.keys() == set(SEATS),
        "external_row_shape",
    )
    seats: list[Row] = []
    sessions: list[str] = []
    for entry in row["seats"]:
        seat = entry["seat"]
        if seat not in chosen:
            continue
        turn = wire_turn(connector, seat, entry)
        bound = seat_provenance(owner_dir, seat, entry, turn)
        seats.append(bound["out"])
        if bound["session"] is not None:
            sessions.append(bound["session"])
    need(len(seats) == len(chosen), "model_seat_selection")
    need(len(set(sessions)) == len(sessions), "shared_provider_session")
    return summary(row, seats)


class Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        self.exit(2, "fanin external provenance: invalid_arguments\n")


def arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = Parser(
        prog="python -BS -m examples.seatlab.fanin.owner_provenance",
        description=DESCRIPTION,
        allow_abbrev=False,
    )
    parser.add_argument("cell", type=Path)
    parser.add_argument("--world", type=int, required=True)
    parser.add_argument("--arm", required=True, choices=ARMS)
    parser.add_argument("--owner-dir", type=Path, required=True)
    parser.add_argument("--model-seat", action="append", required=True, choices=SEATS)
    args = parser.parse_args(argv)
    if not protocol.external_admitted(args.world, args.arm):
        parser.error("world_arm_pairing")
    if not args.owner_dir.is_absolute():
        parser.error("owner_dir")
    seats = args.model_seat
    if not 1 <= len(seats) <= len(SEATS) or len(set(seats)) != len(seats):
        parser.error("model_seat")
    return args


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)
    try:
        cell = args.cell.resolve(strict=True)
        row = audit(cell, args.world, args.arm, args.owner_dir, args.model_seat)
        sys.stdout.buffer.write(canonical(row) + b"\n")
    except Bad as exc:
        print(f"fanin external provenance: {exc}", file=sys.stderr)
        return 2
    except packet._Invalid:
        print("fanin external provenance: invalid_record", file=sys.stderr)
        return 2
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        IndexError,
        AttributeError,
        RecursionError,
    ):
        print(
            "fanin external provenance: invalid_or_unreadable_evidence",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
