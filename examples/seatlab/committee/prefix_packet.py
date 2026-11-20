"""Export and verify replayable prefixes of the registered native sources."""

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal

from . import analysis, contract, recommendation, recommendation_report, schedule

type _JsonObject = dict[str, Any]

_SCHEMA = "seatlab.committee-native-prefix/v1"
_ROLES = schedule.ROLES
_SOURCES = tuple(
    cell
    for cell in schedule.recommendation_grid()
    if cell["arm"] == "masked" and cell["repetition"] == 0
)
_POSTS = 2 * len(_ROLES) + 1
_READY_COUNTS = {"proposal": len(_ROLES), "recommend": _POSTS - 1, "vote": _POSTS}
_SEED = 42
# The source seat polls at most once per second for 120 seconds per phase.
_MAX_EVENTS = 2048
_MAX_PACKET_BYTES = 4 * 1024 * 1024
_PACKET_FIELDS = {"schema", "task_git_tree", "sources"}
_SOURCE_FIELDS = {*schedule.Cell.__required_keys__, "chair"}
_EVENT_FIELDS = {
    "seq",
    "task_family",
    "condition",
    "seed",
    "event_type",
    "actor",
    "authenticated",
    "visibility",
    "recipients",
    "payload",
}
_STAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:Z|\+00:00)"
)
_NO_SOURCE = "source_not_accepted"
_NO_SPEND = "source_spend_unavailable"
_CHANGED = "source_audit_not_bound"
_INVALID = "prefix_invalid"
_NOT_SERIAL = "prefix_not_serial"
_REASONS = frozenset({_NO_SOURCE, _NO_SPEND, _CHANGED, _INVALID, _NOT_SERIAL})


@dataclass(frozen=True, slots=True)
class Step:
    role: str
    kind: Literal["dossier", "read", "post"]
    seq: int
    body: _JsonObject | None
    expected: _JsonObject | None


@dataclass(frozen=True, slots=True)
class Checkpoint:
    map_index: int
    chair: str
    prefix_sha256: str
    events: tuple[_JsonObject, ...]
    steps: tuple[Step, ...]
    board: _JsonObject


