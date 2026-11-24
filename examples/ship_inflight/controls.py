import argparse
import hashlib
import json
import os
import re
import stat
import sys
from collections import Counter
from contextlib import suppress
from itertools import islice
from pathlib import Path
from typing import NamedTuple, cast

from ..native_evidence import (
    Incomplete,
    Invalid,
    Record,
    canonical_uuid,
    check_binding,
    native_reward,
    read_bytes,
    same,
    seat_map,
    strict_object,
)
from ..native_evidence import require as need
from ..native_study import (
    DIGEST,
    TASK_FILES,
    as_choice,
    as_list,
    as_record,
    as_text,
    check_task,
    record_bytes,
    record_task_files,
    registration_request,
    require,
    task_files_digest,
    write_exclusive,
)
from ..seat_checkpoints.materialize import read_plain
from ..seat_handoffs.native_records import read_record, read_record_with_digest
from . import inflight_verify as trusted
from .materialize import TASK_NAME, check_tree, compose, load_stock

PIN_PATH = Path(__file__).resolve().parent / "samples" / "task-pin.json"
QUESTION_SCHEMA = "harbor.ship-inflight.controls/v1"
PIN_SCHEMA = "harbor.ship-inflight.source/v1"
EMIT_SCHEMA = "harbor.ship-inflight.emitted/v1"
REPORT_SCHEMA = "harbor.ship-inflight.control-score/v1"
ROLES = ("outgoing", "relief")
POLICIES = {
    "outgoing": ("original", "draft", "pass", "idle", "late"),
    "relief": (
        "replace",
        "resume",
        "fresh",
        "close",
        "blind-fresh",
        "blind-close",
        "idle",
        "late-read",
    ),
}
ORACLE = "oracle"
POLICY = "SH_POLICY"
CASES = 16
TICKET = re.compile(r"si-[0-9a-f]{16}")
QUESTION_FIELDS = frozenset(
    {
        "schema",
        "question",
        "task_name",
        "stock_source_tree",
        "eligibility",
        "primary_pass",
        "contrast",
        "cutoff",
        "scope",
        "cases",
    }
)
CASE_KEYS = ("ticket", "outgoing", "relief", "fate", "channel")
CASE_FIELDS = frozenset({*CASE_KEYS, "expected"})
PIN_FIELDS = frozenset({"schema", "question", "task", "task_files", "files"})
EXPECTED = (
    "outgoing_handoff",
    "relief_action",
    "settled",
    "drained",
    "grade_before_drain",
    "reward",
    "outcome",
)
HANDOFFS = ("absent", "original", "draft")
RELIEF_ACTIONS: dict[str, tuple[str | None, str | None]] = {
    "absent": (None, None),
    "close": ("close", None),
    "fresh": ("fresh", None),
    "join-original": ("original", None),
    "join-draft": ("draft", None),
    "replace-original": ("fresh", "original"),
    "replace-draft": ("fresh", "draft"),
}
PACKAGED = ("README.md", "task.toml", "environment", "tests", "solution")
UNPACKAGED = ("instructions",)
DISPOSITIONS = ("returned", "raised", "cancelled", "stop_requested")
STATUSES = (
    "missing",
    "duplicate",
    "quarantined",
    "incomplete",
    "native_error",
    "grade_unknown",
    "conflict",
    "mismatch",
    "disposition_unknown",
    "zero",
    "positive",
)
GRADED = ("native_error", "mismatch", "disposition_unknown", "zero", "positive")
RECEIPTS = ("matched", "absent", "wrong")
MAX_QUESTION_BYTES = 64 * 1024
MAX_PIN_BYTES = 16 * 1024
MAX_ROOTS = 16
MAX_TRIAL_ENTRIES = 4096
MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_CONFIG_BYTES = 1024 * 1024
MAX_OBSERVATION_BYTES = 16 * 1024
MAX_SMALL_BYTES = 4096
MAX_RECEIPT_BYTES = 128
MAX_REASON = 160
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
CONFIG = ("config.json",)
RESULT = ("result.json",)
LOCK = ("lock.json",)
CUT = ("verifier", trusted.CUT_NAME)
OBSERVATION = ("verifier", trusted.OBSERVATION_NAME)
REWARD = ("verifier", trusted.REWARD_NAME)

