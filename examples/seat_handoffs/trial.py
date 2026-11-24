import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from .core import (
    BOUNDARIES,
    MAX_LINE_BYTES,
    MAX_LOG_BYTES,
    Finding,
    Replay,
    canonical_uuid,
    check_choice,
    check_reward,
    read_frames,
    reject_constant,
    replay_frames,
    unique_object,
)

DECISION_SCHEMA = "harbor.revised-dispatch.decision/v1"
DECISION_KEYS = frozenset(
    {"schema", "run", "draft_changed", "decision_correct", "event_counts"}
)


def _grade(result: dict[str, Any]) -> tuple[str, float | None]:
    valid = result.get("reward_valid")
    verifier = result.get("verifier_result")
    if valid is not None and type(valid) is not bool:
        raise ValueError("invalid native reward_valid")
    if verifier is not None and not isinstance(verifier, dict):
        raise ValueError("invalid native verifier_result")
    rewards = (verifier or {}).get("rewards")
    if rewards is not None and not isinstance(rewards, dict):
        raise ValueError("invalid native rewards")
    value = check_reward((rewards or {}).get("reward"))
    if valid is False:
        return "invalid", None
    if value is None or valid is not True:
        return "unknown", None
    return "valid", value


def read_native_result(path: Path) -> tuple[str | None, str, float | None]:
    result_path = path / "result.json"
    if not result_path.exists():
        return None, "unavailable", None
    result = _load(result_path, "native result", MAX_LOG_BYTES)
    native_id = result.get("id")
    if not canonical_uuid(native_id):
        raise ValueError("missing native trial identifier")
    status, grade = _grade(result)
    return native_id, status, grade


def _sealed(sealed: Path, world: Path) -> Replay:
    frames, torn = read_frames(sealed)
    replay = replay_frames(frames)
    if torn:
        raise ValueError("sealed handoff log ends with a torn frame")
    if not replay.closed:
        raise ValueError("sealed handoff log lacks a closing frame")
    if world.exists():
        artifact, artifact_torn = read_frames(world)
        if artifact_torn:
            raise ValueError("world artifact ends with a torn frame")
        if (
            not artifact
            or len(artifact) >= len(frames)
            or artifact != frames[: len(artifact)]
        ):
            raise ValueError(
                "world artifact is not a frame prefix of the sealed log before its close"
            )
    return replay


def _unsealed(world: Path) -> Replay:
    frames, _ = read_frames(world)
    replay = replay_frames(frames)
    if replay.closed:
        raise ValueError(
            "unsealed world artifact carries a closed record; absence needs the sealed log"
        )
    return replay


def _load(path: Path, what: str, limit: int) -> dict[str, Any]:
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{what} exceeds {limit} bytes")
    try:
        record = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (ValueError, RecursionError) as error:
        raise ValueError(f"{what} is not valid JSON") from error
    if not isinstance(record, dict):
        raise ValueError(f"{what} is not a JSON object")
    return cast(dict[str, Any], record)


def _decision(
    path: Path, replay: Replay, grade: float | None
) -> dict[str, bool | None] | None:
    if not path.exists():
        return None
    record = _load(path, "verifier decision", MAX_LINE_BYTES)
    if set(record) != DECISION_KEYS or record["schema"] != DECISION_SCHEMA:
        raise ValueError(f"verifier decision is not a {DECISION_SCHEMA} record")
    if record["run"] != replay.run:
        raise ValueError("verifier decision and sealed handoff log name different runs")
    journal = {
        "sent": len(replay.sent),
        "returned": len(replay.returned),
        "acknowledged": len(replay.acknowledged),
        "commit": len(replay.commits),
    }
    counts = record["event_counts"]
    if not isinstance(counts, dict) or any(
        type(value) is not int for value in counts.values()
    ):
        raise ValueError("verifier decision event counts are not integers")
    if counts != journal:
        raise ValueError(
            "verifier decision event counts disagree with the sealed handoff log"
        )
    committed = journal["commit"] > 0
    changed, correct = record["draft_changed"], record["decision_correct"]
    if changed is not None and not (committed and type(changed) is bool):
        raise ValueError(
            "verifier decision draft_changed contradicts the commit record"
        )
    if not (type(correct) is bool if committed else correct is None):
        raise ValueError("verifier decision correctness contradicts the commit record")
    reward = float(correct is True)
    for source, value in (
        ("sealed handoff log", replay.reward),
        ("native verifier", grade),
    ):
        if value is not None and value != reward:
            raise ValueError(f"verifier decision and {source} disagree on correctness")
    return {"draft_changed": changed, "decision_correct": correct}


def read_trial(path: Path, *, boundary: str) -> tuple[list[Finding], dict[str, object]]:
    check_choice(boundary, BOUNDARIES, "boundary")
    sealed = path / "verifier" / "handoffs.jsonl"
    decision_path = path / "verifier" / "decision.json"
    world = path / "artifacts" / "logs" / "world" / "handoffs.jsonl"
    if sealed.is_file():
        replay = _sealed(sealed, world)
    elif decision_path.exists():
        raise ValueError("verifier decision has no sealed handoff log")
    elif world.is_file():
        replay = _unsealed(world)
    else:
        raise ValueError("no handoff log in the trial directory; coverage is unknown")
    findings = [replay.finding(edge, boundary) for edge in replay.edges]
    native_id, status, grade = read_native_result(path)
    if grade is not None and replay.reward is not None and replay.reward != grade:
        raise ValueError("world closure and native verifier disagree on reward")
    link: dict[str, object] = {
        "world_run": replay.run,
        "native_trial": native_id,
        "log": "verifier" if sealed.is_file() else "world",
        "native_grade_status": status,
        "decision": _decision(decision_path, replay, grade),
    }
    return [replace(row, reward=grade) for row in findings], link
