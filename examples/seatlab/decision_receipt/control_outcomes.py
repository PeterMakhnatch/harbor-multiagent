"""Classify one native `harbor/ship-decision-receipt` calibration control trial.

`inspect_case` reads one trial folder with bounded, no-follow reads and never writes.
It corroborates config.json, result.json (and its config), trial-events.jsonl as one
closed attempt, the v2 lock.json and the verifier files against one enrollment
request, requires the order-free canonical digest of the
whole lock.json to equal the held enrollment lock pin, requires the relief seat to
carry the scripted control receipt sealed for the requested citation, regrades the
published cut with `decision_verify.evaluate`, and compares the replay with the
scripted control expectation for the case fate and citation. The trial folder, its
cut and the enrollment workspace are private files that nobody signs: a verified row
shows that the files agree with each other, with the pin and with the replay, not
that they are authentic. Reasons are fixed literals and never carry paths, prompts or
references.
"""

import copy
import json
import os
import re
from datetime import datetime
from itertools import islice
from pathlib import Path
from typing import NamedTuple, cast

from ...native_evidence import (
    Incomplete,
    Invalid,
    Record,
    canonical_uuid,
    read_bytes,
    same,
    strict_object,
)
from ...native_study import DIGEST, as_text, check_identity, native, require
from . import attempt_journal as journal
from . import control_client as client
from . import control_inputs as inputs
from . import decision_verify as verify
from .materialize import TASK_NAME

STATUSES = (
    "missing",
    "incomplete",
    "conflict",
    "seat_unqualified",
    "verified",
    "mismatch",
)
GRADED = ("verified", "mismatch")
ROLES = ("outgoing", "relief")
ORACLE = "oracle"
CONTROL = "decision-receipt-control"
CONTROL_IMPORT = "examples.seatlab.decision_receipt.control_seat:DecisionReceiptControl"
POLICY = {"SH_POLICY": "original"}
CHANNEL = "deliver"
DELIVERED = "original"
ACTION = "close"
RETURNED = "returned"
REQUEST_FIELDS = frozenset(
    {"task", "trial_name", "trials_dir", "environment", "participants"}
)
GRADES = {"settles": (1, "exactly_one"), "fails": (0, "omission")}
COMPARED = (
    "events",
    "replace",
    "settled",
    "drained",
    "grade_before_drain",
    "reward",
    "outcome",
    "outgoing_handoff",
    "relief_action",
    verify.CITATION,
    "citation_status",
)
MAX_ENTRIES = 256
MAX_CONFIG_BYTES = 1 << 20
MAX_RESULT_BYTES = 8 << 20
MAX_OBSERVATION_BYTES = 16 << 10
MAX_REWARD_BYTES = 4096
MAX_TIMESTAMP_CHARS = 64
RECEIPT_KEY = "decision_receipt_control"
RECEIPT_SCHEMA = "harbor.ship-decision-receipt.control/v1"
CLIENT_PIN = re.compile(r"[0-9a-f]{64}")
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
CONFIG = ("config.json",)
RESULT = ("result.json",)
LOCK = ("lock.json",)
EVENTS = (journal.JOURNAL_NAME,)
CUT = ("verifier", verify.CUT_NAME)
OBSERVATION = ("verifier", verify.OBSERVATION_NAME)
REWARD = ("verifier", verify.REWARD_NAME)
EARLIER = (
    ("verifier", verify.inflight.CUT_NAME),
    ("verifier", verify.inflight.OBSERVATION_NAME),
)

assert CHANNEL in verify.inflight.CHANNELS and DELIVERED in verify.CITABLE
assert ACTION in verify.stock.CHOICES["resolve"]
assert set(GRADES) == set(verify.inflight.FATES)
assert len(set(COMPARED)) == len(COMPARED) and "run" not in COMPARED
assert client.SEALED in client.LITERALS


class CaseResult(NamedTuple):
    status: str
    trial: str | None
    world: str | None
    citation: str | None
    citation_status: str | None
    grade: int | None
    action: str | None
    outcome: str | None
    reason: str | None


class _Verdict(Exception):
    def __init__(self, status: str, reason: str) -> None:
        assert status in STATUSES and status not in GRADED and reason
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _need(condition: bool, reason: str, status: str = "conflict") -> None:
    if not condition:
        raise _Verdict(status, reason)