assert set(trusted.JOINS.values()) | {"close", "fresh"} < set(RELIEF_ACTIONS)
assert set(CASE_KEYS) == set(ROLES) | {"ticket", "fate", "channel"}


class Contract(NamedTuple):
    cases: list[Record]
    question: str
    task: Record
    files: str
    task_dir: Path
    task_paths: frozenset[str]


class Root(NamedTuple):
    path: Path
    receipt: str
    trials: frozenset[str]


class Bound(NamedTuple):
    ticket: str
    env: dict[str, str]
    seats: dict[str, Record]
    task_paths: frozenset[str]
    trials: frozenset[str]


class Judged(NamedTuple):
    observed: Record
    fault: str | None
    dispositions: dict[str, str]


class Verdict(Exception):
    def __init__(
        self, status: str, reason: str, dispositions: dict[str, str] | None = None
    ) -> None:
        assert status in STATUSES and reason
        super().__init__(reason)
        self.status = status
        self.dispositions = dispositions


def spellings(path: Path, label: str) -> tuple[Path, frozenset[str]]:
    lexical = os.path.abspath(path)
    canonical = path.resolve(strict=True)
    left, right = os.stat(lexical), os.stat(canonical)
    require(
        (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino),
        f"{label} spellings name different directories",
    )
    return canonical, frozenset({lexical, str(canonical)})


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_expected(raw: object, fate: str, where: str) -> None:
    expected = as_record(raw, f"{where} expected", frozenset(EXPECTED))
    as_choice(expected["outgoing_handoff"], f"{where} outgoing_handoff", HANDOFFS)
    action = as_choice(
        expected["relief_action"], f"{where} relief_action", RELIEF_ACTIONS
    )
    choice, replaced = RELIEF_ACTIONS[action]
    resolve = None if choice is None else (0, choice)
    settled, drained = trusted.replay_settlement(fate, resolve, replaced)
    outcome = trusted.OUTCOMES[min(len(drained), 2)]
    replayed = {
        "settled": settled,
        "drained": drained,
        "grade_before_drain": int(len(settled) == 1),
        "reward": int(outcome == "exactly_one"),
        "outcome": outcome,
    }
    for field, value in replayed.items():
        require(
            same(expected[field], value),
            f"{where} expected {field} contradicts the trusted settlement replay",
        )


def _check_case(item: object, where: str) -> Record:
    case = as_record(item, where, CASE_FIELDS)
    as_text(case["ticket"], f"{where} ticket", TICKET)
    for role in ROLES:
        as_choice(case[role], f"{where} {role}", POLICIES[role])
    fate = as_choice(case["fate"], f"{where} fate", trusted.FATES)
    as_choice(case["channel"], f"{where} channel", trusted.CHANNELS)
    _check_expected(case["expected"], fate, where)
    return case


def load_question(path: Path) -> tuple[list[Record], str]:
    loaded = read_record_with_digest(path, MAX_QUESTION_BYTES)
    require(loaded is not None, "question file does not exist")
    question, digest = cast(tuple[Record, str], loaded)
    as_record(question, "question", QUESTION_FIELDS)
    require(question["schema"] == QUESTION_SCHEMA, "question schema differs")
    require(question["task_name"] == TASK_NAME, f"question task is not {TASK_NAME}")
    raw = as_list(question["cases"], "cases", CASES, CASES)
    cases = [_check_case(item, f"case {position}") for position, item in enumerate(raw)]
    require(len({case["ticket"] for case in cases}) == CASES, "question repeats a ticket")
    return cases, "sha256:" + digest


def _content_digest(hashes: dict[str, str]) -> str:
    outer = hashlib.sha256()
    for relative in sorted(hashes):
        top = relative.split("/", 1)[0]
        assert top in PACKAGED or top in UNPACKAGED
        if top in PACKAGED:
            outer.update(f"{relative}\0{hashes[relative]}\n".encode("utf-8"))
    return "sha256:" + outer.hexdigest()


