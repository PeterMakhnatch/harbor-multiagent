import re
import stat
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeGuard

from .core import canonical_uuid
from .native_records import read_record, record_digest
from .schedule_bounds import WINDOWS
from .schedule_exit_clock import ExitCut, ExitPolicy, cut_at_exit

RESULT_SCHEMA = 1
FAMILY = "schedule"
CONDITIONS = ("silent", "talk")
TASK_NAME = re.compile(
    r"flotilla/schedule(?:\.[a-z0-9_]+)?-(?P<instance>\d{3})-(?P<condition>silent|talk)",
    flags=re.ASCII,
)
TASK_DIGEST = re.compile(r"[0-9a-f]{64}", flags=re.ASCII)
START = re.compile(r"(\d{1,2})(?::00(?::00)?)?", flags=re.ASCII)
TIMEOUT = "AgentTimeoutError"
MIN_SEATS = 2
MAX_SEATS = 16
STOCK_SEATS = frozenset(
    tuple(sorted(f"p{i}" for i in range(1, count + 1)))
    for count in range(MIN_SEATS, MAX_SEATS + 1)
)
MAX_WRITES = 1024
MAX_MESSAGES = 4096
V1_MAX_TEXT = 64_000
MAX_CLOCK = 10**9

SCORE_V1 = frozenset(
    {
        "answer",
        "condition",
        "error",
        "family",
        "fields_correct",
        "fields_total",
        "instance",
        "n_messages",
        "n_submissions",
        "score",
        "seats",
        "submitted_by",
    }
)
SCORE_V2 = SCORE_V1 | {"deceived", "world_version"}
STATE_V1 = frozenset(
    {"condition", "frozen", "messages", "seats", "sent", "submissions"}
)
STATE_V2 = STATE_V1 | {"world_version", "limits"}
LIMIT_KEYS = frozenset({"max_text", "max_messages"})
MESSAGE_KEYS = frozenset({"id", "from", "to", "text", "t"})
WRITE_KEYS = frozenset({"n", "seat", "answer", "t"})


@dataclass(frozen=True)
class Write:
    n: int
    seat: str
    choice: tuple[str, int] | None
    answer_digest: str


@dataclass(frozen=True)
class Team:
    native_id: str
    task_digest: str
    seats: tuple[str, ...]
    condition: str
    version: int
    writes: tuple[Write, ...]
    messages: tuple[tuple[str, str, str], ...]
    reward: int
    timed_out: bool = False
    seat_exit: ExitCut | None = None


class AmbiguousRubric(ValueError):
    pass


AMBIGUOUS = "native answer spelling is graded differently by shipped schedule rubrics"
WINDOW_DAYS = frozenset(day for day, _ in WINDOWS)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _record(path: Path) -> dict[str, Any]:
    record = read_record(path)
    if record is None:
        raise FileNotFoundError("native schedule record is missing")
    return record


def _count(value: object) -> bool:
    return type(value) is int and value >= 0


def _clock(value: Any) -> float:
    number = type(value) in (int, float) and not isinstance(value, bool)
    _require(
        number and 0 <= value <= MAX_CLOCK,
        "native schedule time is not a bounded non-negative number",
    )
    return float(value)


def answer_signature(answer: dict[str, Any]) -> str:
    if not isinstance(answer, dict):
        raise ValueError("native schedule answer is not a JSON object")
    try:
        signature = record_digest(answer)
    except (ValueError, TypeError) as error:
        raise ValueError("native schedule answer is not canonical JSON") from error
    assert TASK_DIGEST.fullmatch(signature) is not None
    return signature


