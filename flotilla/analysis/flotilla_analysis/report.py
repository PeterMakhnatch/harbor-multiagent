"""Assemble the analysis into one JSON-ready report.

Groups: results are grouped by task label (``<family>`` or ``<family>.<variant>``, never pooled) and
by model team (the agent and model of the seats under test). Conditions are paired by instance.
"""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from flotilla_analysis import __version__, stats
from flotilla_analysis.load import CONDITIONS, Job, Problem, Trial, load, load_evidence, load_trajectories
from flotilla_analysis.names import BUILD_FIELDS
from flotilla_analysis.play import ANNOUNCE_BEFORE, FULL, WINDOW, Play, analyze
from flotilla_analysis.tasks import TaskInfo, own_briefing_score, task_info

GroupKey = tuple[str, str]  # (label, team)


class MixedBuildsError(ValueError):
    """The requested report would pool trials with different build settings."""


def _different_fields(records: list[dict[str, Any]], fields: Iterable[str]) -> set[str]:
    different = set()
    for field in fields:
        values = [record[field] for record in records if field in record]
        if values and any(value != values[0] for value in values[1:]):
            different.add(field)
    return different


def _build_checks(
    groups: dict[GroupKey, list[Trial]], world_settings: dict[Path, dict[str, Any]]
) -> list[dict[str, Any]]:
    checks = []
    for (label, team), trials in groups.items():
        unknown = sum(t.build is None for t in trials)
        recorded = [t.build or {} for t in trials]
        effective = [r | world_settings.get(t.directory, {}) for t, r in zip(trials, recorded, strict=True)]
        shared = [f for f in BUILD_FIELDS if f != "n_seats"]
        fields = _different_fields(recorded, shared) | _different_fields(effective, shared)
        for solo in (True, False):
            arm = [r for t, r in zip(trials, recorded, strict=True) if (t.condition == "solo") == solo]
            fields |= _different_fields(arm, ("n_seats",))
        if 0 < unknown < len(trials):
            fields.add("build record (known vs unknown)")
        checks.append(
            {
                "label": label,
                "team": team,
                "unknown_build_trials": unknown,
                "differing_fields": sorted(fields),
            }
        )
    return checks


def _mean(values: Iterable[float | int | None]) -> float | None:
    present = [float(v) for v in values if v is not None]
    return math.fsum(present) / len(present) if present else None


def _sum(values: Iterable[float | int | None]) -> float | int | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return math.fsum(present) if any(isinstance(v, float) for v in present) else sum(present)  # type: ignore[arg-type]


def _estimate(estimate: stats.Estimate) -> dict[str, Any]:
    return {
        "value": estimate.value,
        "low": estimate.low,
        "high": estimate.high,
        "n": estimate.n,
        "note": estimate.note,
    }


def _rng(seed: int, *parts: str) -> random.Random:
    # str seeds are hashed with SHA-512 by random.seed: the stream does not depend on PYTHONHASHSEED
    return random.Random("/".join([str(seed), *parts]))


def _instance_means(trials: list[Trial]) -> dict[int, float]:
    rewards: dict[int, list[float]] = defaultdict(list)
    for trial in trials:
        if trial.reward is not None:
            rewards[trial.instance].append(trial.reward)
    return {instance: math.fsum(values) / len(values) for instance, values in sorted(rewards.items())}


def score_rows(groups: dict[GroupKey, list[Trial]], leaks: dict[str, bool | None]) -> list[dict[str, Any]]:
    rows = []
    for (label, team), trials in groups.items():
        for condition in CONDITIONS:
            members = [t for t in trials if t.condition == condition]
            if not members:
                continue
            means = _instance_means(members)
            clean = _instance_means([t for t in members if leaks.get(t.path) is not True])
            scored = [t for t in members if t.reward is not None]
            unscored = Counter(t.unscored for t in members if t.reward is None)
            rows.append(
                {
                    "label": label,
                    "team": team,
                    "condition": condition,
                    "instances": len(means),
                    "trials": sum(1 for t in members if t.reward is not None),
                    "unscored": sum(unscored.values()),
                    "unscored_reasons": dict(sorted((str(k), v) for k, v in unscored.items())),
                    "with_exception": sum(1 for t in members if t.exceptions),
                    "mean": _estimate(stats.t_interval(list(means.values()), bounds=(0.0, 1.0))),
                    "example_leak_trials": sum(1 for t in scored if leaks.get(t.path) is True),
                    "example_leak_unknown": sum(1 for t in scored if leaks.get(t.path) is None),
                    "mean_without_example_leaks": _estimate(
                        stats.t_interval(list(clean.values()), bounds=(0.0, 1.0))
                    ),
                }
            )
    return rows