def _tree_checksum(hashes: dict[str, str]) -> str:
    parts: dict[str, list[str]] = {"": []}
    for relative, digest in hashes.items():
        segments = relative.split("/")
        parent = "/".join(segments[:-1])
        parts.setdefault(parent, []).append(f"data:{digest}\0name:{segments[-1]}")
        for depth in range(1, len(segments) - 1):
            parts.setdefault("/".join(segments[:depth]), [])
    sealed: dict[str, str] = {}
    for directory in sorted(parts, key=lambda d: d.count("/") + bool(d), reverse=True):
        entries = sorted(parts[directory])
        assert entries
        sealed[directory] = sha256("\0\0".join(entries).encode("utf-8"))
        if directory:
            parent, _, name = directory.rpartition("/")
            parts[parent].append(f"dirhash:{sealed[directory]}\0name:{name}")
    return sealed[""]


def load_pin() -> tuple[Record, str, dict[str, bytes]]:
    pin = read_record(PIN_PATH, MAX_PIN_BYTES)
    require(pin is not None, "task pin does not exist")
    record = as_record(pin, "task pin", PIN_FIELDS)
    require(record["schema"] == PIN_SCHEMA, "task pin schema differs")
    task = check_task(record["task"], TASK_NAME)
    files = as_text(record["task_files"], "task pin task_files", DIGEST)
    composed = compose(load_stock())
    hashes = {relative: sha256(data) for relative, data in composed.items()}
    require(
        same(record["files"], {r: f"sha256:{h}" for r, h in hashes.items()}),
        "task pin files differ from the repository composition",
    )
    require(
        task["digest"] == _content_digest(hashes)
        and task["checksum"] == _tree_checksum(hashes),
        "task pin digest or checksum differs from its files",
    )
    return task, files, composed


def load_contract(question: Path, task_text: str) -> Contract:
    cases, digest = load_question(question)
    task, files, composed = load_pin()
    task_dir, task_paths = spellings(Path(task_text), "task")
    check_tree(task_dir, list(composed))
    for relative, data in composed.items():
        found, _ = read_plain(task_dir / relative)
        require(found == data, f"task file {relative} differs from the pin")
    require(
        task_files_digest(task_dir, task) == files,
        "task package differs from the pinned task_files",
    )
    return Contract(cases, digest, task, files, task_dir, task_paths)


def _participant(seat: str, policy: str) -> Record:
    return {
        "name": ORACLE,
        "skip_model_parsing": False,
        "skills": [],
        "resume_trajectory": False,
        "extra_allowed_hosts": [],
        "kwargs": {},
        "env": {POLICY: policy},
        "mcp_servers": [],
        "seat": seat,
    }


def team(case: Record) -> list[Record]:
    return [_participant(role, case[role]) for role in ROLES]


def world_env(case: Record) -> dict[str, str]:
    return {"SH_FATE": case["fate"], "SH_CHANNEL": case["channel"]}


def new_workspace(text: str) -> Path:
    out = Path(text).absolute()
    require(out.name not in ("", ".", ".."), f"{text} names no new directory")
    workspace = out.parent.resolve(strict=True) / out.name
    require(not os.path.lexists(workspace), f"workspace {workspace} already exists")
    return workspace


def emit(contract: Contract, workspace: Path) -> Record:
    trials = workspace / "trials"
    requests = {
        case["ticket"]: registration_request(
            contract.task_dir, trials, case["ticket"], team(case), world_env(case)
        )
        for case in contract.cases
    }
    configs = workspace / "configs"
    os.mkdir(workspace, 0o755)
    written: list[Path] = []
    digests: dict[str, str] = {}
    try:
        require(
            record_task_files(workspace / TASK_FILES, contract.files),
            f"another writer bound {workspace} first",
        )
        os.mkdir(configs, 0o755)
        for ticket, request in requests.items():
            target = configs / f"{ticket}.json"
            write_exclusive(target, request, 0o644)
            written.append(target)
            digests[ticket] = "sha256:" + sha256(record_bytes(request))
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        (workspace / TASK_FILES).unlink(missing_ok=True)
        with suppress(OSError):
            configs.rmdir()
        with suppress(OSError):
            workspace.rmdir()
        raise
    assert len(digests) == CASES
    return {
        "schema": EMIT_SCHEMA,
        "question": contract.question,
        "task": contract.task,
        "task_files": contract.files,
        "configs": digests,
    }


