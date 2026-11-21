import argparse
import json
import os
import stat
import sys
from collections import Counter
from collections.abc import Sequence
from itertools import islice
from pathlib import Path
from typing import Any

from ..visibility.clarify_run import _directory, _object, _private
from ..visibility.omp_seat import _count, _validate_model
from ..visibility.route_audit import _read, _usage
from .analysis import _ARM_STATUSES, _NOTES, _POLICIES, _ROLES, _STATES
from .journal import _FINGERPRINT, _PUBLIC_FIELDS
from .packet import _identifier, _slug

_MAX_REPORT = 16 * 1024 * 1024
_MAX_BLOCKS = 512
_SEATS = {"writer": "route_review", "successor": "route_decide"}
_USAGE = {
    "prompt_tokens": "input_tokens",
    "completion_tokens": "output_tokens",
    "cached_tokens": "cache_tokens",
}
_RECEIPT_FIELDS = {"schema", "prompt_sha256", "reply_sha256", "served_model", "usage"}
type Identity = tuple[str, str, str]


def _digest(value: object) -> str:
    if not isinstance(value, str) or _FINGERPRINT.fullmatch(value) is None:
        raise ValueError
    return value


def _report(path: Path) -> dict[str, Any]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError
        raw = stream.read(_MAX_REPORT + 1)
    if len(raw) > _MAX_REPORT:
        raise ValueError
    return _object(raw)


def _row_key(row: dict[str, Any], index: int, fingerprint: str) -> tuple[str, str]:
    case, cluster = _slug(row["case"]), _slug(row["cluster"])
    rep = index % 2
    if (
        row["fingerprint"] != fingerprint
        or type(row["rep"]) is not int
        or row["rep"] != rep
        or row["block"] != f"ho-{fingerprint[:12]}-{case}-r{rep}"
        or row["state"] not in _STATES
        or not isinstance(row["turns"], list)
        or len(row["turns"]) > 5
    ):
        raise ValueError
    return case, cluster


