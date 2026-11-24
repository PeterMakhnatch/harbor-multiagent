import json
import random
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from examples.seatlab.decision_receipt import attempt_journal
from harbor.trial.hooks import TrialEvent

STARTED = "2026-01-01T00:00:00Z"
FINISHED = "2026-01-01T00:00:05Z"
FULL = (
    ("start", "2026-01-01T00:00:01Z"),
    ("environment-start", "2026-01-01T00:00:02Z"),
    ("agent-start", "2026-01-01T00:00:03Z"),
    ("agent-end", "2026-01-01T00:00:04Z"),
    ("verification-start", "2026-01-01T00:00:04.5Z"),
)
CLOSING = ("end", "2026-01-01T00:00:06.000123Z")


def line(event: str, stamp: str) -> bytes:
    record = {"event": event, "timestamp": stamp}
    return json.dumps(record, separators=(",", ":")).encode() + b"\n"


def journal(*records: tuple[str, str]) -> bytes:
    return b"".join(line(event, stamp) for event, stamp in records)


def check(data: bytes, started: str = STARTED, finished: str = FINISHED) -> str:
    return attempt_journal.check_journal(data, started_at=started, finished_at=finished)


class NativeLine(BaseModel):
    event: TrialEvent
    timestamp: datetime


def test_grammar_covers_every_native_event():
    native = {event.value for event in TrialEvent}
    known = {*attempt_journal.PHASES, attempt_journal.END, attempt_journal.CANCEL}
    assert native == known


def test_native_serialization_is_the_canonical_line():
    stamps = (
        datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 0, 2, 120000, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 0, 3, 7, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 0, 4, 999999, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 0, 4, 999999, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 0, 6, tzinfo=UTC),
    )
    events = [TrialEvent(name) for name in (*attempt_journal.PHASES, "end")]
    data = b"".join(
        NativeLine(event=event, timestamp=stamp)
        .model_dump_json(include={"event", "timestamp"})
        .encode()
        + b"\n"
        for event, stamp in zip(events, stamps)
    )
    assert check(data) == attempt_journal.COMPLETE


@pytest.mark.parametrize("count", sorted(attempt_journal.ENDABLE_PHASE_COUNTS))
def test_each_native_finalized_path_is_complete(count: int):
    assert check(journal(*FULL[:count], CLOSING)) == attempt_journal.COMPLETE


def test_end_may_share_the_finish_instant():
    assert check(journal(*FULL, ("end", FINISHED))) == attempt_journal.COMPLETE


@pytest.mark.parametrize("count", range(len(FULL) + 1))
def test_unfinalized_prefix_is_incomplete(count: int):
    assert check(journal(*FULL[:count])) == attempt_journal.INCOMPLETE


def test_unterminated_end_is_incomplete():
    data = journal(*FULL, CLOSING)
    assert check(data[:-1]) == attempt_journal.INCOMPLETE
    assert check(data[:-9]) == attempt_journal.INCOMPLETE


def ran(data: bytes) -> str:
    return attempt_journal.check_journal(
        data, started_at=STARTED, finished_at=FINISHED, verifier_ran=True
    )


def test_verified_reward_requires_the_full_closed_trace():
    assert ran(journal(*FULL, CLOSING)) == attempt_journal.COMPLETE
    for count in sorted(attempt_journal.ENDABLE_PHASE_COUNTS - {len(FULL)}):
        short = journal(*FULL[:count], CLOSING)
        assert check(short) == attempt_journal.COMPLETE
        assert ran(short) == attempt_journal.CONFLICT


@pytest.mark.parametrize("count", range(len(FULL) + 1))
def test_verified_reward_keeps_open_prefix_incomplete(count: int):
    assert ran(journal(*FULL[:count])) == attempt_journal.INCOMPLETE


def test_verified_reward_keeps_unterminated_end_incomplete():
    assert ran(journal(*FULL, CLOSING)[:-1]) == attempt_journal.INCOMPLETE
    assert ran(journal(*FULL[:2], CLOSING)[:-1]) == attempt_journal.INCOMPLETE


def test_cancelled_attempt_is_conflict():
    cancelled = (*FULL[:4], ("cancel", "2026-01-01T00:00:04.2Z"), CLOSING)
    assert check(journal(*cancelled)) == attempt_journal.CONFLICT
    assert check(journal(*FULL[:4], ("cancel", "2026-01-01T00:00:04.2Z"))) == (
        attempt_journal.CONFLICT
    )


def test_reused_directory_with_earlier_attempt_is_conflict():
    earlier = (
        ("start", "2025-12-31T23:00:01Z"),
        ("environment-start", "2025-12-31T23:00:02Z"),
        ("end", "2025-12-31T23:00:03Z"),
    )
    assert check(journal(*earlier, *FULL, CLOSING)) == attempt_journal.CONFLICT
    assert check(journal(*earlier)) == attempt_journal.CONFLICT


def test_repeated_start_is_conflict():
    again = ("start", "2026-01-01T00:00:01.5Z")
    repeated = journal(FULL[0], again, *FULL[1:], CLOSING)
    assert check(repeated) == attempt_journal.CONFLICT
    assert check(journal(FULL[0], again)) == attempt_journal.CONFLICT


def test_events_after_end_are_conflict():
    later = ("start", "2026-01-01T00:00:07Z")
    assert check(journal(*FULL, CLOSING, later)) == attempt_journal.CONFLICT
    assert check(journal(*FULL, CLOSING, CLOSING)) == attempt_journal.CONFLICT
    assert check(journal(*FULL, CLOSING) + b'{"ev') == attempt_journal.CONFLICT


