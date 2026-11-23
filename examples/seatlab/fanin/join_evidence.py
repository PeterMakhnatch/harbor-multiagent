import argparse
import stat
import sys
from pathlib import Path
from typing import Any, NoReturn, override

from ..visibility import agy_seat
from ..visibility import calendar_packet as packet
from ..wait_peer.receipt import Bad, canonical, need, present, read
from . import evidence as ev
from . import join_owner, protocol, screen

DESCRIPTION = (
    "Audit one held-booker recovery cell and print one redacted canonical row. "
    "Exit 2 with a fixed category when any record contradicts another."
)
SCHEMA = "seatlab.join-evidence/v1"
WORLDS = protocol.RECOVERY_WORLDS
ARMS = protocol.RECOVERY_ARMS
TAGS = ("live", "control-correct", "control-inexact")
DECISIONS = {"abstain": "hold", "reduce": "reduce"}
REPORTERS = len(protocol.SEATS) - 1
CONTROLS = {
    (1216, "abstain", "control-correct"): {
        "reason": "held",
        "submissions": 0,
        "reward": 0,
        "exact": [True, True, True],
    },
    (1216, "reduce", "control-correct"): {
        "reason": "reduced",
        "submissions": 1,
        "reward": 1,
        "exact": [True, True, True],
    },
    (1217, "reduce", "control-inexact"): {
        "reason": "insufficient",
        "submissions": 0,
        "reward": 0,
        "exact": [False, True, True],
    },
}
ROW_KEYS = frozenset(
    {
        "schema",
        "world",
        "arm",
        "tag",
        "booker",
        "reporters",
        "trial_id",
        "generator_commit",
        "seed",
        "task_checksum",
        "calendar_sha256",
        "native_reward",
        "complete",
        "incomplete_reason",
        "reporters_expected",
        "reporter_sessions",
        "session_sha256s",
        "client_tokens",
        "reporters_exact",
        "reporter_rows",
        "booker_model_sessions",
        "outage",
        "join_decision",
        "join_reason",
        "submission_count",
        "control_as_designed",
        "inputs",
    }
)
REPORTER_KEYS = frozenset(
    {"seat", "position", "status", "exact", "session_sha256", "total_tokens"}
)
JOIN_FILES = (
    {"request.json", "join.json"},
    {"request.json", "join.json", "reply.json"},
)
RECORD_MODE = 0o400
RECORD_DIRECTORY_MODE = 0o700
Row = dict[str, Any]


def first_arm(world: int) -> str:
    assert type(world) is int and world in WORLDS
    return ARMS[world % 2]


def trial_name(world: int, arm: str, tag: str) -> str:
    assert world in WORLDS and arm in ARMS and tag in TAGS
    return ev.trial_name(world, arm, tag)


def registered(world: int, arm: str, tag: str) -> bool:
    return (
        protocol.admitted(world, arm)
        and tag in TAGS
        and (tag == "live" or (world, arm, tag) in CONTROLS)
    )


def private(path: Path) -> bytes:
    data = read(path)
    need(stat.S_IMODE(path.lstat().st_mode) == RECORD_MODE, "join_record_mode")
    return data


def join_turn(
    owner: Path, booker: str, prompt: str | None, meta: Row | None, arm: str
) -> Row:
    directory = owner / booker
    if not present(directory):
        need(meta is None or meta["reply_sha256"] is None, "owner_turn_missing")
        return {"status": "absent"}
    need(meta is None or meta["started_turn"], "owner_without_turn")
    names = ev.entries(directory)
    need(
        names in JOIN_FILES
        and stat.S_IMODE(directory.lstat().st_mode) == RECORD_DIRECTORY_MODE,
        "owner_entries",
    )
    if prompt is None:
        raise Bad("owner_prompt_binding")
    need(
        private(directory / "request.json") == join_owner.envelope(prompt),
        "owner_prompt_binding",
    )
    need(
        private(directory / "join.json") == join_owner.join_line(arm, booker, prompt),
        "join_decision_binding",
    )
    reason, slot = join_owner.decision(arm, booker, prompt)
    need((slot is None) == (reason != "reduced"), "join_decision_binding")
    need((slot is not None) == ("reply.json" in names), "join_reply_binding")
    if slot is None:
        need(
            meta is None
            or meta["stage"] == "booking"
            and meta["error"] == "turn_failed"
            and meta["reply_sha256"] is None,
            "join_decline_binding",
        )
        return {"status": "declined", "reason": reason}
    need(
        private(directory / "reply.json") == join_owner.reply_line(slot),
        "join_reply_binding",
    )
    text = protocol.wire({"slot": slot})
    bound = meta is not None and meta["reply_sha256"] is not None
    need(not bound or meta["reply_sha256"] == agy_seat.sha(text), "owner_turn_binding")
    return {
        "status": "reduced" if bound else "answered_unused",
        "reason": reason,
        "text": text,
    }