class _NotSerial(ValueError):
    """An authentic audit order cannot reproduce the original API replies."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and analysis._DIGEST.fullmatch(value) is not None


def _truth(role: str, read_seq: int) -> _JsonObject:
    return {
        "read_seq": read_seq,
        "shared_ids": list(analysis._SHARED),
        "private_ids": [analysis._OWN[role]],
    }


def check_dossier(role: str, reply: _JsonObject) -> None:
    """Check the native structure and projected facts, not uncaptured prose."""
    if role not in _ROLES:
        raise ValueError("invalid dossier role")
    expected = recommendation_report._dossier(role)
    actual_facts = contract._dossier_facts(reply)
    if reply["seat"] != role or actual_facts != contract._dossier_facts(expected):
        raise ValueError("dossier fact projection mismatch")
    for group in ("shared_facts", "private_facts"):
        if [row["id"] for row in reply[group]] != [
            row["id"] for row in expected[group]
        ]:
            raise ValueError("dossier fact order mismatch")
        for row in reply[group]:
            # The pin fixes the structured fact category. The model sees only
            # the shared/private provenance, numeric points, and blind aliases.
            kind = (
                "positive_criterion"
                if group == "shared_facts"
                else "critical_flaw"
                if row["points"] < 0
                else "major_strength"
            )
            if row["kind"] != kind:
                raise ValueError("dossier fact category mismatch")


def _post_phase(role: str, text: object) -> str:
    if not isinstance(text, str) or len(text) > recommendation._MAX_POST_CHARS:
        raise ValueError("invalid prefix post text")
    _, aliases = contract._mapping(0)
    prefix, _, payload = text.partition(" ")
    if prefix in ("PROPOSAL", "RECOMMEND"):
        ballot = recommendation._blind_ballot(
            contract._read_json(payload, contract._VOTE_FIELDS, "prefix ballot"),
            set(aliases.values()),
        )
        if text != prefix + " " + contract._compact_json(ballot):
            raise ValueError("noncanonical prefix ballot")
        return "proposal" if prefix == "PROPOSAL" else "recommend"
    fact = contract._read_json(text, recommendation._FACT_FIELDS, "prefix fact")
    fact_id = fact["id"]
    if not isinstance(fact_id, str) or fact_id not in aliases:
        raise ValueError("invalid prefix fact ID")
    expected = contract.share_action(
        contract._compact_json({"fact": aliases[fact_id]}),
        recommendation_report._dossier(role),
        0,
    )
    if text != expected["text"]:
        raise ValueError("prefix fact does not match the stock dossier")
    return "share"


def _event(value: object) -> _JsonObject:
    if not isinstance(value, dict) or value.keys() != _EVENT_FIELDS:
        raise ValueError("invalid native event fields")
    seq, kind, role, payload = (
        value["seq"],
        value["event_type"],
        value["actor"],
        value["payload"],
    )
    recipients, scope, authenticated = (
        value["recipients"],
        value["visibility"],
        value["authenticated"],
    )
    if (
        type(seq) is not int
        or not 1 <= seq <= _MAX_EVENTS
        or type(value["seed"]) is not int
        or value["seed"] != _SEED
        or value["task_family"] != analysis._FAMILY
        or value["condition"] != analysis._CONDITION
        or not isinstance(payload, dict)
    ):
        raise ValueError("invalid native event envelope")
    if kind in ("world_started", "dossier_truth"):
        if role != "world" or scope != "world_private" or authenticated is not False:
            raise ValueError("invalid world event identity")
        if kind == "world_started":
            if (
                seq != 1
                or recipients != []
                or payload != {"mode": "hidden_profile", "seats": list(_ROLES)}
            ):
                raise ValueError("invalid world start")
        elif (
            not isinstance(recipients, list)
            or len(recipients) != 1
            or recipients[0] not in _ROLES
            or type(payload.get("read_seq")) is not int
            or payload != _truth(recipients[0], seq - 1)
            or seq < 3
        ):
            raise ValueError("invalid dossier truth event")
        return value

    public = kind == "chat_message"
    if (
        role not in _ROLES
        or authenticated is not True
        or scope != ("public" if public else "world_audit")
        or recipients != (list(_ROLES) if public else [])
    ):
        raise ValueError("invalid seat event identity")
    if public:
        if payload.keys() != {"seat", "text"} or payload["seat"] != role:
            raise ValueError("invalid native post fields")
        _post_phase(role, payload["text"])
    elif kind == "dossier_read":
        if payload:
            raise ValueError("invalid dossier read fields")
    elif kind == "message_read":
        refs = payload.get("msg_seqs")
        if (
            payload.keys() != {"channel", "msg_seqs"}
            or payload["channel"] != "public"
            or not isinstance(refs, list)
            or len(refs) > _POSTS
            or any(type(ref) is not int or not 1 < ref < seq for ref in refs)
            or any(a >= b for a, b in pairwise(refs))
        ):
            raise ValueError("invalid native GET references")
    elif kind == "vote":
        if (
            payload.keys() != {"seat", "choice", "cited_facts"}
            or payload["seat"] != role
        ):
            raise ValueError("invalid native vote fields")
        recommendation.blind_vote(
            {"choice": payload["choice"], "cited_facts": payload["cited_facts"]}, 0
        )
    elif kind == "seat_done":
        if payload != {"seat": role}:
            raise ValueError("invalid native completion fields")
    else:
        raise ValueError("unrecognized native event")
    return value


def normalize_event(event: object) -> _JsonObject:
    """Check a stock event and return every original field except its clock."""
    if not isinstance(event, dict) or event.keys() != _EVENT_FIELDS | {"ts"}:
        raise ValueError("invalid native audit fields")
    ts = event["ts"]
    if not isinstance(ts, str) or not _STAMP.fullmatch(ts):
        raise ValueError("invalid native timestamp")
    try:
        datetime.fromisoformat(ts)
    except ValueError:
        raise ValueError("invalid native timestamp") from None
    return _event({key: value for key, value in event.items() if key != "ts"})


def _checkpoint(source: schedule.Cell, events: list[_JsonObject]) -> Checkpoint:
    index = source["map_index"]
    chair = schedule.chair(index, 0)
    if not 1 <= len(events) <= _MAX_EVENTS:
        raise ValueError("invalid prefix event count")
    if _event(events[0])["event_type"] != "world_started":
        raise ValueError("missing initial native world event")
    messages: list[_JsonObject] = []
    refs: list[int] = []
    steps: list[Step] = []
    stages: dict[str, str] = {}
    ready: set[str] = set()
    pending: tuple[str, int] | None = None
    for seq, row in enumerate(events[1:], 2):
        event = _event(row)
        kind, role, payload = event["event_type"], event["actor"], event["payload"]
        if event["seq"] != seq or len(messages) == _POSTS:
            raise ValueError("invalid event sequence or event after prefix cut")
        if not isinstance(role, str) or (role != "world" and role not in _ROLES):
            raise ValueError("invalid native event actor")
        if pending is not None:
            reader, read_seq = pending
            if kind != "dossier_truth" or event["recipients"] != [reader]:
                raise _NotSerial("interleaved dossier audit")
            stages[reader] = "share"
            steps.append(Step(reader, "dossier", read_seq, None, None))
            pending = None
        elif kind == "dossier_read" and role not in stages:
            pending = (role, seq)
        elif kind == "message_read" and stages.get(role) in (
            "proposal",
            "recommend",
            "vote",
        ):
            if payload["msg_seqs"] != refs:
                raise _NotSerial("native GET omitted an earlier accepted post")
            if role in ready:
                raise ValueError("extra GET after the first ready snapshot")
            if len(messages) >= _READY_COUNTS[stages[role]]:
                ready.add(role)
            steps.append(
                Step(
                    role,
                    "read",
                    seq,
                    None,
                    recommendation_report._snapshot(messages.copy()),
                )
            )
        elif kind == "chat_message":
            text = payload["text"]
            phase = _post_phase(role, text)
            if stages.get(role) != phase or (phase != "share" and role not in ready):
                raise ValueError("prefix post did not follow its authenticated reads")
            if phase == "share":
                stages[role] = "proposal"
            elif phase == "proposal" and role == chair:
                stages[role] = "recommend"
            else:
                stages[role] = "vote"
            ready.discard(role)
            message = payload | {"seq": seq}
            refs.append(seq)
            messages.append(message)
            steps.append(Step(role, "post", seq, {"text": text}, message))
        else:
            raise ValueError("invalid action before prefix cut")
    board = recommendation_report._snapshot(messages)
    view = recommendation.board(board, index, chair)
    if (
        pending is not None
        or stages != dict.fromkeys(_ROLES, "vote")
        or len(messages) != _POSTS
        or len(view.facts["messages"]) != len(_ROLES)
        or view.proposals.keys() != set(_ROLES)
        or view.recommendation is None
    ):
        raise ValueError("incomplete native prefix")
    return Checkpoint(index, chair, _digest(events), tuple(events), tuple(steps), board)


def _identity(source: schedule.Cell) -> _JsonObject:
    return dict(source) | {"chair": schedule.chair(source["map_index"], 0)}


def _read_source(root: Path, source: schedule.Cell) -> list[_JsonObject]:
    cell_id = source["cell_id"]
    trial = analysis._path(
        root, f"cells/{cell_id}/trials/committee-{cell_id}", directory=True
    )
    events = analysis._events(trial)
    if not 1 <= len(events) <= _MAX_EVENTS:
        raise ValueError("invalid native audit event count")
    return events


def _export_checkpoint(source: schedule.Cell, raw: list[_JsonObject]) -> Checkpoint:
    events = []
    posts = 0
    for seq, row in enumerate(raw, 1):
        event = normalize_event(row)
        if event["seq"] != seq:
            raise ValueError("invalid original event sequence")
        kind = event["event_type"]
        if posts < _POSTS:
            events.append(event)
            posts += kind == "chat_message"
        elif kind not in ("message_read", "vote", "seat_done"):
            raise ValueError("unexpected original event after prefix cut")
    return _checkpoint(source, events)


def _blind_projection(ballot: _JsonObject) -> _JsonObject:
    return {"choice": ballot["choice"], "cited": ballot["cited"]}


def _bind_report(
    source: schedule.Cell, scored: _JsonObject, checkpoint: Checkpoint
) -> None:
    # The report has already joined the exact native path, receipts and paid
    # calls. The extracted shares and ballots must match that admitted source.
    index, chair = checkpoint.map_index, checkpoint.chair
    if (
        _canonical({key: scored[key] for key in source}) != _canonical(source)
        or scored["chair"] != recommendation_report._SEATS[chair]
        or type(scored["settled_direct_calls"]) is not int
        or scored["settled_direct_calls"] != 3 * len(_ROLES) + 1
    ):
        raise ValueError("native source identity mismatch")
    view = recommendation.board(checkpoint.board, index, chair)
    _, aliases = contract._mapping(index)
    seats = dict(zip(_ROLES, scored["seats"], strict=True))
    if view.recommendation != _blind_projection(scored["recommendation"]):
        raise ValueError("original recommendation mismatch")
    for message in view.facts["messages"]:
        role = message["seat"]
        fact_id = analysis._parse(message["text"])["id"]
        seat = seats[role]
        if (
            seat["seat"] != recommendation_report._SEATS[role]
            or seat["is_chair"] is not (role == chair)
            or seat["share"]["fact"] != aliases[fact_id]
            or _blind_projection(seat["proposal"]) != view.proposals[role]
        ):
            raise ValueError("original public action mismatch")


def export_packet(root: Path, *, spend_dir: Path) -> _JsonObject:
    """Admit only the registered sources and keep no original outcomes."""
    try:
        if not stat.S_ISDIR(root.lstat().st_mode):
            raise ValueError("original study directory unavailable")
        root = root.resolve(strict=True)
        # Detect a replaced archive between the report's admission and extraction.
        original = {}
        for source in _SOURCES:
            try:
                original[source["cell_id"]] = _digest(_read_source(root, source))
            except (analysis._Excluded, OSError, ValueError, RecursionError):
                continue
        report = recommendation_report.analyze(root, spend_dir=spend_dir)
    except (analysis._Excluded, OSError, RecursionError):
        raise ValueError("original study evidence unavailable") from None

    scored = {cell["cell_id"]: cell for cell in report["cells"]}
    rows = []
    for source in _SOURCES:
        identity = _identity(source)
        cell_id = source["cell_id"]
        reason = None
        if report["spend_evidence"] != "available":
            reason = _NO_SPEND
        elif cell_id not in scored:
            reason = _NO_SOURCE
        else:
            try:
                raw = _read_source(root, source)
                if original.get(cell_id) != _digest(raw):
                    raise ValueError
            except (analysis._Excluded, OSError, ValueError, RecursionError):
                reason = _CHANGED
            else:
                try:
                    checkpoint = _export_checkpoint(source, raw)
                    _bind_report(source, scored[cell_id], checkpoint)
                except _NotSerial:
                    reason = _NOT_SERIAL
                except (KeyError, TypeError, ValueError, RecursionError):
                    reason = _INVALID
                else:
                    rows.append(
                        identity
                        | {
                            "events": list(checkpoint.events),
                            "prefix_sha256": checkpoint.prefix_sha256,
                        }
                    )
        if reason is not None:
            rows.append(identity | {"reason": reason})
    return {"schema": _SCHEMA, "task_git_tree": schedule.TASK_TREE, "sources": rows}


def load(path: Path, expected_sha256: str) -> dict[int, Checkpoint | None]:
    """Verify the externally bound file and derive the serial API steps."""
    if not _is_digest(expected_sha256):
        raise ValueError("invalid expected packet SHA-256")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("invalid packet path")
            raw = stream.read(_MAX_PACKET_BYTES + 1)
    except OSError:
        raise ValueError("packet unreadable") from None
    if len(raw) > _MAX_PACKET_BYTES:
        raise ValueError("packet exceeds size limit")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("packet SHA-256 mismatch")
    try:
        packet = contract._read_json(
            raw.decode("ascii"), _PACKET_FIELDS, "prefix packet"
        )
        if raw != _canonical(packet):
            raise ValueError("prefix packet is not canonical JSON")
    except (UnicodeError, RecursionError, OverflowError):
        raise ValueError("invalid prefix packet JSON") from None
    rows = packet["sources"]
    if (
        packet["schema"] != _SCHEMA
        or packet["task_git_tree"] != schedule.TASK_TREE
        or not isinstance(rows, list)
        or len(rows) != len(_SOURCES)
    ):
        raise ValueError("invalid prefix packet version, task or source count")
    checkpoints = {}
    for row, source in zip(rows, _SOURCES, strict=True):
        if (
            not isinstance(row, dict)
            or not _SOURCE_FIELDS <= row.keys()
            or _canonical({key: row[key] for key in _SOURCE_FIELDS})
            != _canonical(_identity(source))
        ):
            raise ValueError("unregistered prefix source identity")
        index = source["map_index"]
        if row.keys() == _SOURCE_FIELDS | {"reason"}:
            if not isinstance(row["reason"], str) or row["reason"] not in _REASONS:
                raise ValueError("invalid source exclusion reason")
            checkpoints[index] = None
        elif row.keys() == _SOURCE_FIELDS | {"events", "prefix_sha256"}:
            events, digest = row["events"], row["prefix_sha256"]
            if not isinstance(events, list) or not _is_digest(digest):
                raise ValueError("invalid source checkpoint fields")
            checkpoint = _checkpoint(source, events)
            if digest != checkpoint.prefix_sha256:
                raise ValueError("prefix SHA-256 mismatch")
            checkpoints[index] = checkpoint
        else:
            raise ValueError("invalid source packet fields")
    return checkpoints


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    exporter = sub.add_parser("export")
    exporter.add_argument("study", type=Path)
    exporter.add_argument("--spend-dir", type=Path, required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("packet", type=Path)
    verify.add_argument("--expect-sha256", required=True)
    args = parser.parse_args()
    try:
        if args.command == "export":
            sys.stdout.buffer.write(
                _canonical(export_packet(args.study, spend_dir=args.spend_dir))
            )
        else:
            checkpoints = load(args.packet, args.expect_sha256)
            eligible = sum(
                checkpoint is not None for checkpoint in checkpoints.values()
            )
            print(
                json.dumps(
                    {
                        "eligible_sources": eligible,
                        "ineligible_sources": len(checkpoints) - eligible,
                    }
                )
            )
    except ValueError:
        parser.error("prefix packet command failed validation")


if __name__ == "__main__":
    main()