def decomposition_rows(
    groups: dict[GroupKey, list[Trial]],
    seed: int,
    resamples: int,
    draws: int,
    leaks: dict[str, bool | None],
    keep_leaks: bool,
) -> list[dict[str, Any]]:
    """Paired decomposition. Trials flagged example_leak are left out unless ``keep_leaks``."""
    rows = []
    for (label, team), every in groups.items():
        conditions = ("solo", "silent", "talk")
        members = {c: [t for t in every if t.condition == c] for c in conditions}
        flagged = sum(leaks.get(t.path) is True for group in members.values() for t in group)
        means = {
            c: _instance_means(group if keep_leaks else [t for t in group if leaks.get(t.path) is not True])
            for c, group in members.items()
        }
        if not any(means.values()):
            continue
        paired = sorted(set(means["solo"]) & set(means["silent"]) & set(means["talk"]))
        level_counts = (
            {c: len({t.instance for t in group if t.reward is not None}) for c, group in members.items()}
            if flagged and not keep_leaks
            else {c: len(means[c]) for c in conditions}
        )
        row: dict[str, Any] = {
            "label": label,
            "team": team,
            "scored_instances": {c: len(means[c]) for c in ("solo", "silent", "talk")},
            "paired_instances": len(paired),
            "different_level_sets": {c: n for c, n in level_counts.items() if n != len(paired)},
            "example_leak_trials": flagged,
            "example_leak_rule": "kept" if keep_leaks else "left out",
        }
        if not paired:
            row["note"] = "no instance has scored solo, silent and talk trials"
            rows.append(row)
            continue
        solo = [means["solo"][i] for i in paired]
        silent = [means["silent"][i] for i in paired]
        talk = [means["talk"][i] for i in paired]
        gap = [s - f for s, f in zip(solo, silent, strict=True)]
        gain = [t - f for t, f in zip(talk, silent, strict=True)]
        test = stats.sign_flip_test(gain, _rng(seed, label, team, "sign-flip"), draws=draws)
        recovery = stats.bootstrap_recovery(
            gain, gap, _rng(seed, label, team, "recovery"), resamples=resamples
        )
        row.update(
            {
                "ceiling_solo": _estimate(stats.t_interval(solo, bounds=(0.0, 1.0))),
                "floor_silent": _estimate(stats.t_interval(silent, bounds=(0.0, 1.0))),
                "team_talk": _estimate(stats.t_interval(talk, bounds=(0.0, 1.0))),
                "gap_solo_minus_silent": _estimate(stats.t_interval(gap, bounds=(-1.0, 1.0))),
                "gain_talk_minus_silent": _estimate(stats.t_interval(gain, bounds=(-1.0, 1.0))),
                "gain_test": asdict(test),
                "recovery": asdict(recovery),
            }
        )
        rows.append(row)
    return rows


LIAR_CONDITIONS = ("talk", "talk-liar", "talk-warned", "talk-liar-warned")
LIAR_OUTCOMES = ("correct", "deceived", "corrupted", "unknown")
# (name, minuend condition, subtrahend condition, outcome): each effect is a paired difference of
# per-instance rates, so it is defined only on instances scored in both conditions.
LIAR_EFFECTS = (
    ("lie_effect", "talk", "talk-liar", "correct"),  # accuracy lost to the lie, no warning
    ("lie_effect_warned", "talk-warned", "talk-liar-warned", "correct"),  # same, both warned
    ("warning_benefit", "talk-liar-warned", "talk-liar", "correct"),  # accuracy the warning buys back
    ("warning_cost", "talk", "talk-warned", "correct"),  # accuracy the warning costs with an honest peer
    ("deception_cut", "talk-liar", "talk-liar-warned", "deceived"),  # decoy answers the warning prevents
)


