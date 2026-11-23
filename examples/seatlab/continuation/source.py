from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
import zlib
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, override

HOST = "benchmarks.bespokelabs.ai"
BASE_PATH = "/multi-agent-coordination-bench/"
TASK = "seed-to-profit"
INDEX_MEMBER = "public-rollout-data/index.json"
MODELS = ("opus-5-5", "gpt-6-1-sol", "gemini-4-argon")
TEAM_SIZES = (2, 4, 8)
ROLLOUTS = (1, 2, 3)
MARK_HOURS = (0.5, 1, 2, 3, 4)
MARK60_HOUR = 1
FINAL_HOUR = 4
CURVE_HOURS = (1, 2, 3, 4)
TEAM_RUNS = len(MODELS) * len(TEAM_SIZES) * len(ROLLOUTS)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
CUTOFF_MINUTE = 60
EVERYONE = "everyone"
BOARD_SCHEMA = "message board of the team"
INDEX_MAX_BYTES = 4 << 20
BOARD_MAX_BYTES = 8 << 20
BOARD_MAX_JSON_BYTES = 32 << 20
TIMEOUT_SECONDS = 30
USER_AGENT = "seatlab-continuation-source/1"

MEMBER_PATTERN = re.compile(
    r"public-rollout-data/(?:index\.json"
    r"|trajectories/seed-to-profit/[a-z0-9]+(?:-[a-z0-9]+)*/board\.json\.gz)"
)
GZIP_MAGIC = b"\x1f\x8b"


class SourceError(ValueError):
    pass


def run_id(model: str, team_size: int, rollout: int) -> str:
    return f"{model}-k{team_size}-r{rollout}"


def board_member(run: str) -> str:
    return f"public-rollout-data/trajectories/{TASK}/{run}/board.json.gz"


def layout() -> list[str]:
    members = [INDEX_MEMBER]
    for model in MODELS:
        for team_size in TEAM_SIZES:
            for rollout in ROLLOUTS:
                members.append(board_member(run_id(model, team_size, rollout)))
    if len(members) != 1 + len(MODELS) * len(TEAM_SIZES) * len(ROLLOUTS):
        raise SourceError("cache layout size mismatch")
    return members


def _reject_constant(name: str) -> Any:
    raise SourceError(f"non-finite JSON constant {name}")


def _json(data: bytes, where: str) -> Any:
    try:
        return json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourceError(f"{where}: invalid JSON: {error}") from error


