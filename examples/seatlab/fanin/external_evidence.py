import argparse
import os
import stat
import sys
from pathlib import Path
from typing import Any, NoReturn, override

from ..visibility import agy_seat
from ..visibility import calendar_packet as packet
from ..wait_peer.receipt import Bad, canonical, need, present, read
from . import evidence as ev
from . import external_owner, protocol, screen

DESCRIPTION = (
    "Audit one four-seat fan-in cell answered by external socket owners and print "
    "one redacted canonical row. Exit 2 with a fixed category when any record "
    "contradicts another."
)
SCHEMA = "seatlab.peer-fanin-external/v1"
TAG = "external"
SEATS = protocol.SEATS
ARMS = (*protocol.ARMS, *protocol.TIMING_ARMS)
REQUEST = external_owner.REQUEST
REPLY = external_owner.REPLY
RECORD_NAMES = ({REQUEST}, {REQUEST, REPLY})
DIRECTORY_MODE = 0o700
FILE_MODE = 0o400
Row = dict[str, Any]
Record = tuple[bytes, bytes | None]
assert TAG not in ev.TAGS
assert not set(ARMS) & set(protocol.RECOVERY_ARMS)


def private_node(path: Path, kind: int, mode: int) -> None:
    info = path.lstat()
    need(
        stat.S_IFMT(info.st_mode) == kind
        and stat.S_IMODE(info.st_mode) == mode
        and info.st_uid == os.geteuid(),
        "owner_custody",
    )


def private_file(path: Path) -> bytes:
    private_node(path, stat.S_IFREG, FILE_MODE)
    return read(path)


def owner_root(cell: Path) -> Path | None:
    owner = cell / "owner"
    if not present(owner):
        return None
    private_node(owner, stat.S_IFDIR, DIRECTORY_MODE)
    names = ev.entries(owner)
    staging = {external_owner.staging_name(owner / seat).name for seat in SEATS}
    need(not names & staging, "owner_staging_left")
    need(names <= set(SEATS), "owner_entries")
    return owner


def owner_record(owner: Path | None, seat: str) -> Record | None:
    if owner is None or not present(owner / seat):
        return None
    directory = owner / seat
    private_node(directory, stat.S_IFDIR, DIRECTORY_MODE)
    names = ev.entries(directory)
    need(names in RECORD_NAMES, "owner_entries")
    request = private_file(directory / REQUEST)
    reply = private_file(directory / REPLY) if REPLY in names else None
    return request, reply


def request_prompt(raw: bytes) -> str:
    try:
        return external_owner.request_prompt(raw)
    except ValueError:
        raise Bad("owner_request_shape") from None


def reply_text(raw: bytes) -> str:
    try:
        return external_owner.reply_text(raw)
    except ValueError:
        raise Bad("owner_reply_shape") from None


def owner_turn(record: Record | None, prompt: str | None, meta: Row | None) -> Row:
    replied = meta is not None and meta["reply_sha256"] is not None
    if record is None:
        need(not replied, "owner_turn_missing")
        return {"status": "absent"}
    need(meta is None or meta["started_turn"], "owner_without_turn")
    request, reply = record
    need(
        prompt is not None
        and request_prompt(request) == prompt
        and request == external_owner.envelope(prompt)
        and (meta is None or meta["prompt_sha256"] == agy_seat.sha(prompt)),
        "owner_prompt_binding",
    )
    if reply is None:
        need(not replied, "owner_reply_missing")
        return {"status": "unanswered"}
    text = reply_text(reply)
    need(
        not replied or meta is not None and meta["reply_sha256"] == agy_seat.sha(text),
        "owner_turn_binding",
    )
    return {"status": "bound" if replied else "answered_unused", "text": text}


def seat_row(
    position: int,
    seat: str,
    launch: Row,
    record: Record | None,
    status: str,
    action: str | None,
) -> Row:
    request, reply = (None, None) if record is None else record
    return {
        "seat": seat,
        "role": "booker" if position == 0 else "reporter",
        "position": position,
        "action": action,
        "owner_accepted": launch["owners"][seat]["accepted"],
        "status": status,
        "request": request is not None,
        "reply": reply is not None,
        "request_sha256": None if request is None else packet._digest(request),
        "reply_sha256": None if reply is None else packet._digest(reply),
    }


