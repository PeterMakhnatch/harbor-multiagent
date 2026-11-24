import json
from pathlib import Path

from agentsnet_protocol import is_message_map, score_coloring
from agentsnet_world import (
    FINAL_ROUND,
    FIXTURE_SHA256,
    MAX_STATE,
    MESSAGE_ROUNDS,
    SEATS,
    STATE_PATH,
    released_graph,
)

REWARD_PATH = Path("/logs/verifier/reward.json")


def completed_answers() -> list[object]:
    try:
        with STATE_PATH.open("rb") as log:
            data = log.read(MAX_STATE + 1)
        if len(data) > MAX_STATE:
            return []
        state = json.loads(data)
    except (OSError, ValueError, RecursionError):
        return []
    if (
        not isinstance(state, dict)
        or state.get("fixture_sha256") != FIXTURE_SHA256
        or state.get("round") != FINAL_ROUND
        or state.get("phase") != "done"
    ):
        return []
    seats = set(SEATS)
    rounds, answers = state.get("messages"), state.get("answers")
    if not isinstance(rounds, list) or len(rounds) != MESSAGE_ROUNDS:
        return []
    for outboxes in rounds:
        if not isinstance(outboxes, dict) or outboxes.keys() != seats:
            return []
        if not all(is_message_map(messages) for messages in outboxes.values()):
            return []
    if not isinstance(answers, dict) or answers.keys() != seats:
        return []
    return [answers[seat] for seat in SEATS]


if __name__ == "__main__":
    _, edges = released_graph()
    rewards = score_coloring(completed_answers(), edges)
    REWARD_PATH.write_text(json.dumps(rewards) + "\n", encoding="utf-8")
