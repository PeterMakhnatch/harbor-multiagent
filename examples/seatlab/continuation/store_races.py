from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import random
import re
import stat
import sys
import zipfile
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = "seatlab.continuation-store-races/v1"
QUESTION = (
    "When a seat lists the shared store in the same lockstep step as its partner's first "
    "task-product write, does it later read the partner's value and submit correctly?"
)
PROG = "python3.12 -BS -m examples.seatlab.continuation.store_races"
SOURCE = {
    "title": "Replication package: Unprompted coordination through shared memory in LLM agents, extension E1",
    "record": "https://doi.org/10.5281/zenodo.23965305",
    "license": "data CC BY 4.0",
}
ZIP_SHA256 = "6a9b3cc1af01a65daae70869296aa9a99980c04c8c40c5cc7d8d6a0bc8e2458e"
ZIP_BYTES = 16_123_408
ZIP_ENTRIES = 1_085
MEMBER_MAX_BYTES = 4_000_000
REFERENCE_DIR = "replication-package/reference/extension-e1/exports/"
REPLICATOR_DIR = "replication-package/replicator/e1/outcome/data/"
ASTRA = "openai/gpt-6-astra"
SONNET = "anthropic/claude-sonnet-5"
MINI = "openai/gpt-5.4-mini"
CELLS = {
    1: (ASTRA, "both"),
    2: (ASTRA, "persist-only"),
    3: (SONNET, "both"),
    4: (SONNET, "persist-only"),
    9: (MINI, "both"),
    10: (MINI, "persist-only"),
}
REFERENCE = {
    1: (
        "20261103-2112_sweep-persistence_emergent_lockstep_astra_b20_w10.json",
        "413db089dd66ab674bb53759dc925bc487abffa945fb5c06e94189f6dc7e8006",
    ),
    2: (
        "20261103-2158_sweep-persistence_emergent_lockstep_astra_b20_w10.json",
        "083bfcbf5043399a179c265c2a454506c4967af10d0386613b4aa9fc7c837bd1",
    ),
    3: (
        "20261103-2229_sweep-persistence_emergent_lockstep_sonnet_b20_w10.json",
        "4c01dee457d7290fd375c6d55a5fce09f7d97a0337931df2a285176021a56ba2",
    ),
    4: (
        "20261103-2251_sweep-persistence_emergent_lockstep_sonnet_b20_w10.json",
        "2e473a5ed2cd58834624ec672c84d5d4112abe62fcbdf74820f4916f69717cc3",
    ),
    9: (
        "20261104-0140_sweep-persistence_emergent_lockstep_mini_b20_w10.json",
        "c3dcfbfe8b4db792e5ff762f5c80b6e6298085744c767d71b29c51ab6537ee04",
    ),
    10: (
        "20261104-0205_sweep-persistence_emergent_lockstep_mini_b20_w10.json",
        "d8736c29745e1c803d791bf6a3ae75892442ab56b5b4a05df1b4b89350bd5816",
    ),
}
REPLICATOR = {
    1: (
        "01-gpt-6-astra-shared-both",
        "3f7a33a46ea3949b0be838b8f464a590b0f439bdde70c247c291e99b12d8847c",
        "7fe4a3c6d0d27e1fd44e3e180010f83bc9dc467709c07227bd75d54b24a6eef4",
    ),
    2: (
        "02-gpt-6-astra-shared-persist-only",
        "142e7d4cf5f318fd4dca88db90f9b6e7c5f57e758c7ed7e01494f91566d6ba95",
        "1c2de315080f2345e0c6571d56248f16a6e4f3aa8933dac72b61ccf629d84757",
    ),
    3: (
        "03-claude-sonnet-5-shared-both",
        "5a7ce1ea16b00fabebfd4ea27b85eae45898be534e7e48e548cf22e2afd66ddb",
        "78cb8e816c8531e33b47aa87c86e2ff7e36714c04b901eaea736a3febb688381",
    ),
    4: (
        "04-claude-sonnet-5-shared-persist-only",
        "12678fb7b12438ff18bb8ab4b66e801805dd2360a615b8d2ff35933eab97bf92",
        "1c740bf65b5f44208a6be16c2a62fda08c0761559d168329ba6224112bf6aa7d",
    ),
    9: (
        "09-gpt-5.4-mini-shared-both",
        "0e81300a6526f9183d70c71c063b5e1d67ef879a46cb87912bafd1ded6b0fc30",
        "15838cc28ae607a0c7da8fe354ef54958bbd7731f8b1fba52c2f307235a580c3",
    ),
    10: (
        "10-gpt-5.4-mini-shared-persist-only",
        "26c52e6f47631d51583d283090d440348eed89d4c953af7d2fd7689e560ab211",
        "f1be97bdb042cf9a619bb197aa76b4309cc58ac0f28953c9d30d05a5bb516471",
    ),
}
IMPLEMENTATIONS = ("reference", "replicator")
ARMS = ("both", "persist-only")
PROMPTED = "both"
THRESHOLD = 20
WORLDS = 50
REPLICATES = 5
AGENTS = 4
STEP_CEILING = 20
REFERENCE_WORLD_STEPS = AGENTS * STEP_CEILING * 2
REPLICATOR_WORLD_ROWS = AGENTS * STEP_CEILING * 2 * 4
READ_LIMIT = 800
TOOLS = frozenset({"list", "read", "write", "submit"})
BAD_STATUS = frozenset({"invalidated", "error"})
MISSING = -1
UNRESOLVED = -2
LIST_PREFIX = "keys: "
REFERENCE_MISSING = "no value stored at that key"
REPLICATOR_SAVED = "write -> saved"
REPLICATOR_READ = "read -> "
REPLICATOR_MISSING = "read -> no value stored at that key"
SELF_CONTAINED = "self_contained"
TIMESTAMP = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?\+00:00"
)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
TIMINGS = ("hit", "miss", "tie")
READS = ("read", "not_read", "no_submission", "unknown")
FINALS = ("correct", "incorrect", "no_submission", "unknown")
VISIBILITY = ("visible", "not_visible", "unknown")
ERRORS = (
    OSError,
    ValueError,
    TypeError,
    LookupError,
    ArithmeticError,
    RecursionError,
    EOFError,
    zipfile.BadZipFile,
    zlib.error,
)
OMITTED = (
    {
        "implementation": "replicator",
        "category": "race.hit.key_visibility",
        "reason": "the replicator step log keeps the list as one joined string without per-key authorship, so whether the partner key was shown is not proven",
    },
    {
        "implementation": "both",
        "category": "placebo key_visibility",
        "reason": "not part of the registered control",
    },
    {
        "implementation": "both",
        "category": "role labels A/B",
        "reason": "the reference export does not label pair roles; units are pooled over both interdependent roles",
    },
)
LIMITATIONS = (
    "descriptive counts only; list timing within a lockstep step is not assigned by design, so hits and misses are not a causal contrast",
    "applied order uses recorded store-operation timestamps: reference step timestamp (ISO 8601, microseconds), replicator operation_timestamp_ns; exact equal timestamps are ties and stay out of hits and misses",
    "the reference read author is rebuilt from earlier applied writes and checked against the returned value; a read that cannot be matched counts as unknown, never as not_read",
    "replicator task symbols are decoded from the recorded task seed with generator g2; a world whose decoded seat order differs from the logged roles is unidentified and omitted",
    "final correctness is the archive's per-seat grader flag; seats without a submission are no_submission, not incorrect",
    "worlds, cells and arms are counted from step logs and per-seat status only; no author world flags, manifests or paper counts are used",
    "prompts, model text, keys, values and run, world or seat identifiers are never exported",
)