def expected(fate: str, cite: bool) -> Record:
    assert fate in GRADES and type(cite) is bool
    resolve = (3, ACTION)
    settled, drained = verify.inflight.replay_settlement(fate, resolve, None)
    outcome = verify.inflight.OUTCOMES[min(len(drained), 2)]
    reward = int(outcome == "exactly_one")
    assert (reward, outcome) == GRADES[fate]
    return {
        "events": [
            {"seq": 1, "kind": "handoff", "choice": DELIVERED},
            {"seq": 2, "kind": "inbox", "choice": DELIVERED},
            {"seq": 3, "kind": "resolve", "choice": ACTION},
        ],
        "replace": None,
        "settled": settled,
        "drained": drained,
        "grade_before_drain": int(len(settled) == 1),
        "reward": reward,
        "outcome": outcome,
        "outgoing_handoff": DELIVERED,
        "relief_action": ACTION,
        verify.CITATION: DELIVERED if cite else None,
        "citation_status": "cited_at_decision" if cite else "returned_not_cited",
    }


def receipt(cite: bool, client_sha256: str) -> Record:
    assert type(cite) is bool and CLIENT_PIN.fullmatch(client_sha256) is not None
    return {
        "schema": RECEIPT_SCHEMA,
        "seat": ROLES[1],
        "cite": cite,
        "client_sha256": client_sha256,
        "outcome": client.SEALED,
    }


def _stripped(participant: object) -> Record | None:
    if type(participant) is not dict:
        return None
    return {
        key: value
        for key, value in cast(Record, participant).items()
        if value is not None
    }


def _team(raw: object) -> list[Record] | None:
    if type(raw) is not list or len(cast(list[object], raw)) != len(ROLES):
        return None
    team = [_stripped(item) for item in cast(list[object], raw)]
    if any(member is None for member in team):
        return None
    members = cast(list[Record], team)
    if tuple(member.get("seat") for member in members) != ROLES:
        return None
    return members


def _check_participants(participants: object, cite: bool) -> None:
    team = _team(participants)
    require(team is not None, "request participants are not outgoing then relief")
    outgoing, relief = cast(list[Record], team)
    require(
        outgoing.get("name") == ORACLE
        and same(outgoing.get("env"), POLICY)
        and not outgoing.get("kwargs")
        and "import_path" not in outgoing
        and "model_name" not in outgoing,
        "request outgoing seat is not the built-in oracle on the original policy",
    )
    require(
        relief.get("import_path") == CONTROL_IMPORT
        and same(relief.get("kwargs"), {"cite": cite})
        and not relief.get("env")
        and "name" not in relief
        and "model_name" not in relief,
        "request relief seat is not the decision receipt control for this cite",
    )


def _typed(request: Record) -> Record:
    config = native().TrialConfig.model_validate(copy.deepcopy(request))
    team = [
        member.model_dump(mode="json", exclude_none=True)
        for member in config.participants
    ]
    require(
        same(team, request["participants"]),
        "request participants are not the native normalized team",
    )
    require(
        config.trial_name == request["trial_name"], "native config renames the trial"
    )
    full = json.loads(config.model_dump_json())
    require(
        type(full) is dict and "agent" in full, "native config has no default agent"
    )
    return cast(Record, full)


def _check_inputs(
    folder: Path, request: object, task: object, fate: str, cite: bool
) -> Record:
    require(isinstance(folder, Path) and folder.is_absolute(), "folder is not absolute")
    require(type(fate) is str and fate in GRADES, "fate is not a ship fate")
    require(type(cite) is bool, "cite is not a bool")
    identity = check_identity(task)
    require(identity["name"] == TASK_NAME, "task is not the decision receipt task")
    require(
        type(request) is dict and set(cast(Record, request)) == REQUEST_FIELDS,
        "request fields differ from a native registration request",
    )
    case = cast(Record, request)
    task_ref = case["task"]
    require(
        type(task_ref) is dict
        and set(cast(Record, task_ref)) == {"path"}
        and type(cast(Record, task_ref)["path"]) is str
        and os.path.isabs(cast(Record, task_ref)["path"]),
        "request task is not one absolute local path",
    )
    ticket = case["trial_name"]
    require(
        type(ticket) is str and ticket != "" and folder.name == ticket,
        "folder is not named by the request trial",
    )
    trials = case["trials_dir"]
    require(type(trials) is str and os.path.isabs(trials), "trials_dir is not absolute")
    environment = case["environment"]
    require(
        type(environment) is dict
        and same(
            cast(Record, environment).get("env"),
            {"SH_FATE": fate, "SH_CHANNEL": CHANNEL},
        ),
        "request world is not the delivered case fate",
    )
    _check_participants(case["participants"], cite)
    return _typed(case)


