from typing import Any
from uuid import UUID

from .calendar_inquiry_pair import Record, play_pair
from .calendar_inquiry_protocol import Source
from .calendar_notices import protocol as p
from .calendar_notices.inquiry_summary import _TIMINGS, _validate_pair
from .calendar_notices.team_views import JoinCell
from .visibility import calendar_packet as packet


def _check_outcome(value: object, expected: Record) -> None:
    observed = packet._object(value, *expected)
    if any(
        type(observed[key]) is not type(result) or observed[key] != result
        for key, result in expected.items()
    ):
        raise ValueError("private outcome differs from replay")
    if "days" in observed:
        p._days(observed["days"])


def _replay(source: Source, trace: Record, first_timing: str) -> Record:
    record = packet._object(trace, "turns", "outcomes")
    turns = packet._list(record["turns"])
    if not 1 <= len(turns) <= 5:
        raise ValueError("expected one to five private turns")
    outcomes = packet._object(record["outcomes"], *_TIMINGS)
    index = 0

    def respond(call: str, request: Record) -> Record | None:
        nonlocal index
        if index == len(turns):
            raise ValueError("private trace ends before the next worker call")
        turn = packet._object(turns[index], "call", "timings", "request", "reply")
        if turn["call"] != call or turn["request"] != request:
            raise ValueError("private turn does not match the expected worker call")
        index += 1
        return turn["reply"]

    try:
        replay = play_pair(source, first_timing=first_timing, respond=respond)
    except (TypeError, UnicodeError):
        raise ValueError("invalid private reply envelope") from None
    if index != len(turns) or replay["turns"] != turns:
        raise ValueError("private trace has extra turns or different timing groups")
    for timing in _TIMINGS:
        _check_outcome(outcomes[timing], replay["outcomes"][timing])
    return replay


def _digests(turn: Record | None) -> tuple[str | None, str | None]:
    if turn is None:
        return None, None
    reply = turn["reply"]
    return turn["request"]["prompt_sha256"], (
        None if reply is None else p.digest(reply["reply"])
    )


def _day_choice(turn: Record) -> int:
    return packet._object(packet._json(turn["reply"]["reply"]), "day")["day"]


def _arm(
    source: Source, turns: dict[str, Record], outcome: Record
) -> tuple[Record, int | None]:
    second_prompt, second_reply = _digests(turns.get("second"))
    book_prompt, book_reply = _digests(turns.get("book"))
    second_day = visible = None
    if "book" in turns:
        days = p._days([_day_choice(turns["first"]), _day_choice(turns["second"])])
        second_day = days[1]
        starts = source.revision if p._day(source.target) in days else source.initial
        visible = source.target in starts
        if outcome["status"] != "missing" and (
            outcome["days"] != days or outcome["target_visible"] is not visible
        ):
            raise ValueError("private booking outcome differs from its input")
    return {
        "status": outcome["status"],
        "stage": outcome["stage"],
        "correct": outcome["correct"],
        "target_visible": visible,
        "second_prompt_sha256": second_prompt,
        "second_reply_sha256": second_reply,
        "book_prompt_sha256": book_prompt,
        "book_reply_sha256": book_reply,
    }, second_day


def public_source(
    source: Source, trace: dict[str, Any], *, first_timing: str
) -> dict[str, Any]:
    JoinCell(source.world, source.arm)
    trial = source.trial_uuid
    if not isinstance(trial, str) or str(UUID(trial)) != trial:
        raise ValueError("expected a canonical source trial UUID")
    result = packet._check_digest(source.result_sha256)
    if source._commitment != p.digest(p.wire([trial, result])):
        raise ValueError("source commitment differs from the trial and result")

    replay = _replay(source, trace, first_timing)
    turns: dict[str, dict[str, Record]] = {timing: {} for timing in _TIMINGS}
    for turn in replay["turns"]:
        stage = turn["call"].partition("-")[0]
        for timing in turn["timings"]:
            turns[timing][stage] = turn
    first_prompt, first_reply = _digests(replay["turns"][0])
    immediate, immediate_day = _arm(
        source, turns["immediate"], replay["outcomes"]["immediate"]
    )
    delayed, delayed_day = _arm(source, turns["delayed"], replay["outcomes"]["delayed"])
    same_day = None
    if immediate_day is not None and delayed_day is not None:
        same_day = immediate_day == delayed_day
        shared = turns["immediate"]["book"] is turns["delayed"]["book"]
        if shared != same_day:
            raise ValueError("matching booking inputs must share one worker call")
    row = {
        "world": source.world,
        "donor_arm": source.arm,
        "trial_uuid": trial,
        "result_sha256": result,
        "first_prompt_sha256": first_prompt,
        "first_reply_sha256": first_reply,
        "same_second_day": same_day,
        "arms": {"immediate": immediate, "delayed": delayed},
    }
    _validate_pair(row)
    return row
