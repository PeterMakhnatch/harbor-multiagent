import json
from collections import Counter, defaultdict
from datetime import datetime
from fractions import Fraction
from hashlib import sha256
from math import isfinite
from typing import Any, Literal, NamedTuple
from uuid import UUID

from pydantic import BaseModel, Field, computed_field, model_validator

from harbor.models.agent.context import AgentContext
from harbor.models.trial.result import SeatResult, TrialResult

Rewards = dict[str, float | int]
TrialRewardsMap = dict[str, Rewards | None]
EvalsRewardsMap = dict[str, TrialRewardsMap]

SeatUsageField = Literal[
    "n_input_tokens", "n_cache_tokens", "n_output_tokens", "cost_usd"
]
_SEAT_USAGE_FIELDS: tuple[SeatUsageField, ...] = (
    "n_input_tokens",
    "n_cache_tokens",
    "n_output_tokens",
    "cost_usd",
)
_Usage = tuple[int | None, int | None, int | None, float | None]


def _seat_usage(context: AgentContext | None) -> _Usage:
    if context is None:
        return None, None, None, None
    cost = context.cost_usd
    return (
        context.n_input_tokens,
        context.n_cache_tokens,
        context.n_output_tokens,
        cost if cost is not None and isfinite(cost) else None,
    )


class _SeatGroup(NamedTuple):
    seat: str
    source: str | None
    agent_name: str | None
    model_provider: str | None
    model_name: str | None
    under_test: bool

    @classmethod
    def from_result(cls, source: str | None, result: SeatResult) -> "_SeatGroup":
        agent = result.agent_info
        model = agent.model_info if agent is not None else None
        return cls(
            result.seat,
            source,
            agent.name if agent is not None else None,
            model.provider if model is not None else None,
            model.name if model is not None else None,
            result.under_test,
        )

    def key(self) -> str:
        # The tuple encoding distinguishes None and strings without collisions.
        return json.dumps(self, separators=(",", ":"))


class _SeatContribution(NamedTuple):
    group: _SeatGroup
    exception_type: str | None
    usage: _Usage


class _TrialContribution(NamedTuple):
    trial_name: str
    evals_key: str
    rewards: Rewards
    exception_types: list[str]
    usage: _Usage
    seats: list[_SeatContribution]

    @classmethod
    def from_result(cls, result: TrialResult) -> "_TrialContribution":
        model = result.agent_info.model_info
        verifier = result.verifier_result
        return cls(
            result.trial_name,
            JobStats.format_agent_evals_key(
                result.agent_info.name,
                model.name if model is not None else None,
                result.source or "adhoc",
            ),
            (verifier.rewards or {})
            if verifier is not None and result.reward_valid
            else {},
            sorted({error.exception_type for error in result.recorded_exceptions()}),
            result.compute_token_cost_totals(),
            [
                _SeatContribution(
                    _SeatGroup.from_result(result.source, seat),
                    seat.exception_info.exception_type if seat.exception_info else None,
                    _seat_usage(seat.agent_result),
                )
                for seat in result.seat_results or []
            ],
        )

    def digest(self, trial_id: UUID) -> str:
        payload = json.dumps(
            (str(trial_id), self), sort_keys=True, separators=(",", ":")
        )
        return sha256(payload.encode()).hexdigest()


def _update_trial_names[K](
    buckets: dict[K, list[str]], key: K, trial_name: str, direction: Literal[1, -1]
) -> None:
    if direction == 1:
        buckets.setdefault(key, []).append(trial_name)
    else:
        names = buckets[key]
        names.remove(trial_name)
        if not names:
            del buckets[key]


class AgentDatasetStats(BaseModel):
    n_trials: int = 0
    n_errors: int = 0
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    pass_at_k: dict[int, float] = Field(default_factory=dict)
    reward_stats: dict[str, dict[float | int, list[str]]] = Field(  # ty: ignore[invalid-assignment]
        default_factory=lambda: defaultdict(lambda: defaultdict(list))
    )
    exception_stats: dict[str, list[str]] = Field(
        default_factory=lambda: defaultdict(list)
    )