def _listing(folder: Path) -> tuple[int, int]:
    try:
        fd = os.open(folder, DIRECTORY)
    except FileNotFoundError:
        raise _Verdict("missing", "trial folder does not exist") from None
    except OSError:
        raise _Verdict("conflict", "trial folder is not a plain directory") from None
    try:
        info = os.fstat(fd)
        with os.scandir(fd) as listing:
            count = sum(1 for _ in islice(listing, MAX_ENTRIES + 1))
    finally:
        os.close(fd)
    _need(count <= MAX_ENTRIES, "trial folder holds too many entries")
    if count == 0:
        raise _Verdict("missing", "trial folder is empty")
    return info.st_dev, info.st_ino


def _unchanged(folder: Path, identity: tuple[int, int]) -> bool:
    try:
        info = os.lstat(folder)
    except OSError:
        return False
    return (info.st_dev, info.st_ino) == identity


def _read(folder: Path, parts: tuple[str, ...], limit: int, label: str) -> Record:
    try:
        data = read_bytes(folder, parts, limit)
    except Incomplete:
        raise _Verdict("incomplete", f"{label} is missing") from None
    except Invalid:
        raise _Verdict("conflict", f"{label} is not a bounded plain file") from None
    try:
        return strict_object(data, label)
    except Invalid:
        raise _Verdict("conflict", f"{label} is not strict JSON") from None


def _present(folder: Path, parts: tuple[str, ...]) -> bool:
    try:
        read_bytes(folder, parts, verify.stock.MAX_CUT_BYTES)
    except Incomplete:
        return False
    except Invalid:
        return True
    return True


def _check_world(record: Record, request: Record, label: str) -> None:
    environment = record.get("environment")
    wanted = cast(Record, request["environment"])
    _need(
        type(environment) is dict
        and same(cast(Record, environment).get("env"), wanted["env"])
        and cast(Record, environment).get("type") == wanted.get("type"),
        f"{label} world is not the registered world",
    )
    _need(
        same(_team(record.get("participants")), _team(request["participants"])),
        f"{label} participants are not the registered ordered team",
    )


def _check_config(record: object, full: Record, label: str, written: bool) -> None:
    expected = {key: value for key, value in full.items() if key != "agent"}
    _need(
        same(record, expected if written else full),
        f"{label} is not the normalized registered request",
    )


def _check_result(result: Record, request: Record, task: Record, full: Record) -> None:
    version = result.get("schema_version")
    _need(type(version) is int and version == 1, "result.json is not schema version 1")
    _need(
        same(result.get("trial_name"), request["trial_name"]),
        "result.json trial differs",
    )
    _need(
        same(result.get("task_name"), task["name"])
        and same(result.get("task_checksum"), task["checksum"]),
        "result.json task name or native checksum is not the registered task",
    )
    _need(
        same(result.get("task_id"), {"path": request["task"]["path"]}),
        "result.json task is not the registered local path",
    )
    _check_config(result.get("config"), full, "result.json config", False)
    _need(
        all(key in result for key in ("exception_info", "finished_at", "reward_valid")),
        "result.json lacks its error, finish or reward record",
    )
    folder = Path(request["trials_dir"]) / request["trial_name"]
    _need(
        same(result.get("trial_uri"), folder.as_uri()),
        "result.json trial is not the private workspace folder",
    )


def _instant(value: object, label: str) -> datetime:
    _need(
        type(value) is str
        and 0 < len(value) <= MAX_TIMESTAMP_CHARS
        and value.isascii(),
        f"{label} is not a bounded timestamp",
    )
    try:
        moment = datetime.fromisoformat(cast(str, value))
    except ValueError:
        raise _Verdict("conflict", f"{label} is not a timestamp") from None
    _need(moment.utcoffset() is not None, f"{label} has no timezone")
    return moment


def _check_times(result: Record) -> None:
    started = _instant(result.get("started_at"), "result.json started_at")
    finished = _instant(result["finished_at"], "result.json finished_at")
    _need(finished >= started, "result.json finished before it started")


def _check_journal(folder: Path, result: Record) -> None:
    try:
        data = read_bytes(folder, EVENTS, journal.MAX_JOURNAL_BYTES)
    except Incomplete:
        raise _Verdict("incomplete", "trial events are missing") from None
    except Invalid:
        raise _Verdict(
            "conflict", "trial events are not a bounded plain file"
        ) from None
    state = journal.check_journal(
        data,
        verifier_ran=result.get("reward_valid") is True,
        started_at=cast(str, result["started_at"]),
        finished_at=cast(str, result["finished_at"]),
    )
    assert state in (journal.COMPLETE, journal.INCOMPLETE, journal.CONFLICT)
    _need(state != journal.CONFLICT, "trial events are not one closed attempt")
    _need(state == journal.COMPLETE, "trial events are not closed", "incomplete")


