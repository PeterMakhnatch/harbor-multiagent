import re
import uuid
from typing import Any, NamedTuple

HOST_SCHEMA = "harbor.seat-cut.host/v1"
WORLD_SCHEMA = "harbor.seat-cut.world/v1"
BOUND_SCHEMA = "harbor.seat-cut.bound/v1"
MAX_SAMPLES = 32
MAX_EVENTS = 24
MAX_REVISION = 2
STATES = ("not_started", "running", "clean", "error")
ACKS = ("pending", "ok", "failed")
OPEN_STATES = frozenset({"not_started", "running"})
TERMINAL_STATES = frozenset({"clean", "error"})
STATE_RANK = {"not_started": 0, "running": 1, "clean": 2, "error": 2}
HOST_KEYS = frozenset(
    {"schema", "trial_id", "ticket", "target", "peer", "closed", "samples"}
)
SAMPLE_KEYS = frozenset({"id", "before", "after", "ack"})
SNAPSHOT_KEYS = frozenset({"target", "peer"})
WORLD_KEYS = frozenset({"schema", "run", "ticket", "marks"})
MARK_KEYS = frozenset({"id", "after_seq", "revision"})
MARK_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
TICKET_PATTERN = re.compile(r"sb-[0-9a-f]{16}")
ROLE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


class SeatCutInvalid(ValueError):
    pass


class Snapshot(NamedTuple):
    target: str
    peer: str


class Sample(NamedTuple):
    mark_id: str
    before: Snapshot
    after: Snapshot | None
    ack: str


class Mark(NamedTuple):
    mark_id: str
    after_seq: int
    revision: int


class Bookends(NamedTuple):
    lower_seqs: list[int]
    upper_seqs: list[int]
    straddles: int
    unacked_marks: int
    peer_open_after_return: bool


