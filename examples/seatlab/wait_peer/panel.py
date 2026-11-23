import argparse
import re
import sys
from pathlib import Path
from typing import NoReturn, TypeGuard, override
from uuid import UUID

from ..calendar_notices import heldout
from ..visibility import agy_seat
from ..visibility import calendar_packet as packet
from . import protocol, receipt
from .receipt import Bad, Row, canonical, need, same

SCHEMA = "seatlab.wait-peer-panel/v1"
INPUT_SCHEMA = "seatlab.wait-peer-rows/v1"
DESCRIPTION = (
    "Score public wait-peer receipt rows for the 8-world panel. "
    "Read INPUT.json or '-' for stdin. Exit 2 on any contradiction."
)
MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "results/wait-for-peer/calendar-registration.json"
)
MANIFEST_SHA256 = "bdf26d746f16a27e3b5120cf94c8af48a03f7df8ec56ab43c8431badcdf18bbc"
MAX_INPUT = 1 << 23
WORLDS = tuple(range(protocol.FIRST, protocol.FIRST + protocol.COUNT))
CELLS = len(WORLDS) * len(protocol.ARMS)
LIVE = "live"
CONTROL_TAGS = receipt.TAGS[1:]
CONTROLS = {
    "positive": {
        "did_request": True,
        "released": True,
        "delivered": True,
        "booked_correct": True,
        "native_reward": 1,
    },
    "forced-no-request": {
        "did_request": False,
        "released": True,
        "delivered": True,
        "booked_correct": False,
        "native_reward": 0,
    },
    "no-request": {
        "did_request": False,
        "released": False,
        "delivered": False,
        "booked_correct": False,
        "native_reward": 0,
    },
}
ROW_KEYS = {
    "schema",
    "trial_uuid",
    "world",
    "arm",
    "tag",
    "arm_position",
    "sender",
    "receiver",
    "source",
    "complete",
    "incomplete_reason",
    "native_reward",
    "did_request",
    "released",
    "delivered",
    "booked_slot",
    "booked_correct",
    "booked_fits_receiver",
    "booked_fits_sender",
    "report_size",
    "report_exact",
    "report_has_joint_slot",
    "seat_starts_sha256",
    "turns",
    "inputs",
    "claims",
    "notes",
}
SOURCE_KEYS = {
    "generator_commit",
    "manifest_sha256",
    "manifest_verified",
    "calendar_sha256",
    "stock_semantics_sha256",
    "task_checksum",
    "seat_calendar_sha256",
}
FLAGS = (
    "did_request",
    "released",
    "delivered",
    "booked_correct",
    "booked_fits_receiver",
    "booked_fits_sender",
    "report_exact",
    "report_has_joint_slot",
)
OWNER_SOURCE = "owner_subscription_cli"
LIVE_SOURCES = (OWNER_SOURCE, "unattested_command")
CONTROL_SOURCE = "external_command"
REASON = re.compile(r"(?:(?:sender|receiver):)?[a-z][a-z0-9_]{0,63}")
NOTES = (
    "Counts use complete cells only; an incomplete cell has no credit and is not a zero.",
    "Token totals are client reports per phase; no served model or cost is inferred.",
    "Eight worlds do not support a claim about every model or about reader cognition.",
)


def digest(value: object) -> bool:
    return isinstance(value, str) and receipt.HEX64.fullmatch(value) is not None


def seat_digests(value: object) -> bool:
    return (
        isinstance(value, dict)
        and value.keys() == set(receipt.SEATS)
        and all(digest(v) for v in value.values())
        and len(set(value.values())) == len(receipt.SEATS)
    )


