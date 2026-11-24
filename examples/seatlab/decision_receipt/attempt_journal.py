import json
import math
import re
from collections.abc import Sequence
from datetime import datetime
from itertools import pairwise

JOURNAL_NAME = "trial-events.jsonl"
MAX_JOURNAL_BYTES = 65536
MAX_JOURNAL_LINES = 64

COMPLETE = "complete"
INCOMPLETE = "incomplete"
CONFLICT = "conflict"
VERDICTS = (COMPLETE, INCOMPLETE, CONFLICT)

END = "end"
CANCEL = "cancel"
PHASES = (
    "start",
    "environment-start",
    "agent-start",
    "agent-end",
    "verification-start",
)
ENDABLE_PHASE_COUNTS = frozenset((1, 2, 4, 5))
KEYS = frozenset(("event", "timestamp"))
TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", flags=re.ASCII
)

assert END not in PHASES and CANCEL not in PHASES
assert len(set(PHASES)) == len(PHASES)
assert ENDABLE_PHASE_COUNTS <= frozenset(range(1, len(PHASES) + 1))
assert MAX_JOURNAL_LINES > len(PHASES) + 1

Record = tuple[str, datetime]


class _Malformed(Exception):
    pass


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise _Malformed
    return dict(pairs)


def _nonfinite(_: str) -> float:
    raise _Malformed


def _finite(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise _Malformed
    return value


def _instant(text: object) -> datetime | None:
    if type(text) is not str or TIMESTAMP.fullmatch(text) is None:
        return None
    try:
        instant = datetime.fromisoformat(text)
    except ValueError:
        return None
    assert instant.utcoffset() is not None
    return instant


def _record(line: bytes) -> Record | None:
    try:
        value = json.loads(
            line.decode("utf-8"),
            object_pairs_hook=_unique_keys,
            parse_constant=_nonfinite,
            parse_float=_finite,
        )
    except (ValueError, RecursionError, _Malformed):
        return None
    if type(value) is not dict or value.keys() != KEYS:
        return None
    event, stamp = value["event"], value["timestamp"]
    instant = _instant(stamp)
    if type(event) is not str or instant is None:
        return None
    canonical = json.dumps({"event": event, "timestamp": stamp}, separators=(",", ":"))
    if line != canonical.encode("ascii"):
        return None
    return event, instant


def _sequence_verdict(names: Sequence[str], verifier_ran: bool) -> str:
    ended = bool(names) and names[-1] == END
    phases = list(names[:-1] if ended else names)
    if phases != list(PHASES[: len(phases)]):
        return CONFLICT
    if ended and len(phases) not in ENDABLE_PHASE_COUNTS:
        return CONFLICT
    if ended and verifier_ran and len(phases) != len(PHASES):
        return CONFLICT
    return COMPLETE if ended else INCOMPLETE


def _timing_conflict(
    records: Sequence[Record], opened: datetime, closed: datetime
) -> bool:
    instants = [instant for _, instant in records]
    if any(later < earlier for earlier, later in pairwise(instants)):
        return True
    if instants and instants[0] < opened:
        return True
    ended = bool(records) and records[-1][0] == END
    phases = instants[:-1] if ended else instants
    if any(instant > closed for instant in phases):
        return True
    return ended and instants[-1] < closed


def check_journal(
    data: bytes,
    *,
    started_at: str,
    finished_at: str,
    verifier_ran: bool = False,
) -> str:
    assert type(data) is bytes
    assert type(started_at) is str and type(finished_at) is str
    assert type(verifier_ran) is bool
    opened, closed = _instant(started_at), _instant(finished_at)
    if opened is None or closed is None or closed < opened:
        return CONFLICT
    if len(data) > MAX_JOURNAL_BYTES:
        return CONFLICT
    lines = data.split(b"\n")
    tail = lines.pop()
    if len(lines) > MAX_JOURNAL_LINES:
        return CONFLICT
    records: list[Record] = []
    for line in lines:
        record = _record(line)
        if record is None:
            return CONFLICT
        records.append(record)
    verdict = _sequence_verdict([name for name, _ in records], verifier_ran)
    if verdict == CONFLICT or _timing_conflict(records, opened, closed):
        return CONFLICT
    if tail and verdict == COMPLETE:
        return CONFLICT
    if tail:
        return INCOMPLETE
    assert verdict in (COMPLETE, INCOMPLETE)
    return verdict
