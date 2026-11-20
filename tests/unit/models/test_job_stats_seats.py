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
from harbor.models.job.result import JobResult, JobStats, SeatDatasetStats
from harbor.models.trial.config import TaskConfig, TrialConfig
from harbor.models.trial.result import (
    AgentInfo,
    ExceptionInfo,
    ModelInfo,
    SeatResult,
    StepResult,
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
    source: str | None = "dataset",
    agent_result: AgentContext | None = None,
    steps: list[StepResult] | None = None,
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
    agent_info = (
        next(
            (
                seat.agent_info
                for seat in seats or []
                if seat.under_test and seat.agent_info
            ),
            None,
        )
        or _agent_info()
    )
    return TrialResult(
        task_name=name,
        trial_name=name,
        trial_uri=f"file:///tmp/{name}",
        task_id=config.task.get_task_id(),
        source=source,
        task_checksum="abc123",
        config=config,
        agent_info=agent_info,
        agent_result=agent_result,
        verifier_result=VerifierResult(rewards=rewards)
        if rewards is not None
        else None,
        exception_info=exception_info,
        seat_results=seats,
        step_results=steps,
    )


def _groups(stats: JobStats) -> dict[str, SeatDatasetStats]:
    assert stats.seat_stats is not None
    return stats.seat_stats


def _by_seat(stats: JobStats) -> dict[str, SeatDatasetStats]:
    return {group.seat: group for group in _groups(stats).values()}


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

    by_seat = _by_seat(stats)
    assert set(by_seat) == set(SEATS)
    for seat in SEATS:
        recorded = by_seat[seat]
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

    by_seat = _by_seat(stats)
    assert by_seat["maya"].reward_stats == {"maya": {1: ["hello"]}}
    assert by_seat["maya"].n_trials == 1
    for seat in SEATS[1:]:
        assert by_seat[seat].reward_stats == {}
        assert by_seat[seat].n_trials == 0
        assert by_seat[seat].n_participations == 1
    evals = stats.evals["scripted__openai/gpt-6-luna__dataset"]
    assert evals.reward_stats == {
        "maya": {1: ["hello"]},
        "team": {0: ["hello"]},
    }


@pytest.mark.unit
def test_trial_totals_equal_the_seat_sum_and_match_today() -> None:
    seats = [_seat_result(seat) for seat in SEATS]
    trial = _trial_result(seats=seats, rewards={"maya": 1})

    stats = JobStats()
    stats.increment(trial)

    assert stats.n_input_tokens == sum(
        seat.n_input_tokens or 0 for seat in _groups(stats).values()
    )
    assert stats.n_cache_tokens == sum(
        seat.n_cache_tokens or 0 for seat in _groups(stats).values()
    )
    assert stats.n_output_tokens == sum(
        seat.n_output_tokens or 0 for seat in _groups(stats).values()
    )
    assert stats.cost_usd == sum(seat.cost_usd or 0 for seat in _groups(stats).values())
    assert stats.n_completed_trials == 1
    assert stats.n_errored_trials == 0
    assert list(stats.evals) == ["scripted__openai/gpt-6-luna__dataset"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("context", "expected"),
    [
        (AgentContext(cost_usd=0.0), (None, None, None, 0.0)),
        (
            AgentContext(n_input_tokens=100, n_cache_tokens=0, n_output_tokens=20),
            (100, 0, 20, None),
        ),
        (
            AgentContext(n_input_tokens=100, n_output_tokens=20, cost_usd=0.5),
            (100, None, 20, 0.5),
        ),
    ],
)
def test_complete_usage_is_independent_for_each_field(
    context: AgentContext,
    expected: tuple[int | None, int | None, int | None, float | None],
) -> None:
    seat = _seat_result("maya").model_copy(update={"agent_result": context})
    stats = JobStats.from_trial_results([_trial_result(seats=[seat])])
    group = _by_seat(stats)["maya"]

    assert (
        group.complete_usage("n_input_tokens"),
        group.complete_usage("n_cache_tokens"),
        group.complete_usage("n_output_tokens"),
        group.complete_usage("cost_usd"),
    ) == expected


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


@pytest.mark.unit
@pytest.mark.parametrize(
    ("second_source", "agent", "model", "provider", "under_test"),
    [
        pytest.param("dsB", "other", "claude-B", "y", False, id="all-different"),
        pytest.param("dsB", "scripted", "gpt-A", "x", True, id="source"),
        pytest.param("dsA", "other", "gpt-A", "x", True, id="agent"),
        pytest.param("dsA", "scripted", "claude-B", "x", True, id="model"),
        pytest.param("dsA", "scripted", "gpt-A", "y", True, id="provider"),
        pytest.param("dsA", "scripted", "gpt-A", "x", False, id="role"),
    ],
)
def test_same_named_seat_keeps_distinct_assignments_across_trials(
    second_source: str, agent: str, model: str, provider: str, under_test: bool
) -> None:
    first_seat = _seat_result("maya")
    first_seat.agent_info = AgentInfo(
        name="scripted", version="1", model_info=ModelInfo(name="gpt-A", provider="x")
    )
    second_seat = _seat_result("maya")
    second_seat.under_test = under_test
    second_seat.agent_info = AgentInfo(
        name=agent, version="1", model_info=ModelInfo(name=model, provider=provider)
    )
    first = _trial_result(
        "first", seats=[first_seat], rewards={"maya": 1}, source="dsA"
    )
    second = _trial_result(
        "second", seats=[second_seat], rewards={"maya": 0}, source=second_source
    )
    stats = JobStats.from_trial_results([first])
    (first_group,) = _groups(stats).values()
    stats.increment(second)

    assert len(_groups(stats)) == 2
    (new_group,) = [
        group for group in _groups(stats).values() if group is not first_group
    ]
    assert first_group.model_name == "gpt-A"
    assert first_group.n_trials == 1
    assert first_group.n_input_tokens == _context("maya").n_input_tokens
    assert (new_group.seat, new_group.source, new_group.agent_name) == (
        "maya",
        second_source,
        agent,
    )
    assert (new_group.model_name, new_group.model_provider, new_group.under_test) == (
        model,
        provider,
        under_test,
    )
    assert new_group.n_trials == 1
    assert new_group.n_input_tokens == _context("maya").n_input_tokens
    stats.remove_trial(second)
    assert list(_groups(stats).values()) == [first_group]


@pytest.mark.unit
def test_retry_without_valid_rewards_keeps_other_trials_usage() -> None:
    first = _trial_result("first", seats=[_seat_result("maya", cost=0.01)])
    second = _trial_result("second", seats=[_seat_result("maya", cost=1e20)])
    stats = JobStats.from_trial_results([first, second])
    # Retracting a cost must also be exact across a JSON round trip.
    stats = JobStats.model_validate_json(stats.model_dump_json())

    stats.remove_trial(second)
    group = _by_seat(stats)["maya"]
    assert group.n_trials == group.n_errors == 0
    assert group.n_participations == 1
    assert group.n_input_tokens == _context("maya").n_input_tokens
    assert group.n_cache_tokens == _context("maya").n_cache_tokens
    assert group.n_output_tokens == _context("maya").n_output_tokens
    assert group.cost_usd == 0.01

    stats.remove_trial(first)
    assert stats.seat_stats == {}


@pytest.mark.unit
def test_retraction_restores_completeness_after_setup_failure() -> None:
    good = _trial_result("good", seats=[_seat_result("maya"), _seat_result("lee")])
    bad_seats = [
        _seat_result("maya", exception_type="RuntimeError"),
        _seat_result("lee"),
    ]
    for seat in bad_seats:
        seat.agent_result = None
    bad = _trial_result("bad", seats=bad_seats)
    stats = JobStats.from_trial_results([good])
    before = stats.model_dump()["seat_stats"]
    stats.increment(bad)
    for group in _groups(stats).values():
        assert group.n_participations == 2
        assert group.complete_usage("n_input_tokens") is None
        assert group.complete_usage("cost_usd") is None

    stats.remove_trial(bad)
    assert stats.model_dump()["seat_stats"] == before


@pytest.mark.unit
def test_partial_token_sum_is_unknown_and_last_report_retracts_to_none() -> None:
    full = _seat_result("maya")
    cost_only = _seat_result("maya")
    cost_only.agent_result = AgentContext(cost_usd=0.5)
    good = _trial_result("good", seats=[full], rewards={"maya": 1})
    partial = _trial_result("partial", seats=[cost_only], rewards={"maya": 1})
    stats = JobStats.from_trial_results([good, partial])
    group = _by_seat(stats)["maya"]

    assert group.n_input_tokens == _context("maya").n_input_tokens
    assert group.complete_usage("n_input_tokens") is None
    assert group.complete_usage("cost_usd") == 1.0
    assert group.n_trials == 2
    stats.remove_trial(good)
    assert group.reward_stats == {"maya": {1: ["partial"]}}
    assert group.n_input_tokens is None
    assert "n_input_tokens" not in group.usage_report_counts
    assert group.complete_usage("cost_usd") == 0.5


@pytest.mark.unit
@pytest.mark.parametrize("top_level_context", [True, False])
def test_seat_usage_covers_only_seat_contexts(top_level_context: bool) -> None:
    seat = _seat_result("maya")
    seat.agent_result = AgentContext(n_input_tokens=100, cost_usd=1.0)
    hybrid = _trial_result(
        "hybrid",
        seats=[seat],
        agent_result=AgentContext(n_input_tokens=999, cost_usd=9.0)
        if top_level_context
        else None,
        steps=[
            StepResult(
                step_name="build",
                agent_result=AgentContext(n_input_tokens=200, cost_usd=2.0),
            )
        ],
    )
    plain = _trial_result(
        "plain", agent_result=AgentContext(n_input_tokens=25, cost_usd=0.25)
    )
    stats = JobStats.from_trial_results([hybrid, plain])
    group = _by_seat(stats)["maya"]

    assert group.n_input_tokens == 100
    assert group.cost_usd == 1.0
    assert stats.n_input_tokens == (999 if top_level_context else 300) + 25
    assert stats.cost_usd == (9.0 if top_level_context else 3.0) + 0.25
    stats.remove_trial(hybrid)
    assert stats.seat_stats == {}
    assert stats.n_input_tokens == 25


@pytest.mark.unit
def test_errors_count_per_seat_not_per_trial() -> None:
    both = _trial_result(
        "both",
        seats=[
            _seat_result("maya", exception_type="RuntimeError"),
            _seat_result("lee", exception_type="RuntimeError"),
        ],
        exception_type="RuntimeError",
    )
    trial_only = _trial_result(
        "trial-only", seats=[_seat_result("maya")], exception_type="EnvError"
    )
    stats = JobStats.from_trial_results([both])
    assert stats.n_errored_trials == 1
    for group in _groups(stats).values():
        assert group.n_errors == 1
        assert group.exception_stats == {"RuntimeError": ["both"]}
    (eval_stats,) = stats.evals.values()
    assert eval_stats.exception_stats == {"RuntimeError": ["both"]}

    stats.remove_trial(both)
    assert stats.seat_stats == {}
    stats.increment(trial_only)
    assert stats.n_errored_trials == 1
    group = _by_seat(stats)["maya"]
    assert group.n_errors == 0
    assert group.exception_stats == {}


@pytest.mark.unit
@pytest.mark.parametrize("has_version", [True, False])
def test_legacy_job_omits_unavailable_seat_stats(has_version: bool) -> None:
    trial = _trial_result(seats=[_seat_result("maya")], rewards={"maya": 1})
    job = JobResult(
        id=uuid4(),
        started_at=datetime.now(UTC),
        n_total_trials=1,
        stats=JobStats.from_trial_results([trial]),
        trial_results=[trial],
    )
    legacy = job.model_dump(mode="json")
    del legacy["stats"]["seat_stats"]
    if not has_version:
        del legacy["schema_version"]

    restored = JobResult.model_validate(legacy)
    assert restored.schema_version == 1
    assert restored.stats.seat_stats is None
    assert "seat_stats" not in restored.model_dump(mode="json")["stats"]
    assert restored.trial_results[0].seat_results
    new = JobStats.from_trial_results([_trial_result(rewards={"reward": 1})])
    assert new.model_dump(mode="json")["seat_stats"] == {}
