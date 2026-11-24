from typing import Any

from ...native_study import as_choice, as_record, require
from ...ship_inflight import inflight_verify as verify
from ...ship_inflight import replay

WORLD = "inflight"
QUESTION = replay.SAMPLES / "question.json"
PACK = replay.SAMPLES / "public-cuts.json"
PACK_SHA256 = "sha256:17ca54b49e79896d7704b6b13f880e3961ae62197b527caf46ff4e62eb73a8ae"
ROW_KEYS = ("world", "axis_state", "decision", "inbox", "citation", "reward", "status")
DECISIONS = ("accepted", "absent", "unrecorded")
INBOXES = ("absent", "empty", "original", "draft", "unrecorded")
STATUSES = ("observed", "missing")
SHOWN = {"none": "empty", "original": "original", "draft": "draft"}
KINDS = tuple(verify.stock.CHOICES)

assert SHOWN.keys() == set(verify.stock.CHOICES["inbox"])
assert set(verify.FATES) == {"settles", "fails"}
assert set(KINDS) == {"handoff", "inbox", "resolve"}


def _row(
    fate: str, decision: str, inbox: str, reward: int | None, status: str
) -> dict[str, object]:
    assert fate in verify.FATES and decision in DECISIONS and inbox in INBOXES
    assert status in STATUSES
    assert (status == "missing") == (reward is None) == (decision == "unrecorded")
    assert (decision in ("absent", "unrecorded")) == (inbox == "unrecorded")
    assert reward is None or (type(reward) is int and reward in (0, 1))
    values = (WORLD, fate, decision, inbox, "unrecorded", reward, status)
    row: dict[str, object] = dict(zip(ROW_KEYS, values, strict=True))
    assert tuple(row) == ROW_KEYS
    return row


def _moments(events: object) -> tuple[str, str]:
    bounded = isinstance(events, list) and len(events) <= len(KINDS)
    require(bounded, "pinned verifier returned unbounded events")
    assert isinstance(events, list)
    seen: dict[str, tuple[int, str]] = {}
    for item in events:
        event = as_record(item, "pinned verifier event", verify.stock.EVENT_KEYS)
        kind = as_choice(event["kind"], "pinned verifier event kind", KINDS)
        choices = verify.stock.CHOICES[kind]
        choice = as_choice(event["choice"], "pinned verifier event choice", choices)
        seq = event["seq"]
        ordered = type(seq) is int and seq == len(seen) + 1 and kind not in seen
        require(ordered, "pinned verifier returned a malformed event order")
        seen[kind] = (seq, choice)
    resolve, inbox = seen.get("resolve"), seen.get("inbox")
    if resolve is None:
        return "absent", "unrecorded"
    if inbox is None or resolve[0] < inbox[0]:
        return "accepted", "absent"
    return "accepted", SHOWN[inbox[1]]


def classify(case: dict[str, Any], row: dict[str, Any] | None) -> dict[str, object]:
    fate = as_choice(case["fate"], "registered fate", verify.FATES)
    record = replay.replay(case, row)
    if record["status"] == "missing":
        assert row is None
        return _row(fate, "unrecorded", "unrecorded", None, "missing")
    detail = record.get("reason") or ", ".join(record.get("misses", []))
    qualified = record["status"] == "pass"
    require(qualified, f"public cut is not qualified: {record['status']}: {detail}")
    assert row is not None and row["ticket"] == case["ticket"]
    observation = verify.evaluate({**row["cut"], "run": replay.PLACEHOLDER_RUN})
    assert (observation["fate"], observation["channel"]) == (
        case["fate"],
        case["channel"],
    )
    reward = record["replay"]["reward"]
    assert reward == observation["reward"] == row["native"]["reward"]
    decision, inbox = _moments(observation["events"])
    return _row(fate, decision, inbox, reward, "observed")


def _project(raw: bytes) -> list[dict[str, object]]:
    question_sha, task_files, cases = replay.load_source(QUESTION)
    tickets = [case["ticket"] for case in cases]
    rows = replay.parse_rows(raw, question_sha, task_files, tickets)
    result = [classify(case, rows.get(case["ticket"])) for case in cases]
    assert len(result) == len(cases) == replay.REGISTERED
    assert sum(row["status"] == "observed" for row in result) == len(rows)
    return result


def collect() -> list[dict[str, object]]:
    raw = replay.read_bounded(PACK, replay.MAX_PACK_BYTES, "pack")
    require(replay.sha256(raw) == PACK_SHA256, "pack is not the frozen public release")
    return _project(raw)
