"""Read native Dispatch Handoff trial directories without importing Harbor.

Each JSONL row represents one native trial, not one shipment. Per-seat usage
comes from the native AgentContext; unreported values stay null, including
for deterministic agents. Model identity comes from native AgentInfo.
"""

import argparse
import hashlib
import importlib.util
import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict, cast
from uuid import UUID

_ROLES = ("inventory", "dispatch")
_SCORER_PATH = (
    Path(__file__).resolve().parent.parent / "tasks/dispatch-handoff/tests/score.py"
)


class SeatEvidence(TypedDict):
    under_test: bool
    agent_name: str
    agent_version: str
    model_info: dict[str, str | None] | None
    n_input_tokens: int | None
    n_cache_tokens: int | None
    n_output_tokens: int | None
    cost_usd: float | int | None


class TrialEvidence(TypedDict):
    trial_id: str
    condition: str
    seed: int
    score: dict[str, Any]
    fingerprint: str
    seats: dict[str, SeatEvidence]


def _load_scorer() -> Callable[[dict[str, Any]], dict[str, Any]]:
    spec = importlib.util.spec_from_file_location(
        "_dispatch_handoff_score", _SCORER_PATH
    )
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load verifier at {_SCORER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.__dict__["score"]


_score = _load_scorer()


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: expected a JSON object")
    return data


def _trial_id(native: dict[str, Any]) -> str:
    value = native.get("id")
    if not isinstance(value, str):
        raise ValueError("missing native trial UUID")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ValueError("invalid native trial UUID") from exc
    if str(parsed) != value or parsed.int == 0:
        raise ValueError("expected a canonical native trial UUID")
    return value


def _require_success(record: dict[str, Any], subject: str) -> None:
    if "exception_info" not in record:
        raise ValueError(f"{subject}: missing native exception status")
    exception = record["exception_info"]
    if exception is None:
        return
    kind = exception.get("exception_type") if isinstance(exception, dict) else None
    if isinstance(kind, str) and kind.isidentifier():
        raise ValueError(f"{subject}: native execution failed ({kind})")
    raise ValueError(f"{subject}: native execution failed")


def _by_role(value: object, field: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list) or len(value) != len(_ROLES):
        raise ValueError(f"{field}: expected one record per seat")
    by_role: dict[str, dict[str, Any]] = {}
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError(f"{field}: expected a seat object")
        role = entry.get("seat")
        if not isinstance(role, str) or role not in _ROLES or role in by_role:
            raise ValueError(f"{field}: unknown or duplicate seat {role!r}")
        # JSON object member names are strings.
        by_role[role] = cast(dict[str, Any], entry)
    return by_role


def _token_count(context: dict[str, Any], role: str, field: str) -> int | None:
    count = context.get(field)
    if count is None:
        return None
    if type(count) is not int or count < 0:
        raise ValueError(f"seat {role!r}: invalid {field}")
    return count


def _seat(
    role: str, seat: dict[str, Any], participant: dict[str, Any], condition: str
) -> SeatEvidence:
    if seat.get("under_test") is not True:
        raise ValueError(f"seat {role!r}: must be under test")
    _require_success(seat, f"seat {role!r}")
    execution = seat.get("agent_execution")
    if not isinstance(execution, dict) or not all(
        isinstance(execution.get(key), str) and execution[key]
        for key in ("started_at", "finished_at")
    ):
        raise ValueError(f"seat {role!r}: missing complete execution timing")

    info = seat.get("agent_info")
    if not isinstance(info, dict):
        raise ValueError(f"seat {role!r}: missing agent identity")
    name, version = info.get("name"), info.get("version")
    if (
        not isinstance(name, str)
        or not name
        or not isinstance(version, str)
        or not version
    ):
        raise ValueError(f"seat {role!r}: missing agent name or version")
    configured_name = participant.get("name")
    if configured_name in ("nop", "oracle") and configured_name != name:
        raise ValueError(f"seat {role!r}: agent differs from native participant config")
    if (name == "nop") != (condition == "pooled" and role == "inventory"):
        raise ValueError(f"seat {role!r}: nop is required only for pooled inventory")

    model_info = None
    model = info.get("model_info")
    if model is not None:
        if not isinstance(model, dict):
            raise ValueError(f"seat {role!r}: invalid model identity")
        model_name, provider = model.get("name"), model.get("provider")
        if (
            not isinstance(model_name, str)
            or not model_name
            or (
                provider is not None and (not isinstance(provider, str) or not provider)
            )
        ):
            raise ValueError(f"seat {role!r}: invalid model name or provider")
        model_info = {"name": model_name, "provider": provider}

    context = seat.get("agent_result")
    if not isinstance(context, dict):
        raise ValueError(f"seat {role!r}: missing native agent result")
    n_input = _token_count(context, role, "n_input_tokens")
    n_cache = _token_count(context, role, "n_cache_tokens")
    n_output = _token_count(context, role, "n_output_tokens")
    if n_cache is not None and n_input is not None and n_cache > n_input:
        raise ValueError(f"seat {role!r}: cache tokens exceed total input tokens")
    cost = context.get("cost_usd")
    if cost is not None and (
        type(cost) not in (int, float) or not 0 <= cost < math.inf
    ):
        raise ValueError(f"seat {role!r}: invalid cost_usd")
    return {
        "under_test": True,
        "agent_name": name,
        "agent_version": version,
        "model_info": model_info,
        "n_input_tokens": n_input,
        "n_cache_tokens": n_cache,
        "n_output_tokens": n_output,
        "cost_usd": cost,
    }


def read_trial_with_world(path: Path | str) -> tuple[TrialEvidence, dict[str, Any]]:
    """Return the trial receipt and the same world dossier used to score it."""
    root = Path(path)
    native = _read_json(root / "result.json")
    version = native.get("schema_version", 1)
    if type(version) is not int or version != 1:
        raise ValueError("unsupported native trial result schema")
    trial_id = _trial_id(native)
    _require_success(native, "trial")
    finished = native.get("finished_at")
    if (
        not isinstance(finished, str)
        or not finished
        or native.get("reward_valid") is not True
    ):
        raise ValueError("native trial did not finish with a valid reward")
    if native.get("step_results") is not None or native.get("agent_result") is not None:
        raise ValueError(
            "expected a native seat trial, not a step or single-agent trial"
        )
    verifier = native.get("verifier_result")
    rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
    if not isinstance(rewards, dict) or rewards.keys() != {"reward"}:
        raise ValueError("expected the native verifier's single reward")

    dossier = _read_json(root / "artifacts/logs/world/handoff.json")
    outcome = _score(dossier)
    reward = rewards["reward"]
    if (
        type(reward) not in (int, float)
        or reward != outcome["optimal"] / outcome["episodes"]
    ):
        raise ValueError("native verifier reward does not match the independent score")

    settings = dossier["settings"]
    condition = settings["condition"]
    records = _by_role(native.get("seat_results"), "seat_results")
    config = native.get("config")
    if not isinstance(config, dict):
        raise ValueError("missing native trial config")
    participants = _by_role(config.get("participants"), "config.participants")
    seats = {
        role: _seat(role, records[role], participants[role], condition)
        for role in _ROLES
    }

    latent = [
        {field: case[field] for field in ("id", "utilities", "before", "after")}
        for case in dossier["cases"]
    ]
    canonical = json.dumps(latent, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    receipt: TrialEvidence = {
        "trial_id": trial_id,
        "condition": condition,
        "seed": settings["seed"],
        "score": outcome,
        "fingerprint": hashlib.sha256(canonical).hexdigest(),
        "seats": seats,
    }
    return receipt, dossier


def read_trial(path: Path | str) -> TrialEvidence:
    """Return verified trial evidence, or raise ValueError/OSError if unusable."""
    receipt, _ = read_trial_with_world(path)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trial_dirs", metavar="TRIAL", type=Path, nargs="+")
    args = parser.parse_args()
    trials = []
    seen = set()
    for root in args.trial_dirs:
        try:
            trial = read_trial(root)
            trial_id = trial["trial_id"]
            if trial_id in seen:
                raise ValueError(f"duplicate native trial UUID {trial_id}")
        except (ValueError, OSError) as exc:
            parser.exit(1, f"{root}: {exc}\n")
        seen.add(trial_id)
        trials.append(trial)
    for trial in trials:
        print(json.dumps(trial, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
