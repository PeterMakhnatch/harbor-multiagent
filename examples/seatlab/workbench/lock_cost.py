"""Preview a hypothetical USD cost ceiling for a one-roster Harbor job lock."""

import argparse
import math
import sys
from pathlib import Path
from typing import Any

from ...native_study import (
    AGENT_NAME,
    DIGEST,
    KEY_NAME,
    as_int,
    as_list,
    as_record,
    as_text,
    require,
)
from .team_prices import (
    CONDITIONS,
    CURRENCY,
    MAX_ATTEMPTS,
    MAX_SEATS,
    MODEL_NAME,
    NOT_CLAIMED,
    ORACLE,
    PREVIEW,
    ROUNDING,
    SCRIPTED,
    declared_usd,
    exact,
    load_costs,
    new_parser,
    read_json,
    run,
    seat_rows,
    team_ceiling,
    times,
    verdict,
)

SCHEMA = "harbor.job-lock.cost-ceiling-preview/v1"
LOCK_SCHEMA_VERSION = 3
MAX_LOCK_BYTES = 16 << 20
MAX_TRIALS = 4096
MAX_TURNS = 100_000
MODEL_AGENTS = ("terminus-2",)
NATIVE_DEFAULTS: dict[str, Any] = {
    "skip_model_parsing": False,
    "skills": [],
    "resume_trajectory": False,
    "extra_allowed_hosts": [],
    "include_logs": [],
    "exclude_logs": [],
    "env": {},
    "mcp_servers": [],
}
AGENT_DEFAULTS: dict[str, Any] = {**NATIVE_DEFAULTS, "name": ORACLE, "kwargs": {}}
NONE_DEFAULTS = frozenset(
    {
        "import_path",
        "base_url",
        "model_api_format",
        "n_concurrent",
        "concurrency_group",
        "override_timeout_sec",
        "override_setup_timeout_sec",
        "max_timeout_sec",
        "load_trajectory",
    }
)
MODEL_FIELDS = frozenset({"model_name", "model_api_key_name"})
AGENT_NONES = NONE_DEFAULTS | MODEL_FIELDS
PARTICIPANT_FREE = frozenset({"seat", "name", "kwargs"}) | MODEL_FIELDS
MODEL_KWARGS = frozenset(
    {"max_turns", "model_info", "record_terminal_session", "suppress_max_turns_warning"}
)
KWARG_FLAGS = ("record_terminal_session", "suppress_max_turns_warning")
REFUSED_KWARGS = frozenset(
    {
        "episodes",
        "max_episodes",
        "parser_name",
        "api_base",
        "temperature",
        "reasoning_effort",
        "collect_rollout_details",
        "session_id",
        "enable_summarize",
        "proactive_summarization_threshold",
        "max_thinking_tokens",
        "trajectory_config",
        "tmux_pane_width",
        "tmux_pane_height",
        "store_all_messages",
        "interleaved_thinking",
        "use_responses_api",
        "llm_backend",
        "llm_kwargs",
        "llm_call_kwargs",
        "session_id_headers",
    }
)
MODEL_INFO_FIELDS = frozenset(
    {
        "litellm_provider",
        "mode",
        "max_input_tokens",
        "max_output_tokens",
        "input_cost_per_token",
        "output_cost_per_token",
        "cache_read_input_token_cost",
    }
)
MODEL_INFO_NUMBERS = MODEL_INFO_FIELDS - {"litellm_provider", "mode"}
LOCK_CONDITIONS = (
    ("Harbor starts each final slot at most max_retries + 1 times."),
    ("The ceiling uses attempts_per_ticket for each final slot."),
    ("The ceiling covers only the trials in this lock."),
    ("Model seats must be terminus-2 and use only the kwargs that v1 lists."),
    ("Any other agent, field, kwarg or env entry stops the preview."),
    ("v1 reads only the lock. It does not inspect the task or the harness code."),
)
Roster = tuple[tuple[str, str, str | None], ...]


def _same(value: object, default: object) -> bool:
    return type(value) is type(default) and value == default


def _native_fields(
    record: dict[str, Any],
    defaults: dict[str, Any],
    nones: frozenset[str],
    free: frozenset[str],
    where: str,
) -> None:
    for key, value in record.items():
        if key in free:
            continue
        if key in defaults:
            require(_same(value, defaults[key]), f"{where} {key} is not its default")
        elif key in nones:
            require(value is None, f"{where} sets {key}; v1 refuses it")
        else:
            require(False, f"{where} sets a field v1 does not list")


def _non_negative(value: object) -> bool:
    if type(value) is int:
        return value >= 0
    return type(value) is float and math.isfinite(value) and value >= 0


def _model_info(value: object, model: str, where: str) -> None:
    where = f"{where} model_info"
    info = as_record(value, where)
    require(set(info) <= MODEL_INFO_FIELDS, f"{where} sets a field v1 does not list")
    require(_same(info.get("mode", "chat"), "chat"), f"{where} mode must be chat")
    prefix = model.split("/", 1)[0]
    require(
        _same(info.get("litellm_provider", prefix), prefix),
        f"{where} names a provider other than the model_name prefix",
    )
    for field in sorted(MODEL_INFO_NUMBERS & set(info)):
        require(
            _non_negative(info[field]),
            f"{where} {field} is not a finite non-negative number",
        )