class SeatDatasetStats(BaseModel):
    """Contributions from one (seat, source, agent, model, role) group."""

    seat: str
    source: str | None = None
    under_test: bool
    agent_name: str | None = None
    model_name: str | None = None
    model_provider: str | None = None
    n_participations: int = 0
    # Only trials with a valid reward attributed to this seat.
    n_trials: int = 0
    n_errors: int = 0
    reward_stats: dict[str, dict[float | int, list[str]]] = Field(  # ty: ignore[invalid-assignment]
        default_factory=lambda: defaultdict(lambda: defaultdict(list))
    )
    exception_stats: dict[str, list[str]] = Field(
        default_factory=lambda: defaultdict(list)
    )
    n_input_tokens: int | None = None
    n_cache_tokens: int | None = None
    n_output_tokens: int | None = None
    usage_report_counts: dict[SeatUsageField, int] = Field(default_factory=dict)
    # Keep the exact sum through retries and JSON round trips. Floating-point
    # subtraction can lose a surviving trial's cost when amounts differ widely.
    cost_usd_sum: Fraction | None = None

    @computed_field
    @property
    def cost_usd(self) -> float | None:
        return float(self.cost_usd_sum) if self.cost_usd_sum is not None else None

    def complete_usage(self, field: SeatUsageField) -> int | float | None:
        """Return this field's total only if every participation reports it."""
        if self.usage_report_counts.get(field) != self.n_participations:
            return None
        return getattr(self, field)

    def _update_usage(self, usage: _Usage, direction: Literal[1, -1]) -> None:
        for field, value in zip(_SEAT_USAGE_FIELDS, usage, strict=True):
            if value is None:
                continue
            count = self.usage_report_counts.get(field, 0) + direction
            if count:
                self.usage_report_counts[field] = count
            else:
                del self.usage_report_counts[field]
            if field == "cost_usd":
                self.cost_usd_sum = (
                    (self.cost_usd_sum or Fraction()) + direction * Fraction(str(value))
                    if count
                    else None
                )
            else:
                setattr(
                    self,
                    field,
                    (getattr(self, field) or 0) + direction * value if count else None,
                )


