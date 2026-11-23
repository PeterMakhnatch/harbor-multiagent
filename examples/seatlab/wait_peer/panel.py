import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, TypeGuard, override
from uuid import UUID

from ..calendar_notices import heldout
from ..visibility import agy_seat
from ..visibility import calendar_packet as packet
from . import campaign, cohort, protocol, receipt, worker
from .receipt import Bad, Row, canonical, need, same

SCHEMA = "seatlab.wait-peer-panel/v1"
INPUT_SCHEMA = "seatlab.wait-peer-rows/v1"
DESCRIPTION = (
    "Score public wait-peer receipt rows for one 8-world panel. "
    "Read INPUT.json or '-' for stdin, or the bound cell receipts of a live "
    "--from-campaign directory plus optional --control-receipt files. "
    "--manifest/--sha256 select an operator cohort; any digest other than the "
    "registered one is exploratory. Exit 2 on any contradiction."
)
MAX_INPUT = 1 << 23
CELLS = protocol.COUNT * len(protocol.ARMS)
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
EVIDENCE = "exploratory_unregistered_manifest"
EXPLORATORY_NOTE = "This operator manifest is exploratory; it is not the registered panel or its decision."


@dataclass(frozen=True, slots=True)
class Scope:
    sha256: str
    first: int
    pins: tuple[tuple[str, str], ...]

    @property
    def worlds(self) -> range:
        return range(self.first, self.first + len(self.pins))

    @property
    def registered(self) -> bool:
        return self.sha256 == cohort.PUBLISHED_SHA256

    def pin(self, world: int) -> tuple[str, str]:
        return self.pins[world - self.first]

    def order(self, world: int) -> tuple[str, ...]:
        return protocol.ordered_arms(world, self.first)


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


def scoped(manifest: Path, sha256: str) -> Scope:
    study, rows = cohort.load(manifest, sha256)
    need(
        study.last - study.first + 1 == protocol.COUNT == len(rows),
        "manifest_world_count",
    )
    return Scope(sha256, study.first, tuple(rows))