@pytest.mark.parametrize(
    "records",
    [
        (CLOSING,),
        (FULL[0], CLOSING, *FULL[1:]),
        (*FULL[:3], CLOSING),
        (FULL[0], FULL[2], FULL[3], CLOSING),
        (*FULL[:2], FULL[3], FULL[2], CLOSING),
        (*FULL[:2], FULL[1], CLOSING),
        (*FULL, FULL[4], CLOSING),
        (*FULL[:4], ("agent-start", "2026-01-01T00:00:04.1Z"), CLOSING),
        (FULL[0], ("stop", "2026-01-01T00:00:02Z"), CLOSING),
        (FULL[0], ("START", "2026-01-01T00:00:02Z"), CLOSING),
    ],
)
def test_out_of_order_or_foreign_events_are_conflict(
    records: tuple[tuple[str, str], ...],
):
    assert check(journal(*records)) == attempt_journal.CONFLICT


@pytest.mark.parametrize(
    "records",
    [
        (("start", "2025-12-31T23:59:59.999999Z"), *FULL[1:], CLOSING),
        (*FULL[:4], ("verification-start", "2026-01-01T00:00:05.01Z"), CLOSING),
        (*FULL, ("end", "2026-01-01T00:00:04.9Z")),
        (FULL[0], ("environment-start", "2026-01-01T00:00:00.5Z"), CLOSING),
        (
            *FULL[:2],
            ("agent-start", "2026-01-01T00:00:03Z"),
            ("agent-end", "2026-01-01T00:00:02.5Z"),
            CLOSING,
        ),
    ],
)
def test_timestamps_outside_the_result_window_are_conflict(
    records: tuple[tuple[str, str], ...],
):
    assert check(journal(*records)) == attempt_journal.CONFLICT


@pytest.mark.parametrize(
    "bad",
    [
        b'{"event":"start","timestamp":"2026-01-01T00:00:01Z","event":"start"}\n',
        b'{"event":"start","event":"start","timestamp":"2026-01-01T00:00:01Z"}\n',
        b'{"event":"start","timestamp":"2026-01-01T00:00:01Z","trial":"x"}\n',
        b'{"event":"start","timestamp":"2026-01-01T00:00:01Z","n":NaN}\n',
        b'{"event":"start","timestamp":"2026-01-01T00:00:01Z","n":Infinity}\n',
        b'{"event":"start","timestamp":"2026-01-01T00:00:01Z","n":-Infinity}\n',
        b'{"event":"start","timestamp":"2026-01-01T00:00:01Z","n":1e999}\n',
        b'{"event":"start","timestamp":NaN}\n',
        b'{"event":"start","timestamp":1767225601.0}\n',
        b'{"event":"start"}\n',
        b'{"timestamp":"2026-01-01T00:00:01Z","event":"start"}\n',
        b'{"event": "start", "timestamp": "2026-01-01T00:00:01Z"}\n',
        b'{"event":"start","timestamp":"2026-01-01T00:00:01Z"}\r\n',
        b'\xef\xbb\xbf{"event":"start","timestamp":"2026-01-01T00:00:01Z"}\n',
        b'{"event":"st\\u0061rt","timestamp":"2026-01-01T00:00:01Z"}\n',
        b'{"event":"start","timestamp":"2026-01-01T00:00:01+00:00"}\n',
        b'{"event":"start","timestamp":"2026-01-01T00:00:01"}\n',
        b'{"event":"start","timestamp":"2026-01-01 00:00:01Z"}\n',
        b'{"event":"start","timestamp":"2026-02-30T00:00:01Z"}\n',
        b'{"event":"start","timestamp":"2026-01-01T00:00:01.1234567Z"}\n',
        b'{"event":"start","timestamp":"\xff"}\n',
        b'{"event":["start"],"timestamp":"2026-01-01T00:00:01Z"}\n',
        b'["start","2026-01-01T00:00:01Z"]\n',
        b"null\n",
        b"\n",
        b"[" * 40000 + b"\n",
    ],
)
def test_malformed_lines_are_conflict(bad: bytes):
    assert check(bad) == attempt_journal.CONFLICT
    assert check(journal(*FULL[:2]) + bad) == attempt_journal.CONFLICT


@pytest.mark.parametrize(
    ("started", "finished"),
    [
        ("2026-01-01T00:00:00+00:00", FINISHED),
        (STARTED, "2026-01-01T00:00:05"),
        (STARTED, ""),
        ("None", FINISHED),
        (FINISHED, STARTED),
    ],
)
def test_malformed_result_window_is_conflict(started: str, finished: str):
    assert check(journal(*FULL, CLOSING), started, finished) == attempt_journal.CONFLICT


def test_size_and_line_bounds_are_conflict():
    oversize = journal(*FULL) + b" " * attempt_journal.MAX_JOURNAL_BYTES
    assert check(oversize) == attempt_journal.CONFLICT
    flood = b"\n" * (attempt_journal.MAX_JOURNAL_LINES + 1)
    assert check(flood) == attempt_journal.CONFLICT


def test_seeded_corruption_never_raises_or_echoes():
    rng = random.Random(20261124)
    original = journal(*FULL, CLOSING)
    for _ in range(2000):
        mutated = bytearray(original)
        for _ in range(rng.randint(1, 4)):
            index = rng.randrange(len(mutated))
            mutated[index] = rng.randrange(256)
        verdict = check(bytes(mutated))
        assert verdict in attempt_journal.VERDICTS
        if verdict == attempt_journal.COMPLETE:
            names = [json.loads(row)["event"] for row in bytes(mutated).splitlines()]
            assert names == [*attempt_journal.PHASES, "end"]