class JobStats(BaseModel):
    n_completed_trials: int = 0
    n_errored_trials: int = 0
    n_running_trials: int = 0
    n_pending_trials: int = 0
    n_cancelled_trials: int = 0
    n_retries: int = 0
    evals: dict[str, AgentDatasetStats] = Field(
        default_factory=lambda: defaultdict(AgentDatasetStats)
    )
    # Absent means the producer did not aggregate seats (including legacy v1
    # documents). An empty mapping means no seat results were recorded.
    seat_stats: dict[str, SeatDatasetStats] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    # If the original contributions are unavailable, removal must fail closed.
    trial_contribution_digests: dict[str, int] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    n_input_tokens: int | None = None
    n_cache_tokens: int | None = None
    n_output_tokens: int | None = None
    cost_usd: float | None = None

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_progress_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        data = data.copy()
        legacy_field_map = {
            "n_trials": "n_completed_trials",
            "n_errors": "n_errored_trials",
        }
        for legacy_field, current_field in legacy_field_map.items():
            if current_field not in data and legacy_field in data:
                data[current_field] = data[legacy_field]
        return data

    @staticmethod
    def format_agent_evals_key(
        agent_name: str, model_name: str | None, dataset_name: str
    ) -> str:
        """Format a key for the evals dictionary using agent name, model name, and dataset name."""
        if model_name:
            return f"{agent_name}__{model_name}__{dataset_name}"
        return f"{agent_name}__{dataset_name}"

    @classmethod
    def from_trial_results(
        cls,
        trial_results: list[TrialResult],
        *,
        n_total_trials: int | None = None,
        n_running_trials: int = 0,
        n_retries: int = 0,
    ) -> "JobStats":
        result = cls(seat_stats={}, trial_contribution_digests={})

        for trial_result in trial_results:
            result.increment(trial_result)

        if n_total_trials is not None:
            result.update_progress_counts(
                n_total_trials=n_total_trials,
                n_running_trials=n_running_trials,
                n_retries=n_retries,
            )

        return result

    @classmethod
    def from_counts(
        cls,
        *,
        n_total_trials: int,
        n_completed_trials: int = 0,
        n_running_trials: int = 0,
        n_errored_trials: int = 0,
        n_cancelled_trials: int = 0,
        n_retries: int = 0,
    ) -> "JobStats":
        return cls(
            n_completed_trials=n_completed_trials,
            n_errored_trials=n_errored_trials,
            n_running_trials=n_running_trials,
            n_pending_trials=max(
                n_total_trials - n_completed_trials - n_running_trials, 0
            ),
            n_cancelled_trials=n_cancelled_trials,
            n_retries=n_retries,
            seat_stats={} if n_completed_trials == 0 else None,
            trial_contribution_digests={} if n_completed_trials == 0 else None,
        )

    def update_progress_counts(
        self,
        *,
        n_total_trials: int,
        n_running_trials: int = 0,
        n_cancelled_trials: int | None = None,
        n_retries: int = 0,
    ) -> None:
        self.n_running_trials = n_running_trials
        self.n_pending_trials = max(
            n_total_trials - self.n_completed_trials - n_running_trials, 0
        )
        if n_cancelled_trials is not None:
            self.n_cancelled_trials = n_cancelled_trials
        self.n_retries = n_retries

    def increment(self, trial_result: TrialResult) -> None:
        contribution = _TrialContribution.from_result(trial_result)
        # An unpopulated JobStats() starts a new aggregate. An existing result
        # or count-only summary must not become a partial aggregate.
        if (
            "n_completed_trials" not in self.model_fields_set
            and not self.n_completed_trials
        ):
            if "seat_stats" not in self.model_fields_set:
                self.seat_stats = {}
            if "trial_contribution_digests" not in self.model_fields_set:
                self.trial_contribution_digests = {}

        digests = self.trial_contribution_digests
        if digests is not None:
            digest = contribution.digest(trial_result.id)
            digests[digest] = digests.get(digest, 0) + 1
        self.n_completed_trials += 1

        eval_stats = self.evals.setdefault(contribution.evals_key, AgentDatasetStats())
        trial_name = contribution.trial_name
        if contribution.rewards:
            eval_stats.n_trials += 1
            for key, value in contribution.rewards.items():
                reward_stats = eval_stats.reward_stats.setdefault(key, {})
                reward_stats.setdefault(value, []).append(trial_name)

        for exception_type in contribution.exception_types:
            eval_stats.exception_stats.setdefault(exception_type, []).append(trial_name)
        if contribution.exception_types:
            eval_stats.n_errors += 1
            self.n_errored_trials += 1
            if "CancelledError" in contribution.exception_types:
                self.n_cancelled_trials += 1

        n_input, n_cache, n_output, cost = contribution.usage
        if n_input is not None:
            self.n_input_tokens = (self.n_input_tokens or 0) + n_input
        if n_cache is not None:
            self.n_cache_tokens = (self.n_cache_tokens or 0) + n_cache
        if n_output is not None:
            self.n_output_tokens = (self.n_output_tokens or 0) + n_output
        if cost is not None:
            self.cost_usd = (self.cost_usd or 0.0) + cost

        self._update_seat_stats(contribution, 1)

    def _update_seat_stats(
        self, contribution: _TrialContribution, direction: Literal[1, -1]
    ) -> None:
        groups = self.seat_stats
        if groups is None:
            return
        rewards = contribution.rewards
        trial_name = contribution.trial_name
        for group, exception_type, usage in contribution.seats:
            key = group.key()
            if direction == 1 and key not in groups:
                groups[key] = SeatDatasetStats(**group._asdict())
            stats = groups[key]
            stats.n_participations += direction
            if group.seat in rewards:
                stats.n_trials += direction
                values = stats.reward_stats.setdefault(group.seat, {})
                _update_trial_names(values, rewards[group.seat], trial_name, direction)
                if not values:
                    del stats.reward_stats[group.seat]
            if exception_type is not None:
                stats.n_errors += direction
                _update_trial_names(
                    stats.exception_stats,
                    exception_type,
                    trial_name,
                    direction,
                )
            # Usage applies even when verification did not run or was invalid.
            stats._update_usage(usage, direction)
            # All other counters count subsets of these participations.
            if stats.n_participations == 0:
                del groups[key]

    def _validate_removal(
        self, digest: str, contribution: _TrialContribution
    ) -> tuple[AgentDatasetStats, dict[str, int]]:
        name = contribution.trial_name
        digests = self.trial_contribution_digests
        if digests is None:
            raise ValueError(
                "Cannot remove trial: original contributions are unavailable"
            )
        if self.n_completed_trials <= 0 or digests.get(digest, 0) <= 0:
            raise ValueError(
                f"Cannot remove trial {name!r}: contribution not recorded or changed"
            )

        eval_stats = self.evals.get(contribution.evals_key)
        if eval_stats is None:
            raise ValueError(
                f"Cannot remove trial {name!r}: evaluation group is missing"
            )
        if contribution.rewards and (
            eval_stats.n_trials <= 0
            or any(
                name not in eval_stats.reward_stats.get(key, {}).get(value, [])
                for key, value in contribution.rewards.items()
            )
        ):
            raise ValueError(
                f"Cannot remove trial {name!r}: evaluation rewards are inconsistent"
            )
        if contribution.exception_types and (
            eval_stats.n_errors <= 0
            or self.n_errored_trials <= 0
            or (
                "CancelledError" in contribution.exception_types
                and self.n_cancelled_trials <= 0
            )
            or any(
                name not in eval_stats.exception_stats.get(key, [])
                for key in contribution.exception_types
            )
        ):
            raise ValueError(
                f"Cannot remove trial {name!r}: evaluation errors are inconsistent"
            )

        self._validate_seat_removal(contribution)
        return eval_stats, digests

    def _validate_seat_removal(self, contribution: _TrialContribution) -> None:
        if self.seat_stats is None:
            return
        name = contribution.trial_name
        seats_by_group: dict[str, list[_SeatContribution]] = defaultdict(list)
        for seat in contribution.seats:
            seats_by_group[seat.group.key()].append(seat)
        for key, seats in seats_by_group.items():
            stats = self.seat_stats.get(key)
            n_removed = len(seats)
            if stats is None or stats.n_participations < n_removed:
                raise ValueError(
                    f"Cannot remove trial {name!r}: seat group {key} is missing contributions"
                )
            if seats[0].group.seat in contribution.rewards:
                seat_name = seats[0].group.seat
                value = contribution.rewards[seat_name]
                names = stats.reward_stats.get(seat_name, {}).get(value, [])
                if stats.n_trials < n_removed or names.count(name) < n_removed:
                    raise ValueError(
                        f"Cannot remove trial {name!r}: seat rewards for {key} are inconsistent"
                    )
            errors = Counter(
                seat.exception_type for seat in seats if seat.exception_type is not None
            )
            if stats.n_errors < errors.total() or any(
                stats.exception_stats.get(error, []).count(name) < count
                for error, count in errors.items()
            ):
                raise ValueError(
                    f"Cannot remove trial {name!r}: seat errors for {key} are inconsistent"
                )
            for index, field in enumerate(_SEAT_USAGE_FIELDS):
                removed_reports = sum(seat.usage[index] is not None for seat in seats)
                remaining_reports = (
                    stats.usage_report_counts.get(field, 0) - removed_reports
                )
                if not 0 <= remaining_reports <= stats.n_participations - n_removed:
                    raise ValueError(
                        f"Cannot remove trial {name!r}: seat usage for {key} is inconsistent"
                    )

    def remove_trial(self, trial_result: TrialResult) -> None:
        """Remove a trial only after verifying every recorded contribution."""
        contribution = _TrialContribution.from_result(trial_result)
        digest = contribution.digest(trial_result.id)
        eval_stats, digests = self._validate_removal(digest, contribution)

        self.n_completed_trials -= 1
        name = contribution.trial_name
        if contribution.rewards:
            eval_stats.n_trials -= 1
            for key, value in contribution.rewards.items():
                eval_stats.reward_stats[key][value].remove(name)

        for exception_type in contribution.exception_types:
            eval_stats.exception_stats[exception_type].remove(name)
        if contribution.exception_types:
            eval_stats.n_errors -= 1
            self.n_errored_trials -= 1
            if "CancelledError" in contribution.exception_types:
                self.n_cancelled_trials -= 1

        n_input, n_cache, n_output, cost = contribution.usage
        if n_input is not None and self.n_input_tokens is not None:
            self.n_input_tokens = max(0, self.n_input_tokens - n_input)
        if n_cache is not None and self.n_cache_tokens is not None:
            self.n_cache_tokens = max(0, self.n_cache_tokens - n_cache)
        if n_output is not None and self.n_output_tokens is not None:
            self.n_output_tokens = max(0, self.n_output_tokens - n_output)
        if cost is not None and self.cost_usd is not None:
            self.cost_usd = max(0.0, self.cost_usd - cost)
        self._update_seat_stats(contribution, -1)
        if digests[digest] == 1:
            del digests[digest]
        else:
            digests[digest] -= 1

    def update_trial(
        self,
        new_result: TrialResult,
        previous_result: TrialResult | None = None,
    ) -> None:
        """Update stats for a trial, removing previous contributions if this is a retry."""
        if previous_result is not None:
            self.remove_trial(previous_result)
        self.increment(new_result)


