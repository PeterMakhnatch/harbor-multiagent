from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import signal
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import source

SourceError = source.SourceError

INDEX_SHA256 = "68cc6ae215bd81fb94ac88ae43d42387409d1eb6f6bb7fff40b2923870295282"
PROG = "python3.12 -BS -m examples.seatlab.continuation.receipt_source"
CHECKOUT = Path(__file__).resolve().parents[3]
TEAM_SIZE = 2
SPLITS = {"calibration": (1,), "heldout": (2, 3)}
AGENT_SCHEMA = "ATIF steps, shortened for the site"
AGENT_MAX_BYTES = 8 << 20
AGENT_MAX_JSON_BYTES = 32 << 20
GZIP_ENCODINGS = frozenset({"identity", "gzip"})
RUN_AGENTS = 1 + TEAM_SIZE
STEP_TEXT_FIELDS = ("source", "timestamp", "message")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SourceError(message)


def agent_member(run: str, seat: int) -> str:
    _require(1 <= seat <= TEAM_SIZE, f"seat number {seat} outside 1..{TEAM_SIZE}")
    return f"public-rollout-data/trajectories/{source.TASK}/{run}/agent-{seat}.json.gz"


def _check_bounds() -> None:
    _require(
        AGENT_MAX_JSON_BYTES == source.BOARD_MAX_JSON_BYTES,
        "agent JSON bound differs from the shared gunzip bound",
    )
    _require(
        0 < AGENT_MAX_BYTES <= AGENT_MAX_JSON_BYTES,
        "agent gzip bound must be positive and at most the JSON bound",
    )
    _require(set(SPLITS["calibration"]).isdisjoint(SPLITS["heldout"]), "splits overlap")
    _require(
        sorted(SPLITS["calibration"] + SPLITS["heldout"]) == list(source.ROLLOUTS),
        "splits must cover the released rollouts exactly",
    )


def _check_cache(cache: Path) -> Path:
    _require(isinstance(cache, Path), "cache: expected a Path")
    resolved = cache.resolve()
    _require(
        not resolved.is_relative_to(CHECKOUT),
        f"{resolved}: cache must be outside the checkout {CHECKOUT}",
    )
    return resolved


def _member_bytes(cache: Path, member: str, limit: int) -> bytes:
    path = cache / member
    for part in (path, *path.parents):
        if part == cache or not part.is_relative_to(cache):
            break
        _require(not part.is_symlink(), f"{member}: cache path contains a symlink")
    try:
        data = source._read_member(cache, member, limit)
    except OSError as error:
        raise SourceError(f"{member}: unreadable: {error}") from error
    _require(len(data) <= limit, f"{member}: grew past {limit} bytes while reading")
    return data


def _gz_json(data: bytes, member: str) -> dict[str, Any]:
    try:
        value = source._json(source._gunzip(data, member), member)
    except RecursionError as error:
        raise SourceError(f"{member}: JSON nested too deeply") from error
    return source._object(value, member)


def _pinned_models(cache: Path) -> tuple[bytes, dict[str, Any]]:
    data = _member_bytes(cache, source.INDEX_MEMBER, source.INDEX_MAX_BYTES)
    digest = hashlib.sha256(data).hexdigest()
    _require(digest == INDEX_SHA256, f"index: sha256 {digest} != pinned {INDEX_SHA256}")
    try:
        return data, source._models(source._json(data, "index"))
    except RecursionError as error:
        raise SourceError("index: JSON nested too deeply") from error


def _check_args(model: str, rollout: int) -> None:
    _require(isinstance(model, str) and model in source.MODELS, "model: not released")
    _require(
        type(rollout) is int and rollout in source.ROLLOUTS,
        f"rollout: expected one of {list(source.ROLLOUTS)}",
    )


