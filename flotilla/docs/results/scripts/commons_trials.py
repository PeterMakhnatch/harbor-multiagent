"""Commons trials (docs/COMMONS.md): per trial, the arm, the season round by round, the measures, the
messages and each seat's model use.

    python3 flotilla/docs/results/scripts/commons_trials.py .flotilla-local/jobs/<job> [<job> ...]

Reads result.json, verifier/score.json and verifier/world-state.json of every trial folder. Host-side
output: it prints the secret season length. Standard library only.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("commons", Path(__file__).resolve().parents[3] / "games" / "commons.py")
COMMONS = importlib.util.module_from_spec(_SPEC)  # type: ignore[arg-type]
_SPEC.loader.exec_module(COMMONS)  # type: ignore[union-attr]


def pledges(trial: Path, messages: list[dict]) -> str:
    """Pledges kept: a message's pledge against its sender's next request (artifacts/logs/world/events.jsonl)."""
    events_path = trial / "artifacts" / "logs" / "world" / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()] if events_path.is_file() else []
    requests = [(e["t"], e["seat"], e["data"]["amount"]) for e in events if e["kind"] == "request"]
    parsed = evaluable = kept = 0
    for message in messages:
        amount = COMMONS.pledge(str(message.get("text")))
        if amount is None:
            continue
        parsed += 1
        later = [value for t, seat, value in requests if seat == message.get("from") and t > message.get("t", 0)]
        if later:
            evaluable += 1
            kept += later[0] <= amount
    return f"pledges: {parsed} of {len(messages)} messages, kept {kept} of {evaluable} with a later request"


def usage(seat: dict) -> str:
    context = seat.get("agent_result") or {}
    return (f"{seat.get('seat')}: input {context.get('n_input_tokens')}, cached {context.get('n_cache_tokens')}, "
            f"output {context.get('n_output_tokens')}, cost {context.get('cost_usd')}")


def main() -> None:
    for job in map(Path, sys.argv[1:]):
        for trial in sorted(path for path in job.glob("*/") if (path / "verifier" / "score.json").is_file()):
            score = json.loads((trial / "verifier" / "score.json").read_text())
            state = json.loads((trial / "verifier" / "world-state.json").read_text())
            result = json.loads((trial / "result.json").read_text())
            print(f"{job.name}/{trial.name}: arm {score.get('condition')}, reward {score.get('reward')}, "
                  f"survived {score.get('survived')}, rounds {score.get('rounds_played')} of "
                  f"{score.get('rounds_planned')}, collapsed in {score.get('collapsed_in')}, over-use "
                  f"{score.get('over_use')}, missed {score.get('missed')}, totals {score.get('totals')}, "
                  f"messages {score.get('n_messages')}, error {score.get('error')}")
            for entry in (state.get("game") or {}).get("log") or []:
                print(f"    round {entry['round']}: stock {entry['stock']}, requests {entry['requests']}, "
                      f"catch {entry['catch']}, missed {entry['missed']}")
            for message in state.get("messages") or []:
                print(f"    message {message.get('id')} {message.get('from')}->{message.get('to')} at "
                      f"{message.get('t')} s: {str(message.get('text'))[:240]!r}")
            if state.get("messages"):
                print("    " + pledges(trial, state["messages"]))
            seats = result.get("seat_results") or []
            for seat in seats:
                print("    " + usage(seat))
            failed = [seat.get("seat") for seat in seats if seat.get("exception_info")]
            if failed or result.get("exception_info"):
                print(f"    exceptions: seats {failed}, trial {bool(result.get('exception_info'))}")


if __name__ == "__main__":
    main()