def _stats_count(stats: Any, field_name: str) -> int:
    if isinstance(stats, JobStats):
        return getattr(stats, field_name)
    if isinstance(stats, dict):
        value = stats.get(field_name, 0)
        if isinstance(value, int):
            return value
    return 0


class JobResult(BaseModel):
    # Unversioned documents are permanently version 1. Future versions must
    # dispatch/migrate missing versions as v1 before applying current defaults.
    schema_version: Literal[1] = Field(
        default=1,
        description="Serialized document schema version. Omission denotes the legacy version 1 format.",
    )

    id: UUID
    started_at: datetime
    updated_at: datetime | None = None
    finished_at: datetime | None = None
    n_total_trials: int
    stats: JobStats
    trial_results: list[TrialResult] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _derive_progress_stats(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        data = data.copy()
        status = data.pop("status", None)
        if data.get("updated_at") is None:
            status_updated_at = (
                status.get("updated_at") if isinstance(status, dict) else None
            )
            data["updated_at"] = (
                status_updated_at or data.get("finished_at") or data.get("started_at")
            )

        n_total_trials = data.get("n_total_trials", 0)
        if not isinstance(n_total_trials, int):
            n_total_trials = 0

        stats = data.get("stats", {}) or {}
        if isinstance(stats, JobStats):
            stats = stats.model_dump()
        elif isinstance(stats, dict):
            stats = stats.copy()
        else:
            stats = {}

        if "n_completed_trials" not in stats and "n_trials" in stats:
            stats["n_completed_trials"] = stats["n_trials"]
        if "n_errored_trials" not in stats and "n_errors" in stats:
            stats["n_errored_trials"] = stats["n_errors"]

        if isinstance(status, dict):
            status_field_map = {
                "n_completed_trials": "n_completed_trials",
                "n_errored_trials": "n_errored_trials",
                "n_running_trials": "n_running_trials",
                "n_pending_trials": "n_pending_trials",
                "n_cancelled_trials": "n_cancelled_trials",
                "n_retries": "n_retries",
            }
            for status_field, stats_field in status_field_map.items():
                if stats_field not in stats and status_field in status:
                    stats[stats_field] = status[status_field]

        completed_trials = _stats_count(stats, "n_completed_trials")
        running_trials = _stats_count(stats, "n_running_trials")
        if "n_pending_trials" not in stats:
            stats["n_pending_trials"] = max(
                n_total_trials - completed_trials - running_trials, 0
            )
        stats.setdefault("seat_stats", None)
        stats.setdefault("trial_contribution_digests", None)
        data["stats"] = stats
        return data