def base_row(world: int, arm: str, launch: Row, launch_raw: bytes) -> Row:
    order = protocol.order(world)
    return {
        "schema": SCHEMA,
        "world": world,
        "arm": arm,
        "tag": TAG,
        "trial_id": None,
        "booker": order[0],
        "reporters": list(order[1:]),
        "seats": None,
        "reports": None,
        "exact_reports": None,
        "submissions": None,
        "native_reward": None,
        "complete": False,
        "incomplete_reason": ev.launch_reason(launch) or "missing_native_result",
        "model_sessions": None,
        "tokens": None,
        "source": {
            "generator_commit": screen.GENERATOR_COMMIT,
            "seed": screen.SEED,
            "task_checksum": launch["task_checksum"],
            "calendar_sha256": None,
            "stock_verified": False,
        },
        "inputs": {"launch": packet._digest(launch_raw)},
    }


def unverified(row: Row, launch: Row, records: dict[str, Record | None]) -> None:
    order = protocol.order(row["world"])
    for seat in SEATS:
        record, state = records[seat], launch["owners"][seat]
        need(
            state["record_present"] == (record is not None)
            and (record is None or state["accepted"] is True),
            "owner_launch_binding",
        )
        if record is not None:
            request_prompt(record[0])
            if record[1] is not None:
                reply_text(record[1])
    row["seats"] = [
        seat_row(
            position,
            seat,
            launch,
            records[seat],
            "absent" if records[seat] is None else "unverified",
            None,
        )
        for position, seat in enumerate(order)
    ]


def trial_row(
    row: Row,
    cell: Path,
    trial: Path,
    stock: ev.Stock,
    launch: Row,
    records: dict[str, Record | None],
) -> None:
    cfg, starts, target, _ = stock
    seen = ev.observed(row, trial, cfg, launch, starts)
    order, metas, rounds, state = (
        seen["order"],
        seen["metas"],
        seen["rounds"],
        seen["state"],
    )
    owned = {
        seat: owner_turn(records[seat], seen["plan"][seat], metas[seat])
        for seat in order
    }
    ev.owner_launch(cell, launch, rounds, metas)
    bound = {
        seat: turn if turn["status"] == "bound" else {"status": turn["status"]}
        for seat, turn in owned.items()
    }
    ev.replies_bound(order, starts, rounds, bound, metas, state["submit"])
    reward = ev.stock_reward(
        trial,
        seen["result"],
        seen["messages"],
        state["submit"],
        row["world"],
        order[0],
        target,
    )
    reason = ev.incomplete_reason(
        launch,
        seen["result"],
        order,
        metas,
        seen["raised"],
        rounds,
        state,
        reward,
        owned,
    )
    actions = {e["seat"]: e["action"] for e in rounds}
    reports = [e["report"] for e in rounds if e["report"] is not None]
    row.update(
        complete=reason is None,
        incomplete_reason=reason,
        native_reward=reward,
        seats=[
            seat_row(
                position,
                seat,
                launch,
                records[seat],
                owned[seat]["status"],
                actions.get(seat),
            )
            for position, seat in enumerate(order)
        ],
        reports=len(reports),
        exact_reports=sum(r["exact"] is True for r in reports),
        submissions=int(state["submit"] is not None),
    )


def audit(cell: Path, world: int, arm: str) -> Row:
    need(ev.admitted(world, arm) and arm in ARMS, "world_arm_pairing")
    launch_raw, launch, trial, stock = ev.staged(cell, world, arm, TAG)
    need(not present(cell / "admission"), "study_tag_binding")
    owner = owner_root(cell)
    records = {seat: owner_record(owner, seat) for seat in SEATS}
    row = base_row(world, arm, launch, launch_raw)
    if stock is None:
        unverified(row, launch, records)
        return row
    row["inputs"]["config"] = launch["config_sha256"]
    row["source"]["calendar_sha256"] = stock[3]
    row["source"]["stock_verified"] = True
    trial_row(row, cell, trial, stock, launch, records)
    return row


class Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        self.exit(2, "fanin external evidence: invalid_arguments\n")


def main(argv: list[str] | None = None) -> int:
    parser = Parser(
        prog="python -BS -m examples.seatlab.fanin.external_evidence",
        description=DESCRIPTION,
        allow_abbrev=False,
    )
    parser.add_argument("cell", type=Path)
    parser.add_argument("--world", type=int, required=True)
    parser.add_argument("--arm", required=True, choices=ARMS)
    args = parser.parse_args(argv)
    if not ev.admitted(args.world, args.arm):
        parser.error("world_arm_pairing")
    try:
        cell = args.cell.resolve(strict=True)
        row = audit(cell, args.world, args.arm)
        sys.stdout.buffer.write(canonical(row) + b"\n")
    except Bad as exc:
        print(f"fanin external evidence: {exc}", file=sys.stderr)
        return 2
    except packet._Invalid:
        print("fanin external evidence: invalid_record", file=sys.stderr)
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
            "fanin external evidence: invalid_or_unreadable_evidence", file=sys.stderr
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
