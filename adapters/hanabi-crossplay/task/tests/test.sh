#!/bin/bash
set -euo pipefail

python3 - <<'PY'
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, "/world")
from engine import Game

CONFIG_PATH = Path("/world/config.json")
LOG_PATH = Path("/logs/world/hanabi.json")
VERIFIER_DIR = Path("/logs/verifier")
REWARD_PATH = VERIFIER_DIR / "reward.txt"
VERIFY_LOG_PATH = VERIFIER_DIR / "hanabi.json"
AUDIT_SUMMARY_PATH = VERIFIER_DIR / "verify_summary.json"
SEATS = ("p0", "p1")
PLAYER_TO_SEAT = {0: "p0", 1: "p1"}


def canonical_digest(value: object) -> str:
    data = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(data.encode("ascii")).hexdigest()


def fail(reason: str) -> None:
    print(f"VERIFICATION FAILED: {reason}", file=sys.stderr)
    raise SystemExit(1)


def check_seat_receipts(turns: list[dict]) -> dict[str, dict]:
    seat_summary: dict[str, dict] = {}
    for seat in SEATS:
        agent_dir = Path(f"/logs/seats/{seat}/agent")
        has_record = any(
            (agent_dir / name).is_file() and (agent_dir / name).stat().st_size > 0
            for name in ("trajectory.json", "oracle.txt")
        )
        if not has_record:
            fail(f"missing non-empty participant receipt under {agent_dir}")

        expected_entries = [
            (entry["turn"], entry["view_sha256"], entry["action"])
            for entry in turns
            if entry["seat"] == seat
        ]
        audit_path = agent_dir / "policy_audit.jsonl"
        audit_count = None
        if audit_path.is_file():
            raw_lines = [line for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            parsed = [json.loads(line) for line in raw_lines]
            observed = [
                (item.get("turn"), item.get("view_sha256"), item.get("action"))
                for item in parsed
            ]
            if any(item.get("seat") != seat for item in parsed):
                fail(f"seat {seat} policy_audit.jsonl contains wrong seat entry")
            if observed != expected_entries:
                fail(f"seat {seat} policy_audit.jsonl disagrees with world transcript")
            audit_count = len(parsed)

        seat_summary[seat] = {
            "turns": len(expected_entries),
            "policy_audit_entries": audit_count,
        }
    return seat_summary


def main() -> None:
    if not CONFIG_PATH.is_file():
        fail(f"missing {CONFIG_PATH}")
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    deck_id = cfg.get("deck_id")
    if type(deck_id) is not int:
        fail(f"invalid deck_id in {CONFIG_PATH}: {deck_id!r}")

    if not LOG_PATH.is_file():
        fail(f"missing world transcript {LOG_PATH}")
    raw_log = LOG_PATH.read_text(encoding="utf-8")
    transcript = json.loads(raw_log)
    if not isinstance(transcript, dict):
        fail("world transcript is not a JSON object")

    if transcript.get("schema") != "seatlab.hanabi-native/v1":
        fail(f"unexpected schema: {transcript.get('schema')!r}")
    if type(transcript.get("deck_id")) is not int or transcript.get("deck_id") != deck_id:
        fail(f"deck_id mismatch: transcript={transcript.get('deck_id')!r} config={deck_id!r}")

    turns = transcript.get("turns")
    if not isinstance(turns, list):
        fail("transcript 'turns' must be a list")

    game = Game(deck_id)
    if transcript.get("deck_sha256") != game.deck_sha256:
        fail(
            f"deck_sha256 mismatch: transcript={transcript.get('deck_sha256')!r} "
            f"engine={game.deck_sha256!r}"
        )

    history: list[list[object]] = []
    for index, entry in enumerate(turns):
        if not isinstance(entry, dict):
            fail(f"turn entry {index} is not an object")
        if game.player is None:
            fail(f"transcript has extra turn at index {index} after terminal state")

        expected_turn = game.turn
        expected_seat = PLAYER_TO_SEAT[game.player]
        expected_view = game.view()
        expected_digest = canonical_digest(expected_view)

        if type(entry.get("turn")) is not int or entry.get("turn") != expected_turn:
            fail(f"turn mismatch at index {index}: got {entry.get('turn')!r}, expected {expected_turn}")
        if entry.get("seat") != expected_seat:
            fail(f"seat mismatch at turn {expected_turn}: got {entry.get('seat')!r}, expected {expected_seat}")
        if entry.get("view") != expected_view:
            fail(f"view mismatch at turn {expected_turn}")
        if entry.get("view_sha256") != expected_digest:
            fail(
                f"view_sha256 mismatch at turn {expected_turn}: "
                f"got {entry.get('view_sha256')!r}, expected {expected_digest!r}"
            )

        action = entry.get("action")
        if type(action) is not int:
            fail(f"non-integer action at turn {expected_turn}: {action!r}")
        legal_ids = {int(item["id"]) for item in expected_view["legal"]}
        if action not in legal_ids:
            fail(f"illegal action {action} at turn {expected_turn}")

        history.append([expected_turn, expected_seat, expected_digest, action])
        game.apply(action)

    if game.player is not None:
        fail(f"incomplete game: stopped before terminal state at turn {game.turn}")

    expected_scores = game.scores()
    recorded_scores = transcript.get("scores")
    if (
        not isinstance(recorded_scores, list)
        or len(recorded_scores) != 2
        or any(type(s) is not int for s in recorded_scores)
        or recorded_scores != expected_scores
        or expected_scores[0] != expected_scores[1]
        or not 0 <= expected_scores[0] <= 25
    ):
        fail(f"scores mismatch or invalid: transcript={recorded_scores!r} replay={expected_scores!r}")

    seat_summary = check_seat_receipts(turns)
    raw_score = expected_scores[0]
    reward = raw_score / 25.0

    VERIFIER_DIR.mkdir(parents=True, exist_ok=True)
    VERIFY_LOG_PATH.write_text(raw_log, encoding="utf-8")
    for seat in SEATS:
        src_audit = Path(f"/logs/seats/{seat}/agent/policy_audit.jsonl")
        if src_audit.is_file():
            (VERIFIER_DIR / f"policy_audit_{seat}.jsonl").write_text(
                src_audit.read_text(encoding="utf-8"), encoding="utf-8"
            )
    AUDIT_SUMMARY_PATH.write_text(
        json.dumps(
            {
                "schema": "seatlab.hanabi-verify/v1",
                "deck_id": deck_id,
                "deck_sha256": game.deck_sha256,
                "history_sha256": canonical_digest(history),
                "decisions": len(turns),
                "raw_score": raw_score,
                "scores": expected_scores,
                "reward": reward,
                "seats": seat_summary,
            },
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    REWARD_PATH.write_text(f"{reward}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
PY
