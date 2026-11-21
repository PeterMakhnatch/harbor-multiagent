"""What happened during play in one trial: the message funnel, the answer churn, found and lost answers,
exploration, and what each seat did.

Message funnel, per (message, intended recipient) pair:

- sent: the message is in the world's record (world-state.json, else messages.jsonl).
- recipients: every other seat under test for a message to "all"; the named seat otherwise; none for a
  message a seat sends to itself or to a seat that is not under test (a scripted seat).
- delivered: the message id is in ``data.ids`` of a ``deliver`` event of the recipient (a
  ``GET /messages`` that returned it). The first such event gives the delivery time (world clock).
- seen: the recipient's terminal showed the message after delivery (``seen_pairs`` has the rule).
- acted on: a generic per-trial proxy (``acted_on``).
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from flotilla_analysis import shell
from flotilla_analysis.load import Evidence, Trajectory, Trial
from flotilla_analysis.tasks import (
    TaskInfo,
    answer_examples,
    answer_in_text,
    answer_score,
    answer_valid,
    carries_own_data,
    final_answer,
    own_briefing_score,
    score_against,
)

WINDOW = 24  # characters, whitespace removed
FULL = 1.0 - 1e-9  # a score at least this high is correct
# A solver's message sent up to this many world seconds before its first right submission still
# announces it: seats often post "the slot is X, submitting now" and submit on their next turn (3 to 6 s
# later in the canonical runs); proposals and candidate lists came 13 s or more before.
ANNOUNCE_BEFORE = 10.0


def compact(text: str) -> str:
    """``text`` without whitespace: terminal wrapping, indentation and pretty-printing disappear."""
    return "".join(text.split())


def text_forms(text: str) -> list[str]:
    """The forms a message text takes on a terminal, whitespace removed: raw (``jq -r``, Python
    print), JSON-escaped with ASCII escapes (the world's own JSON reply) and JSON-escaped without them
    (``jq``'s pretty print)."""
    forms = {text, json.dumps(text)[1:-1], json.dumps(text, ensure_ascii=False)[1:-1]}
    return sorted({compact(form) for form in forms})


def windows(compacted: str) -> set[str]:
    return {compacted[i : i + WINDOW] for i in range(len(compacted) - WINDOW + 1)}


def probes(text: str) -> set[str]:
    found: set[str] = set()
    for form in text_forms(text):
        found |= windows(form)
    return found


@dataclass
class Pair:
    """One message and one intended recipient."""

    message: str
    sender: str
    recipient: str
    delivered: bool | None
    delivered_t: float | None  # world time of the first delivery; None when not delivered or unrecorded
    seen: bool | None  # None: cannot be checked (see note)
    seen_step: int | None  # ATIF step_id of the first observation that showed the message
    note: str = ""


@dataclass
class Churn:
    submissions: int | None
    distinct_answers: int | None
    answer_changes: int | None  # submissions whose answer differs from the previous one
    overwrites: int | None  # ... and that come from a different seat than the previous one
    invalid: int | None  # submissions that are not a well-formed answer (tasks.answer_valid)
    ever_correct: bool | None
    clobbers: int | None  # a correct standing answer replaced by a wrong one
    last_submitter: str | None
    by_seat: dict[str, int] = field(default_factory=dict)


@dataclass
class FoundLost:
    """What happened to the right answer in a trial with messaging (a talk condition).

    None means unknown; ``notes`` says why. The solver is the seat of the first submission that the family
    scorer gives full marks, in the world's submission order.
    """

    found: bool | None  # some seat submitted the right answer at some point
    final_right: bool | None  # the recorded final score is full
    lost: bool | None  # found, and the final answer is wrong
    solver: str | None = None
    first_right_t: float | None = None  # world time of the first right submission
    teammate_overwrite: bool | None = None  # a later wrong submission by another seat
    overwriter: str | None = None  # the seat of the first such submission
    overwrite_t: float | None = None
    solver_had_teammate_data: bool | None = None  # see found_lost()
    solver_data_messages: list[str] = field(default_factory=list)
    teammate_had_solver_data: bool | None = None  # the overwriter; None when there is no overwrite
    overwriter_data_messages: list[str] = field(default_factory=list)
    solver_announced: bool | None = None
    announce_messages: list[str] = field(default_factory=list)
    later_by_solver: int | None = None  # submissions after the first right one
    later_by_others: int | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class SeatPlay:
    seat: str
    steps: int | None
    tool_calls: int | None
    curl: dict[str, int] | None  # from keystrokes; see shell.classify
    unknown_paths: dict[str, int] | None
    recon: dict[str, int] | None
    requests: dict[str, int] | None  # world events of this seat, by kind
    exception: str | None

    @property
    def explored(self) -> int | None:
        return None if self.curl is None else self.curl.get("unknown_world", 0)

    @property
    def recon_total(self) -> int | None:
        return None if self.recon is None else sum(self.recon.values())


@dataclass
class Play:
    world_version: str  # "1", "2", "3" or "unknown"
    world_version_source: str
    messages: int | None
    pairs: list[Pair]
    first_message_t: float | None
    denied: int | None
    churn: Churn
    final_score: float | None
    proxy: str  # "yes", "no", "n.a." or "unknown"
    proxy_note: str
    probes: int | None  # world v2 'probe' events (requests to unknown paths); None when not logged
    seats: list[SeatPlay]
    notes: list[str]
    example_leak: bool | None = None  # the answer key is an answer example shown in an instruction
    example_source: str = ""  # "trajectory" (what the seat saw) or "instructions" (the task folder)
    liar: str | None = None  # correct, deceived, corrupted or unknown; None when the task has no decoy
    found_lost: FoundLost | None = None  # talk conditions only
    score_problem: str | None = None

    @property
    def delivered(self) -> int | None:
        return _yes_count(p.delivered for p in self.pairs) if self.pairs else 0

    @property
    def delivered_unknown(self) -> int:
        return sum(1 for p in self.pairs if p.delivered is None)

    @property
    def seen(self) -> int | None:
        return _yes_count(p.seen for p in self.pairs) if self.pairs else 0

    @property
    def seen_unknown(self) -> int:
        return sum(1 for p in self.pairs if p.seen is None)

    @property
    def explored(self) -> int | None:
        """Requests to unknown world paths: the world's probe events when it logs them, else keystrokes."""
        if self.probes is not None:
            return self.probes
        counts = [s.explored for s in self.seats if s.explored is not None]
        return sum(counts) if counts else None

    @property
    def recon(self) -> int | None:
        counts = [s.recon_total for s in self.seats if s.recon_total is not None]
        return sum(counts) if counts else None


def _yes_count(values: Iterable[bool | None]) -> int | None:
    yes = known = 0
    for value in values:
        if value is not None:
            known += 1
            yes += value
    return yes if known else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    return float(value)


def world_start_bound(
    trial: Trial, evidence: Evidence, trajectories: dict[str, Trajectory]
) -> datetime | None:
    """The earliest wall time at which the world can have started (its clock's zero).

    The world starts during environment setup, so not before ``environment_setup.started_at``. A seat
    makes its first request after its agent started, so the world started no earlier than (agent start
    - world time of that seat's first request), for every seat. The bound is the latest of these.
    """
    bounds = [trial.environment_started] if trial.environment_started else []
    first: dict[str, float] = {}
    for event in evidence.events or []:
        seat, t = event.get("seat"), _number(event.get("t"))
        if isinstance(seat, str) and t is not None:
            first[seat] = min(first.get(seat, t), t)
    for seat, t in sorted(first.items()):
        starts = [s.started for s in trial.seats if s.seat == seat and s.started is not None]
        trajectory = trajectories.get(seat)
        if trajectory is not None and trajectory.start is not None:
            starts.append(trajectory.start)
        bounds += [start - timedelta(seconds=t) for start in starts]
    return max(bounds) if bounds else None


def seen_pairs(
    messages: list[dict[str, Any]],
    events: list[dict[str, Any]] | None,
    seats: list[str],
    trajectories: dict[str, Trajectory],
    briefings: dict[str, Any],
    start_bound: datetime | None,
) -> list[Pair]:
    """Delivery and sight of every (message, recipient) pair.

    Seen: the message was delivered to the recipient, and a distinctive probe of it appears in the
    observation of a recipient step that did not end before the delivery could have happened.

    - Probes: every 24-character window of each whitespace-free text form (``text_forms``). A message
      without one (fewer than 24 non-whitespace characters) cannot be checked: seen is None.
    - A probe is not distinctive for recipient r when it also occurs in (a) another message of the trial
      with a different whitespace-free text, (b) r's instruction (the messages before its first agent
      step) or r's own briefing, (c) r's keystrokes in this or an earlier step, or (d) r's observations
      in steps that ended before any message with this text could have been delivered to r. Identical
      messages (a seat that repeats itself) are each credited once their own delivery has happened.
    - Timing: an ATIF step's timestamp is taken as the time its observation was complete (terminus-2
      records the step after it captured the terminal). The earliest wall time of a delivery at world
      time t is ``start_bound + t`` (``world_start_bound``). Without timestamps every step counts as
      after the delivery and rule (d) does not apply.
    """
    first_delivery: dict[tuple[str, str], float | None] = {}
    for event in events or []:
        if event.get("kind") != "deliver" or not isinstance(event.get("seat"), str):
            continue
        t = _number(event.get("t"))
        for message_id in (event.get("data") or {}).get("ids") or []:
            key = (str(message_id), event["seat"])
            previous = first_delivery.get(key)
            if key not in first_delivery or (t is not None and (previous is None or t < previous)):
                first_delivery[key] = t

    texts = {str(m.get("id")): str(m.get("text") or "") for m in messages}
    contents = {mid: compact(text) for mid, text in texts.items()}
    all_probes = {mid: probes(text) for mid, text in texts.items()}
    owners: dict[str, set[str]] = {}
    for mid in sorted(all_probes):
        for gram in all_probes[mid]:
            owners.setdefault(gram, set()).add(contents[mid])
    distinct = {mid: {g for g in grams if len(owners[g]) == 1} for mid, grams in all_probes.items()}

    pairs: list[Pair] = []
    for message in messages:
        mid, sender, to = str(message.get("id")), str(message.get("from")), message.get("to")
        if to == "all":
            recipients = [s for s in seats if s != sender]
        else:
            recipients = [to] if isinstance(to, str) and to in seats and to != sender else []
        for recipient in recipients:
            key = (mid, recipient)
            if events is None:
                pairs.append(Pair(mid, sender, recipient, None, None, None, None, "no events.jsonl"))
                continue
            if key not in first_delivery:
                pairs.append(Pair(mid, sender, recipient, False, None, False, None, "not delivered"))
                continue
            t = first_delivery[key]
            if recipient not in trajectories:
                pairs.append(Pair(mid, sender, recipient, True, t, None, None, "no recipient trajectory"))
            elif not all_probes[mid]:
                pairs.append(Pair(mid, sender, recipient, True, t, None, None, "under 24 characters"))
            elif not distinct[mid]:
                pairs.append(Pair(mid, sender, recipient, True, t, None, None, "no distinctive text"))
            else:
                pairs.append(Pair(mid, sender, recipient, True, t, False, None, "not shown"))

    for recipient in sorted(trajectories):
        todo = [p for p in pairs if p.recipient == recipient and p.seen is False and p.delivered]
        if not todo:
            continue
        trajectory = trajectories[recipient]
        own = windows(compact(trajectory.preamble))
        if recipient in briefings:
            own |= probes(json.dumps(briefings[recipient], sort_keys=True))
        pending = {p.message: distinct[p.message] - own for p in todo}

        def earliest(t: float | None) -> datetime | None:
            return start_bound + timedelta(seconds=t) if start_bound is not None and t is not None else None

        # A message's text can be on the screen legitimately from the first delivery of any message with
        # the same text (a seat that repeats itself); output before that is a coincidence (rule d).
        first_of_text: dict[str, float | None] = {}
        for p in pairs:
            if p.recipient == recipient and p.delivered:
                text, t = contents[p.message], p.delivered_t
                known = [v for v in (first_of_text.get(text), t) if v is not None]
                first_of_text[text] = min(known) if known else None
        own_limit = {p.message: earliest(p.delivered_t) for p in todo}
        text_limit = {p.message: earliest(first_of_text[contents[p.message]]) for p in todo}
        before: dict[str, set[str]] = {mid: set() for mid in pending}
        typed: set[str] = set()
        found: dict[str, int] = {}
        for step in trajectory.steps:
            for keystrokes in step.keystrokes:
                typed |= windows(compact(keystrokes))
            shown = windows(compact(step.observation))
            if not shown:
                continue
            for mid in [m for m in pending if m not in found]:
                hits = pending[mid] & shown
                if not hits:
                    continue
                if step.timestamp is not None:
                    limit = text_limit[mid]
                    if limit is not None and step.timestamp < limit:
                        before[mid] |= hits  # shown before this text could have been delivered at all
                        continue
                    limit = own_limit[mid]
                    if limit is not None and step.timestamp < limit:
                        continue  # an earlier message with the same text explains it
                if hits - typed - before[mid]:
                    found[mid] = step.step_id
            if len(found) == len(pending):
                break
        for p in todo:
            if not pending[p.message]:
                p.seen, p.note = None, "no distinctive text"
            elif p.message in found:
                p.seen, p.seen_step, p.note = True, found[p.message], ""
    return pairs


def canonical(answer: Any) -> str:
    return json.dumps(answer, sort_keys=True, separators=(",", ":"))


def churn(evidence: Evidence, task: TaskInfo) -> Churn:
    submissions = evidence.submissions
    if submissions is None:
        return Churn(None, None, None, None, None, None, None, None)
    if any("answer" not in s for s in submissions):
        seats = [str(s.get("seat")) for s in submissions]
        return Churn(
            len(seats), None, None, None, None, None, None, seats[-1], dict(sorted(Counter(seats).items()))
        )
    answers = [canonical(s.get("answer")) for s in submissions]
    seats = [str(s.get("seat")) for s in submissions]
    n = len(submissions)
    changes = sum(1 for i in range(1, n) if answers[i] != answers[i - 1])
    overwrites = sum(1 for i in range(1, n) if answers[i] != answers[i - 1] and seats[i] != seats[i - 1])
    scores = [answer_score(task, s.get("answer")) for s in submissions]
    valid = [answer_valid(task, s.get("answer")) for s in submissions]
    if task.usable and all(score is not None for score in scores):
        correct = [score >= FULL for score in scores if score is not None]
        ever_correct: bool | None = any(correct)
        clobbers: int | None = sum(1 for i in range(1, n) if correct[i - 1] and not correct[i])
    else:
        ever_correct, clobbers = None, None
    invalid = (
        sum(1 for v in valid if v is False) if task.usable and all(v is not None for v in valid) else None
    )
    return Churn(
        submissions=n,
        distinct_answers=len(set(answers)),
        answer_changes=changes,
        overwrites=overwrites,
        invalid=invalid,
        ever_correct=ever_correct,
        clobbers=clobbers,
        last_submitter=seats[-1] if seats else None,
        by_seat=dict(sorted(Counter(seats).items())),
    )


def acted_on(seats: list[str], evidence: Evidence, task: TaskInfo, final: float | None) -> tuple[str, str]:
    """Generic proxy for "the team acted on a teammate's information" (two or more seats).

    yes: the final answer is correct and the seat that submitted it could not have answered alone:
    the family's ``oracle_answer([its own briefing])`` does not score 1 against the answer key.
    no: the final answer is not correct, or nothing was submitted. n.a.: one seat, or the submitter's
    own briefing already gives a correct answer. unknown: no recorded score, or the task directory
    cannot be used. In ``silent`` the same measure counts lucky correct answers: it is the baseline.
    """
    if len(seats) < 2:
        return "n.a.", "one seat"
    if final is None:
        return "unknown", "no recorded score"
    submissions = evidence.submissions
    if submissions is None:
        return "unknown", "submissions not recorded"
    if not submissions:
        return "no", "nothing submitted"
    if final < FULL:
        return "no", "final answer not correct"
    submitter = str(submissions[-1].get("seat"))
    own, why = own_briefing_score(task, submitter)
    if own is None:
        return "unknown", why
    if own >= FULL:
        return "n.a.", f"{submitter}'s own briefing gives the answer"
    return "yes", f"{submitter}'s own briefing alone scores {own:g}"


def example_leak(
    seats: list[str], trajectories: dict[str, Trajectory], task: TaskInfo
) -> tuple[bool | None, str]:
    """Whether the answer key is one of the answer examples shown to the seats under test.

    The instruction each seat saw is read from its trajectory (the first messages); without
    trajectories, from the task folder's instructions/<seat>.md. Each JSON code span in the "Answer
    format" section is scored against the key with the family scorer; a full score is a leak: a seat
    that copies the example is right without any information. None when unknown.
    """
    if not task.usable or task.answer_key is None:
        return None, ""
    texts = [trajectories[seat].preamble for seat in seats if seat in trajectories]
    source = "trajectory"
    if not texts and task.directory is not None:
        source = "instructions"
        for seat in seats:
            path = task.directory / "instructions" / f"{seat}.md"
            if path.is_file():
                texts.append(path.read_text(encoding="utf-8", errors="replace"))
    found = [answer_examples(text) for text in texts]
    if not any(examples is not None for examples in found):
        return None, source if texts else ""
    examples = [example for group in found for example in group or []]
    scores = [score_against(task, example, task.answer_key) for example in examples]
    return any(score is not None and score >= FULL for score in scores), source


def liar_outcome(evidence: Evidence, task: TaskInfo, final: float | None) -> str | None:
    """For tasks with a decoy answer (scripted-liar variants): correct, deceived (the final answer is
    the decoy), corrupted (neither, including no answer) or unknown. None when the task has no decoy."""
    score = evidence.score or {}
    decoy = (task.task_json or {}).get("decoy")
    # World v2 writes "deceived": null for every task without a decoy, so test for a boolean.
    if not isinstance(score.get("deceived"), bool) and decoy is None:
        return None
    if final is None:
        return "unknown"
    if final >= FULL:
        return "correct"
    deceived = score.get("deceived")
    if not isinstance(deceived, bool):
        known, answer = final_answer(evidence)
        against = score_against(task, answer, decoy) if known else None
        if against is None:
            return "unknown"
        deceived = against >= FULL
    return "deceived" if deceived else "corrupted"


def _either(values: list[bool | None]) -> bool | None:
    """True if any value is True; else None if any is unknown; else False (also for no values)."""
    if any(v is True for v in values):
        return True
    return None if any(v is None for v in values) else False


def _delivered_by(events: list[dict[str, Any]], seat: str, until: float) -> set[str]:
    """Ids of the messages that ``seat`` fetched (``deliver`` events) at or before world time ``until``."""
    found: set[str] = set()
    for event in events:
        t = _number(event.get("t"))
        if event.get("kind") == "deliver" and event.get("seat") == seat and t is not None and t <= until:
            found |= {str(i) for i in (event.get("data") or {}).get("ids") or []}
    return found


def _data_from(
    messages: list[dict[str, Any]],
    events: list[dict[str, Any]] | None,
    task: TaskInfo,
    *,
    receiver: str,
    senders: set[str] | None,
    until: float | None,
) -> tuple[bool | None, list[str], str]:
    """Whether ``receiver`` fetched, at or before world time ``until``, a message from another seat (or
    from one of ``senders``) that carries its sender's own information; the ids of those messages; why
    unknown."""
    if events is None:
        return None, [], "deliveries not recorded (no events.jsonl)"
    if until is None:
        return None, [], "the submission has no world time"
    fetched = _delivered_by(events, receiver, until)
    values, ids, why = [], [], ""
    for message in messages:
        sender = str(message.get("from"))
        if str(message.get("id")) not in fetched or sender == receiver:
            continue
        if senders is not None and sender not in senders:
            continue
        value, reason = carries_own_data(task, sender, str(message.get("text") or ""))
        values.append(value)
        if value:
            ids.append(str(message.get("id")))
        elif value is None:
            why = why or reason
    result = _either(values)
    return result, ids, why if result is None else ""


def found_lost(trial: Trial, evidence: Evidence, task: TaskInfo, final: float | None) -> FoundLost | None:
    """Found, kept or lost: what happened to the right answer in a talk trial (None for other conditions).

    - found: some submission scores full with the family scorer; final_right: the recorded final score is
      full; lost: found and not final_right. The solver is the seat of the first right submission (world
      submission order).
    - teammate_overwrite: after the first right submission, another seat submitted a wrong answer; the
      overwriter is the seat of the first such submission.
    - solver_had_teammate_data: at or before the world time of its first right submission, the solver
      fetched a message from another seat that carries that seat's own information (the family hook
      ``carries_own_data(text, private)``; ``private`` is the sender's briefing).
    - teammate_had_solver_data: the same, for the overwriter and the solver's messages, before the
      overwrite; None when there is no overwrite.
    - solver_announced: the solver sent a message that names the right answer (the family hook
      ``answer_in_text(text, key)``) at most ANNOUNCE_BEFORE seconds before its first right submission or
      any time after it.
    - later_by_solver / later_by_others: submissions after the first right one.

    A measure that needs a hook the family module lacks is None ("unknown"), unless no message qualifies.
    """
    if not trial.condition.startswith("talk"):
        return None
    final_right = None if final is None else final >= FULL
    submissions = evidence.submissions
    if submissions is None:
        return FoundLost(None, final_right, None, notes=["submissions not recorded"])
    if any("answer" not in s for s in submissions):
        return FoundLost(None, final_right, None, notes=["a submission answer is unknown"])
    scores = [answer_score(task, s.get("answer")) for s in submissions]
    if not task.usable or any(score is None for score in scores):
        return FoundLost(None, final_right, None, notes=[task.problem or "the family scorer failed"])
    right = [i for i, score in enumerate(scores) if score is not None and score >= FULL]
    if not right:
        return FoundLost(False, final_right, False)
    first = right[0]
    solver = str(submissions[first].get("seat"))
    first_t = _number(submissions[first].get("t"))
    later = list(range(first + 1, len(submissions)))
    wrong = [i for i in later if str(submissions[i].get("seat")) != solver and (scores[i] or 0.0) < FULL]
    result = FoundLost(
        found=True,
        final_right=final_right,
        lost=None if final_right is None else not final_right,
        solver=solver,
        first_right_t=first_t,
        teammate_overwrite=bool(wrong),
        later_by_solver=sum(1 for i in later if str(submissions[i].get("seat")) == solver),
        later_by_others=sum(1 for i in later if str(submissions[i].get("seat")) != solver),
    )
    if wrong:
        result.overwriter = str(submissions[wrong[0]].get("seat"))
        result.overwrite_t = _number(submissions[wrong[0]].get("t"))
    messages = evidence.messages
    if messages is None:
        result.notes.append("messages not recorded: data and announcement measures unknown")
        return result
    had, ids, why = _data_from(messages, evidence.events, task, receiver=solver, senders=None, until=first_t)
    result.solver_had_teammate_data, result.solver_data_messages = had, ids
    if why:
        result.notes.append(f"solver_had_teammate_data: {why}")
    if result.overwriter is not None:
        had, ids, why = _data_from(
            messages,
            evidence.events,
            task,
            receiver=result.overwriter,
            senders={solver},
            until=result.overwrite_t,
        )
        result.teammate_had_solver_data, result.overwriter_data_messages = had, ids
        if why:
            result.notes.append(f"teammate_had_solver_data: {why}")
    values: list[bool | None] = []
    why = ""
    for message in messages:
        if str(message.get("from")) != solver or message.get("to") == solver:
            continue
        t = _number(message.get("t"))
        if t is None or first_t is None:
            values.append(None)
            why = why or "a message or the first right submission has no world time"
            continue
        if t < first_t - ANNOUNCE_BEFORE:
            continue
        value, reason = answer_in_text(task, str(message.get("text") or ""))
        values.append(value)
        if value:
            result.announce_messages.append(str(message.get("id")))
        elif value is None:
            why = why or reason
    result.solver_announced = _either(values)
    if result.solver_announced is None:
        result.notes.append(f"solver_announced: {why}")
    return result


def world_version(evidence: Evidence, task: TaskInfo) -> tuple[str, str]:
    """The world version the trial ran, from the trial's own evidence first."""
    start = next((e for e in evidence.events or [] if e.get("kind") == "start"), None)
    if start is not None:
        # World v2 and later name their version in the start event; v1 wrote a start event without it.
        # A task folder rebuilt later may hold a newer world, so the trial's own events come first.
        version = (start.get("data") or {}).get("world_version")
        if version is None:
            return "1", "start event without a version"
        return str(version), "start event"
    if any(e.get("kind") == "probe" for e in evidence.events or []):
        return "2", "probe events"
    if task.task_json is not None:
        return str(task.task_json.get("world_version", 1)), "task.json"
    return "unknown", ""


def seat_play(seat: str, trial: Trial, evidence: Evidence, trajectory: Trajectory | None) -> SeatPlay:
    exception = next((s.exception for s in trial.seats if s.seat == seat), None)
    requests = None
    if evidence.events is not None:
        requests = dict(
            sorted(Counter(str(e.get("kind")) for e in evidence.events if e.get("seat") == seat).items())
        )
    if trajectory is None:
        return SeatPlay(seat, None, None, None, None, None, requests, exception)
    stream = "".join(keys for step in trajectory.steps for keys in step.keystrokes)
    found = shell.classify(stream)
    return SeatPlay(
        seat=seat,
        steps=len(trajectory.steps),
        tool_calls=sum(step.tool_calls for step in trajectory.steps),
        curl=found.curl,
        unknown_paths=found.unknown_paths,
        recon=found.recon,
        requests=requests,
        exception=exception,
    )


def analyze(trial: Trial, evidence: Evidence, trajectories: dict[str, Trajectory], task: TaskInfo) -> Play:
    notes = list(evidence.problems)
    seats = evidence.seats or sorted(s.seat for s in trial.seats)
    version, version_source = world_version(evidence, task)
    messages = evidence.messages
    team = {s.seat for s in trial.seats if s.under_test}
    pairs = seen_pairs(
        messages or [],
        evidence.events,
        [s for s in seats if s in team] if team else seats,  # the funnel follows the seats under test
        trajectories,
        task.briefings if task.task_json is not None else {},
        world_start_bound(trial, evidence, trajectories),
    )
    times = [t for t in (_number(m.get("t")) for m in messages or []) if t is not None]
    denied = None
    if evidence.events is not None:
        denied = sum(1 for e in evidence.events if e.get("kind") == "denied")
    probes_logged = version not in ("1", "unknown") and evidence.events is not None
    probe_count = sum(1 for e in evidence.events or [] if e.get("kind") == "probe") if probes_logged else None
    final = trial.reward
    proxy, proxy_note = acted_on(seats, evidence, task, final)
    if not task.usable:
        notes.append(f"task directory: {task.problem}")
    leak, leak_source = example_leak(sorted(team) or seats, trajectories, task)
    return Play(
        world_version=version,
        world_version_source=version_source,
        messages=None if messages is None else len(messages),
        pairs=pairs,
        first_message_t=min(times) if times else None,
        denied=denied,
        churn=churn(evidence, task),
        final_score=final,
        proxy=proxy,
        proxy_note=proxy_note,
        probes=probe_count,
        seats=[seat_play(seat, trial, evidence, trajectories.get(seat)) for seat in seats],
        notes=notes,
        example_leak=leak,
        example_source=leak_source,
        liar=liar_outcome(evidence, task, final),
        found_lost=found_lost(trial, evidence, task, final),
        score_problem=evidence.score_problem,
    )