def _require_dict(value: Any, keys: frozenset[str], where: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise SeatCutInvalid(f"{where} must be an object")
    if set(value) != keys:
        raise SeatCutInvalid(f"{where} keys must be exactly {sorted(keys)}")
    return value


def _require_list(value: Any, max_len: int, where: str) -> list[Any]:
    if type(value) is not list:
        raise SeatCutInvalid(f"{where} must be a list")
    if len(value) > max_len:
        raise SeatCutInvalid(f"{where} exceeds {max_len} entries")
    return value


def _require_int(value: Any, low: int, high: int, where: str) -> int:
    if type(value) is not int:
        raise SeatCutInvalid(f"{where} must be an int")
    if not low <= value <= high:
        raise SeatCutInvalid(f"{where} must be in [{low}, {high}]")
    return value


def _require_match(value: Any, pattern: re.Pattern[str], where: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise SeatCutInvalid(f"{where} has an invalid format")
    return value


def _require_choice(value: Any, choices: tuple[str, ...], where: str) -> str:
    if type(value) is not str or value not in choices:
        raise SeatCutInvalid(f"{where} must be one of {list(choices)}")
    return value


def _require_uuid(value: Any, where: str, *, version4: bool) -> str:
    if type(value) is not str:
        raise SeatCutInvalid(f"{where} must be a string")
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise SeatCutInvalid(f"{where} is not a UUID") from error
    if str(parsed) != value:
        raise SeatCutInvalid(f"{where} must be a canonical lowercase UUID")
    if version4 and parsed.version != 4:
        raise SeatCutInvalid(f"{where} must be a UUID4")
    return value


def _validate_series(scores: Any, revisions: Any) -> int:
    _require_list(scores, MAX_EVENTS + 1, "scores")
    _require_list(revisions, MAX_EVENTS + 1, "revisions")
    if len(scores) == 0:
        raise SeatCutInvalid("scores must include the empty prefix")
    if len(revisions) != len(scores):
        raise SeatCutInvalid("revisions and scores must have equal length")
    for index, score in enumerate(scores):
        _require_int(score, 0, 1, f"scores[{index}]")
    for index, revision in enumerate(revisions):
        _require_int(revision, 0, MAX_REVISION, f"revisions[{index}]")
    return len(scores) - 1


def _validate_snapshot(value: Any, where: str) -> Snapshot:
    snapshot = _require_dict(value, SNAPSHOT_KEYS, where)
    return Snapshot(
        target=_require_choice(snapshot["target"], STATES, f"{where}.target"),
        peer=_require_choice(snapshot["peer"], STATES, f"{where}.peer"),
    )


def _validate_sample(value: Any, where: str) -> Sample:
    sample = _require_dict(value, SAMPLE_KEYS, where)
    mark_id = _require_match(sample["id"], MARK_ID_PATTERN, f"{where}.id")
    before = _validate_snapshot(sample["before"], f"{where}.before")
    after = None
    if sample["after"] is not None:
        after = _validate_snapshot(sample["after"], f"{where}.after")
    ack = _require_choice(sample["ack"], ACKS, f"{where}.ack")
    if ack == "ok" and after is None:
        raise SeatCutInvalid(f"{where} acknowledged without an after snapshot")
    if ack == "pending" and after is not None:
        raise SeatCutInvalid(f"{where} pending with an after snapshot")
    return Sample(mark_id=mark_id, before=before, after=after, ack=ack)


def _validate_host(host: Any) -> tuple[str, bool, list[Sample]]:
    document = _require_dict(host, HOST_KEYS, "host")
    if document["schema"] != HOST_SCHEMA:
        raise SeatCutInvalid("host schema mismatch")
    _require_uuid(document["trial_id"], "host.trial_id", version4=False)
    ticket = _require_match(document["ticket"], TICKET_PATTERN, "host.ticket")
    target = _require_match(document["target"], ROLE_PATTERN, "host.target")
    peer = _require_match(document["peer"], ROLE_PATTERN, "host.peer")
    if target == peer:
        raise SeatCutInvalid("host target and peer roles must differ")
    closed = document["closed"]
    if type(closed) is not bool:
        raise SeatCutInvalid("host.closed must be a bool")
    raw_samples = _require_list(document["samples"], MAX_SAMPLES, "host.samples")
    samples = [
        _validate_sample(value, f"host.samples[{index}]")
        for index, value in enumerate(raw_samples)
    ]
    if len({sample.mark_id for sample in samples}) != len(samples):
        raise SeatCutInvalid("host sample ids must be unique")
    for index, sample in enumerate(samples):
        if sample.ack != "pending":
            continue
        if index != len(samples) - 1:
            raise SeatCutInvalid("only the last host sample may be pending")
        if closed:
            raise SeatCutInvalid("a closed host cannot hold a pending sample")
    return ticket, closed, samples


def _validate_world(world: Any) -> tuple[str, list[Mark]]:
    document = _require_dict(world, WORLD_KEYS, "world")
    if document["schema"] != WORLD_SCHEMA:
        raise SeatCutInvalid("world schema mismatch")
    _require_uuid(document["run"], "world.run", version4=True)
    ticket = _require_match(document["ticket"], TICKET_PATTERN, "world.ticket")
    raw_marks = _require_list(document["marks"], MAX_SAMPLES, "world.marks")
    marks = []
    for index, value in enumerate(raw_marks):
        where = f"world.marks[{index}]"
        mark = _require_dict(value, MARK_KEYS, where)
        marks.append(
            Mark(
                mark_id=_require_match(mark["id"], MARK_ID_PATTERN, f"{where}.id"),
                after_seq=_require_int(
                    mark["after_seq"], 0, MAX_EVENTS, f"{where}.after_seq"
                ),
                revision=_require_int(
                    mark["revision"], 0, MAX_REVISION, f"{where}.revision"
                ),
            )
        )
    if len({mark.mark_id for mark in marks}) != len(marks):
        raise SeatCutInvalid("world mark ids must be unique")
    return ticket, marks


def _check_progress(previous: str, current: str, where: str) -> None:
    if STATE_RANK[current] < STATE_RANK[previous]:
        raise SeatCutInvalid(f"{where} reverts from {previous} to {current}")
    if previous in TERMINAL_STATES and current != previous:
        raise SeatCutInvalid(f"{where} changes terminal state {previous}")


def _check_lifecycle(
    samples: list[Sample], native_target: str, native_peer: str
) -> None:
    observations = []
    for sample in samples:
        observations.append(sample.before)
        if sample.after is not None:
            observations.append(sample.after)
    finals = Snapshot(target=native_target, peer=native_peer)
    for seat in ("target", "peer"):
        states = [getattr(snapshot, seat) for snapshot in observations]
        states.append(getattr(finals, seat))
        for index in range(1, len(states)):
            _check_progress(states[index - 1], states[index], f"{seat} lifecycle")


def _match_marks(
    samples: list[Sample], marks: list[Mark], revisions: list[int], cut_length: int
) -> dict[str, int]:
    host_index = {sample.mark_id: index for index, sample in enumerate(samples)}
    previous_index = -1
    previous_seq = 0
    ordinals: dict[str, int] = {}
    for mark in marks:
        index = host_index.get(mark.mark_id)
        if index is None:
            raise SeatCutInvalid("world holds a mark the host never issued")
        if index <= previous_index:
            raise SeatCutInvalid("world marks are out of host issue order")
        if mark.after_seq > cut_length:
            raise SeatCutInvalid("world mark ordinal exceeds the final cut")
        if mark.after_seq < previous_seq:
            raise SeatCutInvalid("world mark ordinals are not monotone")
        if revisions[mark.after_seq] != mark.revision:
            raise SeatCutInvalid("world mark revision mismatches its ordinal")
        previous_index = index
        previous_seq = mark.after_seq
        ordinals[mark.mark_id] = mark.after_seq
    for sample in samples:
        if sample.ack == "ok" and sample.mark_id not in ordinals:
            raise SeatCutInvalid("acknowledged sample has no world mark")
    return ordinals


def _collect_bookends(samples: list[Sample], ordinals: dict[str, int]) -> Bookends:
    lower_seqs: list[int] = []
    upper_seqs: list[int] = []
    straddles = 0
    unacked_marks = 0
    peer_open_after_return = False
    for sample in samples:
        snapshots = (
            [sample.before] if sample.after is None else [sample.before, sample.after]
        )
        for snapshot in snapshots:
            if snapshot.target == "clean" and snapshot.peer == "running":
                peer_open_after_return = True
        seq = ordinals.get(sample.mark_id)
        if seq is None:
            continue
        if sample.ack != "ok":
            unacked_marks += 1
        if sample.before.target == "clean":
            upper_seqs.append(seq)
            continue
        if sample.ack != "ok" or sample.after is None:
            continue
        if sample.after.target in OPEN_STATES:
            lower_seqs.append(seq)
        elif sample.after.target == "clean":
            straddles += 1
    return Bookends(
        lower_seqs=lower_seqs,
        upper_seqs=upper_seqs,
        straddles=straddles,
        unacked_marks=unacked_marks,
        peer_open_after_return=peer_open_after_return,
    )


def _counts(
    samples: list[Sample], ordinals: dict[str, int], ends: Bookends
) -> dict[str, int]:
    return {
        "samples": len(samples),
        "ack_ok": sum(1 for sample in samples if sample.ack == "ok"),
        "ack_failed": sum(1 for sample in samples if sample.ack == "failed"),
        "ack_pending": sum(1 for sample in samples if sample.ack == "pending"),
        "marks": len(ordinals),
        "unacked_marks": ends.unacked_marks,
        "lower_bookends": len(ends.lower_seqs),
        "upper_bookends": len(ends.upper_seqs),
        "straddles": ends.straddles,
    }


def _context_flags(
    samples: list[Sample], closed: bool, native_peer: str, ends: Bookends
) -> list[str]:
    flags = []
    if not samples:
        flags.append("no_samples")
    if not closed:
        flags.append("host_open")
    if any(sample.ack == "pending" for sample in samples):
        flags.append("host_interrupted")
    if ends.unacked_marks:
        flags.append("receipt_lost")
    if native_peer in OPEN_STATES:
        flags.append("peer_incomplete")
    return flags


def analyze(
    host: dict[str, Any],
    world: dict[str, Any],
    *,
    native_target: str,
    native_peer: str,
    scores: list[int],
    revisions: list[int],
) -> dict[str, Any]:
    cut_length = _validate_series(scores, revisions)
    target_state = _require_choice(native_target, STATES, "native_target")
    peer_state = _require_choice(native_peer, STATES, "native_peer")
    host_ticket, closed, samples = _validate_host(host)
    world_ticket, marks = _validate_world(world)
    if host_ticket != world_ticket:
        raise SeatCutInvalid("host and world tickets differ")
    _check_lifecycle(samples, target_state, peer_state)
    ordinals = _match_marks(samples, marks, revisions, cut_length)
    ends = _collect_bookends(samples, ordinals)
    result = {
        "schema": BOUND_SCHEMA,
        "status": "no_clean_return",
        "native_target": target_state,
        "native_peer": peer_state,
        "host_closed": closed,
        "cut_length": cut_length,
        "lower_seq": None,
        "upper_seq": None,
        "min_grade": None,
        "max_grade": None,
        "final_grade": scores[cut_length],
        "exact_ordinal": False,
        "grade_identified": False,
        "peer_open_after_return": ends.peer_open_after_return,
        "counts": _counts(samples, ordinals, ends),
        "flags": [],
    }
    flags = _context_flags(samples, closed, peer_state, ends)
    if target_state != "clean":
        flags.insert(
            0, "target_error" if target_state == "error" else "target_incomplete"
        )
        result["flags"] = flags
        return result
    return _bounded(result, flags, ends, scores, cut_length)


def _bounded(
    result: dict[str, Any],
    flags: list[str],
    ends: Bookends,
    scores: list[int],
    cut_length: int,
) -> dict[str, Any]:
    lower_seq = max(ends.lower_seqs) if ends.lower_seqs else 0
    upper_seq = min(ends.upper_seqs) if ends.upper_seqs else cut_length
    if lower_seq > upper_seq:
        raise SeatCutInvalid("lower bookend exceeds upper bookend")
    window = scores[lower_seq : upper_seq + 1]
    exact_ordinal = lower_seq == upper_seq
    grade_identified = min(window) == max(window)
    bound_flags = []
    if not ends.lower_seqs:
        bound_flags.append("no_lower_bookend")
    if not ends.upper_seqs:
        bound_flags.append("no_upper_bookend")
    if not exact_ordinal:
        bound_flags.append("no_prefix_identification")
    if not grade_identified:
        bound_flags.append("no_score_identification")
    result.update(
        {
            "status": "bounded",
            "lower_seq": lower_seq,
            "upper_seq": upper_seq,
            "min_grade": min(window),
            "max_grade": max(window),
            "exact_ordinal": exact_ordinal,
            "grade_identified": grade_identified,
            "flags": bound_flags + flags,
        }
    )
    return result