def _roster(run: dict[str, Any], rid: str, seats: tuple[str, ...]) -> list[Any]:
    agents = [agent for agent in run["agents"] if agent["kind"] == "agent"]
    roster = []
    for number, agent in enumerate(agents, 1):
        at = f"index.{rid}.agents[agent {number}]"
        name = agent["name"]
        _require(bool(name) and name == name.strip(), f"{at}.name: blank or padded")
        _require(source._field(agent, "id", str, at) == f"agent-{number}", f"{at}.id")
        label = source._field(agent, "label", str, at)
        _require(label == f"Agent {number} · {name}", f"{at}.label: id/name mismatch")
        member = agent_member(rid, number)
        path = source._field(agent, "trajectoryPath", str, at)
        _require(path == member, f"{at}.trajectoryPath: expected {member}")
        _require(
            source.MEMBER_PATTERN.fullmatch(member) is not None,
            f"{member}: the shared source reader does not admit agent members",
        )
        steps = source._field(agent, "steps", int, at)
        _require(steps >= 0, f"{at}.steps: negative")
        roster.append((name, member, steps))
    _require(
        tuple(name for name, _, _ in roster) == seats,
        f"index.{rid}.agents: seat order differs from agent ids",
    )
    return roster


def _run_entry(models: dict[str, Any], model: str, rollout: int) -> dict[str, Any]:
    rid, run = source._find_run(models, model, TEAM_SIZE, rollout)
    seats, board_steps = source._seats_and_board(run, rid, TEAM_SIZE)
    _require(
        len(run["agents"]) == RUN_AGENTS,
        f"index.{rid}.agents: expected {RUN_AGENTS} entries",
    )
    board = next(agent for agent in run["agents"] if agent["kind"] == "board")
    senders = source._field(board, "senders", int, f"index.{rid}.board")
    _require(1 <= senders <= TEAM_SIZE, f"index.{rid}.board.senders: out of range")
    entry = {
        "run_id": rid,
        "board_member": source.board_member(rid),
        "seats": seats,
        "steps": board_steps,
        "senders": senders,
        "roster": _roster(run, rid, seats),
    }
    _require(len(entry["roster"]) == TEAM_SIZE, f"index.{rid}: roster size")
    return entry


def _recipients(to: Any) -> list[Any]:
    return to if isinstance(to, list) else [to]


def _board(data: bytes, entry: dict[str, Any]) -> dict[str, Any]:
    member = entry["board_member"]
    board = _gz_json(data, member)
    _require(board.keys() == {"schema", "steps"}, f"{member}: unexpected fields")
    try:
        steps = source._board_steps(data, entry)
    except RecursionError as error:
        raise SourceError(f"{member}: JSON nested too deeply") from error
    _require(len(steps) == len(board["steps"]), f"{member}: reparse mismatch")
    audience = set(entry["seats"]) | {source.EVERYONE}
    sources = set()
    for position, step in enumerate(steps):
        at = f"{member}.steps[{position}]"
        _require(step["source"] in entry["seats"], f"{at}.source: not a seat")
        sources.add(step["source"])
        _require(
            all(name in audience for name in _recipients(step["to"])),
            f"{at}.to: not a seat or {source.EVERYONE!r}",
        )
    _require(
        len(sources) == entry["senders"],
        f"{member}: {len(sources)} senders, index declares {entry['senders']}",
    )
    return board


def _step(step: Any, position: int, member: str) -> None:
    at = f"{member}.steps[{position}]"
    step = source._object(step, at)
    _require(source._field(step, "step_id", int, at) == position + 1, f"{at}.step_id")
    for key in STEP_TEXT_FIELDS:
        source._field(step, key, str, at)
    _require("minute" in step, f"{at}: missing 'minute'")
    _require(source._number(step["minute"], f"{at}.minute") >= 0, f"{at}.minute")
    calls = step.get("tool_calls", [])
    _require(isinstance(calls, list), f"{at}.tool_calls: expected list")
    for number, call in enumerate(calls):
        where = f"{at}.tool_calls[{number}]"
        name = source._field(source._object(call, where), "function_name", str, where)
        _require(bool(name), f"{where}.function_name: blank")


def _trajectory(data: bytes, member: str, declared: int) -> dict[str, Any]:
    trajectory = _gz_json(data, member)
    schema = source._field(trajectory, "schema", str, member)
    _require(schema == AGENT_SCHEMA, f"{member}.schema: expected {AGENT_SCHEMA!r}")
    agent = source._field(trajectory, "agent", dict, member)
    for key in ("harness", "model"):
        value = source._field(agent, key, str, f"{member}.agent")
        _require(bool(value), f"{member}.agent.{key}: blank")
    steps = source._field(trajectory, "steps", list, member)
    _require(
        len(steps) == declared,
        f"{member}: {len(steps)} steps, index declares {declared}",
    )
    for position, step in enumerate(steps):
        _step(step, position, member)
    return trajectory