def _check_pin(lock: Record, lock_sha256: str) -> None:
    try:
        digest = inputs.lock_digest(lock)
    except (ValueError, RecursionError):
        raise _Verdict("conflict", "lock.json has no canonical bytes") from None
    _need(digest == lock_sha256, "lock.json is not the enrolled lock")


def _check_lock(lock: Record, request: Record, task: Record, lock_sha256: str) -> None:
    _check_pin(lock, lock_sha256)
    version = lock.get("schema_version")
    _need(type(version) is int and version == 2, "lock.json is not schema version 2")
    pinned = lock.get("task")
    path = request["task"]["path"]
    _need(
        type(pinned) is dict
        and cast(Record, pinned).get("type") == "local"
        and same(cast(Record, pinned).get("digest"), task["digest"])
        and same(cast(Record, pinned).get("path"), path)
        and cast(Record, pinned).get("name") == os.path.basename(path),
        "lock.json task is not the registered local task",
    )
    _need(lock.get("source_trial") is None, "lock.json derives from another trial")
    _need(lock.get("install_only") is False, "lock.json is install-only")
    _check_world(lock, request, "lock.json")


def _seats(result: Record) -> dict[str, Record]:
    rows = result.get("seat_results")
    _need(
        type(rows) is list and len(cast(list[object], rows)) == len(ROLES),
        "native seat results are not one per role",
    )
    found: dict[str, Record] = {}
    for row in cast(list[object], rows):
        _need(type(row) is dict, "native seat result is not an object")
        seat = cast(Record, row)
        role = seat.get("seat")
        _need(
            type(role) is str and role in ROLES and role not in found,
            "native seat result role differs",
        )
        _need("exception_info" in seat, "native seat error record is absent")
        found[cast(str, role)] = seat
    return found


def _check_receipt(seat: Record, cite: bool, client_sha256: str) -> None:
    context = seat.get("agent_result")
    metadata = cast(Record, context).get("metadata") if type(context) is dict else None
    _need(
        type(metadata) is dict and RECEIPT_KEY in cast(Record, metadata),
        "relief seat recorded no control receipt",
        "seat_unqualified",
    )
    _need(
        same(metadata, {RECEIPT_KEY: receipt(cite, client_sha256)}),
        "relief seat receipt is not the sealed control for this cite",
    )


def _qualify(result: Record, seats: dict[str, Record]) -> None:
    unqualified = "seat_unqualified"
    _need(
        result["exception_info"] is None,
        "native trial recorded an exception",
        unqualified,
    )
    for role, runner in zip(ROLES, (ORACLE, CONTROL)):
        seat = seats[role]
        _need(
            seat.get("under_test") is True,
            f"{role} seat is not under test",
            unqualified,
        )
        info = seat.get("agent_info")
        _need(
            type(info) is dict and cast(Record, info).get("name") == runner,
            f"{role} seat runner is not the registered control",
            unqualified,
        )
        _need(
            cast(Record, info).get("model_info") is None,
            f"{role} seat recorded a model",
            unqualified,
        )
        _need(
            seat["exception_info"] is None,
            f"{role} seat recorded an exception",
            unqualified,
        )
        disposition = seat.get("execution_disposition")
        _need(
            disposition is not None,
            f"{role} seat disposition is not recorded",
            unqualified,
        )
        _need(disposition == RETURNED, f"{role} seat did not return", unqualified)


def _reward(result: Record) -> int:
    valid = result["reward_valid"]
    _need(type(valid) is bool, "native reward_valid is not a bool")
    verifier = result.get("verifier_result")
    _need(
        verifier is None or type(verifier) is dict,
        "native verifier result is malformed",
    )
    rewards = None if verifier is None else cast(Record, verifier).get("rewards")
    if valid is False:
        _need(not rewards, "native reward_valid contradicts its recorded rewards")
        raise _Verdict("incomplete", "native trial recorded no valid reward")
    _need(
        type(rewards) is dict and set(cast(Record, rewards)) == {"reward"},
        "native rewards are not one reward",
    )
    reward = cast(Record, rewards)["reward"]
    _need(
        type(reward) in (int, float) and reward in (0, 1),
        "native reward is not an exact binary scalar",
    )
    return int(reward)