def owner_turns(cell: Path, seen: Row, launch: Row) -> dict[str, Row]:
    order, plan, metas = seen["order"], seen["plan"], seen["metas"]
    owner = cell / "owner"
    need(ev.entries(owner) <= set(protocol.SEATS), "owner_entries")
    owned = {
        seat: ev.owner_turn(owner, seat, plan[seat], metas[seat], launch)
        for seat in order[1:]
    }
    owned[order[0]] = join_turn(
        owner, order[0], plan[order[0]], metas[order[0]], launch["arm"]
    )
    ev.distinct_sessions(owned)
    return owned


def incomplete_reason(
    launch: Row, seen: Row, reward: int | None, owned: dict[str, Row]
) -> str | None:
    order, metas, raised, state = (
        seen["order"],
        seen["metas"],
        seen["raised"],
        seen["state"],
    )
    reason = ev.native_failure(launch, seen["result"])
    if reason is not None:
        return reason
    declined = owned[order[0]]["status"] == "declined"
    for seat in order:
        meta = metas[seat]
        if raised[seat]:
            return f"{seat}:seat_exception"
        if meta is None:
            return f"{seat}:missing_seat_result"
        if meta["stage"] != "done" and not (seat == order[0] and declined):
            return f"{seat}:{meta['error'] or 'seat_interrupted'}"
    if (
        state["identified"] != set(protocol.SEATS)
        or not ev.settled(seen["rounds"])
        or (state["submit"] is None) != declined
    ):
        return "behavior_incomplete"
    if reward is None:
        return "missing_native_reward"
    if any(owned[seat]["status"] != "bound" for seat in order[1:]):
        return "owner_turn_unbound"
    if owned[order[0]]["status"] not in ("declined", "reduced"):
        return "owner_turn_unbound"
    return None


def reporter_rows(seen: Row, owned: dict[str, Row]) -> list[Row]:
    exact = {
        e["seat"]: e["report"]["exact"]
        for e in seen["rounds"]
        if e["report"] is not None
    }
    rows = []
    for turn in ev.turn_rows(seen["order"], owned)[1:]:
        rows.append(
            {
                "seat": turn["seat"],
                "position": turn["position"],
                "status": turn["status"],
                "exact": exact.get(turn["seat"]),
                "session_sha256": turn["session_sha256"],
                "total_tokens": turn["total_tokens"],
            }
        )
    return rows


def designed(row: Row) -> bool | None:
    if row["tag"] == "live":
        return None
    want = CONTROLS[(row["world"], row["arm"], row["tag"])]
    return (
        row["complete"]
        and row["native_reward"] == want["reward"]
        and row["submission_count"] == want["submissions"]
        and row["join_reason"] == want["reason"]
        and [r["exact"] for r in row["reporter_rows"]] == want["exact"]
    )


def finish_row(
    row: Row, seen: Row, reason: str | None, reward: int | None, owned: dict[str, Row]
) -> None:
    rows = reporter_rows(seen, owned)
    sessions = sorted(r["session_sha256"] for r in rows if r["session_sha256"])
    tokens = [r["total_tokens"] for r in rows if r["total_tokens"] is not None]
    join = owned[seen["order"][0]]
    joined = join["status"] != "absent"
    row.update(
        complete=reason is None,
        incomplete_reason=reason,
        native_reward=reward,
        reporter_sessions=len(sessions),
        session_sha256s=sessions,
        client_tokens=sum(tokens) if row["tag"] == "live" else None,
        reporters_exact=sum(r["exact"] is True for r in rows),
        reporter_rows=rows,
        outage=joined,
        join_decision=DECISIONS[row["arm"]] if joined else None,
        join_reason=join["reason"] if joined else None,
        submission_count=None
        if reward is None
        else int(seen["state"]["submit"] is not None),
    )
    row["control_as_designed"] = designed(row)


