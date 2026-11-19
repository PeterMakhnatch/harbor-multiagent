"""Per-seat job statistics.

A multi-seat trial records one ``SeatResult`` per seat. ``JobStats`` must
attribute each seat's usage to that seat, while the trial-level totals and
``evals`` keep the values a single-agent job produces today.
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from harbor.models.agent.context import AgentContext
from harbor.models.job.result import JobStats
from harbor.models.trial.config import TaskConfig, TrialConfig
from harbor.models.trial.result import (
    AgentInfo,
    ExceptionInfo,
    ModelInfo,
    SeatResult,
    TrialResult,
)
from harbor.models.verifier.result import VerifierResult

SEATS = ("maya", "raj", "lee", "ada")
MODELS = {
    "maya": "openai/gpt-6-luna",
    "raj": "anthropic/claude-opus",
    "lee": "google/gemini",
    "ada": "openai/gpt-6-luna",
    "zed": "mistral/large",
}


def _agent_info(seat: str | None = None) -> AgentInfo:
    model_name = MODELS[seat] if seat is not None else "openai/gpt-6-luna"
    return AgentInfo(
        name="scripted",
        version="1.0",
        model_info=ModelInfo(name=model_name, provider=model_name.split("/")[0]),
    )


def _context(seat: str, *, cost: float | None = 0.5) -> AgentContext:
    index = sum(ord(char) for char in seat)
    return AgentContext(
        n_input_tokens=100 * index,
        n_cache_tokens=10 * index,
        n_output_tokens=20 * index,
        cost_usd=cost,
    )


def _seat_result(
    seat: str, *, cost: float | None = 0.5, exception_type: str | None = None
) -> SeatResult:
    exception_info = None
    if exception_type is not None:
        exception_info = ExceptionInfo(
            exception_type=exception_type,
            exception_message=f"{seat} failed",
            exception_traceback="traceback",
            occurred_at=datetime(2026, 4, 28, tzinfo=UTC),
        )
    return SeatResult(
        seat=seat,
        under_test=seat != "lee",
        agent_info=_agent_info(seat),
        agent_result=_context(seat, cost=cost),
        exception_info=exception_info,
    )


def _trial_result(
    name: str = "hello",
    *,
    seats: list[SeatResult] | None = None,
    rewards: dict[str, float | int] | None = None,
    exception_type: str | None = None,
) -> TrialResult:
    config = TrialConfig(
        task=TaskConfig(path=Path(f"/tmp/{name}")),
        trial_name=name,
        job_id=uuid4(),
    )
    exception_info = None
    if exception_type is not None:
        exception_info = ExceptionInfo(
            exception_type=exception_type,
            exception_message="failed",
            exception_traceback="traceback",
            occurred_at=datetime(2026, 4, 28, tzinfo=UTC),
        )
    seat_results = seats
    agent_info = (
        next(seat.agent_info for seat in seats if seat.under_test and seat.agent_info)
        if seats
        else _agent_info()
    )
    return TrialResult(
        task_name=name,
        trial_name=name,
        trial_uri=f"file:///tmp/{name}",
        task_id=config.task.get_task_id(),
        source="dataset",
        task_checksum="abc123",
        config=config,
        agent_info=agent_info,
        verifier_result=VerifierResult(rewards=rewards)
        if rewards is not None
        else None,
        exception_info=exception_info,
        seat_results=seat_results,
    )


@pytest.mark.unit
def test_single_agent_trial_leaves_seat_stats_empty_and_matches_today() -> None:
    trial = _trial_result(rewards={"reward": 1})
    today = {
        "n_completed_trials": 1,
        "n_errored_trials": 0,
        "n_running_trials": 0,
        "n_pending_trials": 0,
        "n_cancelled_trials": 0,
        "n_retries": 0,
        "evals": {
            "scripted__openai/gpt-6-luna__dataset": {
                "n_trials": 1,
                "n_errors": 0,
                "metrics": [],
                "pass_at_k": {},
                "reward_stats": {"reward": {1: ["hello"]}},
                "exception_stats": {},
            }
        },
        "n_input_tokens": None,
        "n_cache_tokens": None,
        "n_output_tokens": None,
        "cost_usd": None,
    }

    stats = JobStats()
    stats.increment(trial)

    dumped = stats.model_dump()
    assert dumped["seat_stats"] == {}
    assert {key: dumped[key] for key in today} == today


@pytest.mark.unit
def test_four_seat_trial_records_each_seat_model_usage_and_role() -> None:
    seats = [_seat_result(seat) for seat in SEATS]
    trial = _trial_result(seats=seats, rewards={"maya": 1})

    stats = JobStats()
    stats.increment(trial)

    assert list(stats.seat_stats) == list(SEATS)
    for seat in SEATS:
        recorded = stats.seat_stats[seat]
        assert recorded.agent_name == "scripted"
        assert recorded.model_name == MODELS[seat]
        assert recorded.under_test is (seat != "lee")
        assert recorded.n_input_tokens == _context(seat).n_input_tokens
        assert recorded.n_cache_tokens == _context(seat).n_cache_tokens
        assert recorded.n_output_tokens == _context(seat).n_output_tokens
        assert recorded.cost_usd == 0.5


@pytest.mark.unit
def test_only_a_reward_key_equal_to_a_seat_name_lands_in_seat_stats() -> None:
    seats = [_seat_result(seat) for seat in SEATS]
    trial = _trial_result(seats=seats, rewards={"maya": 1, "team": 0})

    stats = JobStats()
    stats.increment(trial)

    assert stats.seat_stats["maya"].reward_stats == {"maya": {1: ["hello"]}}
    assert stats.seat_stats["maya"].n_trials == 1
    for seat in SEATS[1:]:
        assert stats.seat_stats[seat].reward_stats == {}
    assert "team" not in stats.model_dump()["seat_stats"]
    evals = stats.evals["scripted__openai/gpt-6-luna__dataset"]
    assert evals.reward_stats == {"maya": {1: ["hello"]}, "team": {0: ["hello"]}}


@pytest.mark.unit
def test_trial_totals_equal_the_seat_sum_and_match_today() -> None:
    seats = [_seat_result(seat) for seat in SEATS]
    trial = _trial_result(seats=seats, rewards={"maya": 1})

    stats = JobStats()
    stats.increment(trial)

    assert stats.n_input_tokens == sum(
        seat.n_input_tokens for seat in stats.seat_stats.values()
    )
    assert stats.n_cache_tokens == sum(
        seat.n_cache_tokens for seat in stats.seat_stats.values()
    )
    assert stats.n_output_tokens == sum(
        seat.n_output_tokens for seat in stats.seat_stats.values()
    )
    assert stats.cost_usd == sum(seat.cost_usd for seat in stats.seat_stats.values())
    assert stats.n_completed_trials == 1
    assert stats.n_errored_trials == 0
    assert list(stats.evals) == ["scripted__openai/gpt-6-luna__dataset"]


@pytest.mark.unit
def test_usage_complete_is_false_when_one_seat_reports_no_cost() -> None:
    seats = [_seat_result(seat, cost=None if seat == "ada" else 0.5) for seat in SEATS]
    trial = _trial_result(seats=seats, rewards={"maya": 1})

    stats = JobStats()
    stats.increment(trial)

    assert stats.seat_stats["ada"].usage_complete is False
    assert stats.seat_stats["ada"].cost_usd is None
    assert stats.seat_stats["maya"].usage_complete is True


@pytest.mark.unit
def test_increment_then_remove_returns_to_the_prior_state() -> None:
    first = _trial_result(
        "first", seats=[_seat_result(seat) for seat in SEATS], rewards={"maya": 1}
    )
    second = _trial_result(
        "second",
        seats=[_seat_result("maya", exception_type="RuntimeError")],
        rewards={"team": 0},
        exception_type="RuntimeError",
    )
    stats = JobStats()
    stats.increment(first)
    before = stats.model_dump()

    stats.increment(second)
    stats.remove_trial(second)

    after = stats.model_dump()
    evals = after["evals"]["scripted__openai/gpt-6-luna__dataset"]
    # remove_trial leaves an empty reward bucket and an empty exception list
    # in evals. That is today's behavior, so the comparison ignores it.
    evals["reward_stats"].pop("team", None)
    evals["exception_stats"].pop("RuntimeError", None)
    assert after == before


@pytest.mark.unit
def test_seat_stats_survive_a_json_round_trip() -> None:
    trial = _trial_result(
        seats=[_seat_result(seat) for seat in SEATS], rewards={"maya": 1, "team": 1}
    )
    stats = JobStats()
    stats.increment(trial)

    restored = JobStats.model_validate_json(stats.model_dump_json())

    assert restored.model_dump() == stats.model_dump()