def _settled(trial: Path, parts: tuple[str, ...], limit: int) -> bytes:
    try:
        return read_bytes(trial, parts, limit)
    except Incomplete as error:
        raise Invalid(f"finished trial: {error}") from None


def _check_config(config: object, bound: Bound, label: str) -> None:
    need(type(config) is dict, f"{label} is not an object")
    record = cast(Record, config)
    need(record.get("trial_name") == bound.ticket, f"{label} trial_name differs")
    task = record.get("task")
    path = task.get("path") if type(task) is dict else None
    need(
        type(path) is str and path in bound.task_paths,
        f"{label} task path is not the pinned task",
    )
    trials = record.get("trials_dir")
    need(type(trials) is str and trials in bound.trials, f"{label} trials_dir differs")
    check_binding(record, bound.env, bound.seats, ROLES, label)


def _check_lock(lock: Record, bound: Bound, task: Record) -> None:
    version = lock.get("schema_version")
    need(type(version) is int and version == 2, "lock.json is not schema version 2")
    pinned = lock.get("task")
    need(type(pinned) is dict, "lock.json has no task")
    pin = cast(Record, pinned)
    need(
        pin.get("type") == "local" and pin.get("digest") == task["digest"],
        "lock.json task digest is not the pin",
    )
    path = pin.get("path")
    need(
        type(path) is str
        and path in bound.task_paths
        and pin.get("name") == os.path.basename(path),
        "lock.json task is not the pinned task path",
    )
    need(lock.get("source_trial") is None, "lock.json derives from another trial")
    need(lock.get("install_only") is False, "lock.json is install-only")
    check_binding(lock, bound.env, bound.seats, ROLES, "lock.json")


def _check_seats(result: Record) -> tuple[dict[str, str], str | None]:
    rows = result.get("seat_results")
    need(
        type(rows) is list and len(cast(list[object], rows)) == len(ROLES),
        "native seat results are not one per role",
    )
    found: dict[str, Record] = {}
    for row in cast(list[object], rows):
        need(type(row) is dict, "native seat result is not an object")
        seat = cast(Record, row)
        role = seat.get("seat")
        need(
            type(role) is str and role in ROLES and role not in found,
            "native seat result role differs",
        )
        need(seat.get("under_test") is True, "native seat is not under test")
        info = seat.get("agent_info")
        need(
            type(info) is dict
            and cast(Record, info).get("name") == ORACLE
            and cast(Record, info).get("model_info") is None,
            "native seat runner is not the registered Oracle",
        )
        need("exception_info" in seat, "native seat error record is absent")
        disposition = seat.get("execution_disposition")
        need(
            disposition is None or disposition in DISPOSITIONS,
            "native seat disposition is malformed",
        )
        found[cast(str, role)] = seat
    dispositions = {
        role: found[role].get("execution_disposition") or "unknown" for role in ROLES
    }
    for role in ROLES:
        if found[role]["exception_info"] is not None:
            return dispositions, f"{role} seat recorded an exception"
    for role in ROLES:
        if dispositions[role] not in ("returned", "unknown"):
            return dispositions, f"{role} seat disposition is {dispositions[role]}"
    return dispositions, None


def _seats(result: Record, fault: str | None) -> tuple[dict[str, str], str | None]:
    try:
        dispositions, seat_fault = _check_seats(result)
    except Invalid:
        if fault is None:
            raise
        raise Verdict("native_error", fault) from None
    return dispositions, fault or seat_fault