def liar_rows(
    groups: dict[GroupKey, list[Trial]], plays: dict[str, Play], seed: int, draws: int
) -> list[dict[str, Any]]:
    """The scripted-peer 2x2 (peer honest or lying, seats warned or not), per label and team.

    Rates per condition: correct (reward 1), deceived (final answer is the decoy), corrupted (neither,
    including no answer). Effects are paired by instance with t intervals and a sign-flip test.
    """
    rows = []
    for (label, team), every in groups.items():
        trials = [t for t in every if t.condition in LIAR_CONDITIONS and t.reward is not None]
        if not any(t.condition != "talk" for t in trials):
            continue
        per: dict[str, dict[str, dict[int, list[float]]]] = {
            c: {k: defaultdict(list) for k in LIAR_OUTCOMES} for c in LIAR_CONDITIONS
        }
        # final writer x outcome over the lying-peer conditions: who commits decides the result?
        writers: dict[str, Counter[str]] = defaultdict(Counter)
        for trial in trials:
            play = plays.get(trial.path)
            outcome = play.liar if play is not None else None
            if outcome is None or outcome == "unknown":
                # Every scored trial stays in the denominator: a failed trial whose final answer cannot be
                # compared with the decoy is "unknown", not dropped (dropping it would raise "correct").
                outcome = "correct" if trial.reward >= FULL else "unknown"
            for key in LIAR_OUTCOMES:
                per[trial.condition][key][trial.instance].append(1.0 if outcome == key else 0.0)
            if "liar" in trial.condition:
                last = getattr(getattr(play, "churn", None), "last_submitter", None)
                writers[last or "none"][outcome] += 1
        means = {
            c: {k: {i: math.fsum(v) / len(v) for i, v in sorted(by.items())} for k, by in d.items()}
            for c, d in per.items()
        }
        row: dict[str, Any] = {
            "label": label,
            "team": team,
            "conditions": {},
            "effects": {},
            "final_writer": {seat: dict(sorted(c.items())) for seat, c in sorted(writers.items())},
        }
        for c in LIAR_CONDITIONS:
            inst = means[c]["correct"]
            if not inst:
                continue
            row["conditions"][c] = {
                "instances": len(inst),
                "trials": sum(len(v) for v in per[c]["correct"].values()),
                **{
                    k: _estimate(stats.t_interval(list(means[c][k].values()), bounds=(0.0, 1.0)))
                    for k in LIAR_OUTCOMES
                },
            }
        for name, plus, minus, key in LIAR_EFFECTS:
            paired = sorted(set(means[plus][key]) & set(means[minus][key]))
            if not paired:
                continue
            diffs = [means[plus][key][i] - means[minus][key][i] for i in paired]
            test = stats.sign_flip_test(diffs, _rng(seed, label, team, name), draws=draws)
            row["effects"][name] = {
                "minuend": plus,
                "subtrahend": minus,
                "outcome": key,
                "different_level_sets": {
                    c: len(means[c][key]) for c in (plus, minus) if len(means[c][key]) != len(paired)
                },
                **_estimate(stats.t_interval(diffs, bounds=(-1.0, 1.0))),
                "test": asdict(test),
            }
        rows.append(row)
    return rows


def _trial_cost(trial: Trial) -> dict[str, float | int | None]:
    seats = trial.under_test

    def total(field: str) -> float | int | None:
        values = [getattr(s, field) for s in seats]
        return None if not values or any(v is None for v in values) else _sum(values)

    return {
        "input": total("n_input"),
        "cached": total("n_cache"),
        "output": total("n_output"),
        "cost_usd": total("cost_usd"),
    }