def _model_kwargs(kwargs: dict[str, Any], model: str, where: str) -> None:
    where = f"{where} kwargs"
    extra = set(kwargs) - MODEL_KWARGS
    named = sorted(extra & REFUSED_KWARGS)
    require(not named, f"{where} set terminus-2 options {named}; v1 refuses them")
    require(not extra, f"{where} set a field v1 does not list")
    if kwargs.get("max_turns") is not None:
        as_int(kwargs["max_turns"], f"{where} max_turns", 1, MAX_TURNS)
    for flag in KWARG_FLAGS:
        require(type(kwargs.get(flag, False)) is bool, f"{where} {flag} is not bool")
    if kwargs.get("model_info") is not None:
        _model_info(kwargs["model_info"], model, where)


def _participant(value: object, where: str) -> tuple[str, str, str | None]:
    member = as_record(value, where)
    _native_fields(member, NATIVE_DEFAULTS, NONE_DEFAULTS, PARTICIPANT_FREE, where)
    seat = as_text(member.get("seat"), f"{where} seat", AGENT_NAME)
    name = as_text(member.get("name"), f"{where} name", AGENT_NAME)
    kwargs = as_record(member.get("kwargs", {}), f"{where} kwargs")
    if name in SCRIPTED:
        require(
            not kwargs and all(member.get(key) is None for key in MODEL_FIELDS),
            f"{where} is scripted but sets a model or kwargs",
        )
        return seat, name, None
    require(
        name in MODEL_AGENTS,
        f"{where} agent is outside v1; model seats must be {list(MODEL_AGENTS)}",
    )
    model = as_text(member.get("model_name"), f"{where} model_name", MODEL_NAME)
    if member.get("model_api_key_name") is not None:
        as_text(member["model_api_key_name"], f"{where} key name", KEY_NAME)
    _model_kwargs(kwargs, model, where)
    return seat, name, model


def _roster(value: object, position: int) -> Roster:
    where = f"lock trial {position}"
    trial = as_record(value, where)
    require(_same(trial.get("install_only", False), False), f"{where} is install-only")
    require(trial.get("user_agent") is None, f"{where} has a simulated user agent")
    require(trial.get("source_trial") is None, f"{where} regrades a source trial")
    agent = as_record(trial.get("agent", {}), f"{where} agent")
    _native_fields(
        agent, AGENT_DEFAULTS, AGENT_NONES, frozenset(), f"{where} default agent"
    )
    members = trial.get("participants")
    require(bool(members), f"{where} has no participants; v1 prices seat rosters only")
    listed = as_list(members, f"{where} participants", 1, MAX_SEATS)
    found = [
        _participant(member, f"{where} participant {index}")
        for index, member in enumerate(listed)
    ]
    roster = tuple(sorted(found, key=lambda row: row[0]))
    require(len({row[0] for row in roster}) == len(roster), f"{where} repeats a seat")
    return roster


def load_lock(path: Path, expected: str | None) -> tuple[str, int, int, Roster]:
    if expected is not None:
        as_text(expected, "--expected-lock", DIGEST)
    record, digest = read_json(path, MAX_LOCK_BYTES, "lock")
    require(
        expected is None or digest == expected,
        "lock digest differs from --expected-lock",
    )
    lock = as_record(record, "lock")
    as_int(
        lock.get("schema_version"),
        "lock schema_version",
        LOCK_SCHEMA_VERSION,
        LOCK_SCHEMA_VERSION,
    )
    retry = as_record(lock.get("retry"), "lock retry")
    retries = as_int(
        retry.get("max_retries"), "lock retry max_retries", 0, MAX_ATTEMPTS - 1
    )
    trials = as_list(lock.get("trials"), "lock trials", 1, MAX_TRIALS)
    roster = _roster(trials[0], 0)
    for position, trial in enumerate(trials[1:], start=1):
        require(
            _roster(trial, position) == roster,
            "lock mixes seat rosters; v1 binds one exact roster",
        )
    return digest, retries, len(trials), roster


def build(args: argparse.Namespace) -> dict[str, Any]:
    declared = declared_usd(args.declared_usd)
    digest, retries, slots, roster = load_lock(args.lock, args.expected_lock)
    members = {
        seat: {"name": name, "model_name": model} for seat, name, model in roster
    }
    attempts, seats, digests = load_costs(args, members)
    require(
        attempts >= retries + 1,
        "seat limits attempts_per_ticket must be at least lock max_retries + 1 "
        f"= {retries + 1}",
    )
    per_seat, team = team_ceiling(seats)
    runs = slots * attempts
    return {
        "schema": SCHEMA,
        "preview": PREVIEW,
        "lock": digest,
        "lock_digest": "expected" if args.expected_lock is not None else "computed",
        **digests,
        "currency": CURRENCY,
        "rounding": ROUNDING,
        "final_slots": slots,
        "max_retries": retries,
        "attempts_per_ticket": attempts,
        "trial_attempts": runs,
        "seats": seat_rows(seats, per_seat, runs),
        "team_attempt_ceiling_usd_exact": exact(team),
        **verdict(times(runs, team), declared),
        "conditions": [*CONDITIONS, *LOCK_CONDITIONS],
        "not": list(NOT_CLAIMED),
    }


def parser() -> argparse.ArgumentParser:
    found = new_parser(
        "python3.12 -BS -m examples.seatlab.workbench.lock_cost",
        "Preview a hypothetical USD cost ceiling for a Harbor job lock whose "
        "trials share one seat roster. It reads files only and launches nothing. "
        "It is not a spend cap and not permission to start a trial. Exit 0: at "
        "or below the declared USD. Exit 1: above. Exit 2: malformed, "
        "unsupported or incomplete input.",
    )
    found.add_argument("lock", type=Path, metavar="LOCK")
    found.add_argument("--expected-lock", metavar="SHA256")
    return found


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return run(build, args, [args.lock, args.rates, args.limits], "lock_cost")


if __name__ == "__main__":
    sys.exit(main())