def checked_identity(row: Row, live: bool, scope: Scope) -> None:
    world, arm, tag, position = (
        row[k] for k in ("world", "arm", "tag", "arm_position")
    )
    need(row["schema"] == receipt.SCHEMA, "row_schema")
    need(type(world) is int and world in scope.worlds, "row_world")
    need(isinstance(arm, str) and arm in protocol.ARMS, "row_arm")
    need(
        isinstance(tag, str) and (tag == LIVE if live else tag in CONTROL_TAGS),
        "row_tag",
    )
    need(
        type(position) is int and position == scope.order(world).index(arm),
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


def checked_source(row: Row, scope: Scope) -> None:
    source, inputs = row["source"], row["inputs"]
    need(isinstance(source, dict) and source.keys() == SOURCE_KEYS, "source_shape")
    need(
        source["generator_commit"] == heldout.GENERATOR_COMMIT
        and source["manifest_verified"] is True
        and source["manifest_sha256"] == scope.sha256
        and isinstance(inputs, dict)
        and inputs.get("manifest") == scope.sha256,
        "source_manifest",
    )
    need(
        (source["calendar_sha256"], source["stock_semantics_sha256"])
        == scope.pin(row["world"]),
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


def checked_row(value: object, live: bool, scope: Scope) -> Row:
    if not row_object(value):
        raise Bad("row_shape")
    checked_identity(value, live, scope)
    checked_source(value, scope)
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


def bound_receipt(path: Path) -> object:
    raw = campaign.owned(path, worker.MAX_RECEIPT_BYTES)
    try:
        value = packet._json(raw)
    except packet._Invalid:
        raise Bad("receipt_json") from None
    need(raw == canonical(value) + b"\n", "receipt_noncanonical")
    return value


def live_receipts(raw: Path, scope: Scope) -> list[object]:
    root = campaign.campaign_dir(raw, create=False)
    reg = campaign.registered(root)
    if reg is None:
        raise Bad("campaign_unregistered")
    need(reg["tag"] == LIVE, "campaign_not_live")
    need(reg["sha256"] == scope.sha256, "campaign_manifest_mismatch")
    need(campaign.stored_worlds(root, scope.sha256) == scope.worlds, "campaign_cohort")
    rows = []
    for world in scope.worlds:
        for arm in scope.order(world):
            state = campaign.cell_state(root, reg, world, arm)
            need(state not in ("mismatch", "unmarked"), "campaign_cell_mismatch")
            if state in ("complete", "incomplete"):
                cell = root / campaign.name(world, arm)
                rows.append(bound_receipt(cell / campaign.RECEIPT))
    need(len(rows) <= CELLS, "input_size")
    return rows


def campaign_envelope(raw: Path, controls: list[Path], scope: Scope) -> Row:
    need(len(controls) <= len(CONTROLS), "input_size")
    return {
        "schema": INPUT_SCHEMA,
        "rows": live_receipts(raw, scope),
        "controls": [bound_receipt(path.absolute()) for path in controls],
    }


def collect(
    envelope: Row, scope: Scope
) -> tuple[dict[tuple[int, str], Row], dict[str, Row]]:
    cells: dict[tuple[int, str], Row] = {}
    controls: dict[str, Row] = {}
    uuids: set[str] = set()
    worlds: dict[int, Row] = {}
    entries = [(v, True) for v in envelope["rows"]]
    entries += [(v, False) for v in envelope["controls"]]
    for value, live in entries:
        row = checked_row(value, live, scope)
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


def arm_report(cells: dict[tuple[int, str], Row], arm: str, scope: Scope) -> Row:
    present = [cells[(w, arm)] for w in scope.worlds if (w, arm) in cells]
    done = [row for row in present if row["complete"]]

    def count(key: str) -> int:
        return sum(row[key] is True for row in done)

    return {
        "expected": protocol.COUNT,
        "present": len(present),
        "complete": len(done),
        "coverage": f"{len(done)}/{protocol.COUNT}",
        "requested": count("did_request"),
        "delivered_before_book": count("delivered"),
        "booked_correct": count("booked_correct"),
        "reports_exact": count("report_exact"),
        "client_reported_tokens": client_tokens(present),
    }


def cell_status(cells: dict[tuple[int, str], Row], scope: Scope) -> list[Row]:
    rows = []
    for world in scope.worlds:
        for arm in scope.order(world):
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


def paired(cells: dict[tuple[int, str], Row], arm: str, scope: Scope) -> Row:
    worlds, scores = [], []
    totals = {"requested": 0, "delivered": 0, "score": 0}
    for world in scope.worlds:
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
        "totals": totals if len(scores) == protocol.COUNT else None,
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


def decision(arms: Row, controls: Row, registered: bool) -> Row:
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
        "status": "measured" if registered else "exploratory",
        "reasons": [],
        "remind": "useful"
        if margins["remind_minus_neutral_requested"] >= 2
        and margins["remind_minus_neutral_booked"] >= 1
        else "not_shown",
        "auto": "helped"
        if margins["auto_minus_neutral_booked"] >= 2
        and margins["auto_delivered"] == protocol.COUNT
        else "not_shown",
        "margins": margins,
    }


def panel(envelope: Row, scope: Scope) -> Row:
    cells, controls = collect(envelope, scope)
    arms = {arm: arm_report(cells, arm, scope) for arm in protocol.ARMS}
    checks = control_report(controls)
    report: Row = {
        "schema": SCHEMA,
        "manifest_sha256": scope.sha256,
        "generator_commit": heldout.GENERATOR_COMMIT,
        "cells": cell_status(cells, scope),
        "arms": arms,
        "paired": {
            "remind_vs_neutral": paired(cells, "remind", scope),
            "auto_vs_neutral": paired(cells, "auto", scope),
        },
        "controls": checks,
        "decision": decision(arms, checks, scope.registered),
        "notes": list(NOTES),
    }
    if not scope.registered:
        report["evidence"] = EVIDENCE
        report["notes"].append(EXPLORATORY_NOTE)
    return report


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
    parser.add_argument("input", nargs="?")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--from-campaign", type=Path)
    parser.add_argument("--control-receipt", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    if (
        (args.manifest is None) != (args.sha256 is None)
        or (args.input is None) == (args.from_campaign is None)
        or (args.input is not None and bool(args.control_receipt))
        or len(args.control_receipt) > len(CONTROLS)
    ):
        parser.error("arguments")
    manifest, sha256 = (
        (cohort.PUBLISHED_MANIFEST, cohort.PUBLISHED_SHA256)
        if args.manifest is None
        else (args.manifest, args.sha256)
    )
    try:
        scope = scoped(manifest, sha256)
        envelope = (
            load(args.input)
            if args.from_campaign is None
            else campaign_envelope(args.from_campaign, args.control_receipt, scope)
        )
        report = panel(envelope, scope)
        sys.stdout.buffer.write(canonical(report) + b"\n")
    except Bad as exc:
        print(f"wait-peer panel: {exc}", file=sys.stderr)
        return 2
    except worker.Refused as exc:
        print(f"wait-peer panel: {exc.reason}", file=sys.stderr)
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