def cost_section(groups: dict[GroupKey, list[Trial]]) -> dict[str, Any]:
    rows, ratios = [], []
    for (label, team), trials in groups.items():
        per_condition: dict[str, dict[int, list[dict[str, Any]]]] = {}
        for condition in CONDITIONS:
            members = [t for t in trials if t.condition == condition and t.finished]
            if not members:
                continue
            costs = [_trial_cost(t) for t in members]
            complete_cost = [c for c in costs if c["cost_usd"] is not None]
            complete_tokens = [c for c in costs if c["input"] is not None and c["output"] is not None]
            rows.append(
                {
                    "label": label,
                    "team": team,
                    "condition": condition,
                    "trials": len(members),
                    "cost_complete": len(complete_cost),
                    "tokens_complete": len(complete_tokens),
                    "mean_input_tokens": _mean(c["input"] for c in complete_tokens),
                    "mean_cached_tokens": _mean(c["cached"] for c in complete_tokens),
                    "mean_output_tokens": _mean(c["output"] for c in complete_tokens),
                    "mean_cost_usd": _mean(c["cost_usd"] for c in complete_cost),
                    "total_cost_usd": _sum(c["cost_usd"] for c in complete_cost),
                }
            )
            by_instance: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for trial, cost in zip(members, costs, strict=True):
                by_instance[trial.instance].append(cost)
            per_condition[condition] = by_instance
        for numerator in ("talk", "silent"):
            if numerator not in per_condition or "solo" not in per_condition:
                continue
            ratios.append(
                {
                    "label": label,
                    "team": team,
                    "ratio": f"{numerator}/solo",
                    **_paired_ratio(per_condition[numerator], per_condition["solo"]),
                }
            )
    return {"rows": rows, "ratios": ratios}


def _paired_ratio(
    top: dict[int, list[dict[str, Any]]], bottom: dict[int, list[dict[str, Any]]]
) -> dict[str, Any]:
    """Ratio of mean per-instance cost (and tokens) over instances complete in both conditions."""
    out: dict[str, Any] = {}
    for name, key in (("cost", ("cost_usd",)), ("tokens", ("input", "output"))):

        def instance_mean(costs: list[dict[str, Any]], key: tuple[str, ...] = key) -> float | None:
            totals = [math.fsum(c[k] for k in key) for c in costs if all(c[k] is not None for k in key)]
            return math.fsum(totals) / len(totals) if totals else None

        pairs = []
        for instance in sorted(set(top) & set(bottom)):
            a, b = instance_mean(top[instance]), instance_mean(bottom[instance])
            if a is not None and b is not None:
                pairs.append((a, b))
        denominator = math.fsum(b for _, b in pairs)
        out[f"{name}_ratio"] = (
            math.fsum(a for a, _ in pairs) / denominator if pairs and denominator > 0 else None
        )
        out[f"{name}_paired_instances"] = len(pairs)
    return out


def _play_trial_row(trial: Trial, play: Play) -> dict[str, Any]:
    churn = play.churn
    return {
        "trial": trial.trial_name,
        "path": trial.path,
        "label": trial.label,
        "team": trial.team,
        "condition": trial.condition,
        "instance": trial.instance,
        "reward": trial.reward,
        "unscored": trial.unscored,
        "build_known": trial.build is not None,
        "exceptions": trial.exceptions,
        "world_version": play.world_version,
        "world_version_source": play.world_version_source,
        "messages": play.messages,
        "pairs": len(play.pairs),
        "delivered": play.delivered,
        "delivered_unknown": play.delivered_unknown,
        "seen": play.seen,
        "seen_unknown": play.seen_unknown,
        "first_message_t": play.first_message_t,
        "denied": play.denied,
        "submissions": churn.submissions,
        "distinct_answers": churn.distinct_answers,
        "answer_changes": churn.answer_changes,
        "overwrites": churn.overwrites,
        "invalid_submissions": churn.invalid,
        "ever_correct": churn.ever_correct,
        "clobbers": churn.clobbers,
        "last_submitter": churn.last_submitter,
        "submissions_by_seat": churn.by_seat,
        "final_score": play.final_score,
        "score_json_issue": play.score_problem,
        "acted_on": play.proxy,
        "acted_on_note": play.proxy_note,
        "example_leak": play.example_leak,
        "example_leak_source": play.example_source,
        "liar_outcome": play.liar,
        "unknown_world_requests": play.explored,
        "unknown_world_source": "probe events" if play.probes is not None else "keystrokes",
        "recon_commands": play.recon,
        "message_pairs": [asdict(p) for p in play.pairs],
        "notes": play.notes,
    }