def uuid4(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        identifier = UUID(value)
    except ValueError:
        return False
    return str(identifier) == value and identifier.version == 4


def control_kind(row: Row) -> str:
    if row["tag"] == "control-ok":
        return "positive"
    return "forced-no-request" if row["arm"] == "auto" else "no-request"


def pinned() -> dict[int, tuple[str, str]]:
    rows = heldout._pinned_manifest_rows(MANIFEST, MANIFEST_SHA256, receipt.COHORT)
    need(len(rows) == len(WORLDS), "manifest_world_count")
    return dict(zip(WORLDS, rows, strict=True))


def checked_identity(row: Row, live: bool) -> None:
    world, arm, tag, position = (
        row[k] for k in ("world", "arm", "tag", "arm_position")
    )
    need(row["schema"] == receipt.SCHEMA, "row_schema")
    need(type(world) is int and world in WORLDS, "row_world")
    need(isinstance(arm, str) and arm in protocol.ARMS, "row_arm")
    need(
        isinstance(tag, str) and (tag == LIVE if live else tag in CONTROL_TAGS),
        "row_tag",
    )
    need(
        type(position) is int and position == protocol.ordered_arms(world).index(arm),
        "row_arm_position",
    )
    need((row["sender"], row["receiver"]) == protocol.role_ids(world), "row_roles")
    need(type(row["complete"]) is bool, "row_complete")
    uuid = row["trial_uuid"]
    need(uuid4(uuid) if row["complete"] or uuid is not None else True, "row_trial_uuid")
    starts = row["seat_starts_sha256"]
    need(
        seat_digests(starts) if row["complete"] or starts is not None else True,
        "row_seat_starts",
    )


def checked_source(row: Row, pins: dict[int, tuple[str, str]]) -> None:
    source = row["source"]
    need(isinstance(source, dict) and source.keys() == SOURCE_KEYS, "source_shape")
    need(
        source["generator_commit"] == heldout.GENERATOR_COMMIT
        and source["manifest_verified"] is True
        and source["manifest_sha256"] == MANIFEST_SHA256,
        "source_manifest",
    )
    need(
        (source["calendar_sha256"], source["stock_semantics_sha256"])
        == pins[row["world"]],
        "source_world_calendar",
    )
    need(
        digest(source["task_checksum"])
        and seat_digests(source["seat_calendar_sha256"]),
        "source_seats",
    )


def checked_outcome(row: Row) -> None:
    need(all(row[k] is None or type(row[k]) is bool for k in FLAGS), "outcome_type")
    slot, size, reward = row["booked_slot"], row["report_size"], row["native_reward"]
    need(slot is None or receipt.slot(slot), "outcome_type")
    need(
        size is None or type(size) is int and 0 <= size <= protocol.SLOTS,
        "outcome_type",
    )
    need(reward is None or type(reward) is int and reward in (0, 1), "reward_type")
    requested, released, delivered = (
        row[k] for k in ("did_request", "released", "delivered")
    )
    need(
        released is None
        or requested is not None
        and released == protocol.allowed(row["arm"], requested),
        "arm_rule",
    )
    need(
        delivered is None or released is not None and (released or not delivered),
        "arm_rule",
    )
    need((slot is None) == (row["booked_correct"] is None), "outcome_binding")
    reason = row["incomplete_reason"]
    if not row["complete"]:
        need(isinstance(reason, str) and REASON.fullmatch(reason), "incomplete_reason")
        return
    need(reason is None and reward is not None, "complete_reward")
    need(
        requested is not None
        and released is not None
        and delivered is released
        and slot is not None
        and row["booked_correct"] is (reward == 1)
        and row["report_exact"] is not None,
        "complete_outcome",
    )


def checked_claims(row: Row, live: bool) -> None:
    claims = row["claims"]
    need(
        isinstance(claims, dict)
        and claims.get("native_trial_complete") is row["complete"]
        and claims.get("stock_source_verified") is True,
        "claims_binding",
    )
    designed = claims.get("control_as_designed")
    need(
        "control_as_designed" not in claims
        if live
        else designed is None or type(designed) is bool,
        "claims_control",
    )
    if not row["complete"]:
        return
    need(
        claims.get("stock_reward_verified") is True
        and claims.get("all_prompts_reconstructed") is True
        and claims.get("served_model_reported") is False
        and claims.get("report_delivered_before_book") is row["delivered"],
        "claims_complete",
    )
    if live:
        need(
            claims.get("owner_answers_bound") is True
            and claims.get("separate_provider_sessions") is True,
            "claims_owner_unbound",
        )
    else:
        need(type(designed) is bool, "claims_control")


def checked_usage(usage: object) -> None:
    need(
        isinstance(usage, dict)
        and usage.keys() == agy_seat.USAGE_FIELDS
        and all(type(n) is int and n >= 0 for n in usage.values()),
        "turn_usage",
    )


def checked_turns(row: Row, live: bool) -> None:
    turns = row["turns"]
    if turns is None:
        need(not row["complete"], "turns_missing")
        return
    need(
        isinstance(turns, dict) and turns.keys() == set(protocol.PHASES), "turns_shape"
    )
    sources = (OWNER_SOURCE,) if live and row["complete"] else LIVE_SOURCES
    for phase in protocol.PHASES:
        turn = turns[phase]
        need(isinstance(turn, dict), "turns_shape")
        source = turn.get("turn_source")
        need(source in sources if live else source == CONTROL_SOURCE, "turn_source")
        if row["complete"]:
            need(
                digest(turn.get("prompt_sha256")) and digest(turn.get("reply_sha256")),
                "turn_binding",
            )
            need(not live or turn.get("owner_status") == "bound", "turn_owner_unbound")
        if "usage" in turn:
            need(source == OWNER_SOURCE, "turn_usage")
            checked_usage(turn["usage"])


def row_object(value: object) -> TypeGuard[Row]:
    return (
        isinstance(value, dict)
        and all(isinstance(key, str) for key in value)
        and value.keys() == ROW_KEYS
    )


def checked_row(value: object, live: bool, pins: dict[int, tuple[str, str]]) -> Row:
    if not row_object(value):
        raise Bad("row_shape")
    checked_identity(value, live)
    checked_source(value, pins)
    checked_outcome(value)
    checked_claims(value, live)
    checked_turns(value, live)
    return value


def load(name: str) -> Row:
    if name == "-":
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    else:
        with Path(name).open("rb") as handle:
            raw = handle.read(MAX_INPUT + 1)
    need(0 < len(raw) <= MAX_INPUT, "input_size")
    try:
        envelope = packet._json(raw)
    except packet._Invalid:
        raise Bad("input_json") from None
    need(
        isinstance(envelope, dict)
        and envelope.keys() == {"schema", "rows", "controls"}
        and envelope["schema"] == INPUT_SCHEMA
        and isinstance(envelope["rows"], list)
        and isinstance(envelope["controls"], list),
        "input_envelope",
    )
    need(
        len(envelope["rows"]) <= CELLS and len(envelope["controls"]) <= len(CONTROLS),
        "input_size",
    )
    return envelope


def collect(
    envelope: Row, pins: dict[int, tuple[str, str]]
) -> tuple[dict[tuple[int, str], Row], dict[str, Row]]:
    cells: dict[tuple[int, str], Row] = {}
    controls: dict[str, Row] = {}
    uuids: set[str] = set()
    worlds: dict[int, Row] = {}
    entries = [(v, True) for v in envelope["rows"]]
    entries += [(v, False) for v in envelope["controls"]]
    for value, live in entries:
        row = checked_row(value, live, pins)
        if live:
            need((row["world"], row["arm"]) not in cells, "duplicate_cell")
            cells[(row["world"], row["arm"])] = row
        else:
            need(control_kind(row) not in controls, "duplicate_control")
            controls[control_kind(row)] = row
        uuid = row["trial_uuid"]
        need(uuid is None or uuid not in uuids, "shared_trial_uuid")
        if uuid is not None:
            uuids.add(uuid)
        world = worlds.setdefault(
            row["world"], {"source": row["source"], "starts": None}
        )
        need(same(world["source"], row["source"]), "world_source_mismatch")
        starts = row["seat_starts_sha256"]
        need(
            starts is None or world["starts"] is None or same(world["starts"], starts),
            "world_starts_mismatch",
        )
        world["starts"] = starts if starts is not None else world["starts"]
    return cells, controls


def client_tokens(rows: list[Row]) -> Row:
    totals = {
        phase: {"turns": 0, **dict.fromkeys(sorted(agy_seat.USAGE_FIELDS), 0)}
        for phase in protocol.PHASES
    }
    for row in rows:
        for phase, turn in (row["turns"] or {}).items():
            if "usage" in turn:
                totals[phase]["turns"] += 1
                for field, count in turn["usage"].items():
                    totals[phase][field] += count
    return totals


def arm_report(cells: dict[tuple[int, str], Row], arm: str) -> Row:
    present = [cells[(w, arm)] for w in WORLDS if (w, arm) in cells]
    done = [row for row in present if row["complete"]]

    def count(key: str) -> int:
        return sum(row[key] is True for row in done)

    return {
        "expected": len(WORLDS),
        "present": len(present),
        "complete": len(done),
        "coverage": f"{len(done)}/{len(WORLDS)}",
        "requested": count("did_request"),
        "delivered_before_book": count("delivered"),
        "booked_correct": count("booked_correct"),
        "reports_exact": count("report_exact"),
        "client_reported_tokens": client_tokens(present),
    }


def cell_status(cells: dict[tuple[int, str], Row]) -> list[Row]:
    rows = []
    for world in WORLDS:
        for arm in protocol.ordered_arms(world):
            row = cells.get((world, arm))
            status = (
                "missing"
                if row is None
                else "complete"
                if row["complete"]
                else "incomplete"
            )
            rows.append(
                {
                    "world": world,
                    "arm": arm,
                    "status": status,
                    "reason": None if row is None else row["incomplete_reason"],
                    "trial_uuid": None if row is None else row["trial_uuid"],
                }
            )
    return rows


def paired(cells: dict[tuple[int, str], Row], arm: str) -> Row:
    worlds, scores = [], []
    totals = {"requested": 0, "delivered": 0, "score": 0}
    for world in WORLDS:
        a, b = cells.get((world, arm)), cells.get((world, "neutral"))
        if a is None or b is None or not (a["complete"] and b["complete"]):
            worlds.append({"world": world, "status": "inconclusive"})
            continue
        diff = {
            "requested": int(a["did_request"]) - int(b["did_request"]),
            "delivered": int(a["delivered"]) - int(b["delivered"]),
            "score": a["native_reward"] - b["native_reward"],
        }
        worlds.append({"world": world, "status": "measured", **diff})
        scores.append(diff["score"])
        totals = {k: totals[k] + diff[k] for k in totals}
    return {
        "worlds": worlds,
        "measured": len(scores),
        "wins": sum(s > 0 for s in scores),
        "ties": sum(s == 0 for s in scores),
        "losses": sum(s < 0 for s in scores),
        "totals": totals if len(scores) == len(WORLDS) else None,
    }


def control_report(controls: dict[str, Row]) -> Row:
    report = {}
    for kind, expected in CONTROLS.items():
        row = controls.get(kind)
        if row is None:
            report[kind] = {"present": False, "verified": False}
            continue
        misses = sorted(
            k for k, v in expected.items() if row["complete"] and not same(row[k], v)
        )
        report[kind] = {
            "present": True,
            "world": row["world"],
            "arm": row["arm"],
            "tag": row["tag"],
            "trial_uuid": row["trial_uuid"],
            "complete": row["complete"],
            "incomplete_reason": row["incomplete_reason"],
            "mismatches": misses,
            "verified": row["complete"]
            and not misses
            and row["claims"].get("control_as_designed") is True,
        }
    return report


def decision(arms: Row, controls: Row) -> Row:
    measured = sum(arm["complete"] for arm in arms.values())
    reasons = [] if measured == CELLS else [f"live_cells_measured:{measured}/{CELLS}"]
    reasons += [
        f"control_unverified:{k}" for k, c in controls.items() if not c["verified"]
    ]
    if reasons:
        return {
            "status": "inconclusive",
            "reasons": reasons,
            "remind": "inconclusive",
            "auto": "inconclusive",
            "margins": None,
        }
    neutral, remind, auto = arms["neutral"], arms["remind"], arms["auto"]
    margins = {
        "remind_minus_neutral_requested": remind["requested"] - neutral["requested"],
        "remind_minus_neutral_booked": remind["booked_correct"]
        - neutral["booked_correct"],
        "auto_minus_neutral_booked": auto["booked_correct"] - neutral["booked_correct"],
        "auto_delivered": auto["delivered_before_book"],
    }
    return {
        "status": "measured",
        "reasons": [],
        "remind": "useful"
        if margins["remind_minus_neutral_requested"] >= 2
        and margins["remind_minus_neutral_booked"] >= 1
        else "not_shown",
        "auto": "helped"
        if margins["auto_minus_neutral_booked"] >= 2
        and margins["auto_delivered"] == len(WORLDS)
        else "not_shown",
        "margins": margins,
    }


def panel(envelope: Row) -> Row:
    cells, controls = collect(envelope, pinned())
    arms = {arm: arm_report(cells, arm) for arm in protocol.ARMS}
    checks = control_report(controls)
    return {
        "schema": SCHEMA,
        "manifest_sha256": MANIFEST_SHA256,
        "generator_commit": heldout.GENERATOR_COMMIT,
        "cells": cell_status(cells),
        "arms": arms,
        "paired": {
            "remind_vs_neutral": paired(cells, "remind"),
            "auto_vs_neutral": paired(cells, "auto"),
        },
        "controls": checks,
        "decision": decision(arms, checks),
        "notes": list(NOTES),
    }


class Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        self.exit(2, "wait-peer panel: invalid arguments; use --help\n")


def main(argv: list[str] | None = None) -> int:
    parser = Parser(
        prog="python -BS -m examples.seatlab.wait_peer.panel",
        description=DESCRIPTION,
        allow_abbrev=False,
    )
    parser.add_argument("input")
    args = parser.parse_args(argv)
    try:
        report = panel(load(args.input))
        sys.stdout.buffer.write(canonical(report) + b"\n")
    except Bad as exc:
        print(f"wait-peer panel: {exc}", file=sys.stderr)
        return 2
    except packet._Invalid as exc:
        print(f"wait-peer panel: manifest: {exc}", file=sys.stderr)
        return 2
    except (ValueError, OSError, KeyError, TypeError, RecursionError):
        print("wait-peer panel: invalid_or_unreadable_input", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
