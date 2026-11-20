"""Offline visibility projections and counts for recorded seat events."""

import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

MessagePair = tuple[str, str]
ReceiptKind = Literal["available", "fetch", "bound"]
EventKind = Literal["send", "available", "fetch", "bound", "action"]
Coverage = Literal["complete", "partial", "unavailable"]
ActionTiming = Literal["before_action", "after_action", "no_known_action"]
EvidenceStatus = Literal["changed", "no_channel_traffic", "no_demonstrated_delivery"]

_RECEIPTS: tuple[ReceiptKind, ...] = ("available", "fetch", "bound")
_EVENT_KINDS: tuple[EventKind, ...] = (*_RECEIPTS, "send", "action")
_COVERAGE: tuple[Coverage, ...] = ("complete", "partial", "unavailable")
_TIMING: tuple[ActionTiming, ...] = ("before_action", "after_action", "no_known_action")
_STATUS: tuple[EvidenceStatus, ...] = (
    "changed",
    "no_channel_traffic",
    "no_demonstrated_delivery",
)


@dataclass(frozen=True)
class Event:
    kind: EventKind
    seat: str
    messages: tuple[MessagePair, ...] = ()


@dataclass(frozen=True)
class Trial:
    """A native roster and events in source order within each seat."""

    key: str = field(repr=False)
    arm: str
    seats: tuple[str, ...]
    events: tuple[Event, ...] = field(repr=False)
    coverage: dict[str, Coverage]
    finished: bool | None = None
    sources: tuple[Path, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class MessageCounts:
    events: int
    message_occurrences: int
    unique_messages: int


@dataclass(frozen=True)
class ReceiptCounts(MessageCounts):
    """Affected event counts by timing, in addition to message counts."""

    before_action: int
    after_action: int
    no_known_action: int


@dataclass(frozen=True)
class FirstAffectedReceipt:
    """First affected receipt at a zero-based position in the stored stream."""

    event_index: int
    kind: ReceiptKind
    timing: ActionTiming

    @property
    def followed_by_action(self) -> bool:
        return self.timing == "before_action"


@dataclass(frozen=True)
class SeatMeasurement:
    seat: str
    coverage: Coverage
    status: EvidenceStatus
    action_events: int
    sent: MessageCounts
    receipts: dict[ReceiptKind, ReceiptCounts]
    first_affected: FirstAffectedReceipt | None


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate(trial: Trial) -> set[str]:
    if not _nonblank(trial.key):
        raise ValueError("trial key must be a nonblank string")
    if (
        not isinstance(trial.arm, str)
        or len(trial.arm) > 64
        or not re.fullmatch(r"[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*", trial.arm)
    ):
        raise ValueError("trial arm must be a short, controlled label")
    if (
        not isinstance(trial.seats, tuple)
        or not trial.seats
        or not all(_nonblank(seat) for seat in trial.seats)
    ):
        raise ValueError("trial roster must be a nonempty ordered tuple of seat strings")
    roster = set(trial.seats)
    if len(roster) != len(trial.seats):
        raise ValueError("trial roster contains duplicate seats")
    if not isinstance(trial.coverage, dict) or trial.coverage.keys() != roster:
        raise ValueError("trial coverage must have one entry for every roster seat")
    if any(value not in _COVERAGE for value in trial.coverage.values()):
        raise ValueError("trial coverage must be complete, partial, or unavailable")
    if trial.finished is not None and not isinstance(trial.finished, bool):
        raise ValueError("trial finished must be a boolean or None")
    if not isinstance(trial.events, tuple):
        raise ValueError("trial events must be an ordered tuple")
    for index, event in enumerate(trial.events):
        if not isinstance(event, Event):
            raise ValueError(f"event {index}: expected an Event")
        if not isinstance(event.kind, str) or event.kind not in _EVENT_KINDS:
            raise ValueError(f"event {index}: unknown event kind")
        if not isinstance(event.seat, str) or event.seat not in roster:
            raise ValueError(f"event {index}: event seat is not in the roster")
        if not isinstance(event.messages, tuple):
            raise ValueError(f"event {index}: messages must be an ordered tuple")
        for pair in event.messages:
            if not isinstance(pair, tuple) or len(pair) != 2 or not all(map(_nonblank, pair)):
                raise ValueError(f"event {index}: expected a nonblank sender/message pair")
    return roster


def _is_peer(sender: str, recipient: str, roster: set[str]) -> bool:
    return sender != recipient and sender in roster


def project(trial: Trial, muted_seat: str | None = None) -> Trial:
    """Return the control or mute incoming peers in one seat's receipt events."""
    roster = _validate(trial)
    if muted_seat is None:
        return trial
    if not isinstance(muted_seat, str) or muted_seat not in roster:
        raise ValueError("muted seat is not in the trial roster")

    projected = []
    changed = False
    for event in trial.events:
        if event.seat == muted_seat and event.kind in _RECEIPTS:
            messages = tuple(
                pair for pair in event.messages if not _is_peer(pair[0], muted_seat, roster)
            )
            if len(messages) != len(event.messages):
                event = replace(event, messages=messages)
                changed = True
        projected.append(event)
    return replace(trial, events=tuple(projected)) if changed else trial


@dataclass
class _Tally:
    events: int = 0
    occurrences: int = 0
    unique: set[MessagePair] = field(default_factory=set)
    timing: Counter[ActionTiming] = field(default_factory=Counter)

    def add(self, messages: tuple[MessagePair, ...]) -> None:
        self.events += 1
        self.occurrences += len(messages)
        self.unique.update(messages)

    def sent(self) -> MessageCounts:
        return MessageCounts(self.events, self.occurrences, len(self.unique))

    def receipts(self) -> ReceiptCounts:
        return ReceiptCounts(
            self.events,
            self.occurrences,
            len(self.unique),
            self.timing["before_action"],
            self.timing["after_action"],
            self.timing["no_known_action"],
        )


def _evidence_status(
    *, changed: bool, channel_traffic: bool, complete: bool
) -> EvidenceStatus:
    if changed:
        return "changed"
    if complete and not channel_traffic:
        return "no_channel_traffic"
    return "no_demonstrated_delivery"


def measure_seats(trial: Trial) -> Iterator[SeatMeasurement]:
    """Yield one mute measurement per seat, including seats without events.

    Receipt counts describe peer tuples the projection removes. An event is
    before_action if a later task action is recorded for that seat. An event
    after the last recorded action is after_action. An event in a seat with
    no recorded action is no_known_action, regardless of receipt coverage.
    """
    roster = _validate(trial)
    yield from _measure_seats(trial, roster)


def _measure_seats(trial: Trial, roster: set[str]) -> Iterator[SeatMeasurement]:
    action_events: Counter[str] = Counter()
    last_action: dict[str, int] = {}
    for index, event in enumerate(trial.events):
        if event.kind == "action":
            action_events[event.seat] += 1
            last_action[event.seat] = index

    sent = {seat: _Tally() for seat in trial.seats}
    receipts = {seat: {kind: _Tally() for kind in _RECEIPTS} for seat in trial.seats}
    first: dict[str, FirstAffectedReceipt] = {}
    for index, event in enumerate(trial.events):
        seat, kind = event.seat, event.kind
        if kind == "send":
            sent[seat].add(event.messages)
        elif kind in ("available", "fetch", "bound"):
            messages = tuple(pair for pair in event.messages if _is_peer(pair[0], seat, roster))
            if not messages:
                continue
            last = last_action.get(seat)
            timing: ActionTiming
            if last is None:
                timing = "no_known_action"
            elif index < last:
                timing = "before_action"
            else:
                timing = "after_action"
            tally = receipts[seat][kind]
            tally.add(messages)
            tally.timing[timing] += 1
            if seat not in first:
                first[seat] = FirstAffectedReceipt(index, kind, timing)

    channel_traffic = bool(first) or any(tally.events for tally in sent.values())
    for seat in trial.seats:
        coverage = trial.coverage[seat]
        yield SeatMeasurement(
            seat=seat,
            coverage=coverage,
            status=_evidence_status(
                changed=seat in first, channel_traffic=channel_traffic, complete=coverage == "complete"
            ),
            action_events=action_events[seat],
            sent=sent[seat].sent(),
            receipts={kind: tally.receipts() for kind, tally in receipts[seat].items()},
            first_affected=first.get(seat),
        )


@dataclass
class _ArmSummary:
    trials: int = 0
    finished: Counter[str] = field(
        default_factory=lambda: Counter(finished=0, unfinished=0, unknown=0)
    )
    trial_status: Counter[EvidenceStatus] = field(
        default_factory=lambda: Counter(dict.fromkeys(_STATUS, 0))
    )
    seat_coverage: Counter[Coverage] = field(
        default_factory=lambda: Counter(dict.fromkeys(_COVERAGE, 0))
    )
    seat_status: Counter[EvidenceStatus] = field(
        default_factory=lambda: Counter(dict.fromkeys(_STATUS, 0))
    )
    interventions: Counter[str] = field(
        default_factory=lambda: Counter(
            recipient_interventions=0,
            fetch_or_bound_changed_interventions=0,
            availability_only_changed_interventions=0,
            fetch_before_action_interventions=0,
            fetch_or_bound_before_action_interventions=0,
        )
    )
    action_events: int = 0
    sent: Counter[str] = field(default_factory=lambda: _message_totals())
    receipts: dict[ReceiptKind, Counter[str]] = field(
        default_factory=lambda: {kind: _message_totals(receipt=True) for kind in _RECEIPTS}
    )
    first_affected: dict[ReceiptKind, Counter[ActionTiming]] = field(
        default_factory=lambda: {kind: Counter(dict.fromkeys(_TIMING, 0)) for kind in _RECEIPTS}
    )

    def add(self, trial: Trial, measurements: Iterator[SeatMeasurement]) -> None:
        self.trials += 1
        if trial.finished is None:
            self.finished["unknown"] += 1
        else:
            self.finished["finished" if trial.finished else "unfinished"] += 1
        changed = channel_traffic = False
        complete = True
        for row in measurements:
            self.interventions["recipient_interventions"] += 1
            complete &= row.coverage == "complete"
            self.seat_coverage[row.coverage] += 1
            self.seat_status[row.status] += 1
            self.action_events += row.action_events
            _add_totals(self.sent, row.sent)
            channel_traffic |= row.sent.events > 0
            if row.first_affected is not None:
                changed = True
                self.first_affected[row.first_affected.kind][row.first_affected.timing] += 1
                fetch, bound = row.receipts["fetch"], row.receipts["bound"]
                if fetch.events or bound.events:
                    self.interventions["fetch_or_bound_changed_interventions"] += 1
                else:
                    self.interventions["availability_only_changed_interventions"] += 1
                if fetch.before_action:
                    self.interventions["fetch_before_action_interventions"] += 1
                if fetch.before_action or bound.before_action:
                    self.interventions["fetch_or_bound_before_action_interventions"] += 1
            for kind, counts in row.receipts.items():
                totals = self.receipts[kind]
                _add_totals(totals, counts)
                totals["before_action"] += counts.before_action
                totals["after_action"] += counts.after_action
                totals["no_known_action"] += counts.no_known_action
        status = _evidence_status(
            changed=changed, channel_traffic=channel_traffic or changed, complete=complete
        )
        self.trial_status[status] += 1

    def as_dict(self) -> dict[str, object]:
        return {
            "trials": self.trials,
            "finished": dict(self.finished),
            "trial_status": dict(self.trial_status),
            "seat_coverage": dict(self.seat_coverage),
            "seat_status": dict(self.seat_status),
            **self.interventions,
            "action_events": self.action_events,
            "sent": dict(self.sent),
            "receipts": {kind: dict(counts) for kind, counts in self.receipts.items()},
            "first_affected": {kind: dict(counts) for kind, counts in self.first_affected.items()},
        }


def _message_totals(*, receipt: bool = False) -> Counter[str]:
    counts: Counter[str] = Counter(events=0, message_occurrences=0, sum_seat_unique_messages=0)
    if receipt:
        for timing in _TIMING:
            counts[timing] = 0
    return counts


def _add_totals(totals: Counter[str], counts: MessageCounts) -> None:
    totals["events"] += counts.events
    totals["message_occurrences"] += counts.message_occurrences
    totals["sum_seat_unique_messages"] += counts.unique_messages


def summarize(trials: Iterable[Trial]) -> dict[str, dict[str, object]]:
    """Aggregate by controlled arm; omit trial, seat, message, and source identifiers.

    Unique message counts are deduplicated within each tier, seat, and trial
    before summing. Receipt tiers stay separate: availability never counts as
    a fetch or as model input. No count measures use or a behavioral effect.
    Action timing uses only the actions in the supplied record for that seat.
    """
    seen: set[str] = set()
    arms: dict[str, _ArmSummary] = {}
    for index, trial in enumerate(trials):
        try:
            roster = _validate(trial)
            if trial.key in seen:
                raise ValueError("duplicate trial key")
        except ValueError as error:
            raise ValueError(f"trial {index}: {error}") from None
        seen.add(trial.key)
        if trial.arm not in arms:
            arms[trial.arm] = _ArmSummary()
        arms[trial.arm].add(trial, _measure_seats(trial, roster))
    return {arm: counts.as_dict() for arm, counts in arms.items()}