def play_section(groups: dict[GroupKey, list[Trial]], plays: dict[str, Play]) -> dict[str, Any]:
    trial_rows, condition_rows = [], []
    for (label, team), trials in groups.items():
        for condition in CONDITIONS:
            members = [t for t in trials if t.condition == condition and t.path in plays]
            if not members:
                continue
            rows = [_play_trial_row(t, plays[t.path]) for t in members]
            trial_rows += rows

            def count(key: str, value: Any, rows: list[dict[str, Any]] = rows) -> int:
                return sum(1 for r in rows if r[key] == value)

            def known(key: str, rows: list[dict[str, Any]] = rows) -> list[Any]:
                return [r[key] for r in rows if r[key] is not None]

            pairs = sum(r["pairs"] for r in rows)
            delivered_unknown = sum(r["delivered_unknown"] for r in rows)
            seen_unknown = sum(r["seen_unknown"] for r in rows)
            condition_rows.append(
                {
                    "label": label,
                    "team": team,
                    "condition": condition,
                    "trials": len(rows),
                    "world_versions": dict(sorted(Counter(r["world_version"] for r in rows).items())),
                    "messages": _sum(r["messages"] for r in rows),
                    "pairs": pairs,
                    "delivered": None if pairs and delivered_unknown == pairs else _sum(known("delivered")),
                    "delivered_unknown": delivered_unknown,
                    "seen": None if pairs and seen_unknown == pairs else _sum(known("seen")),
                    "seen_unknown": seen_unknown,
                    "score_json_issues": len(known("score_json_issue")),
                    "mean_first_message_t": _mean(r["first_message_t"] for r in rows),
                    "denied": _sum(r["denied"] for r in rows),
                    "mean_submissions": _mean(r["submissions"] for r in rows),
                    "mean_distinct_answers": _mean(r["distinct_answers"] for r in rows),
                    "overwrites": _sum(r["overwrites"] for r in rows),
                    "trials_with_overwrites": sum(1 for v in known("overwrites") if v > 0),
                    "invalid_submissions": _sum(r["invalid_submissions"] for r in rows),
                    "invalid_known_trials": len(known("invalid_submissions")),
                    "clobbered_trials": sum(1 for v in known("clobbers") if v > 0),
                    "clobber_known_trials": len(known("clobbers")),
                    "ever_correct_trials": sum(1 for v in known("ever_correct") if v),
                    "last_submitter": dict(
                        sorted(
                            Counter(
                                str(r["last_submitter"]) for r in rows if r["last_submitter"] is not None
                            ).items()
                        )
                    ),
                    "acted_on": {k: count("acted_on", k) for k in ("yes", "no", "n.a.", "unknown")},
                    "example_leak_trials": count("example_leak", True),
                    "liar_outcomes": (
                        {k: count("liar_outcome", k) for k in ("correct", "deceived", "corrupted", "unknown")}
                        if any(r["liar_outcome"] is not None for r in rows)
                        else None
                    ),
                    "unknown_world_requests": _sum(r["unknown_world_requests"] for r in rows),
                    "trials_exploring": sum(1 for v in known("unknown_world_requests") if v > 0),
                    "recon_commands": _sum(r["recon_commands"] for r in rows),
                    "trials_with_recon": sum(1 for v in known("recon_commands") if v > 0),
                }
            )
    return {"conditions": condition_rows, "trials": trial_rows}


