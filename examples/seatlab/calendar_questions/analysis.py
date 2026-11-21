from collections import Counter
from dataclasses import dataclass, field
from statistics import fmean

from ..rolepack import evidence, protocol
from . import source, stages

type _Document = protocol.Document
type _Rows = dict[tuple[str, int], _Document]

_DRAWS = (0, 1)
_CONDITIONS = ("visible", "withheld")
_QUESTION_COUNTS = (
    "completed_sources",
    "sources_with_locally_valid_query",
    "sources_with_joint_slot_queried",
    "unique_queries",
)
_ANSWER_COUNTS = (
    "answered_sources",
    "eligible_query_occurrences",
    "eligible_yes_occurrences",
    "eligible_no_occurrences",
    "answered_query_occurrences",
    "matched_query_occurrences",
    "matched_yes",
    "matched_no",
    "false_positive",
    "false_negative",
)
_FINAL_COUNTS = (
    "eligible_source_draws",
    "attempted_source_draws",
    "valid_source_draws",
    "receiver_feasible_source_draws",
    "optimal_source_draws",
    "matched_optimal_source_draws",
    "errors_after_inaccurate_sender_status",
    "errors_after_joint_slot_not_queried",
    "errors_after_either_prefix_issue",
)
_PAIR_COUNTS = (
    "valid_matched_source_draws",
    "sources_with_valid_matched_draws",
    "correct_to_incorrect",
    "incorrect_to_correct",
    "both_correct",
    "both_incorrect",
    "decision_changes",
)


@dataclass(slots=True)
class _Tally:
    registered_sources: int = 0
    question: Counter[str] = field(default_factory=Counter)
    answer: Counter[str] = field(default_factory=Counter)
    final: dict[str, Counter[str]] = field(
        default_factory=lambda: {condition: Counter() for condition in _CONDITIONS}
    )
    paired: Counter[str] = field(default_factory=Counter)
    matched_source_differences: list[float] = field(default_factory=list)

    def merge_counts(self, other: "_Tally") -> None:
        self.registered_sources += other.registered_sources
        self.question.update(other.question)
        self.answer.update(other.answer)
        self.paired.update(other.paired)
        for condition in _CONDITIONS:
            self.final[condition].update(other.final[condition])


def _verified_rows(
    pack: _Document | None, report: _Document | None, *, stage: str
) -> _Rows:
    if report is None:
        return {}
    if pack is None:
        raise ValueError(f"{stage} report has no eligible stage pack")
    checked = evidence.verify(pack, report)
    return {
        (row["record_id"], row["draw"]): row
        for row in checked["rows"]
        if row["arm"] == "original"
    }


def _outcome(row: _Document | None) -> _Document | None:
    if row is None or row["state"] != "complete":
        return None
    return row["outcome"]


def _tally_prefix(
    tally: _Tally,
    record: _Document,
    question: _Document | None,
    answer: _Document | None,
) -> tuple[bool, bool] | None:
    if question is None:
        return None
    queries: list[int] = question["values"]
    true_slot_queried = record["optimum"] in queries
    tally.question["completed_sources"] += 1
    tally.question["sources_with_locally_valid_query"] += any(
        slot in record["receiver_starts"] for slot in queries
    )
    tally.question["sources_with_joint_slot_queried"] += true_slot_queried
    tally.question["unique_queries"] += len(set(queries))
    tally.answer["eligible_query_occurrences"] += len(queries)
    expected = [slot in record["sender_starts"] for slot in queries]
    tally.answer["eligible_yes_occurrences"] += sum(expected)
    tally.answer["eligible_no_occurrences"] += len(expected) - sum(expected)
    if answer is None:
        return None

    tally.answer["answered_sources"] += 1
    inaccurate = False
    for available, status, graded in zip(
        expected, answer["values"], answer["correct"], strict=True
    ):
        match = available == (status == "yes")
        if graded != match:
            raise ValueError("answer grade differs from sender availability")
        inaccurate |= not match
        tally.answer["answered_query_occurrences"] += 1
        tally.answer["matched_query_occurrences"] += match
        if match:
            tally.answer["matched_yes" if available else "matched_no"] += 1
        else:
            tally.answer["false_negative" if available else "false_positive"] += 1
    return inaccurate, not true_slot_queried


def _tally_final(
    counts: Counter[str],
    record: _Document,
    row: _Document | None,
    prefix_issues: tuple[bool, bool] | None,
    *,
    eligible: bool,
) -> int | None:
    counts["eligible_source_draws"] += eligible
    if row is not None and row["state"] in ("complete", "incomplete"):
        counts["attempted_source_draws"] += 1
    outcome = _outcome(row)
    if outcome is None:
        return None
    if prefix_issues is None or not eligible:
        raise ValueError("completed final draw has no completed prefix")
    (slot,) = outcome["values"]
    optimal = slot == record["optimum"]
    if outcome["correct"] != [optimal]:
        raise ValueError("final grade differs from the native joint slot")
    counts["valid_source_draws"] += 1
    counts["receiver_feasible_source_draws"] += slot in record["receiver_starts"]
    counts["optimal_source_draws"] += optimal
    if not optimal:
        inaccurate, unqueried = prefix_issues
        counts["errors_after_inaccurate_sender_status"] += inaccurate
        counts["errors_after_joint_slot_not_queried"] += unqueried
        counts["errors_after_either_prefix_issue"] += inaccurate or unqueried
    return slot


