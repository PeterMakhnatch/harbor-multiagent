import argparse
import json
import re
from pathlib import Path
from typing import Any

from ..visibility import calendar_packet
from . import heldout, repeat_summary, team_source, team_views
from .visible_summary import _stock_score

_AUDIT_SCHEMA = "seatlab.native-calendar-visible-repeat-results/v1"
_MAX_AUDIT_BYTES = 1024 * 1024
_CALL_LIMIT = len(team_views.PHASES)
_FLAGS = (
    "attempted",
    "complete",
    "evidence_complete",
    "reward_valid",
    "reward_verified",
)
_ROW_FIELDS = set(_FLAGS) | {
    "error",
    "reward",
    "stock",
    "source",
    "trial_uuid",
    "result_sha256",
}
type _Row = dict[str, Any]
type _Calls = tuple[int, int]


def _call_counts(record: _Row) -> _Calls | None:
    settled, held = record.get("settled_calls"), record.get("held_calls")
    if any(
        type(count) is not int or count < 0
        for count in (settled, held)
        if count is not None
    ):
        raise ValueError("invalid native ledger call count")
    if settled is None or held is None:
        return None
    return settled, held


def _accounting(wave: _Row, rows: dict[str, _Row]) -> dict[str, _Calls]:
    if type(wave.get("unassigned_calls")) is not int or wave["unassigned_calls"] != 0:
        raise ValueError("expected a native repeat ledger without unassigned calls")
    ledger_error = wave.get("ledger_error", "ledger_unavailable")
    if ledger_error not in (None, "ledger_unavailable"):
        raise ValueError("invalid native ledger status")
    totals = _call_counts(wave)
    calls = {
        run: count
        for run, row in rows.items()
        if (count := _call_counts(row)) is not None
    }
    if any(settled + held > _CALL_LIMIT for settled, held in calls.values()):
        raise ValueError("too many calls for a native repeat team")
    if totals is None or len(calls) != len(rows):
        return {}
    settled = sum(settled for settled, _ in calls.values())
    held = sum(held for _, held in calls.values())
    if totals != (settled, held):
        raise ValueError("native repeat ledger totals do not match the cells")
    return calls if ledger_error is None else {}


def _wave_rows(
    wave: Any, repetition: int, enrolled: dict[str, team_views.RepeatJoinCell]
) -> dict[str, _Row]:
    if (
        not isinstance(wave, dict)
        or wave.get("schema") != _AUDIT_SCHEMA
        or wave.get("mode") != "live"
        or type(wave.get("repetition")) is not int
        or wave["repetition"] != repetition
    ):
        raise ValueError(f"expected a live native repeat audit for wave {repetition}")
    cells = wave.get("cells")
    if not isinstance(cells, list) or len(cells) != len(enrolled):
        raise ValueError("native repeat audit must contain every enrolled cell")
    rows: dict[str, _Row] = {}
    for row in cells:
        if not isinstance(row, dict) or not _ROW_FIELDS <= row.keys():
            raise ValueError("invalid native repeat audit cell")
        run = row.get("run")
        if not isinstance(run, str) or run not in enrolled or run in rows:
            raise ValueError("unknown or duplicate native repeat run")
        cell = enrolled[run]
        if (
            type(row.get("repetition")) is not int
            or row["repetition"] != cell.repetition
            or (row.get("world"), row.get("arm"), row.get("control"))
            != (cell.world, cell.arm, cell.control)
        ):
            raise ValueError("native repeat run does not match its enrolled cell")
        rows[run] = row
    return rows


def _bind_source(row: _Row, world: str, pinned: str, tasks: dict[str, str]) -> bool:
    if row["error"] in ("stock_source_binding", "matched_stock_source_mismatch"):
        raise ValueError("native repeat audit reports a stock source mismatch")
    source = row["source"]
    if source is None:
        return False
    if not isinstance(source, dict) or source.get("semantics_sha256") != pinned:
        raise ValueError(
            "native repeat source does not match the pinned stock semantics"
        )
    task = calendar_packet._check_digest(source.get("task_sha256"))
    if tasks.setdefault(world, task) != task:
        raise ValueError("native repeat teams do not share the same stock task")
    return True


def _target_visible(row: _Row) -> bool:
    opened = row.get("target_in_opened")
    unopened = row.get("target_in_initial_unopened")
    final = row.get("joint_slot_in_final_report")
    if (
        any(type(value) is not bool for value in (opened, unopened, final))
        or (opened and unopened)
        or (opened or unopened) != final
    ):
        raise ValueError("native repeat target visibility disagrees with delivery")
    return final


