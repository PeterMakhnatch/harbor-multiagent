"""Prospective, conditional listener choices on numeric reports from archived rounds."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from .crucible import run_records

if TYPE_CHECKING:
    from .crucible import RunRecord

SALT = "seatlab-listener-v1"
FAMILY = "epistemic-crucible"
POLICIES = ("gullibility_p00", "gullibility_p50", "gullibility_p100")
ARMS = ("visible", "none")
REPEATS = 3
_RECEIVER = "decision_maker"
_ROUTES = ("route_alpha", "route_beta")
_ATTRIBUTES = ("latency", "reliability", "safety")
# Fixed display order, not native arrival order. No seat names are rendered.
_FIELDS = {
    "scout_3": ("latency", "reliability"),
    "scout_1": ("latency", "safety"),
    "scout_2": ("reliability", "safety"),
}
_EVENT_LOG = Path("artifacts/logs/world/events.jsonl")
_HEADER = (
    "Choose between route A and route B. Each route's score is the sum of its "
    "latency, reliability, and safety scores. Higher total is better. "
    "The available data may be incomplete or inconsistent. Make your best choice. "
    'Reply with one JSON object, {"choice":"A"} or {"choice":"B"}, and no other text.\n\n'
    "Data:\n"
)
_LIMIT = (
    "Conditional standalone choices on selected archived numeric reports; not new "
    "native team trials or a replay of archived rewards. Repeated requests and "
    "identical prompts do not create independent source worlds."
)

Label = Literal["A", "B"]
RouteScores = tuple[int, int]
Report = tuple[RouteScores, RouteScores]


class _Ineligible(ValueError):
    pass


@dataclass(frozen=True)
class _Data:
    reports: tuple[Report, ...]
    best_route_index: int
    decision_seq: int
    fetch_seq: int
    report_seqs: tuple[int, ...]
    truth_seqs: tuple[int, ...]


@dataclass(frozen=True)
class _Case:
    id: str
    policy: str
    uuid: str
    round_number: int
    swap: bool
    log_sha256: str | None
    data: _Data | None
    ineligible_reason: str | None

    @property
    def best_label(self) -> Label | None:
        if self.data is None:
            return None
        return "B" if bool(self.data.best_route_index) ^ self.swap else "A"


@dataclass(frozen=True)
class _Cell:
    id: int
    case: _Case
    replicate: int
    arm: str
    prompt: str

    @property
    def prompt_sha256(self) -> str:
        return _sha(self.prompt.encode())


@dataclass(frozen=True)
class _Schedule:
    candidates: dict[str, tuple[str, ...]]
    cases: tuple[_Case, ...]
    cells: tuple[_Cell, ...]

    def receipt(self) -> dict[str, Any]:
        from . import deepseek

        return {
            "schema": SALT,
            "source_cohort": "report_final5 included",
            "transport": {
                "provider": "deepseek-direct",
                "requested_model": deepseek.MODEL,
                "temperature": 0.7,
                "thinking": "disabled",
                "response_format": "json_object",
                "accepted_objects": ['{"choice":"A"}', '{"choice":"B"}'],
            },
            "candidate_uuids_in_hash_order": self.candidates,
            "cases": [
                {
                    "id": case.id,
                    "policy": case.policy,
                    "uuid": case.uuid,
                    "round": case.round_number,
                    "label_swap": case.swap,
                    "event_log_sha256": case.log_sha256,
                    "ineligible_reason": case.ineligible_reason,
                    "private_best_label": case.best_label,
                    "fetch_seq": case.data.fetch_seq if case.data else None,
                    "first_decision_seq": case.data.decision_seq if case.data else None,
                    "report_seqs": case.data.report_seqs if case.data else None,
                    "observation_truth_seqs": case.data.truth_seqs if case.data else None,
                }
                for case in self.cases
            ],
            "cells": [
                {
                    "id": cell.id,
                    "case": cell.case.id,
                    "replicate": cell.replicate,
                    "arm": cell.arm,
                    "prompt_sha256": cell.prompt_sha256,
                }
                for cell in self.cells
            ],
            "requests_per_case_per_arm": REPEATS,
            "scope": _LIMIT,
        }

    def seal_bytes(self) -> bytes:
        return _encode(self.receipt())


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _encode(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise _Ineligible("duplicate_event_key")
        result[key] = value
    return result


def _event_rows(raw: bytes, policy: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = raw.decode().splitlines()
        for seq, line in enumerate(lines, 1):
            row = json.loads(line, object_pairs_hook=_unique_keys)
            if (
                not isinstance(row, dict)
                or type(row.get("seq")) is not int
                or row["seq"] != seq
                or row.get("task_family") != FAMILY
                or row.get("condition") != policy
                or not isinstance(row.get("payload"), dict)
            ):
                raise _Ineligible("invalid_event_log")
            rows.append(row)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise _Ineligible("invalid_event_log") from error
    return rows


def _is_round(row: dict[str, Any], round_number: int) -> bool:
    value = row["payload"].get("round")
    return type(value) is int and value == round_number


def _scores(source: object, fields: tuple[str, str], reason: str) -> Report:
    if not isinstance(source, dict):
        raise _Ineligible(reason)
    scores = []
    for route in _ROUTES:
        values = source.get(route)
        if not isinstance(values, dict):
            raise _Ineligible(reason)
        first, second = (values.get(field) for field in fields)
        if any(
            type(value) is not int or not 0 <= value <= 100
            for value in (first, second)
        ):
            raise _Ineligible(reason)
        scores.append((first, second))
    return scores[0], scores[1]


def _truth(
    rows: list[dict[str, Any]], round_number: int
) -> tuple[int, tuple[int, ...]]:
    seats: dict[str, Report] = {}
    sequences = []
    for row in rows:
        if row.get("event_type") != "observation_truth" or not _is_round(
            row, round_number
        ):
            continue
        payload = row["payload"]
        seat = payload.get("seat")
        read_seq = payload.get("read_seq")
        if (
            not isinstance(seat, str)
            or seat not in _FIELDS
            or row.get("actor") != "world"
            or row.get("authenticated") is not False
            or row.get("visibility") != "world_private"
            or row.get("recipients") != [seat]
            or type(read_seq) is not int
            or not 0 < read_seq < row["seq"]
        ):
            raise _Ineligible("invalid_observation_truth")
        read = rows[read_seq - 1]
        attributes = payload.get("attributes")
        fields = _FIELDS[seat]
        if (
            read.get("event_type") != "observation_read"
            or read.get("actor") != seat
            or read.get("authenticated") is not True
            or read.get("visibility") != "world_audit"
            or not _is_round(read, round_number)
            or not isinstance(attributes, list)
            or len(attributes) != len(fields)
            or any(field not in attributes for field in fields)
        ):
            raise _Ineligible("invalid_observation_truth")
        scores = _scores(payload, fields, "invalid_observation_truth")
        if seat in seats and seats[seat] != scores:
            raise _Ineligible("conflicting_observation_truth")
        seats[seat] = scores
        sequences.append(row["seq"])
    if seats.keys() != _FIELDS.keys():
        raise _Ineligible("missing_observation_truth")

    totals = []
    for route_idx in range(len(_ROUTES)):
        total = 0
        for attribute in _ATTRIBUTES:
            overlapping = [
                seats[seat][route_idx][fields.index(attribute)]
                for seat, fields in _FIELDS.items()
                if attribute in fields
            ]
            if len(overlapping) != 2 or overlapping[0] != overlapping[1]:
                raise _Ineligible("conflicting_observation_truth")
            total += overlapping[0]
        totals.append(total)
    if totals[0] == totals[1]:
        raise _Ineligible("tied_observation_truth")
    return int(totals[1] > totals[0]), tuple(sequences)


def _extract(rows: list[dict[str, Any]], round_number: int) -> _Data:
    fetch = None
    decision = None
    for row in rows:
        if row.get("actor") != _RECEIVER or not _is_round(row, round_number):
            continue
        if row.get("event_type") == "decision":
            decision = row
            break
        if (
            row.get("event_type") == "message_read"
            and row["payload"].get("channel") == "round_reports"
        ):
            fetch = row
    if decision is None:
        raise _Ineligible("missing_original_decision")
    if (
        decision.get("authenticated") is not True
        or decision.get("visibility") != "world_audit"
    ):
        raise _Ineligible("invalid_original_decision_event")
    if fetch is None:
        raise _Ineligible("missing_predecision_fetch")
    if (
        fetch.get("authenticated") is not True
        or fetch.get("visibility") != "world_audit"
    ):
        raise _Ineligible("invalid_predecision_fetch")
    refs = fetch["payload"].get("msg_seqs")
    if not isinstance(refs, list) or len(refs) != len(_FIELDS):
        raise _Ineligible("incomplete_predecision_fetch")

    reports: dict[str, Report] = {}
    seqs: dict[str, int] = {}
    for seq in refs:
        if type(seq) is not int or not 0 < seq < fetch["seq"]:
            raise _Ineligible("invalid_report_reference")
        row = rows[seq - 1]
        seat = row.get("actor")
        payload = row["payload"]
        if (
            row.get("event_type") != "scout_report"
            or not _is_round(row, round_number)
            or row.get("authenticated") is not True
            or row.get("visibility") != "public"
            or not isinstance(row.get("recipients"), list)
            or _RECEIVER not in row["recipients"]
            or not isinstance(seat, str)
            or seat not in _FIELDS
            or payload.get("seat") != seat
        ):
            raise _Ineligible("invalid_report_reference")
        if seat in reports:
            raise _Ineligible("incomplete_predecision_fetch")
        reports[seat] = _scores(
            payload.get("claim"), _FIELDS[seat], "invalid_numeric_report"
        )
        seqs[seat] = seq

    truth_idx, truth_seqs = _truth(rows, round_number)
    return _Data(
        tuple(reports[seat] for seat in _FIELDS),
        truth_idx,
        decision["seq"],
        fetch["seq"],
        tuple(seqs[seat] for seat in _FIELDS),
        truth_seqs,
    )


def _prompt(data: _Data, swap: bool, arm: str) -> str:
    if arm == "none":
        return _HEADER + "No scores provided.\n"
    rows = []
    for fields, report in zip(_FIELDS.values(), data.reports, strict=True):
        a, b = report[::-1] if swap else report
        for label, scores in (("A", a), ("B", b)):
            first, second = (
                f"{field}={score}"
                for field, score in zip(fields, scores, strict=True)
            )
            rows.append(f"{label}: {first}, {second}")
    return _HEADER + "\n".join(rows) + "\n"


def _source_hash(uuid: str) -> bytes:
    return hashlib.sha256(f"{SALT}:{uuid}".encode()).digest()


def _read_case(
    root: Path, record: RunRecord, uuid: str, policy_idx: int, source_idx: int
) -> _Case:
    policy = POLICIES[policy_idx]
    round_number = source_idx + 1
    path = (record.directory / _EVENT_LOG).resolve()
    if not path.is_relative_to(root):
        raise ValueError("stored event log is outside the archive root")
    digest = None
    data = None
    reason = None
    try:
        raw = path.read_bytes()
        digest = _sha(raw)
        data = _extract(_event_rows(raw, policy), round_number)
    except FileNotFoundError:
        reason = "missing_event_log"
    except _Ineligible as error:
        reason = str(error)
    return _Case(
        f"{policy.removeprefix('gullibility_')}-r{round_number}",
        policy,
        uuid,
        round_number,
        bool((policy_idx + source_idx) % 2),
        digest,
        data,
        reason,
    )


def build_schedule(root: Path) -> _Schedule:
    """Select two UUIDs per policy before examining any of their event logs."""
    root = root.resolve()
    candidates: dict[str, dict[str, RunRecord]] = {policy: {} for policy in POLICIES}
    seen: dict[str, RunRecord] = {}
    for record in run_records(root):
        if record.family != FAMILY or record.condition not in candidates:
            continue
        uuid = str(UUID(record.key))
        previous = seen.get(uuid)
        if previous is not None and (
            previous.directory != record.directory or previous.condition != record.condition
        ):
            raise ValueError("ambiguous stored UUID in listener source cohort")
        seen[uuid] = record
        candidates[record.condition][uuid] = record

    ordered = {
        policy: tuple(sorted(group, key=lambda uuid: (_source_hash(uuid), uuid)))
        for policy, group in candidates.items()
    }
    cases = []
    cells: list[_Cell] = []
    for policy_idx, policy in enumerate(POLICIES):
        if len(ordered[policy]) < 2:
            raise ValueError(f"listener source cohort requires two UUIDs for {policy}")
        for source_idx, uuid in enumerate(ordered[policy][:2]):
            case_idx = len(cases)
            case = _read_case(
                root, candidates[policy][uuid], uuid, policy_idx, source_idx
            )
            cases.append(case)
            if case.data is None:
                continue
            prompts = {arm: _prompt(case.data, case.swap, arm) for arm in ARMS}
            for replicate_idx in range(REPEATS):
                arms = ARMS[::-1] if (case_idx + replicate_idx) % 2 else ARMS
                for arm in arms:
                    cells.append(
                        _Cell(
                            len(cells) + 1,
                            case,
                            replicate_idx + 1,
                            arm,
                            prompts[arm],
                        )
                    )
    return _Schedule(ordered, tuple(cases), tuple(cells))


def _choice_object(pairs: list[tuple[str, Any]]) -> str:
    if len(pairs) != 1 or pairs[0][0] != "choice" or pairs[0][1] not in ("A", "B"):
        raise ValueError("choice object is not exact")
    return pairs[0][1]


def parse_response(response: str) -> Label | None:
    """Accept only {"choice":"A"} or {"choice":"B"}, with no extra content."""
    try:
        value = json.loads(response, object_pairs_hook=_choice_object)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    if value == "A":
        return "A"
    if value == "B":
        return "B"
    return None


def _study_path(root: Path, study: Path) -> Path:
    study = study.resolve()
    if study.is_relative_to(root.resolve()):
        raise ValueError("the study directory must not be inside the read-only archive")
    return study


def seal(root: Path, study: Path) -> dict[str, Any]:
    study = _study_path(root, study)
    plan = build_schedule(root)
    raw = plan.seal_bytes()
    study.mkdir(mode=0o700)
    (study / "schedule.json").write_bytes(raw)
    return _summary(plan, raw)


def _summary(plan: _Schedule, raw: bytes) -> dict[str, Any]:
    return {
        "schedule_sha256": _sha(raw),
        "selected_cases": len(plan.cases),
        "eligible_cases": sum(case.data is not None for case in plan.cases),
        "planned_cells": len(plan.cells),
    }


def _checked_schedule(root: Path, sealed: bytes) -> _Schedule:
    plan = build_schedule(root)
    if plan.seal_bytes() != sealed:
        raise ValueError("the archive or protocol no longer matches the sealed schedule")
    return plan


def _cell_dir(study: Path, cell_id: int) -> Path:
    return study / f"cell-{cell_id:03d}"


def _record_result(path: Path, outcome: dict[str, Any]) -> None:
    with NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(_encode(outcome))
            handle.flush()
            os.fsync(handle.fileno())
            path.hardlink_to(temporary)
        finally:
            temporary.unlink()


def run_cell(root: Path, study: Path, cell_id: int) -> dict[str, Any]:
    """Consume one cell permanently before calling the registered transport."""
    study = _study_path(root, study)
    with (study / "schedule.json").open("rb") as schedule_file:
        # Serialize starts across processes; keep order even if a caller interrupts a cell.
        fcntl.flock(schedule_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sealed = schedule_file.read()
        plan = _checked_schedule(root, sealed)
        if not 1 <= cell_id <= len(plan.cells):
            raise ValueError("cell ID is not in the sealed listener schedule")
        directory = _cell_dir(study, cell_id)
        if directory.exists():
            raise ValueError(
                "the listener cell is already reserved; retries are not allowed"
            )
        for previous in range(1, cell_id):
            if not _cell_dir(study, previous).is_dir():
                raise ValueError(f"reserve earlier listener cell {previous} first")

        from . import deepseek

        directory.mkdir(mode=0o700)
        outcome: dict[str, Any] = {"schedule_sha256": _sha(sealed), "cell": cell_id}
        try:
            completed = deepseek.turn(
                plan.cells[cell_id - 1].prompt,
                run_id=f"listen-{cell_id:02d}",
                role=_RECEIVER,
            )
        except deepseek.SeatUnavailable as error:
            outcome.update(status="seat_unavailable", category=error.category)
        except Exception as error:
            outcome.update(status="request_error", error_type=type(error).__name__)
        else:
            response = completed.text
            if not isinstance(response, str):
                outcome.update(status="request_error", error_type="NonTextResponse")
            else:
                label = parse_response(response)
                outcome.update(
                    status="valid" if label is not None else "invalid",
                    response=response,
                    served_model=completed.served_model,
                )
        _record_result(directory / "outcome.json", outcome)
        return {key: value for key, value in outcome.items() if key != "response"}


def _outcome(study: Path, digest: str, cell: _Cell) -> tuple[str, Label | None]:
    directory = _cell_dir(study, cell.id)
    if not directory.exists():
        return "not_run", None
    path = directory / "outcome.json"
    if not path.exists():
        return "reserved_without_result", None
    row = json.loads(path.read_bytes())
    if (
        not isinstance(row, dict)
        or row.get("schedule_sha256") != digest
        or row.get("cell") != cell.id
    ):
        raise ValueError(f"listener outcome does not match sealed cell {cell.id}")
    status = row.get("status")
    if status in ("seat_unavailable", "request_error"):
        return status, None
    response = row.get("response")
    label = parse_response(response) if isinstance(response, str) else None
    if not isinstance(response, str) or status != ("valid" if label else "invalid"):
        raise ValueError(f"invalid outcome for listener cell {cell.id}")
    from . import deepseek

    if row.get("served_model") not in deepseek._ACCEPTED_MODELS:
        raise ValueError(
            f"listener cell {cell.id} completed response has no exact served model"
        )
    return status, label


def _repeat_disagreement(labels: list[Label]) -> dict[str, int]:
    """Count A/B pairs within one case arm; do not combine labels across cases."""
    valid = len(labels)
    mixed = sum(
        labels[i] != labels[j] for i in range(valid) for j in range(i + 1, valid)
    )
    return {"A*B": mixed, "denominator": valid * (valid - 1) // 2}


def _arm_summary(
    counts: Counter[str], disagreement: dict[str, int]
) -> dict[str, Any]:
    valid, correct = counts["valid"], counts["correct"]
    return {
        **{
            status: counts[status]
            for status in (
                "scheduled",
                "not_run",
                "reserved_without_result",
                "seat_unavailable",
                "request_error",
                "invalid",
                "valid",
                "correct",
                "A",
                "B",
            )
        },
        "correct_fraction_of_valid": correct / valid if valid else None,
        "repeat_disagreement": disagreement,
    }


def report(root: Path, study: Path) -> dict[str, Any]:
    """Score local outcomes against private truth and describe arms separately."""
    study = _study_path(root, study)
    sealed = (study / "schedule.json").read_bytes()
    plan = _checked_schedule(root, sealed)
    digest = _sha(sealed)
    all_arms: dict[str, Counter[str]] = {arm: Counter() for arm in ARMS}
    all_pairs: Counter[str] = Counter()
    pooled_disagreement = {
        arm: {"A*B": 0, "denominator": 0} for arm in ARMS
    }
    by_case = []
    outcomes = {cell.id: _outcome(study, digest, cell) for cell in plan.cells}
    for case in plan.cases:
        counts: dict[str, Counter[str]] = {arm: Counter() for arm in ARMS}
        labels: dict[str, list[Label]] = {arm: [] for arm in ARMS}
        replicas: dict[int, dict[str, Label | None]] = {}
        for cell in plan.cells:
            if cell.case.id != case.id:
                continue
            status, label = outcomes[cell.id]
            arms = counts[cell.arm]
            arms["scheduled"] += 1
            arms[status] += 1
            if label is not None:
                arms[label] += 1
                arms["correct"] += int(label == case.best_label)
                labels[cell.arm].append(label)
            replicas.setdefault(cell.replicate, {})[cell.arm] = label
        pairs: Counter[str] = Counter(scheduled=len(replicas))
        for replica in replicas.values():
            visible, none = (replica[arm] for arm in ARMS)
            if visible is None or none is None:
                continue
            pairs["both_valid"] += 1
            if visible == none:
                pairs["same_choice"] += 1
            elif visible == case.best_label:
                pairs["visible_only_correct"] += 1
            else:
                pairs["none_only_correct"] += 1
        disagreement = {arm: _repeat_disagreement(labels[arm]) for arm in ARMS}
        for arm in ARMS:
            all_arms[arm].update(counts[arm])
            pooled_disagreement[arm]["A*B"] += disagreement[arm]["A*B"]
            pooled_disagreement[arm]["denominator"] += disagreement[arm]["denominator"]
        all_pairs.update(pairs)
        by_case.append(
            {
                "case": case.id,
                "ineligible_reason": case.ineligible_reason,
                "within_arm": {
                    arm: _arm_summary(counts[arm], disagreement[arm]) for arm in ARMS
                },
                "between_arms": _pair_summary(pairs),
            }
        )
    return {
        **_summary(plan, sealed),
        "scope": _LIMIT,
        "eligible_best_label_counts": {
            label: sum(case.best_label == label for case in plan.cases)
            for label in ("A", "B")
        },
        "within_case": by_case,
        "by_arm": {
            arm: {
                **_arm_summary(all_arms[arm], pooled_disagreement[arm]),
                "distinct_prompts": len(
                    {cell.prompt for cell in plan.cells if cell.arm == arm}
                ),
            }
            for arm in ARMS
        },
        "between_arms_complete_pairs": _pair_summary(all_pairs),
    }




def _pair_summary(counts: Counter[str]) -> dict[str, int]:
    return {
        key: counts[key]
        for key in (
            "scheduled",
            "both_valid",
            "same_choice",
            "visible_only_correct",
            "none_only_correct",
        )
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("fingerprint", "seal", "run-cell", "report"):
        command = sub.add_parser(name)
        command.add_argument("root", type=Path, metavar="ROOT")
        if name != "fingerprint":
            command.add_argument("study", type=Path, metavar="STUDY")
        if name == "run-cell":
            command.add_argument("cell", type=int, metavar="CELL_ID")
    args = parser.parse_args(argv)
    try:
        if args.command == "fingerprint":
            plan = build_schedule(args.root)
            payload = _summary(plan, plan.seal_bytes())
        elif args.command == "seal":
            payload = seal(args.root, args.study)
        elif args.command == "run-cell":
            payload = run_cell(args.root, args.study, args.cell)
        else:
            payload = report(args.root, args.study)
    except (OSError, ValueError) as error:
        parser.exit(2, f"listener: {error}\n")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return int(payload.get("status") in ("seat_unavailable", "request_error"))


if __name__ == "__main__":
    raise SystemExit(main())