class Refusal(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Op:
    agent: int
    step: int
    time: int
    tool: str
    key: str | None
    ok: bool
    origin: int
    shown: tuple[str, ...] | None
    listing: str | None


@dataclass(frozen=True, slots=True)
class World:
    needs: dict[int, str]
    solo: tuple[int, int]
    correct: dict[int, bool | None]
    ops: tuple[Op, ...]


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise Refusal(reason)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _archive(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        _require(stat.S_ISREG(info.st_mode), "not_regular_file")
        _require(info.st_size == ZIP_BYTES, "zip_size")
        data = handle.read(ZIP_BYTES + 1)
    _require(len(data) == ZIP_BYTES, "zip_size")
    _require(_sha256(data) == ZIP_SHA256, "zip_sha256")
    return data


def _member(archive: zipfile.ZipFile, name: str, digest: str) -> Any:
    info = archive.getinfo(name)
    _require(
        info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
        "member_compression",
    )
    _require(info.flag_bits & 1 == 0, "member_encrypted")
    _require(0 < info.file_size <= MEMBER_MAX_BYTES, "member_size")
    _require(info.compress_size <= MEMBER_MAX_BYTES, "member_compressed_size")
    with archive.open(info) as handle:
        data = handle.read(MEMBER_MAX_BYTES + 1)
    _require(len(data) == info.file_size, "member_length")
    _require(_sha256(data) == digest, "member_sha256")
    return json.loads(
        data.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant
    )


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    _require(len(result) == len(pairs), "duplicate_key")
    return result


def _constant(_name: str) -> Any:
    raise Refusal("json_constant")


def _object(value: Any, reason: str) -> dict[str, Any]:
    _require(type(value) is dict, reason)
    return value


def _array(value: Any, limit: int, reason: str) -> list[Any]:
    _require(type(value) is list and len(value) <= limit, reason)
    return value


def _index(value: Any, bound: int, reason: str) -> int:
    _require(type(value) is int and 0 <= value < bound, reason)
    return value


def _micros(text: Any) -> int:
    _require(type(text) is str, "timestamp")
    match = TIMESTAMP.fullmatch(text)
    if match is None:
        raise Refusal("timestamp")
    parts = [int(match[position]) for position in range(1, 7)]
    moment = datetime(*parts, tzinfo=UTC)
    fraction = int((match[7] or "").ljust(6, "0"))
    return (moment - EPOCH) // timedelta(microseconds=1) + fraction


def _flag(value: Any) -> bool | None:
    return value if type(value) is bool else None


def _agents(
    world: dict[str, Any], kinds: dict[str, str], kind_field: str
) -> dict[int, dict[str, Any]]:
    rows = _array(world["agents"], AGENTS, "agents")
    _require(len(rows) == AGENTS, "agent_count")
    agents: dict[int, dict[str, Any]] = {}
    for row in rows:
        agent = _object(row, "agent")
        index = _index(agent["index"], AGENTS, "agent_index")
        _require(index not in agents, "agent_duplicate")
        _require(type(agent["status"]) is str, "agent_status")
        kind = agent["task"]["category"] if kind_field == "task" else agent["task_type"]
        _require(kind in kinds, "agent_kind")
        agents[index] = {
            "kind": kinds[kind],
            "status": agent["status"],
            "correct": _flag(agent["submission_correct"]),
            "row": agent,
        }
    return agents


def _seats(agents: dict[int, dict[str, Any]]) -> tuple[list[int], list[int]] | None:
    pair = sorted(index for index, agent in agents.items() if agent["kind"] == "pair")
    solo = sorted(index for index, agent in agents.items() if agent["kind"] == "solo")
    if len(pair) != 2 or len(solo) != 2:
        return None
    return pair, solo


def _reference_pair(
    agents: dict[int, dict[str, Any]], pair: list[int]
) -> dict[int, str] | None:
    terms: dict[int, tuple[str, str, Any]] = {}
    for index in pair:
        link = agents[index]["row"]["task"]["interdependency"]
        if type(link) is not dict:
            return None
        needs, produces = link["needs"], link["produces"]
        if type(needs) is not list or len(needs) != 1 or type(needs[0]) is not str:
            return None
        if type(produces) is not dict or len(produces) != 1:
            return None
        produced = next(iter(produces))
        if not isinstance(produced, str):
            return None
        terms[index] = (needs[0], produced, link["group_id"])
    first, second = pair
    if terms[first][0] != terms[second][1] or terms[second][0] != terms[first][1]:
        return None
    if terms[first][0] == terms[second][0] or terms[first][2] != terms[second][2]:
        return None
    if type(terms[first][2]) is not str:
        return None
    return {first: terms[first][0], second: terms[second][0]}


def _reference_rows(
    steps: list[Any],
) -> list[tuple[int, int, int, str, Any, Any, str, Any]]:
    rows = []
    seen: set[tuple[int, int]] = set()
    for item in steps:
        step = _object(item, "step")
        invalid = step["invalid"]
        _require(type(invalid) is bool, "step_invalid")
        if invalid:
            continue
        agent = _index(step["agent_index"], AGENTS, "step_agent")
        index = _index(step["step_index"], STEP_CEILING, "step_index")
        _require((agent, index) not in seen, "step_duplicate")
        seen.add((agent, index))
        action = _object(step["action"], "action")
        tool = action["tool"]
        _require(tool in TOOLS, "tool")
        arguments = _object(action["args"], "args")
        result = _object(step["result"], "result")
        raw = result["raw"]
        _require(type(raw) is str, "raw")
        shown = _array(result["returned_foreign_keys"], READ_LIMIT, "foreign_keys")
        _require(all(type(key) is str for key in shown), "foreign_key")
        rows.append(
            (
                agent,
                index,
                _micros(step["timestamp"]),
                tool,
                arguments.get("key"),
                arguments.get("value"),
                raw,
                shown,
            )
        )
    return rows


def _reference_origin(
    writes: list[tuple[int, int, str, str]], key: str, time: int, returned: str | None
) -> int:
    prior = [write for write in writes if write[2] == key and write[0] <= time]
    if any(write[0] == time for write in prior):
        return UNRESOLVED
    if not prior:
        return MISSING if returned is None else UNRESOLVED
    if returned is None:
        return UNRESOLVED
    latest = max(write[0] for write in prior)
    last = [write for write in prior if write[0] == latest]
    if len(last) != 1:
        return UNRESOLVED
    value = last[0][3]
    if returned == value or (
        len(value) > READ_LIMIT and returned == value[:READ_LIMIT]
    ):
        return last[0][1]
    return UNRESOLVED


def _reference_ops(steps: Any) -> tuple[Op, ...]:
    rows = _reference_rows(_array(steps, REFERENCE_WORLD_STEPS, "steps"))
    writes = []
    for agent, _step, time, tool, key, value, raw, _listed in rows:
        if (
            tool == "write"
            and type(key) is str
            and type(value) is str
            and raw == f'write("{key}") -> saved'
        ):
            writes.append((time, agent, key, value))
    ops = []
    for agent, index, time, tool, key, value, raw, shown in rows:
        ok, origin, listed, listing = False, MISSING, None, None
        if tool == "write":
            ok = (time, agent, key, value) in writes
        elif tool == "list":
            ok = raw.startswith(LIST_PREFIX)
            listed, listing = (
                (tuple(shown), raw[len(LIST_PREFIX) :]) if ok else (None, None)
            )
        elif tool == "read" and type(key) is str:
            prefix = f'read("{key}") -> '
            ok = raw.startswith(prefix)
            returned = raw[len(prefix) :] if ok else None
            if ok:
                origin = _reference_origin(
                    writes,
                    key,
                    time,
                    None if returned == REFERENCE_MISSING else returned,
                )
            else:
                origin = UNRESOLVED
        elif tool == "submit":
            ok = True
        ops.append(
            Op(
                agent,
                index,
                time,
                tool,
                key if type(key) is str else None,
                ok,
                origin,
                listed,
                listing,
            )
        )
    return tuple(ops)


def _reference_world(world: dict[str, Any]) -> World | str:
    agents = _agents(
        world, {"interdependent": "pair", "solvable_alone": "solo"}, "task"
    )
    _require(type(world["status"]) is str, "world_status")
    pair = [index for index, agent in agents.items() if agent["kind"] == "pair"]
    if world["status"] != "complete" or any(
        agents[index]["status"] in BAD_STATUS for index in pair
    ):
        return "invalid"
    seats = _seats(agents)
    needs = _reference_pair(agents, seats[0]) if seats is not None else None
    if seats is None or needs is None:
        return "unidentified"
    return World(
        needs=needs,
        solo=(seats[1][0], seats[1][1]),
        correct={index: agents[index]["correct"] for index in seats[0]},
        ops=_reference_ops(world["steps"]),
    )


def _reference_cell(document: Any, model: str, arm: str) -> list[World | str]:
    experiment = _object(_object(document, "document")["experiment"], "experiment")
    _require(experiment["task_mode"] == "emergent", "task_mode")
    _require(experiment["models"]["agent_model"] == model, "model")
    config = _object(experiment["fixed_config"], "fixed_config")
    _require(config["persistence_clause"] == arm, "arm")
    _require(config["scheduler_mode"] == "lockstep", "scheduler")
    points = _array(document["points"], 1, "points")
    _require(len(points) == 1, "points")
    replicates = _array(points[0]["replicates"], REPLICATES, "replicates")
    _require(len(replicates) == REPLICATES, "replicates")
    worlds: list[World | str] = []
    for replicate in replicates:
        rows = _array(replicate["run"]["worlds"], WORLDS, "worlds")
        for world in rows:
            worlds.append(_reference_world(_object(world, "world")))
    _require(len(worlds) == WORLDS, "world_count")
    return worlds


def _decode_seats(
    seed: Any, roles: dict[int, str], agents: dict[int, dict[str, Any]]
) -> tuple[dict[int, str], tuple[int, int]] | None:
    if type(seed) is not int:
        return None
    order = ["A", "B", SELF_CONTAINED, SELF_CONTAINED]
    random.Random(seed).shuffle(order)
    for index in range(AGENTS):
        if roles.get(index) != order[index]:
            return None
        if (agents[index]["kind"] == "solo") != (order[index] == SELF_CONTAINED):
            return None
    letter = "C" if random.Random(seed).randrange(2) == 0 else "R"
    first, second = order.index("A"), order.index("B")
    solo = [index for index in range(AGENTS) if order[index] == SELF_CONTAINED]
    return {first: f"{letter}_B", second: f"{letter}_A"}, (solo[0], solo[1])


def _replicator_op(row: dict[str, Any], tool: str) -> Op:
    _require(row["transport_error"] is None and row["parse_error"] is None, "row_error")
    agent = _index(row["agent_index"], AGENTS, "row_agent")
    index = _index(row["step_index"], STEP_CEILING, "row_step")
    time = row["operation_timestamp_ns"]
    _require(type(time) is int and time > 0, "operation_timestamp_ns")
    observation = row["observation"]
    _require(type(observation) is str, "observation")
    key = row["key"] if type(row["key"]) is str else None
    ok, origin, author = False, MISSING, row["returned_author"]
    if tool == "write":
        ok = key is not None and observation == REPLICATOR_SAVED
    elif tool == "list":
        ok = observation.startswith(LIST_PREFIX)
    elif tool == "read":
        _require(row["seeded_read"] is False, "seeded_read")
        ok = key is not None and observation.startswith(REPLICATOR_READ)
        if ok and observation != REPLICATOR_MISSING:
            origin = (
                author if type(author) is int and 0 <= author < AGENTS else UNRESOLVED
            )
        elif observation != REPLICATOR_MISSING:
            origin = UNRESOLVED
    else:
        ok = True
    return Op(agent, index, time, tool, key, ok, origin, None, None)


def _replicator_ops(
    rows: list[dict[str, Any]],
) -> tuple[tuple[Op, ...], dict[int, str]]:
    ops: list[Op] = []
    roles: dict[int, set[str]] = {}
    seen: set[tuple[int, int]] = set()
    for row in rows:
        agent = _index(row["agent_index"], AGENTS, "row_agent")
        _require(type(row["task_role"]) is str, "task_role")
        roles.setdefault(agent, set()).add(row["task_role"])
        tool = row["action"]
        if tool is None:
            _require(row["operation_timestamp_ns"] is None, "null_action_time")
            continue
        _require(tool in TOOLS, "action")
        op = _replicator_op(row, tool)
        _require((op.agent, op.step) not in seen, "row_duplicate")
        seen.add((op.agent, op.step))
        ops.append(op)
    single = {
        agent: next(iter(found)) for agent, found in roles.items() if len(found) == 1
    }
    return tuple(ops), single


def _replicator_world(world: dict[str, Any], rows: list[dict[str, Any]]) -> World | str:
    agents = _agents(
        world, {"interdependent": "pair", SELF_CONTAINED: "solo"}, "task_type"
    )
    pair = [index for index, agent in agents.items() if agent["kind"] == "pair"]
    if any(agents[index]["status"] in BAD_STATUS for index in pair):
        return "invalid"
    ops, roles = _replicator_ops(rows)
    if _seats(agents) is None:
        return "unidentified"
    decoded = _decode_seats(world["task_seed"], roles, agents)
    if decoded is None:
        return "unidentified"
    needs, solo = decoded
    return World(
        needs=needs,
        solo=solo,
        correct={index: agents[index]["correct"] for index in needs},
        ops=ops,
    )


def _replicator_cell(
    worlds_document: Any, steps_document: Any, model: str, arm: str
) -> list[World | str]:
    header = _object(worlds_document, "worlds_document")
    _require(header["phase"] == "E1" and header["tag"] == "data", "phase")
    _require(
        header["condition"] == "store" and header["store_mode"] == "shared",
        "store_mode",
    )
    _require(header["model"] == model and header["sentence"] == arm, "cell")
    steps = _object(steps_document, "steps_document")
    _require(steps["run_id"] == header["run_id"], "run_pairing")
    rows = _array(steps["steps"], WORLDS * REPLICATOR_WORLD_ROWS, "rows")
    worlds = _array(header["worlds"], WORLDS, "worlds")
    _require(len(worlds) == WORLDS, "world_count")
    keys = []
    for world in worlds:
        item = _object(world, "world")
        keys.append(
            (
                _index(item["replicate"], REPLICATES, "replicate"),
                _index(item["world"], WORLDS, "world"),
            )
        )
    _require(len(set(keys)) == WORLDS, "world_duplicate")
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = {key: [] for key in keys}
    for row in rows:
        item = _object(row, "row")
        key = (item["replicate"], item["world_index"])
        _require(key in grouped, "row_world")
        _require(len(grouped[key]) < REPLICATOR_WORLD_ROWS, "world_rows")
        grouped[key].append(item)
    return [
        _replicator_world(world, grouped[key])
        for world, key in zip(worlds, keys, strict=True)
    ]


def _ordered(ops: tuple[Op, ...]) -> dict[int, list[Op]]:
    lanes: dict[int, list[Op]] = {index: [] for index in range(AGENTS)}
    for op in ops:
        lanes[op.agent].append(op)
    for lane in lanes.values():
        lane.sort(key=lambda op: (op.step, op.time))
    return lanes


def _first_write(lane: list[Op], key: str | None) -> Op | None:
    for op in lane:
        if op.tool == "write" and op.ok and (key is None or op.key == key):
            return op
    return None


def _list_at(lane: list[Op], step: int) -> Op | None:
    for op in lane:
        if op.step == step:
            return op if op.tool == "list" and op.ok else None
    return None


def _submit(lane: list[Op]) -> Op | None:
    submits = [op for op in lane if op.tool == "submit"]
    _require(len(submits) <= 1, "submit_count")
    _require(not submits or submits[0] is lane[-1], "submit_not_last")
    return submits[0] if submits else None


def _timing(listing: Op, write: Op) -> str:
    _require(listing.step == write.step, "race_step")
    if listing.time > write.time:
        return "hit"
    if listing.time < write.time:
        return "miss"
    return "tie"


def _required_read(lane: list[Op], after: int, key: str, partner: int) -> str:
    submit = _submit(lane)
    if submit is None:
        return "no_submission"
    found = "not_read"
    for op in lane:
        if op.tool != "read" or op.key != key or not after < op.step < submit.step:
            continue
        if op.origin == partner:
            return "read"
        if op.origin == UNRESOLVED:
            found = "unknown"
    return found


def _final(lane: list[Op], correct: bool | None) -> str:
    if _submit(lane) is None:
        return "no_submission"
    if correct is None:
        return "unknown"
    return "correct" if correct else "incorrect"


def _visibility(listing: Op, key: str) -> str:
    if listing.shown is None or listing.listing is None:
        return "unknown"
    if key in listing.shown:
        return "visible"
    if key not in listing.listing:
        return "not_visible"
    return "unknown"


def _group(visibility: bool) -> dict[str, Any]:
    group: dict[str, Any] = {
        "n": 0,
        "required_read": dict.fromkeys(READS, 0),
        "final": dict.fromkeys(FINALS, 0),
    }
    if visibility:
        group["key_visibility"] = dict.fromkeys(VISIBILITY, 0)
    return group


def _tally(visibility: bool) -> dict[str, Any]:
    return {
        "worlds": dict.fromkeys(
            ("launched", "valid", "invalid", "identified", "unidentified"), 0
        ),
        "focal_units": 0,
        "race": {
            "partner_product_write": 0,
            "no_partner_product_write": 0,
            "no_same_step_list": 0,
            "hit": _group(visibility),
            "miss": _group(False),
            "tie": _group(False),
        },
        "placebo": {
            "no_candidate": 0,
            "hit": _group(False),
            "miss": _group(False),
            "tie": _group(False),
        },
    }


def _record(
    group: dict[str, Any],
    lane: list[Op],
    listing: Op,
    key: str,
    partner: int,
    correct: bool | None,
) -> None:
    group["n"] += 1
    group["required_read"][_required_read(lane, listing.step, key, partner)] += 1
    group["final"][_final(lane, correct)] += 1
    if "key_visibility" in group:
        group["key_visibility"][_visibility(listing, key)] += 1


def _race(
    tally: dict[str, Any],
    lanes: dict[int, list[Op]],
    world: World,
    focal: int,
    partner: int,
) -> None:
    race = tally["race"]
    key = world.needs[focal]
    write = _first_write(lanes[partner], key)
    if write is None:
        race["no_partner_product_write"] += 1
        return
    race["partner_product_write"] += 1
    listing = _list_at(lanes[focal], write.step)
    if listing is None:
        race["no_same_step_list"] += 1
        return
    _record(
        race[_timing(listing, write)],
        lanes[focal],
        listing,
        key,
        partner,
        world.correct[focal],
    )


def _placebo(
    tally: dict[str, Any],
    lanes: dict[int, list[Op]],
    world: World,
    focal: int,
    partner: int,
) -> None:
    placebo = tally["placebo"]
    candidates = []
    for seat in world.solo:
        write = _first_write(lanes[seat], None)
        listing = _list_at(lanes[focal], write.step) if write is not None else None
        if write is not None and listing is not None:
            candidates.append((write.step, write.time, seat, write, listing))
    if not candidates:
        placebo["no_candidate"] += 1
        return
    _, _, _, write, listing = min(candidates, key=lambda item: item[:3])
    _record(
        placebo[_timing(listing, write)],
        lanes[focal],
        listing,
        world.needs[focal],
        partner,
        world.correct[focal],
    )


def _score(tally: dict[str, Any], worlds: list[World | str]) -> None:
    _require(len(worlds) == WORLDS, "world_count")
    counts = tally["worlds"]
    for world in worlds:
        counts["launched"] += 1
        if world == "invalid":
            counts["invalid"] += 1
            continue
        counts["valid"] += 1
        if isinstance(world, str):
            _require(world == "unidentified", "world_state")
            counts["unidentified"] += 1
            continue
        counts["identified"] += 1
        lanes = _ordered(world.ops)
        first, second = sorted(world.needs)
        for focal, partner in ((first, second), (second, first)):
            tally["focal_units"] += 1
            _race(tally, lanes, world, focal, partner)
            _placebo(tally, lanes, world, focal, partner)


def _add_group(total: dict[str, Any], part: dict[str, Any]) -> None:
    total["n"] += part["n"]
    for field in ("required_read", "final", "key_visibility"):
        for name, value in part.get(field, {}).items():
            total[field][name] += value


def _add(total: dict[str, Any], part: dict[str, Any]) -> None:
    for name, value in part["worlds"].items():
        total["worlds"][name] += value
    total["focal_units"] += part["focal_units"]
    for section in ("race", "placebo"):
        for name, value in part[section].items():
            if type(value) is int:
                total[section][name] += value
            else:
                _add_group(total[section][name], value)


def _scan(data: bytes) -> list[dict[str, Any]]:
    cells = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        _require(len(infos) == ZIP_ENTRIES, "zip_entries")
        _require(
            len({info.filename for info in infos}) == ZIP_ENTRIES, "duplicate_member"
        )
        for cell, (model, arm) in CELLS.items():
            name, digest = REFERENCE[cell]
            worlds = _reference_cell(
                _member(archive, REFERENCE_DIR + name, digest), model, arm
            )
            tally = _tally(True)
            _score(tally, worlds)
            cells.append(
                {
                    "implementation": "reference",
                    "cell": cell,
                    "model": model,
                    "arm": arm,
                }
                | tally
            )
            folder, steps_digest, worlds_digest = REPLICATOR[cell]
            worlds = _replicator_cell(
                _member(
                    archive, f"{REPLICATOR_DIR}{folder}/worlds.json", worlds_digest
                ),
                _member(archive, f"{REPLICATOR_DIR}{folder}/steps.json", steps_digest),
                model,
                arm,
            )
            tally = _tally(False)
            _score(tally, worlds)
            cells.append(
                {
                    "implementation": "replicator",
                    "cell": cell,
                    "model": model,
                    "arm": arm,
                }
                | tally
            )
    return cells


def _arms(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for implementation in IMPLEMENTATIONS:
        for arm in ARMS:
            total = _tally(implementation == "reference")
            for row in cells:
                if row["implementation"] == implementation and row["arm"] == arm:
                    _add(total, row)
            rows.append(
                {
                    "implementation": implementation,
                    "arm": arm,
                    "cells": sorted(
                        cell for cell, value in CELLS.items() if value[1] == arm
                    ),
                }
                | total
            )
    return rows


def _threshold(arms: list[dict[str, Any]]) -> dict[str, Any]:
    implementations = {}
    for row in arms:
        if row["arm"] != PROMPTED:
            continue
        hits, misses = row["race"]["hit"]["n"], row["race"]["miss"]["n"]
        implementations[row["implementation"]] = {
            "hits": hits,
            "narrow_misses": misses,
            "met": hits >= THRESHOLD and misses >= THRESHOLD,
        }
    _require(set(implementations) == set(IMPLEMENTATIONS), "threshold_implementations")
    return {
        "rule": f"continue only if every implementation has at least {THRESHOLD} race hits and {THRESHOLD} narrow misses in the prompted (both) shared cells",
        "implementations": implementations,
        "continue": all(row["met"] for row in implementations.values()),
    }


def _design() -> dict[str, Any]:
    return {
        "cells": "E1 public shared-store cells 1, 2, 3, 4, 9, 10 in the reference exports and the replicator data, lockstep scheduler",
        "valid_world": "neither interdependent seat ended invalidated or in error (reference also requires world status complete); every launched world counted",
        "identification": "reference: the two interdependent seats whose single needed key is exactly the other's single produced key in one group; replicator: task symbols decoded from the recorded task seed, accepted only when the decoded seat order equals the logged roles",
        "steps": "invalid attempts, null-action rows and transport failures are omitted; one applied operation per seat and step",
        "race": "focal seat's successful list in the same scheduler step_index as the partner's first successful write to the exact key the focal task needs; one unit per focal seat",
        "timing": "hit: list applied after the partner write; miss (narrow): list applied before it; tie: equal applied timestamps, reported apart",
        "required_read": "read: a later focal read of the required key returning the partner's value before the focal submission; not_read: submitted without one; no_submission: no submission; unknown: a matching read whose author cannot be proven",
        "final": "archive per-seat grader flag for the focal submission: correct, incorrect, no_submission, unknown when the flag is absent",
        "key_visibility": "reference race hits only: visible when the focal list's recorded foreign keys include the required key; not_visible when the key is absent from the listing; otherwise unknown",
        "placebo": "same rule with each self-contained seat's first successful write of any key; per focal seat only the earliest qualifying candidate by step and applied time; still scored for a later read of the true partner value",
    }


def analyze(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "question": QUESTION,
        "status": "incomplete",
        "source": SOURCE | {"zip_sha256": ZIP_SHA256, "zip_bytes": ZIP_BYTES},
        "design": _design(),
        "error": None,
        "cells": None,
        "arms": None,
        "threshold": None,
        "omitted": list(OMITTED),
        "limitations": list(LIMITATIONS),
    }
    stage = "archive"
    try:
        data = _archive(Path(path))
        stage = "cells"
        cells = _scan(data)
        stage = "summary"
        arms = _arms(cells)
        threshold = _threshold(arms)
    except Refusal as error:
        return result | {"error": {"stage": stage, "reason": str(error)}}
    except ERRORS as error:
        return result | {"error": {"stage": stage, "reason": type(error).__name__}}
    return result | {
        "status": "complete",
        "cells": cells,
        "arms": arms,
        "threshold": threshold,
    }


def _encode(result: dict[str, Any]) -> bytes:
    return (
        json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
    ).encode()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Count same-step store races and placebo races in the pinned E1 shared cells.",
        allow_abbrev=False,
    )
    parser.add_argument("release", type=Path, metavar="RELEASE_ZIP")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    result = analyze(args.release)
    sys.stdout.buffer.write(_encode(result))
    sys.stdout.buffer.flush()
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