def _charged(row: _Row) -> bool:
    known = row.get("known_usd")
    if known is None:
        return False
    if not isinstance(known, str) or not re.fullmatch(
        r"(?:0|[1-9][0-9]*)\.[0-9]{9}", known
    ):
        raise ValueError("invalid native repeat charge")
    return known != "0.000000000"


def _outcome(
    row: _Row, source_bound: bool, calls: _Calls | None
) -> tuple[str, int | None, bool | None]:
    attempted, complete, evidence_complete, valid, verified = (
        row[flag] for flag in _FLAGS
    )
    error, reward, stock = row["error"], row["reward"], row["stock"]
    if (
        any(type(row[flag]) is not bool for flag in _FLAGS)
        or complete != evidence_complete
        or (error is not None and (not isinstance(error, str) or not error))
        or (reward is not None and (type(reward) is not int or reward not in (0, 1)))
        or (valid and reward is None)
        or not isinstance(stock, dict)
    ):
        raise ValueError("invalid native repeat outcome fields")
    score = _stock_score(stock)
    if reward is not None and any(
        grade is not None and grade != reward for grade in stock.values()
    ):
        raise ValueError("native reward disagrees with an observed stock grade")
    if verified and not (
        valid
        and score is not None
        and attempted
        and source_bound
        and row["trial_uuid"] is not None
        and row["result_sha256"] is not None
    ):
        raise ValueError("verified native reward lacks bound stock evidence")
    if complete:
        if not verified or error is not None:
            raise ValueError("complete native repeat team lacks a verified outcome")
        visible = _target_visible(row)
        if calls == (_CALL_LIMIT, 0):
            return "complete", score, visible
    elif error == "model_invalid" and verified:
        if score != 0:
            raise ValueError("verified model-invalid reward must be zero")
        if (
            calls is not None
            and 1 <= calls[0] <= _CALL_LIMIT
            and calls[1] == 0
            and _charged(row)
        ):
            return "model_invalid", score, None
    return "missing", None, None


def from_audits(first: Any, second: Any) -> dict[str, Any]:
    manifest = heldout._pinned_manifest_rows(
        team_source.JOIN_MANIFEST, team_source.JOIN_MANIFEST_SHA256, heldout._VISIBLE
    )
    teams: list[_Row] = []
    tasks: dict[str, str] = {}
    for repetition, audit in enumerate((first, second), start=1):
        enrolled = {
            cell.run: cell
            for world in team_views.JOIN_WORLDS
            for arm in team_views.JOIN_ARMS
            for cell in (team_views.RepeatJoinCell(world, arm, repetition=repetition),)
        }
        rows = _wave_rows(audit, repetition, enrolled)
        calls = _accounting(audit, rows)
        for run, cell in enrolled.items():
            row = rows[run]
            _, pinned = manifest[cell.index - heldout._VISIBLE.first]
            bound = _bind_source(row, cell.world, pinned, tasks)
            status, reward, visible = _outcome(row, bound, calls.get(run))
            teams.append(
                {
                    "world": cell.world,
                    "arm": cell.arm,
                    "repetition": cell.repetition,
                    "run": run,
                    "source_sha256": pinned,
                    "trial_uuid": row["trial_uuid"],
                    "result_sha256": row["result_sha256"],
                    "status": status,
                    "reward": reward,
                    "target_visible": visible,
                }
            )
    receipt = {"schema": repeat_summary._SCHEMA, "teams": teams}
    repeat_summary._grid(receipt)
    return receipt


def _read_audit(path: Path) -> Any:
    with path.open("rb") as stream:
        data = stream.read(_MAX_AUDIT_BYTES + 1)
    if len(data) > _MAX_AUDIT_BYTES:
        raise ValueError("oversize native repeat audit")
    return calendar_packet._json(data)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Export a public receipt from two live native calendar repeat audits.",
        allow_abbrev=False,
    )
    parser.add_argument("first", type=Path, metavar="WAVE1.json")
    parser.add_argument("second", type=Path, metavar="WAVE2.json")
    args = parser.parse_args(argv)
    try:
        receipt = from_audits(_read_audit(args.first), _read_audit(args.second))
    except OSError:
        parser.error("cannot read a native repeat audit or the pinned stock manifest")
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(receipt, separators=(",", ":"), allow_nan=False))


if __name__ == "__main__":
    main()