def _native(
    trial: Path, bound: Bound, task: Record
) -> tuple[int, str | None, dict[str, str]]:
    config = strict_object(read_bytes(trial, CONFIG, MAX_CONFIG_BYTES), "config.json")
    _check_config(config, bound, "config.json")
    result = strict_object(read_bytes(trial, RESULT, MAX_RESULT_BYTES), "result.json")
    version = result.get("schema_version")
    need(type(version) is int and version == 1, "result.json is not schema version 1")
    need(canonical_uuid(result.get("id")), "result.json id is not a canonical UUID")
    need(result.get("trial_name") == bound.ticket, "result.json trial_name differs")
    need(
        result.get("task_name") == task["name"]
        and result.get("task_checksum") == task["checksum"],
        "result.json task name or checksum is not the pin",
    )
    _check_config(result.get("config"), bound, "result.json config")
    need("exception_info" in result, "native trial error record is absent")
    fault = None
    if result["exception_info"] is not None:
        fault = "native trial recorded an exception"
    finished = result.get("finished_at")
    if finished is None or finished == "":
        if fault is not None:
            raise Verdict("native_error", fault)
        raise Incomplete("native trial has not finished")
    need(type(finished) is str, "result.json finished_at is not a timestamp")
    dispositions, fault = _seats(result, fault)
    lock = strict_object(_settled(trial, LOCK, MAX_CONFIG_BYTES), "lock.json")
    _check_lock(lock, bound, task)
    ungraded = "grade_unknown" if fault is None else "native_error"
    prefix = "" if fault is None else f"{fault}; "
    if result.get("reward_valid") is not True:
        raise Verdict(ungraded, prefix + "native reward_valid is not true", dispositions)
    try:
        return native_reward(result), fault, dispositions
    except Invalid:
        reason = prefix + "native reward is not one scalar 0/1"
        raise Verdict(ungraded, reason, dispositions) from None


def _replay(trial: Path, case: Record, reward: int) -> Record:
    try:
        cut = read_bytes(trial, CUT, trusted.stock.MAX_CUT_BYTES)
    except Incomplete:
        raise Verdict("grade_unknown", "verified cut is missing") from None
    try:
        replay = trusted.evaluate(trusted.stock.parse_cut(cut))
    except trusted.CutError:
        raise Verdict("grade_unknown", "cut does not replay under inflight_verify") from None
    if (replay["fate"], replay["channel"]) != (case["fate"], case["channel"]):
        raise Verdict("conflict", "cut world is not the registered case")
    try:
        published = strict_object(
            read_bytes(trial, OBSERVATION, MAX_OBSERVATION_BYTES), "observation"
        )
        recorded = strict_object(
            read_bytes(trial, REWARD, MAX_SMALL_BYTES), "verifier reward"
        )
    except Incomplete:
        raise Verdict("conflict", "verifier output is incomplete beside its cut") from None
    if not same(published, replay):
        raise Verdict("conflict", "published observation or run differs from the replay")
    if not same(recorded, {"reward": reward}):
        raise Verdict("conflict", "verifier reward.json differs from the native reward")
    if replay["reward"] != reward:
        raise Verdict("conflict", "native reward differs from the replayed reward")
    return {field: replay[field] for field in EXPECTED}


def _judge(root: Root, case: Record, contract: Contract) -> Judged:
    if root.receipt == "wrong":
        raise Verdict("quarantined", "root task.sha256 binds another task package")
    bound = Bound(
        case["ticket"],
        world_env(case),
        seat_map(team(case), "registered team", ROLES),
        contract.task_paths,
        root.trials,
    )
    trial = root.path / "trials" / case["ticket"]
    try:
        reward, fault, dispositions = _native(trial, bound, contract.task)
        try:
            observed = _replay(trial, case, reward)
        except Verdict as verdict:
            if fault is None:
                raise Verdict(verdict.status, str(verdict), dispositions) from None
            reason = f"{fault}; {verdict}"
            raise Verdict("native_error", reason, dispositions) from None
        return Judged(observed, fault, dispositions)
    except Incomplete as error:
        raise Verdict("incomplete", str(error)) from None
    except Invalid as error:
        raise Verdict("quarantined", str(error)) from None