def _parse_run(
    entry: dict[str, Any], board_data: bytes, agent_data: list[bytes]
) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]:
    _require(len(agent_data) == len(entry["roster"]) == TEAM_SIZE, "agent count")
    board = _board(board_data, entry)
    seats = [
        (name, _trajectory(data, member, declared))
        for (name, member, declared), data in zip(entry["roster"], agent_data)
    ]
    identities = {
        (trajectory["agent"]["harness"], trajectory["agent"]["model"])
        for _, trajectory in seats
    }
    _require(len(identities) == 1, f"{entry['run_id']}: seats differ in harness/model")
    return board, seats


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_run(
    cache: Path, model: str, rollout: int
) -> tuple[str, dict[str, Any], list[tuple[str, dict[str, Any]]], dict[str, str]]:
    _check_bounds()
    _check_args(model, rollout)
    cache = _check_cache(Path(cache))
    index, models = _pinned_models(cache)
    entry = _run_entry(models, model, rollout)
    board_data = _member_bytes(cache, entry["board_member"], source.BOARD_MAX_BYTES)
    agent_data = [
        _member_bytes(cache, member, AGENT_MAX_BYTES)
        for _, member, _ in entry["roster"]
    ]
    board, seats = _parse_run(entry, board_data, agent_data)
    hashes = {
        source.INDEX_MEMBER: _sha256(index),
        entry["board_member"]: _sha256(board_data),
    }
    for (_, member, _), data in zip(entry["roster"], agent_data):
        hashes[member] = _sha256(data)
    _require(len(hashes) == 2 + TEAM_SIZE, f"{entry['run_id']}: member collision")
    return entry["run_id"], board, seats, hashes


def _obtain(cache: Path, member: str, limit: int, pending: dict[str, bytes]) -> bytes:
    target = cache / member
    if target.exists() or target.is_symlink():
        return _member_bytes(cache, member, limit)
    _require(member not in pending, f"{member}: requested twice")
    pending[member] = source._download(member, limit, GZIP_ENCODINGS)
    return pending[member]


def fetch(cache: Path, rollouts: tuple[int, ...]) -> dict[str, Any]:
    _check_bounds()
    allowed = set(SPLITS.values()) | {(rollout,) for rollout in source.ROLLOUTS}
    _require(rollouts in allowed, f"rollouts {rollouts!r}: not a split or one rollout")
    cache = _check_cache(Path(cache))
    _require(
        cache.is_dir(),
        f"{cache}: missing; first run "
        f"`python3.12 -BS -m examples.seatlab.continuation fetch {cache}`",
    )
    _, models = _pinned_models(cache)
    pending: dict[str, bytes] = {}
    for model in source.MODELS:
        for rollout in rollouts:
            entry = _run_entry(models, model, rollout)
            board = _obtain(
                cache, entry["board_member"], source.BOARD_MAX_BYTES, pending
            )
            agents = [
                _obtain(cache, member, AGENT_MAX_BYTES, pending)
                for _, member, _ in entry["roster"]
            ]
            _parse_run(entry, board, agents)
    staging = Path(tempfile.mkdtemp(prefix=".fetch-", dir=cache))
    try:
        source._install(cache, staging, pending)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    runs = []
    for model in source.MODELS:
        for rollout in rollouts:
            run, _, _, hashes = load_run(cache, model, rollout)
            runs.append({"run_id": run, "members": hashes})
    return {
        "index_sha256": INDEX_SHA256,
        "rollouts": list(rollouts),
        "downloaded": sorted(pending),
        "runs": runs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Fetch the published two-seat agent transcripts for receipt checks.",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("fetch", help="download one split into CACHE")
    command.add_argument("cache", type=Path, metavar="CACHE")
    group = command.add_mutually_exclusive_group(required=True)
    group.add_argument("--split", choices=sorted(SPLITS))
    group.add_argument("--rollout", type=int, choices=source.ROLLOUTS)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    rollouts = SPLITS[args.split] if args.split else (args.rollout,)
    try:
        result = fetch(args.cache, rollouts)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    text = json.dumps(result, sort_keys=True, allow_nan=False)
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
    return 0


def _interrupt(_signal: int, _frame: object) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _interrupt)
    raise SystemExit(main())