def trial_row(row: Row, cell: Path, trial: Path, launch: Row, stock: ev.Stock) -> None:
    cfg, starts, target, calendar = stock
    row["inputs"]["config"] = launch["config_sha256"]
    row["calendar_sha256"] = calendar
    seen = ev.observed(row, trial, cfg, launch, starts)
    order, metas, rounds, state = (
        seen["order"],
        seen["metas"],
        seen["rounds"],
        seen["state"],
    )
    owned = owner_turns(cell, seen, launch)
    ev.owner_launch(cell, launch, rounds, metas)
    ev.replies_bound(order, starts, rounds, owned, metas, state["submit"])
    reward = ev.stock_reward(
        trial,
        seen["result"],
        seen["messages"],
        state["submit"],
        row["world"],
        order[0],
        target,
    )
    finish_row(row, seen, incomplete_reason(launch, seen, reward, owned), reward, owned)


def base_row(cell: Path, world: int, arm: str, tag: str, launch: Row) -> Row:
    order = protocol.order(world)
    return {
        "schema": SCHEMA,
        "world": world,
        "arm": arm,
        "tag": tag,
        "booker": order[0],
        "reporters": list(order[1:]),
        "trial_id": None,
        "generator_commit": screen.GENERATOR_COMMIT,
        "seed": screen.SEED,
        "task_checksum": launch["task_checksum"],
        "calendar_sha256": None,
        "native_reward": None,
        "complete": False,
        "incomplete_reason": ev.launch_reason(launch) or "missing_native_result",
        "reporters_expected": REPORTERS,
        "reporter_sessions": 0,
        "session_sha256s": [],
        "client_tokens": None,
        "reporters_exact": 0,
        "reporter_rows": [
            {
                "seat": seat,
                "position": position,
                "status": "unaudited" if present(cell / "owner" / seat) else "absent",
                "exact": None,
                "session_sha256": None,
                "total_tokens": None,
            }
            for position, seat in enumerate(order[1:], 1)
        ],
        "booker_model_sessions": 0,
        "outage": False,
        "join_decision": None,
        "join_reason": None,
        "submission_count": None,
        "control_as_designed": None if tag == "live" else False,
        "inputs": {},
    }


def checked(row: Row) -> Row:
    rows = row["reporter_rows"]
    need(
        row.keys() == ROW_KEYS
        and len(rows) == row["reporters_expected"] == REPORTERS
        and all(r.keys() == REPORTER_KEYS for r in rows)
        and row["booker_model_sessions"] == 0
        and row["reporters_exact"] == sum(r["exact"] is True for r in rows)
        and row["reporter_sessions"] == len(row["session_sha256s"])
        and (row["native_reward"] is None) == (row["submission_count"] is None)
        and (row["join_reason"] is None) == (row["join_decision"] is None)
        and row["outage"] == (row["join_decision"] is not None),
        "row_invariant",
    )
    if row["complete"]:
        held = row["join_reason"] != "reduced"
        need(
            row["trial_id"] is not None
            and row["calendar_sha256"] is not None
            and row["outage"]
            and all(r["status"] == "bound" for r in rows)
            and row["submission_count"] == int(not held),
            "row_invariant",
        )
    return row


def audit(cell: Path, world: int, arm: str, tag: str) -> Row:
    launch_raw, launch, trial, stock = ev.staged(cell, world, arm, tag)
    row = base_row(cell, world, arm, tag, launch)
    row["inputs"]["launch"] = packet._digest(launch_raw)
    if stock is not None:
        trial_row(row, cell, trial, launch, stock)
    return checked(row)


class Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        self.exit(2, "join evidence: invalid_arguments\n")


def main(argv: list[str] | None = None) -> int:
    parser = Parser(
        prog="python -BS -m examples.seatlab.fanin.join_evidence",
        description=DESCRIPTION,
        allow_abbrev=False,
    )
    parser.add_argument("cell", type=Path)
    parser.add_argument("--world", type=int, required=True, choices=WORLDS)
    parser.add_argument("--arm", required=True, choices=ARMS)
    parser.add_argument("--tag", required=True, choices=TAGS)
    args = parser.parse_args(argv)
    if not registered(args.world, args.arm, args.tag):
        parser.error("unregistered_cell")
    try:
        cell = args.cell.resolve(strict=True)
        row = audit(cell, args.world, args.arm, args.tag)
        sys.stdout.buffer.write(canonical(row) + b"\n")
    except Bad as exc:
        print(f"join evidence: {exc}", file=sys.stderr)
        return 2
    except packet._Invalid:
        print("join evidence: invalid_record", file=sys.stderr)
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
        print("join evidence: invalid_or_unreadable_evidence", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