def _graded(case: Record, judged: Judged) -> tuple[str, str | None]:
    if judged.fault is not None:
        return "native_error", f"{judged.fault}; grade verified by replay"
    observed = judged.observed
    differs = [f for f in EXPECTED if not same(observed[f], case["expected"][f])]
    if differs:
        return "mismatch", "observed " + ",".join(differs) + " differ"
    unknown = [role for role in ROLES if judged.dispositions[role] == "unknown"]
    if unknown:
        return "disposition_unknown", ",".join(unknown) + " disposition not recorded"
    return ("positive" if observed["reward"] else "zero"), None


def _row(
    case: Record,
    found: list[tuple[int, bool]],
    duplicated: frozenset[str],
    roots: list[Root],
    contract: Contract,
) -> Record:
    row: Record = {field: case[field] for field in CASE_KEYS}
    judged: Judged | None = None
    dispositions: dict[str, str] | None = None
    reason: str | None = None
    if not found:
        status = "missing"
    elif len(found) > 1 or case["ticket"] in duplicated:
        status, reason = "duplicate", "trial is not one first attempt"
    elif not found[0][1]:
        status, reason = "quarantined", "trial entry is not a plain directory"
    else:
        try:
            judged = _judge(roots[found[0][0]], case, contract)
            dispositions = judged.dispositions
            status, reason = _graded(case, judged)
        except Verdict as verdict:
            status, reason = verdict.status, str(verdict)
            dispositions = verdict.dispositions
    assert status in STATUSES
    assert judged is None or status in GRADED
    observed = None if judged is None else judged.observed
    row["status"] = status
    row["reason"] = None if reason is None else _safe(reason)
    row["dispositions"] = dispositions
    row["grade_source"] = None if observed is None else "native_reward_equals_replay"
    row["reward"] = None if observed is None else observed["reward"]
    row["observed"] = observed
    return row


def _roots(paths: list[Path], files: str) -> list[Root]:
    require(1 <= len(paths) <= MAX_ROOTS, f"give 1 to {MAX_ROOTS} trial roots")
    seen: set[tuple[int, int]] = set()
    roots: list[Root] = []
    for position, path in enumerate(paths, start=1):
        info = os.lstat(path)
        require(stat.S_ISDIR(info.st_mode), f"root {position} is not a plain directory")
        require((info.st_dev, info.st_ino) not in seen, f"root {position} repeats")
        seen.add((info.st_dev, info.st_ino))
        root, names = spellings(path, f"root {position}")
        try:
            receipt = read_bytes(root, (TASK_FILES,), MAX_RECEIPT_BYTES)
            state = "matched" if receipt == f"{files}\n".encode("ascii") else "wrong"
        except Incomplete:
            state = "absent"
        except Invalid:
            state = "wrong"
        roots.append(Root(root, state, frozenset(f"{name}/trials" for name in names)))
    return roots


def _entries(root: Path, position: int) -> list[tuple[str, bool]] | None:
    try:
        trials = os.open(root / "trials", DIRECTORY)
    except FileNotFoundError:
        return None
    except OSError:
        raise ValueError(f"root {position} trials is not a plain directory") from None
    try:
        with os.scandir(trials) as listing:
            entries = [
                (entry.name, entry.is_dir(follow_symlinks=False))
                for entry in islice(listing, MAX_TRIAL_ENTRIES + 1)
            ]
    finally:
        os.close(trials)
    require(
        len(entries) <= MAX_TRIAL_ENTRIES,
        f"root {position} holds over {MAX_TRIAL_ENTRIES} trials",
    )
    return entries


def _claims(
    roots: list[Root], tickets: frozenset[str]
) -> tuple[dict[str, list[tuple[int, bool]]], Record]:
    claims: dict[str, list[tuple[int, bool]]] = {}
    counts = {"with_trials": 0, "claimed_entries": 0, "unregistered_entries": 0}
    for index, root in enumerate(roots):
        entries = _entries(root.path, index + 1)
        if entries is None:
            continue
        counts["with_trials"] += 1
        for name, plain in entries:
            if name in tickets:
                counts["claimed_entries"] += 1
                claims.setdefault(name, []).append((index, plain))
            else:
                counts["unregistered_entries"] += 1
    return claims, counts