def _tally(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    return {
        "yes": sum(1 for r in rows if r[key] is True),
        "no": sum(1 for r in rows if r[key] is False),
        "unknown": sum(1 for r in rows if r[key] is None),
    }


def found_lost_section(groups: dict[GroupKey, list[Trial]], plays: dict[str, Play]) -> dict[str, Any]:
    """Found, kept and lost answers in talk conditions (``play.found_lost``), per trial and per condition.

    Won = found and final answer right; lost = found and final answer wrong. Data measures count over found
    trials (the solver) and over trials with a teammate overwrite (the overwriter), split by won and lost.
    """
    trial_rows, condition_rows = [], []
    for (label, team), trials in groups.items():
        for condition in CONDITIONS:
            members = [
                t
                for t in trials
                if t.condition == condition and t.path in plays and plays[t.path].found_lost is not None
            ]
            if not members:
                continue
            rows = [
                {
                    "trial": t.trial_name,
                    "path": t.path,
                    "label": label,
                    "team": team,
                    "condition": condition,
                    "instance": t.instance,
                    **asdict(plays[t.path].found_lost),  # type: ignore[arg-type]
                }
                for t in members
            ]
            trial_rows += rows
            found = [r for r in rows if r["found"] is True]
            won = [r for r in found if r["final_right"] is True]
            lost = [r for r in found if r["lost"] is True]
            condition_rows.append(
                {
                    "label": label,
                    "team": team,
                    "condition": condition,
                    "trials": len(rows),
                    "found_known": sum(1 for r in rows if r["found"] is not None),
                    "found": len(found),
                    "final_right": sum(1 for r in rows if r["final_right"] is True),
                    "final_right_unknown": sum(1 for r in rows if r["final_right"] is None),
                    "won": len(won),
                    "lost": len(lost),
                    "teammate_overwrite": {
                        "lost": sum(1 for r in lost if r["teammate_overwrite"]),
                        "won": sum(1 for r in won if r["teammate_overwrite"]),
                    },
                    "solver_had_teammate_data": _tally(found, "solver_had_teammate_data"),
                    "teammate_had_solver_data": {
                        "lost": _tally(
                            [r for r in lost if r["teammate_overwrite"]], "teammate_had_solver_data"
                        ),
                        "won": _tally(
                            [r for r in won if r["teammate_overwrite"]], "teammate_had_solver_data"
                        ),
                    },
                    "solver_announced": {
                        "lost": _tally(lost, "solver_announced"),
                        "won": _tally(won, "solver_announced"),
                    },
                    "later_by_solver": _sum(r["later_by_solver"] for r in found),
                    "later_by_others": _sum(r["later_by_others"] for r in found),
                    "trials_with_later_by_others": sum(1 for r in found if (r["later_by_others"] or 0) > 0),
                }
            )
    return {"conditions": condition_rows, "trials": trial_rows}


def seats_section(groups: dict[GroupKey, list[Trial]], plays: dict[str, Play]) -> dict[str, Any]:
    seat_rows, group_rows = [], []
    for (label, team), trials in groups.items():
        for condition in CONDITIONS:
            members = [t for t in trials if t.condition == condition and t.path in plays]
            by_seat: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for trial in members:
                for seat in plays[trial.path].seats:
                    row = {
                        "trial": trial.trial_name,
                        "label": label,
                        "team": team,
                        "condition": condition,
                        "instance": trial.instance,
                        **asdict(seat),
                    }
                    seat_rows.append(row)
                    by_seat[seat.seat].append(row)
            for seat, rows in sorted(by_seat.items()):
                traced = [r for r in rows if r["steps"] is not None]

                def mean_of(table: str, key: str, traced: list[dict[str, Any]] = traced) -> float | None:
                    return _mean((r[table] or {}).get(key, 0) for r in traced) if traced else None

                logged = [r for r in rows if r["requests"] is not None]
                group_rows.append(
                    {
                        "label": label,
                        "team": team,
                        "condition": condition,
                        "seat": seat,
                        "trials": len(rows),
                        "with_trajectory": len(traced),
                        "mean_steps": _mean(r["steps"] for r in traced),
                        "mean_tool_calls": _mean(r["tool_calls"] for r in traced),
                        "mean_curl": {
                            k: mean_of("curl", k)
                            for k in (
                                "messages_post",
                                "messages_get",
                                "submit",
                                "whoami",
                                "unknown_world",
                                "external",
                                "other",
                            )
                        },
                        "mean_recon": {
                            k: mean_of("recon", k)
                            for k in ("environment", "network", "processes", "filesystem")
                        },
                        "mean_requests": {
                            k: (_mean((r["requests"] or {}).get(k, 0) for r in logged) if logged else None)
                            for k in ("send", "deliver", "submit", "whoami", "denied", "probe")
                        },
                        "exceptions": dict(
                            sorted(Counter(r["exception"] for r in rows if r["exception"]).items())
                        ),
                    }
                )
    return {"groups": group_rows, "trials": seat_rows}


def briefing_check(trials: list[Trial], infos: dict[str, TaskInfo]) -> list[dict[str, Any]]:
    """Per label: how many seat briefings already give a correct answer alone (oracle_answer)."""
    seen: dict[str, dict[tuple[int, str], bool]] = defaultdict(dict)
    for trial in trials:
        info = infos.get(trial.path)
        if info is None or not info.usable or trial.condition not in CONDITIONS[1:]:
            continue
        for seat in sorted(info.briefings):
            key = (trial.instance, seat)
            if key in seen[trial.label]:
                continue
            score, _ = own_briefing_score(info, seat)
            if score is not None:
                seen[trial.label][key] = score >= FULL
    return [
        {"label": label, "seat_briefings": len(found), "sufficient_alone": sum(found.values())}
        for label, found in sorted(seen.items())
    ]


def build(
    paths: list[str],
    *,
    seed: int = 1,
    resamples: int = stats.RESAMPLES,
    draws: int = stats.DRAWS,
    task_roots: list[Path] | None = None,
    keep_leaks: bool = False,
    manifests: list[Path] | None = None,
    hook_dirs: list[Path] | None = None,
    allow_mixed_builds: bool = False,
) -> dict[str, Any]:
    trials, jobs, problems = load(list(paths), manifests=manifests, task_roots=task_roots)
    roots = task_roots or []
    groups: dict[GroupKey, list[Trial]] = defaultdict(list)
    for trial in trials:
        groups[(trial.label, trial.team)].append(trial)
    groups = dict(sorted(groups.items()))
    plays: dict[str, Play] = {}
    infos: dict[str, TaskInfo] = {}
    world_settings: dict[Path, dict[str, Any]] = {}
    for trial in trials:
        if not trial.finished:
            continue
        evidence = load_evidence(trial)
        if not evidence.sources:
            problems.append(Problem(trial.path, "no world evidence (events, messages, score or world state)"))
            continue
        world_settings[trial.directory] = evidence.world_settings
        problems.extend(Problem(trial.path, reason) for reason in evidence.problems)
        trajectories = load_trajectories(trial)
        infos[trial.path] = task_info(trial, evidence, roots, hook_dirs, trajectories)
        if infos[trial.path].detail:
            problems.append(Problem(trial.path, f"{infos[trial.path].problem}: {infos[trial.path].detail}"))
        plays[trial.path] = analyze(trial, evidence, trajectories, infos[trial.path])
    checks = _build_checks(groups, world_settings)
    mixed = [
        f"{check['label']} | {check['team']}: {', '.join(check['differing_fields'])}"
        for check in checks
        if check["differing_fields"]
    ]
    if mixed and not allow_mixed_builds:
        raise MixedBuildsError(
            "refusing to pool trials with different build settings:\n  "
            + "\n  ".join(mixed)
            + "\nAnalyze compatible runs separately or pass --allow-mixed-builds."
        )
    leaks = {path: play.example_leak for path, play in plays.items()}
    return {
        "tool": {"name": "flotilla-analyze", "version": __version__},
        "settings": {
            "seed": seed,
            "resamples": resamples,
            "draws": draws,
            "level": stats.LEVEL,
            "exact_sign_flip_max_nonzero": stats.EXACT_MAX_NONZERO,
            "seen_window_chars": WINDOW,
            "keep_example_leaks": keep_leaks,
            "announce_before_s": ANNOUNCE_BEFORE,
            "recovery_min_gap": stats.RECOVERY_MIN_GAP,
            "allow_mixed_builds": allow_mixed_builds,
        },
        "inputs": list(paths),
        "jobs": _jobs(jobs, trials),
        "build_checks": checks,
        "problems": [asdict(p) for p in sorted(problems, key=lambda p: (p.path, p.reason))],
        "scores": score_rows(groups, leaks),
        "decomposition": decomposition_rows(groups, seed, resamples, draws, leaks, keep_leaks),
        "cost": cost_section(groups),
        "play": play_section(groups, plays),
        "liar_design": liar_rows(groups, plays, seed, draws),
        "found_lost": found_lost_section(groups, plays),
        "seats": seats_section(groups, plays),
        "own_briefing_check": briefing_check(trials, infos),
        "task_directories": dict(
            sorted(Counter("usable" if info.usable else str(info.problem) for info in infos.values()).items())
        ),
        "family_hooks": dict(
            sorted(Counter(info.hooks_source or "none" for info in infos.values() if info.usable).items())
        ),
    }


def _jobs(jobs: list[Job], trials: list[Trial]) -> list[dict[str, Any]]:
    rows = []
    for job in jobs:
        members = [t for t in trials if t.path.startswith(job.path + "/")]
        rows.append(
            {
                "path": job.path,
                "configured_trials": job.configured,
                "trial_results": len(members),
                "finished_trials": sum(1 for t in members if t.finished),
                "scored_trials": sum(1 for t in members if t.reward is not None),
                "trial_dirs_without_result": job.unfinished_dirs,
                "job_finished": job.finished_at is not None,
            }
        )
    return rows