def _replay(folder: Path, fate: str, reward: int) -> Record:
    _need(
        not any(_present(folder, parts) for parts in EARLIER),
        "verifier published an earlier ship-inflight artifact",
    )
    try:
        raw = read_bytes(folder, CUT, verify.stock.MAX_CUT_BYTES)
    except Incomplete:
        raise _Verdict("incomplete", "verified decision cut is missing") from None
    except Invalid:
        raise _Verdict(
            "conflict", "verified decision cut is not a bounded plain file"
        ) from None
    try:
        replay = verify.evaluate(verify.stock.parse_cut(raw))
    except verify.CutError:
        raise _Verdict(
            "conflict", "decision cut does not replay under decision_verify"
        ) from None
    published = _read(
        folder, OBSERVATION, MAX_OBSERVATION_BYTES, "decision observation"
    )
    recorded = _read(folder, REWARD, MAX_REWARD_BYTES, "verifier reward")
    _need(
        same(published, replay), "published observation differs from the replayed cut"
    )
    _need(
        same(recorded, {"reward": reward}),
        "verifier reward differs from the native reward",
    )
    _need(replay["reward"] == reward, "native reward differs from the replayed reward")
    _need(
        (replay["fate"], replay["channel"]) == (fate, CHANNEL),
        "cut world is not the registered case",
    )
    _need(canonical_uuid(replay["run"]), "cut run is not a canonical UUID")
    return replay


def _inspect(
    folder: Path,
    request: Record,
    task: Record,
    full: Record,
    seen: list[str],
    *,
    cite: bool,
    lock_sha256: str,
    client_sha256: str,
) -> Record:
    config = _read(folder, CONFIG, MAX_CONFIG_BYTES, "config.json")
    result = _read(folder, RESULT, MAX_RESULT_BYTES, "result.json")
    identifier = result.get("id")
    _need(canonical_uuid(identifier), "result.json id is not a canonical UUID")
    seen.append(cast(str, identifier))
    _check_config(config, full, "config.json", True)
    _check_result(result, request, task, full)
    fate = cast(str, request["environment"]["env"]["SH_FATE"])
    finished = result["finished_at"]
    if finished is None or finished == "":
        _need(
            result["exception_info"] is None,
            "native trial recorded an exception",
            "seat_unqualified",
        )
        raise _Verdict("incomplete", "native trial has not finished")
    _check_times(result)
    _check_journal(folder, result)
    lock = _read(folder, LOCK, MAX_CONFIG_BYTES, "lock.json")
    _check_lock(lock, request, task, lock_sha256)
    seats = _seats(result)
    _qualify(result, seats)
    _check_receipt(seats[ROLES[1]], cite, client_sha256)
    return _replay(folder, fate, _reward(result))


def _graded(trial: str, replay: Record, fate: str, cite: bool) -> CaseResult:
    want = expected(fate, cite)
    assert want.keys() == set(COMPARED) and set(COMPARED) <= replay.keys()
    differs = [field for field in COMPARED if not same(replay[field], want[field])]
    status = "mismatch" if differs else "verified"
    reason = None if not differs else "observed " + ",".join(differs) + " differ"
    reward, outcome = replay["reward"], replay["outcome"]
    assert type(reward) is int and reward in (0, 1)
    assert status == "mismatch" or (reward, outcome) == GRADES[fate]
    return CaseResult(
        status=status,
        trial=trial,
        world=cast(str, replay["run"]),
        citation=cast(str | None, replay[verify.CITATION]),
        citation_status=cast(str, replay["citation_status"]),
        grade=reward,
        action=cast(str, replay["relief_action"]),
        outcome=cast(str, outcome),
        reason=reason,
    )


def _ungraded(status: str, trial: str | None, reason: str) -> CaseResult:
    assert status in STATUSES and status not in GRADED and reason
    return CaseResult(status, trial, None, None, None, None, None, None, reason)


def inspect_case(
    folder: Path,
    request: Record,
    task: Record,
    *,
    fate: str,
    cite: bool,
    lock_sha256: str,
    client_sha256: str,
) -> CaseResult:
    pin = as_text(lock_sha256, "held lock pin", DIGEST)
    source = as_text(client_sha256, "held client pin", CLIENT_PIN)
    full = _check_inputs(folder, request, task, fate, cite)
    seen: list[str] = []
    try:
        identity = _listing(folder)
    except _Verdict as verdict:
        return _ungraded(verdict.status, None, verdict.reason)
    try:
        replay = _inspect(
            folder,
            request,
            task,
            full,
            seen,
            cite=cite,
            lock_sha256=pin,
            client_sha256=source,
        )
    except _Verdict as verdict:
        outcome = _ungraded(verdict.status, seen[0] if seen else None, verdict.reason)
    else:
        outcome = _graded(seen[0], replay, fate, cite)
    assert len(seen) <= 1
    if not _unchanged(folder, identity):
        return _ungraded(
            "conflict", outcome.trial, "trial folder changed during inspection"
        )
    return outcome