def _plan(
    outcome: dict[str, Any], index: int
) -> tuple[str, dict[str, str], list[tuple[str, bool]]]:
    note, arms = outcome["note_status"], outcome["arms"]
    if (
        note not in _NOTES
        or not isinstance(arms, dict)
        or arms.keys() != set(_POLICIES)
    ):
        raise ValueError
    plan = [("note", note == "missing")]
    statuses: dict[str, str] = {}
    draws: dict[str, list[str]] = {}
    order = _POLICIES if (index // 2 + index % 2) % 2 == 0 else _POLICIES[::-1]
    for arm in order:
        item = arms[arm]
        status, lookup = item["status"], item["lookup"]
        if status not in _ARM_STATUSES or (status == "no_note") != (note != "valid"):
            raise ValueError
        statuses[arm] = status
        if note == "valid":
            plan.append((f"{arm}-select", status == "missing_select"))
        if status in ("missing_decide", "invalid_decide", "complete"):
            draws.setdefault(_identifier(lookup), []).append(arm)
        elif lookup is not None or item["decision_stage"] is not None:
            raise ValueError
    for arms_at_draw in draws.values():
        first = arms_at_draw[0]
        stage = "both-decide" if len(arms_at_draw) == 2 else f"{first}-decide"
        status = statuses[first]
        for arm in arms_at_draw:
            actual = arms[arm]["decision_stage"]
            if statuses[arm] != status or (
                actual != stage and not (actual is None and status == "missing_decide")
            ):
                raise ValueError
        plan.append((stage, status == "missing_decide"))
    return note, statuses, plan


def _turns(
    row: dict[str, Any],
    plan: list[tuple[str, bool]],
    expected: Counter[Identity],
    errors: Counter[str],
    seen: set[str],
) -> None:
    turns = row["turns"]
    if len(turns) > len(plan):
        raise ValueError
    halted = False
    for i, (stage, missing) in enumerate(plan):
        if i >= len(turns):
            if not missing:
                raise ValueError
            continue
        turn = turns[i]
        role, run = _ROLES[stage], f"{row['block']}-{stage}"
        if (
            halted
            or not isinstance(turn, dict)
            or turn.keys() != set(_PUBLIC_FIELDS)
            or turn["run"] != run
            or turn["role"] != role
            or run in seen
            or turn["status"] != ("callback_error" if missing else "received")
        ):
            raise ValueError
        seen.add(run)
        prompt = _digest(turn["prompt_sha256"])
        if missing:
            if turn["response_sha256"] is not None or turn["usage"] is not None:
                raise ValueError
            errors[role] += 1
            halted = True
        else:
            _usage(turn["usage"])
            expected[role, prompt, _digest(turn["response_sha256"])] += 1


def _selected(
    rows: list[Any], indices: set[int], fingerprint: str
) -> tuple[list[dict[str, Any]], Counter[Identity], Counter[str]]:
    summaries: list[dict[str, Any]] = []
    expected: Counter[Identity] = Counter()
    errors: Counter[str] = Counter()
    seen: set[str] = set()
    cases: dict[str, tuple[int, str]] = {}
    pairs: dict[int, str] = {}
    for index, row in enumerate(rows):
        state, note, arms = "missing", "unknown", dict.fromkeys(_POLICIES, "unknown")
        if row is not None:
            case, cluster = _row_key(row, index, fingerprint)
            if (
                cases.setdefault(case, (index // 2, cluster)) != (index // 2, cluster)
                or pairs.setdefault(index // 2, case) != case
            ):
                raise ValueError
            state = row["state"]
            if index not in indices and (state != "unclaimed" or row["turns"]):
                raise ValueError
            recorded_note, recorded_arms, plan = _plan(row["outcome"], index)
            if state == "claimed":
                note, arms = recorded_note, recorded_arms
                _turns(row, plan, expected, errors, seen)
            elif row["turns"] or recorded_note != "missing":
                raise ValueError
        if index in indices:
            summaries.append(
                {"index": index, "state": state, "note_status": note, "arms": arms}
            )
    return summaries, expected, errors


def _receipts(
    fd: int, role: str, model: str, expected: Counter[Identity], errors: int
) -> dict[str, int]:
    totals = dict.fromkeys(_USAGE.values(), 0)
    limit = sum(n for key, n in expected.items() if key[0] == role)
    matched = 0
    with os.scandir(fd) as entries:
        for entry in islice(entries, limit + 1):
            if matched == limit or not entry.name.endswith(".json"):
                raise ValueError
            receipt = _read(fd, entry.name)
            if (
                receipt.keys() != _RECEIPT_FIELDS
                or receipt["schema"] != "seatlab.omp-seat-receipt/v1"
                or receipt["served_model"] != model
            ):
                raise ValueError
            key = (
                role,
                _digest(receipt["prompt_sha256"]),
                _digest(receipt["reply_sha256"]),
            )
            if expected[key] <= 0:
                raise ValueError
            usage = receipt["usage"]
            if not isinstance(usage, dict) or usage.keys() != _USAGE.keys() | {
                "total_tokens"
            }:
                raise ValueError
            for source, target in _USAGE.items():
                totals[target] += _count(usage[source])
            if (
                usage["cached_tokens"] > usage["prompt_tokens"]
                or _count(usage["total_tokens"])
                != usage["prompt_tokens"] + usage["completion_tokens"]
            ):
                raise ValueError
            expected[key] -= 1
            matched += 1
    if matched != limit:
        raise ValueError
    return {"matched": matched, "callback_errors": errors, **totals}


def _audit(
    report: Path, writer: Path, successor: Path, model: str, indices: Sequence[int]
) -> dict[str, Any]:
    _validate_model(model)
    if any(
        not path.is_absolute() or ".." in path.parts for path in (writer, successor)
    ):
        raise ValueError
    source = _report(report)
    rows = source["outcomes"]
    if (
        source["schema"] != "seatlab.handoff-report/v1"
        or not isinstance(rows, list)
        or not 0 < len(indices) <= len(rows) <= _MAX_BLOCKS
        or any(type(i) is not int or not 0 <= i < len(rows) for i in indices)
        or len(set(indices)) != len(indices)
    ):
        raise ValueError
    fingerprint = _digest(source["packet_sha256"])
    blocks, expected, errors = _selected(rows, set(indices), fingerprint)
    with _directory(writer) as writer_fd, _directory(successor) as successor_fd:
        _private(writer_fd, mode=0o700)
        _private(successor_fd, mode=0o700)
        if os.path.samestat(os.fstat(writer_fd), os.fstat(successor_fd)):
            raise ValueError
        roles = {
            seat: _receipts(fd, role, model, expected, errors[role])
            for (seat, role), fd in zip(
                _SEATS.items(), (writer_fd, successor_fd), strict=True
            )
        }
    return {
        "schema": "seatlab.handoff-external-audit/v1",
        "packet_sha256": fingerprint,
        "requested_model": model,
        "verified_served_model": model
        if any(r["matched"] for r in roles.values())
        else None,
        "selected": len(indices),
        "not_selected": len(rows) - len(indices),
        "roles": roles,
        "blocks": blocks,
        "complete_deliveries": {
            arm: sum(block["arms"][arm] == "complete" for block in blocks)
            for arm in _POLICIES
        },
    }


class _Arguments(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError


def main(argv: Sequence[str] | None = None) -> int:
    parser = _Arguments(allow_abbrev=False)
    parser.add_argument("report", type=Path)
    parser.add_argument("--writer-receipts", type=Path, required=True)
    parser.add_argument("--successor-receipts", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--indices", type=int, nargs="+", required=True)
    try:
        args = parser.parse_args(argv)
        result = _audit(
            args.report,
            args.writer_receipts,
            args.successor_receipts,
            args.model,
            args.indices,
        )
        print(json.dumps(result, sort_keys=True))
    except (OSError, KeyError, TypeError, ValueError, RecursionError):
        print("Cannot verify handoff receipt evidence.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