def _hour(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        match = START.fullmatch(value.strip())
        return None if match is None else int(match.group(1))
    return None


def _legacy_hour(value: object) -> int | None:
    if isinstance(value, int):
        return int(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    digits = text if text.isdigit() else text.split(":", 1)[0]
    if not digits.isdigit():
        return None
    try:
        return int(digits)
    except ValueError as error:
        raise AmbiguousRubric(AMBIGUOUS) from error


def _window(day: str, hour: int | None) -> tuple[str, int] | None:
    if hour is None or (day, hour) not in WINDOWS:
        return None
    return day, hour


def _choice(answer: dict[str, Any]) -> tuple[str, int] | None:
    day = answer.get("day")
    if not isinstance(day, str):
        return None
    name = day.strip()[:3].title()
    if name not in WINDOW_DAYS:
        return None
    start = answer.get("start")
    modern = _window(name, _hour(start))
    legacy = _window(name, _legacy_hour(start))
    if modern != legacy:
        raise AmbiguousRubric(AMBIGUOUS)
    return modern


def _reward(result: dict[str, Any]) -> int:
    _require(result.get("reward_valid") is True, "native reward is not valid")
    verifier = result.get("verifier_result")
    if not isinstance(verifier, dict):
        raise ValueError("native verifier result is missing")
    rewards = verifier.get("rewards")
    if not isinstance(rewards, dict) or set(rewards) != {"reward"}:
        raise ValueError("native rewards are not a single reward")
    value = rewards["reward"]
    _require(
        type(value) in (int, float) and value in (0, 1),
        "native reward is not binary",
    )
    return int(value)


def _timeout(info: object) -> bool:
    if info is None:
        return False
    _require(
        type(info) is dict and info.get("exception_type") == TIMEOUT,
        "native trial recorded a non-timeout error",
    )
    return True


def _seat_results(result: dict[str, Any]) -> tuple[tuple[str, ...], int]:
    rows = result.get("seat_results")
    if not isinstance(rows, list) or not MIN_SEATS <= len(rows) <= MAX_SEATS:
        raise ValueError("native seat results are not a stock team")
    seats: list[str] = []
    timed = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("native seat result is not an object")
        _require(row.get("under_test") is True, "native seat is not under test")
        timed += _timeout(row.get("exception_info"))
        seat = row.get("seat")
        if not isinstance(seat, str):
            raise ValueError("native seat result has no seat")
        seats.append(seat)
    _require(len(set(seats)) == len(seats), "native seat results repeat a seat")
    assert 0 <= timed <= len(seats)
    return tuple(sorted(seats)), timed


Native = tuple[str, str, int, str, int, tuple[str, ...], bool]


def _native(result: dict[str, Any]) -> Native:
    schema = result.get("schema_version")
    _require(
        type(schema) is int and schema == RESULT_SCHEMA,
        "native result is not schema version 1",
    )
    native_id = result.get("id")
    if not isinstance(native_id, str) or not canonical_uuid(native_id):
        raise ValueError("native result has no canonical trial id")
    name = result.get("task_name")
    if not isinstance(name, str):
        raise ValueError("native task is not a stock team schedule task")
    match = TASK_NAME.fullmatch(name)
    if match is None:
        raise ValueError("native task is not a stock team schedule task")
    digest = result.get("task_checksum")
    if not isinstance(digest, str) or TASK_DIGEST.fullmatch(digest) is None:
        raise ValueError("native task checksum is not lowercase SHA-256 hex")
    finished = result.get("finished_at")
    _require(type(finished) is str and bool(finished), "native trial did not finish")
    timed_out = _timeout(result.get("exception_info"))
    seats, timed_seats = _seat_results(result)
    _require(
        timed_out == (timed_seats > 0),
        "native trial and seat timeout records disagree",
    )
    return (
        native_id,
        digest,
        int(match.group("instance")),
        match.group("condition"),
        _reward(result),
        seats,
        timed_out,
    )


def _directory(path: Path) -> None:
    mode = path.lstat().st_mode
    _require(stat.S_ISDIR(mode), "native trial path is not a real directory")


def _limits(state: dict[str, Any]) -> tuple[int, int, int]:
    if "world_version" not in state:
        _require(set(state) == STATE_V1, "native world state has an unknown schema")
        return 1, V1_MAX_TEXT, MAX_MESSAGES
    _require(set(state) == STATE_V2, "native world state has an unknown schema")
    version = state["world_version"]
    _require(type(version) is int and version == 2, "native world version is unknown")
    limits = state["limits"]
    _require(
        type(limits) is dict and set(limits) == LIMIT_KEYS,
        "native world limits are malformed",
    )
    text, messages = limits["max_text"], limits["max_messages"]
    _require(
        type(text) is int and text > 0 and type(messages) is int and messages > 0,
        "native world limits are not positive integers",
    )
    return 2, text, messages


def _fields(value: object, expected: frozenset[str]) -> TypeGuard[dict[str, Any]]:
    return isinstance(value, dict) and value.keys() == expected


def _writes(rows: object, seats: tuple[str, ...]) -> tuple[Write, ...]:
    if not isinstance(rows, list) or len(rows) > MAX_WRITES:
        raise ValueError("native submissions are not a bounded list")
    writes: list[Write] = []
    last = 0.0
    for index, row in enumerate(rows, start=1):
        if not _fields(row, WRITE_KEYS):
            raise ValueError("native submission has missing or extra fields")
        n = row["n"]
        _require(type(n) is int and n == index, "native submissions are not ordered")
        _require(row["seat"] in seats, "native submission names an unknown seat")
        moment = _clock(row["t"])
        _require(moment >= last, "native submissions go back in time")
        last = moment
        answer = row["answer"]
        signature = answer_signature(answer)
        writes.append(Write(n, row["seat"], _choice(answer), signature))
    assert len(writes) == len(rows)
    return tuple(writes)


def _messages(
    rows: object, seats: tuple[str, ...], condition: str, max_text: int
) -> tuple[tuple[str, str, str], ...]:
    if not isinstance(rows, list) or len(rows) > MAX_MESSAGES:
        raise ValueError("native messages are not a bounded list")
    _require(condition == "talk" or not rows, "native silent run carries messages")
    messages: list[tuple[str, str, str]] = []
    last = 0.0
    for index, row in enumerate(rows, start=1):
        if not _fields(row, MESSAGE_KEYS):
            raise ValueError("native message has missing or extra fields")
        _require(row["id"] == f"m{index}", "native messages are not ordered")
        sender, addressee, text = row["from"], row["to"], row["text"]
        _require(sender in seats, "native message names an unknown sender")
        _require(
            addressee == "all" or addressee in seats,
            "native message names an unknown addressee",
        )
        _require(
            type(text) is str and bool(text.strip()) and len(text) <= max_text,
            "native message text is empty or over its limit",
        )
        moment = _clock(row["t"])
        _require(moment >= last, "native messages go back in time")
        last = moment
        messages.append((row["id"], sender, addressee))
    assert len(messages) == len(rows)
    return tuple(messages)


def _sent(
    state: dict[str, Any],
    seats: tuple[str, ...],
    messages: tuple[tuple[str, str, str], ...],
    max_messages: int,
) -> None:
    sent = state["sent"]
    _require(
        type(sent) is dict and set(sent) == set(seats),
        "native sent counts do not name the seats",
    )
    counts = Counter(sender for _, sender, _ in messages)
    for seat in seats:
        value = sent[seat]
        _require(
            _count(value) and value == counts[seat] and value <= max_messages,
            "native sent counts disagree with the messages",
        )


def _state(
    state: dict[str, Any], condition: str, seats: tuple[str, ...]
) -> tuple[int, tuple[Write, ...], tuple[tuple[str, str, str], ...]]:
    version, max_text, max_messages = _limits(state)
    _require(state["frozen"] is True, "native world state is not frozen")
    _require(state["condition"] == condition, "native world condition disagrees")
    listed = state["seats"]
    _require(
        type(listed) is list and tuple(listed) == seats,
        "native world seats are not the stock seat list",
    )
    writes = _writes(state["submissions"], seats)
    messages = _messages(state["messages"], seats, condition, max_text)
    _sent(state, seats, messages, max_messages)
    return version, writes, messages


def _final(score: dict[str, Any], writes: tuple[Write, ...], reward: int) -> None:
    answer, seat = score["answer"], score["submitted_by"]
    if not writes:
        _require(
            answer is None and seat is None and reward == 0,
            "native score invents a final answer",
        )
        return
    final = writes[-1]
    _require(
        seat == final.seat and answer_signature(answer) == final.answer_digest,
        "native score final answer disagrees with the frozen world",
    )
    _require(
        reward == 0 or final.choice is not None,
        "native reward credits an answer no schedule key can match",
    )


def _score(
    score: dict[str, Any],
    team: tuple[int, str, tuple[str, ...], int],
    writes: tuple[Write, ...],
    messages: tuple[tuple[str, str, str], ...],
) -> None:
    instance, condition, seats, reward = team
    _require(set(score) in (SCORE_V1, SCORE_V2), "native score has an unknown schema")
    _require(score["error"] is None, "native score recorded a verifier error")
    _require(
        score["family"] == FAMILY
        and type(score["instance"]) is int
        and score["instance"] == instance
        and score["condition"] == condition
        and type(score["seats"]) is list
        and tuple(score["seats"]) == seats,
        "native score names another task",
    )
    value = score["score"]
    _require(
        type(value) in (int, float) and value == reward,
        "native score disagrees with the native reward",
    )
    _require(
        type(score["fields_correct"]) is int
        and score["fields_correct"] == reward
        and type(score["fields_total"]) is int
        and score["fields_total"] == 1,
        "native score fields are not a schedule grade",
    )
    _require(
        _count(score["n_submissions"])
        and score["n_submissions"] == len(writes)
        and _count(score["n_messages"])
        and score["n_messages"] == len(messages),
        "native score counts disagree with the frozen world",
    )
    _final(score, writes, reward)


def _versions(score: dict[str, Any], version: int) -> None:
    if "world_version" not in score:
        return
    found = score["world_version"]
    _require(
        type(found) is int and found == version,
        "native score and world state disagree on world version",
    )
    _require(score["deceived"] is None, "native score carries a scripted decoy")


def read_team(path: Path, *, seat_exit: ExitPolicy | None = None) -> Team:
    _directory(path)
    _directory(path / "verifier")
    result = _record(path / "result.json")
    score = _record(path / "verifier" / "score.json")
    state = _record(path / "verifier" / "world-state.json")
    native = _native(result)
    native_id, digest, instance, condition, reward, seat_results, timed_out = native
    assert condition in CONDITIONS
    listed = state.get("seats")
    if (
        not isinstance(listed, list)
        or not all(isinstance(seat, str) for seat in listed)
        or tuple(listed) not in STOCK_SEATS
    ):
        raise ValueError("native world seats are not the stock seat list")
    seats = tuple(listed)
    _require(
        tuple(sorted(seats)) == seat_results,
        "native seat results disagree with the world seats",
    )
    version, writes, messages = _state(state, condition, seats)
    _score(score, (instance, condition, seats, reward), writes, messages)
    _versions(score, version)
    assert all(write.n == index for index, write in enumerate(writes, start=1))
    assert reward == 0 or (writes and writes[-1].choice is not None)
    return Team(
        native_id=native_id,
        task_digest=digest,
        seats=seats,
        condition=condition,
        version=version,
        writes=writes,
        messages=messages,
        reward=reward,
        timed_out=timed_out,
        seat_exit=(
            cut_at_exit(result, state, seats, drift_ms=seat_exit.drift_ms)
            if seat_exit is not None and len(seats) == 2
            else None
        ),
    )