def _object(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SourceError(f"{where}: expected object, got {type(value).__name__}")
    return value


def _field(value: dict[str, Any], key: str, kind: type, where: str) -> Any:
    if key not in value:
        raise SourceError(f"{where}: missing {key!r}")
    item = value[key]
    if isinstance(item, bool) or not isinstance(item, kind):
        raise SourceError(
            f"{where}.{key}: expected {kind.__name__}, got {type(item).__name__}"
        )
    return item


def _number(value: Any, where: str) -> float | int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SourceError(f"{where}: expected number, got {type(value).__name__}")
    if not math.isfinite(value):
        raise SourceError(f"{where}: non-finite number")
    return value


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _models(index: Any) -> dict[str, Any]:
    index = _object(index, "index")
    hours = _field(index, "markHours", list, "index")
    if len(hours) != len(MARK_HOURS) or any(
        _number(hour, "index.markHours[]") != expected
        for hour, expected in zip(hours, MARK_HOURS)
    ):
        raise SourceError(f"index.markHours: expected {list(MARK_HOURS)}")
    tasks = _field(index, "tasks", dict, "index")
    where = f"index.tasks.{TASK}"
    if TASK not in tasks:
        raise SourceError(f"index.tasks: missing {TASK!r}")
    return _field(_object(tasks[TASK], where), "models", dict, where)


def _find_run(models: dict[str, Any], model: str, team_size: int, rollout: int):
    where = f"index.tasks.{TASK}.models"
    if model not in models:
        raise SourceError(f"{where}: missing {model!r}")
    runs = _field(_object(models[model], f"{where}.{model}"), "runs", list, where)
    expected = run_id(model, team_size, rollout)
    matches = [
        run for run in runs if isinstance(run, dict) and run.get("runId") == expected
    ]
    if len(matches) != 1:
        raise SourceError(f"index: expected one run {expected}, found {len(matches)}")
    run = matches[0]
    at = f"index.{expected}"
    identity = (
        _field(run, "teamSize", int, at),
        _field(run, "setting", str, at),
        _field(run, "rollout", int, at),
    )
    if identity != (team_size, "team", rollout):
        raise SourceError(f"{at}: teamSize/setting/rollout {identity} mismatch")
    return expected, run


def _seats_and_board(
    run: dict[str, Any], expected: str, team_size: int
) -> tuple[tuple[str, ...], int]:
    where = f"index.{expected}"
    seats: list[str] = []
    boards: list[dict[str, Any]] = []
    for position, agent in enumerate(_field(run, "agents", list, where)):
        at = f"{where}.agents[{position}]"
        kind = _field(_object(agent, at), "kind", str, at)
        if kind == "agent":
            seats.append(_field(agent, "name", str, at))
        elif kind == "board":
            boards.append(agent)
    if len(seats) != team_size or len(set(seats)) != team_size:
        raise SourceError(f"{where}: expected {team_size} distinct seat names")
    if EVERYONE in seats:
        raise SourceError(f"{where}: a seat is named {EVERYONE!r}")
    if len(boards) != 1:
        raise SourceError(f"{where}: expected exactly one board, found {len(boards)}")
    board = boards[0]
    at = f"{where}.board"
    if _field(board, "id", str, at) != "board":
        raise SourceError(f"{at}.id: expected 'board'")
    if _field(board, "trajectoryPath", str, at) != board_member(expected):
        raise SourceError(f"{at}.trajectoryPath: expected {board_member(expected)}")
    steps = _field(board, "steps", int, at)
    if steps < 0:
        raise SourceError(f"{at}.steps: negative")
    return tuple(seats), steps


def _marks(run: dict[str, Any], expected: str) -> list[float | int]:
    where = f"index.{expected}.metrics"
    marks = _field(
        _field(run, "metrics", dict, f"index.{expected}"), "marks", list, where
    )
    if len(marks) != len(MARK_HOURS):
        raise SourceError(f"{where}.marks: expected {len(MARK_HOURS)} values")
    for mark in marks:
        if not 0 <= _number(mark, f"{where}.marks[]") <= 1:
            raise SourceError(f"{where}.marks[]: outside [0, 1]")
    return marks


def _grades(run: dict[str, Any], expected: str) -> tuple[float | int, float | int]:
    marks = _marks(run, expected)
    return marks[MARK_HOURS.index(MARK60_HOUR)], marks[MARK_HOURS.index(FINAL_HOUR)]


def _exact(value: float) -> Fraction:
    if isinstance(value, float):
        return Fraction(Decimal(repr(value)))
    return Fraction(value)


def _curve(run: dict[str, Any], expected: str) -> dict[int, Fraction]:
    marks = _marks(run, expected)
    _, final = _grades(run, expected)
    curve = {hour: _exact(marks[MARK_HOURS.index(hour)]) for hour in CURVE_HOURS}
    if not all(0 <= grade <= 1 for grade in curve.values()):
        raise SourceError(f"index.{expected}.metrics.marks[]: outside [0, 1]")
    if curve[CURVE_HOURS[-1]] != _exact(final) or _exact(marks[-1]) != _exact(final):
        raise SourceError(f"index.{expected}: last mark is not the release score")
    return curve


def parse_index(data: bytes) -> list[tuple[str, int, list[dict[str, Any]]]]:
    models = _models(_json(data, "index"))
    groups = []
    for model in MODELS:
        for team_size in TEAM_SIZES:
            selected = []
            for rollout in ROLLOUTS:
                expected, run = _find_run(models, model, team_size, rollout)
                seats, steps = _seats_and_board(run, expected, team_size)
                mark60, final = _grades(run, expected)
                selected.append(
                    {
                        "run_id": expected,
                        "board_member": board_member(expected),
                        "seats": seats,
                        "steps": steps,
                        "mark60": mark60,
                        "final": final,
                    }
                )
            groups.append((model, team_size, selected))
    return groups


def _gunzip(data: bytes, where: str) -> bytes:
    if data[:2] != GZIP_MAGIC:
        raise SourceError(f"{where}: not gzip data")
    stream = zlib.decompressobj(wbits=31)
    try:
        text = stream.decompress(data, BOARD_MAX_JSON_BYTES + 1)
    except zlib.error as error:
        raise SourceError(f"{where}: gzip decompression failed: {error}") from error
    if len(text) > BOARD_MAX_JSON_BYTES or stream.unconsumed_tail:
        raise SourceError(f"{where}: decompressed size exceeds {BOARD_MAX_JSON_BYTES}")
    if not stream.eof:
        raise SourceError(f"{where}: truncated gzip stream")
    if stream.unused_data:
        raise SourceError(f"{where}: trailing bytes after gzip stream")
    return text


def _well_formed_recipients(recipients: Any) -> bool:
    if isinstance(recipients, str):
        return bool(recipients)
    return (
        isinstance(recipients, list)
        and bool(recipients)
        and all(isinstance(name, str) and name for name in recipients)
    )


def _board_steps(data: bytes, run: dict[str, Any]) -> list[dict[str, Any]]:
    where = run["board_member"]
    board = _object(_json(_gunzip(data, where), where), where)
    if _field(board, "schema", str, where) != BOARD_SCHEMA:
        raise SourceError(f"{where}.schema: expected {BOARD_SCHEMA!r}")
    steps = _field(board, "steps", list, where)
    if len(steps) != run["steps"]:
        raise SourceError(f"{where}: {len(steps)} steps, index declares {run['steps']}")
    for position, step in enumerate(steps):
        at = f"{where}.steps[{position}]"
        _object(step, at)
        if _field(step, "step_id", int, at) != position + 1:
            raise SourceError(f"{at}.step_id: expected {position + 1}")
        for key in ("source", "kind", "timestamp", "message"):
            _field(step, key, str, at)
        for key in ("to", "minute"):
            if key not in step:
                raise SourceError(f"{at}: missing {key!r}")
        if not _well_formed_recipients(step["to"]):
            raise SourceError(f"{at}.to: expected non-empty text or text list")
        if _number(step["minute"], f"{at}.minute") < 0:
            raise SourceError(f"{at}.minute: negative")
    return steps


def parse_board(data: bytes, run: dict[str, Any]) -> list[dict[str, Any]]:
    steps = _board_steps(data, run)
    early = [step for step in steps if step["minute"] < CUTOFF_MINUTE]
    public = []
    for step in early:
        if step["kind"] == "board" and step["to"] == EVERYONE:
            if step["source"] not in run["seats"]:
                at = f"{run['board_member']}.steps[{step['step_id'] - 1}]"
                raise SourceError(
                    f"{at}.source: public author is not a seat of this run"
                )
            public.append(step)
    public.sort(key=lambda step: (step["minute"], step["step_id"]))
    aliases: dict[str, int] = {}
    posts = []
    for step in public:
        seat = aliases.setdefault(step["source"], len(aliases) + 1)
        posts.append({"minute": step["minute"], "seat": seat, "text": step["message"]})
    return posts


def _read_member(cache: Path, member: str, limit: int) -> bytes:
    if not MEMBER_PATTERN.fullmatch(member):
        raise SourceError(f"unexpected cache member {member!r}")
    path = cache / member
    if not path.is_file():
        raise SourceError(f"missing cache file {path}; run fetch into {cache} first")
    if path.stat().st_size > limit:
        raise SourceError(f"{path}: larger than {limit} bytes")
    return path.read_bytes()


def load(cache: Path) -> dict[str, Any]:
    cache = Path(cache)
    index_bytes = _read_member(cache, INDEX_MEMBER, INDEX_MAX_BYTES)
    cohorts = []
    for model, team_size, runs in parse_index(index_bytes):
        loaded = []
        for run in runs:
            data = _read_member(cache, run["board_member"], BOARD_MAX_BYTES)
            posts = parse_board(data, run)
            loaded.append(
                {
                    "run_id": run["run_id"],
                    "board_sha256": _sha256(data),
                    "public_posts": len(posts),
                    "posts": posts,
                    "mark60": run["mark60"],
                    "final": run["final"],
                }
            )
        cohorts.append(
            {
                "key": f"{model}:k{team_size}",
                "model": model,
                "team_size": team_size,
                "runs": loaded,
            }
        )
    return {"index_sha256": _sha256(index_bytes), "cohorts": cohorts}


def load_curves(
    cache: Path, expected_index_sha256: str
) -> dict[str, dict[int, Fraction]]:
    if not isinstance(expected_index_sha256, str) or not SHA256_PATTERN.fullmatch(
        expected_index_sha256
    ):
        raise SourceError("expected_index_sha256: expected 64 lowercase hex digits")
    if CURVE_HOURS[-1] != FINAL_HOUR or MARK_HOURS[-1] != FINAL_HOUR:
        raise SourceError("curve hours must end at the release score hour")
    index_bytes = _read_member(Path(cache), INDEX_MEMBER, INDEX_MAX_BYTES)
    actual = _sha256(index_bytes)
    if actual != expected_index_sha256:
        raise SourceError(f"index sha256 {actual} != {expected_index_sha256}")
    models = _models(_json(index_bytes, "index"))
    curves: dict[str, dict[int, Fraction]] = {}
    for model in MODELS:
        for team_size in TEAM_SIZES:
            for rollout in ROLLOUTS:
                expected, run = _find_run(models, model, team_size, rollout)
                _seats_and_board(run, expected, team_size)
                curves[expected] = _curve(run, expected)
    if len(curves) != TEAM_RUNS or any(size < 2 for size in TEAM_SIZES):
        raise SourceError(f"expected {TEAM_RUNS} distinct team runs")
    return curves


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    @override
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SourceError(f"refusing redirect {code} from {req.full_url} to {newurl}")


def _download(member: str, limit: int, encodings: frozenset[str]) -> bytes:
    if not MEMBER_PATTERN.fullmatch(member):
        raise SourceError(f"refusing to download unexpected member {member!r}")
    url = f"https://{HOST}{BASE_PATH}{member}"
    request = urllib.request.Request(
        url, headers={"Accept-Encoding": "identity", "User-Agent": USER_AGENT}
    )
    opener = urllib.request.build_opener(_RefuseRedirect)
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            if response.status != 200 or response.geturl() != url:
                raise SourceError(f"{url}: unexpected response {response.status}")
            encoding = response.headers.get("Content-Encoding", "identity").lower()
            if encoding not in encodings:
                raise SourceError(f"{url}: unexpected Content-Encoding {encoding!r}")
            declared = response.headers.get("Content-Length")
            if declared is not None and (
                not declared.isdigit() or int(declared) > limit
            ):
                raise SourceError(f"{url}: Content-Length {declared!r} over {limit}")
            data = response.read(limit + 1)
    except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
        raise SourceError(f"{url}: download failed: {error}") from error
    if len(data) > limit:
        raise SourceError(f"{url}: body exceeds {limit} bytes")
    if declared is not None and len(data) != int(declared):
        raise SourceError(f"{url}: body length differs from Content-Length")
    return data


def _install(cache: Path, staging: Path, fetched: dict[str, bytes]) -> None:
    pending = []
    for member, data in fetched.items():
        target = cache / member
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise SourceError(f"{target}: exists and is not a regular file")
        if target.exists():
            if target.read_bytes() != data:
                raise SourceError(
                    f"{target}: differs from the official bytes; "
                    "refusing to overwrite, fetch into an empty cache directory"
                )
            continue
        pending.append((target, data))
    for position, (target, data) in enumerate(pending):
        staged = staging / str(position)
        staged.write_bytes(data)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(staged, target)
        except FileExistsError as error:
            raise SourceError(f"{target}: appeared during fetch") from error


def fetch(cache: Path) -> None:
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".fetch-", dir=cache))
    try:
        index_bytes = _download(INDEX_MEMBER, INDEX_MAX_BYTES, frozenset({"identity"}))
        fetched = {INDEX_MEMBER: index_bytes}
        for _, _, runs in parse_index(index_bytes):
            for run in runs:
                data = _download(
                    run["board_member"],
                    BOARD_MAX_BYTES,
                    frozenset({"identity", "gzip"}),
                )
                parse_board(data, run)
                fetched[run["board_member"]] = data
        if sorted(fetched) != sorted(layout()):
            raise SourceError("fetched members differ from the cache layout")
        _install(cache, staging, fetched)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    load(cache)