def _add_source(
    tally: _Tally,
    record: _Document,
    questions: _Rows,
    answers: _Rows,
    finals: _Rows,
    final_ids: set[str],
) -> None:
    record_id = record["id"]
    prefix_issues = _tally_prefix(
        tally,
        record,
        _outcome(questions.get((record_id, 0))),
        _outcome(answers.get((record_id, 0))),
    )
    tally.registered_sources += 1
    differences: list[int] = []
    optimum = record["optimum"]
    for draw in _DRAWS:
        slots: list[int | None] = []
        for condition in _CONDITIONS:
            final_id = f"{record_id}--{condition}"
            slots.append(
                _tally_final(
                    tally.final[condition],
                    record,
                    finals.get((final_id, draw)),
                    prefix_issues,
                    eligible=final_id in final_ids,
                )
            )
        visible, withheld = slots
        if visible is None or withheld is None:
            continue
        old, new = withheld == optimum, visible == optimum
        tally.final["withheld"]["matched_optimal_source_draws"] += old
        tally.final["visible"]["matched_optimal_source_draws"] += new
        tally.paired["valid_matched_source_draws"] += 1
        tally.paired["correct_to_incorrect"] += old and not new
        tally.paired["incorrect_to_correct"] += new and not old
        tally.paired["both_correct"] += old and new
        tally.paired["both_incorrect"] += not old and not new
        tally.paired["decision_changes"] += visible != withheld
        differences.append(int(new) - int(old))
    if differences:
        tally.paired["sources_with_valid_matched_draws"] += 1
        tally.matched_source_differences.append(fmean(differences))


def _count_report(tally: _Tally) -> _Document:
    registered_draws = tally.registered_sources * len(_DRAWS)
    return {
        "registered_sources": tally.registered_sources,
        "question": {key: tally.question[key] for key in _QUESTION_COUNTS},
        "answer": {key: tally.answer[key] for key in _ANSWER_COUNTS},
        "final": {
            condition: {
                "registered_source_draws": registered_draws,
                **{key: tally.final[condition][key] for key in _FINAL_COUNTS},
            }
            for condition in _CONDITIONS
        },
        "paired": {key: tally.paired[key] for key in _PAIR_COUNTS},
    }


def _world_report(tally: _Tally) -> _Document:
    visible, withheld = (tally.final[condition] for condition in _CONDITIONS)
    planned = tally.registered_sources * len(_DRAWS)
    difference = visible["optimal_source_draws"] - withheld["optimal_source_draws"]
    return {
        **_count_report(tally),
        "observed_visible_minus_withheld": (
            fmean(tally.matched_source_differences)
            if tally.matched_source_differences
            else None
        ),
        "visible_minus_withheld_bounds": [
            (difference - (planned - withheld["valid_source_draws"])) / planned,
            (difference + (planned - visible["valid_source_draws"])) / planned,
        ],
    }


def summarize(
    src: _Document,
    question_report: _Document | None,
    answer_report: _Document | None,
    final_report: _Document | None,
) -> _Document:
    src = source.validate(src)
    questions = _verified_rows(stages.question(src), question_report, stage="question")
    answer_pack = (
        stages.answer(src, question_report) if question_report is not None else None
    )
    answers = _verified_rows(answer_pack, answer_report, stage="answer")
    final_pack = (
        stages.final(src, question_report, answer_report)
        if question_report is not None and answer_report is not None
        else None
    )
    finals = _verified_rows(final_pack, final_report, stage="final")
    final_ids = {r["id"] for r in final_pack["records"]} if final_pack else set()
    worlds: dict[str, _Tally] = {}
    for record in src["records"]:
        world = record["world"]
        if world not in worlds:
            worlds[world] = _Tally()
        _add_source(worlds[world], record, questions, answers, finals, final_ids)

    total = _Tally()
    for tally in worlds.values():
        total.merge_counts(tally)
    by_world = {world: _world_report(tally) for world, tally in sorted(worlds.items())}
    observed = [
        report["observed_visible_minus_withheld"]
        for report in by_world.values()
        if report["observed_visible_minus_withheld"] is not None
    ]
    return {
        "schema": "seatlab.calendar-question-summary/v1",
        "source_sha256": protocol.digest(src),
        "report_sha256": {
            stage: protocol.digest(report) if report is not None else None
            for stage, report in (
                ("question", question_report),
                ("answer", answer_report),
                ("final", final_report),
            )
        },
        "measurement": (
            "Conditional continuation, not native Harbor reward. Prefix error counts "
            "describe valid nonoptimal final replies and do not attribute cause. "
            "Bounds cover all registered source draws and are not confidence intervals."
        ),
        "weighting": (
            "Equal draws within source, equal sources within world, equal worlds overall. "
            "Observed differences use only valid matched draws; transitions go from "
            "withheld to visible. Bounds include omitted and unobserved draws."
        ),
        "overall": {
            **_count_report(total),
            "registered_worlds": len(by_world),
            "worlds_with_valid_matched_draws": len(observed),
            "observed_equal_world_visible_minus_withheld": (
                fmean(observed) if observed else None
            ),
            "equal_world_visible_minus_withheld_bounds": [
                fmean(r["visible_minus_withheld_bounds"][side] for r in by_world.values())
                for side in (0, 1)
            ],
        },
        "by_world": by_world,
    }