def _identifiers(trial: Path) -> set[str]:
    found: set[str] = set()
    for parts, limit, key in (
        (RESULT, MAX_RESULT_BYTES, "id"),
        (CUT, trusted.stock.MAX_CUT_BYTES, "run"),
    ):
        try:
            value = strict_object(read_bytes(trial, parts, limit), "record").get(key)
        except (Incomplete, Invalid):
            continue
        if canonical_uuid(value):
            found.add(cast(str, value))
    return found


def _duplicated(
    claims: dict[str, list[tuple[int, bool]]], roots: list[Root]
) -> frozenset[str]:
    owners: dict[str, set[tuple[str, int]]] = {}
    for ticket, found in claims.items():
        for index, plain in found:
            if not plain:
                continue
            for identifier in _identifiers(roots[index].path / "trials" / ticket):
                owners.setdefault(identifier, set()).add((ticket, index))
    return frozenset(
        ticket for copies in owners.values() if len(copies) > 1 for ticket, _ in copies
    )


def score(contract: Contract, paths: list[Path]) -> Record:
    roots = _roots(paths, contract.files)
    tickets = frozenset(case["ticket"] for case in contract.cases)
    claims, counts = _claims(roots, tickets)
    duplicated = _duplicated(claims, roots)
    rows = [
        _row(case, claims.get(case["ticket"], []), duplicated, roots, contract)
        for case in contract.cases
    ]
    assert len(rows) == CASES
    statuses = Counter(row["status"] for row in rows)
    confirmed = statuses["zero"] + statuses["positive"]
    receipts = Counter(root.receipt for root in roots)
    return {
        "schema": REPORT_SCHEMA,
        "question": contract.question,
        "task": contract.task,
        "task_files": contract.files,
        "registered": CASES,
        "confirmed": confirmed,
        "contract_confirmed": confirmed == CASES,
        "statuses": {status: statuses[status] for status in STATUSES},
        "roots": {
            "given": len(roots),
            **counts,
            "receipts": {state: receipts[state] for state in RECEIPTS},
        },
        "cases": rows,
    }


def render(report: Record) -> str:
    lines = [
        f"confirmed {report['confirmed']}/{report['registered']}"
        f" contract_confirmed={str(report['contract_confirmed']).lower()}",
        " ".join(f"{k}={v}" for k, v in report["statuses"].items()),
        " ".join(f"receipt_{k}={v}" for k, v in report["roots"]["receipts"].items()),
        "\t".join((*CASE_KEYS, "status", "reward", "reason")),
    ]
    for row in report["cases"]:
        cells = [*(row[key] for key in CASE_KEYS), row["status"], row["reward"]]
        cells.append(row["reason"])
        lines.append("\t".join("-" if cell is None else str(cell) for cell in cells))
    return "\n".join(lines)


def _safe(text: str) -> str:
    clean = "".join(char if " " <= char <= "~" else "?" for char in text)
    return clean[:MAX_REASON]


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="python3.12 -BS -m examples.ship_inflight.controls", allow_abbrev=False
    )
    commands = root.add_subparsers(dest="command", required=True)
    emitting = commands.add_parser("emit", allow_abbrev=False)
    emitting.add_argument("question", type=Path)
    emitting.add_argument("task")
    emitting.add_argument("workspace")
    scoring = commands.add_parser("score", allow_abbrev=False)
    scoring.add_argument("question", type=Path)
    scoring.add_argument("task")
    scoring.add_argument("roots", type=Path, nargs="+", metavar="TRIAL_ROOT")
    scoring.add_argument("--json", action="store_true")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        contract = load_contract(args.question, args.task)
        if args.command == "emit":
            record = emit(contract, new_workspace(args.workspace))
            print(json.dumps(record, indent=2, ensure_ascii=True))
            return 0
        report = score(contract, args.roots)
    except OSError as error:
        print(f"controls: {_safe(error.strerror or 'storage failed')}", file=sys.stderr)
        return 2
    except ValueError as error:
        print(f"controls: {_safe(str(error))}", file=sys.stderr)
        return 2
    text = json.dumps(report, indent=2, ensure_ascii=True)
    print(text if args.json else render(report))
    return 0 if report["contract_confirmed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
