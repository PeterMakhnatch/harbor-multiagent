from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from examples.seatlab.market_handoff import (
    decisions,
    native_panel,
    native_receipt,
    native_repeat,
    native_stage,
)

SCHEMA = "seatlab.market-native-repeat-panel/v1"
SCORE_SCHEMA = "seatlab.market-native-repeat-panel-score/v1"
CELLS, NAMES, ROLES = native_panel.CELLS, native_panel.NAMES, native_panel.ROLES
INVALID = frozenset(
    {
        "receipt_keys",
        "seat_set",
        "schema",
        "kind",
        "trial_id",
        "sha256",
        "duplicate_cell",
        "duplicate_trial_id",
        "duplicate_result",
        "original_trial_id",
        "original_result",
    }
)
UNSAFE = frozenset(
    {"receipt_keys", "seat_set", "sha256", *(f"seats.{role}" for role in ROLES)}
)
REWARD = frozenset(
    {
        "grade",
        "multiple",
        "prefix_fingerprint",
        "reward_valid",
        "stock_grade",
        "archived_pick",
        "target_prefix",
    }
)
ARCHIVE_REWARD = ("target", "payoffs", "grade", "multiple")
ROLE_CHECKS = {"choice": "choices", "prompt": "prompts", "binding": "seat_bindings"}
FAILURES = native_repeat.FAILURES
Record = dict[str, Any]
Archive = dict[tuple[int, str], Record]


class Malformed(ValueError):
    def __init__(self, reason: str, index: int | None = None) -> None:
        super().__init__(reason)
        self.reason, self.index = reason, index


def guarded[T](reason: str, call: Callable[..., T], *args: object) -> T:
    try:
        return call(*args)
    except FAILURES as exc:
        raise Malformed(reason) from exc


def sources(data: Path, bundle: Path) -> tuple[Path, Archive]:
    root = guarded("invalid_pinned_data", native_panel.pinned, data)
    return root, guarded("invalid_released_bundle", native_repeat.archive, root, bundle)


def cell_of(row: Record) -> tuple[int, str] | None:
    world, arm = row["world"], row["arm"]
    if type(world) is int and isinstance(arm, str) and (world, arm) in NAMES:
        return world, arm
    return None


def identity(row: Record) -> dict[str, str | None]:
    hashes, key = row["sha256"], cell_of(row)
    result = hashes.get("result") if isinstance(hashes, dict) else None
    return {
        "cell": None if key is None else NAMES[key],
        "trial_id": decisions.canonical(row["trial_id"]),
        "result": result if native_receipt.hex64(result) else None,
    }


def clashes(receipts: dict[int, Record], rows: Archive) -> dict[int, list[str]]:
    assert len(receipts) <= len(CELLS) and rows.keys() == set(CELLS)
    known = {
        "trial_id": {decisions.canonical(row["trial_id"]) for row in rows.values()},
        "result": {row["sha256"]["result"] for row in rows.values()},
    }
    keys = {index: identity(row) for index, row in receipts.items()}
    found: dict[int, list[str]] = {}
    for field in ("cell", "trial_id", "result"):
        values = [key[field] for key in keys.values()]
        for index, key in keys.items():
            value = key[field]
            if value is not None and values.count(value) > 1:
                found.setdefault(index, []).append(f"duplicate_{field}")
            if value is not None and value in known.get(field, set()):
                found.setdefault(index, []).append(f"original_{field}")
    return found


def collect(data: Path, bundle: Path, roots: list[Path]) -> tuple[int, Record]:
    if len(roots) > len(CELLS):
        raise Malformed("too_many_trials")
    root, rows = sources(data, bundle)
    receipts: dict[int, Record] = {}
    for index, path in enumerate(roots):
        try:
            receipts[index] = native_repeat.fresh_receipt(root, path)
        except FAILURES as exc:
            raise Malformed("invalid_fresh_trial", index) from exc
    found = clashes(receipts, rows)
    if found:
        conflicts = [
            {
                "input": index,
                "trial_name": receipts[index]["trial_name"],
                "reasons": sorted(found[index]),
            }
            for index in sorted(found)
        ]
        return 1, {"schema": SCHEMA, "status": "refused", "conflicts": conflicts}
    ordered = sorted(
        receipts.values(), key=lambda row: CELLS.index((row["world"], row["arm"]))
    )
    return 0, {"schema": SCHEMA, "receipts": ordered}


def read_new(path: Path) -> tuple[bytes, list[object]]:
    raw = native_receipt.read_bytes(path.expanduser().resolve(strict=True))
    value = decisions.decode(raw.decode("utf-8"))
    if raw != native_stage.canonical(value) + b"\n":
        decisions.fail("new bundle: expected one canonical JSON document")
    bundle = native_receipt.record(value, "new bundle")
    if not (
        bundle.keys() == {"schema", "receipts"}
        and bundle["schema"] == SCHEMA
        and isinstance(bundle["receipts"], list)
        and len(bundle["receipts"]) <= len(CELLS)
    ):
        decisions.fail("new bundle: unexpected schema or receipt list")
    return raw, bundle["receipts"]


def shown(row: Record) -> Record | None:
    choices = row["choices"]
    if (
        native_panel.uuid_ok(row["trial_id"])
        and isinstance(choices, dict)
        and choices.keys() == set(ROLES)
        and all(native_receipt.pick_ok(choices[role]) for role in ROLES)
        and native_receipt.number_ok(row["grade"])
    ):
        return native_repeat.side(row)
    return None


def role_checks(
    row: Record, role: str, want: Record, original: Record
) -> dict[str, bool]:
    choices, seats = row["choices"], row["seats"]
    pick = choices.get(role) if isinstance(choices, dict) else None
    seat = seats.get(role) if isinstance(seats, dict) else None
    prompt = seat.get("prompt_sha256") if isinstance(seat, dict) else None
    refs = (want, original)
    return {
        "choice": all(native_panel.same(pick, ref["choices"][role]) for ref in refs),
        "prompt": all(
            native_panel.same(prompt, ref["seats"][role]["prompt_sha256"])
            for ref in refs
        ),
        "binding": all(native_panel.same(seat, ref["seats"][role]) for ref in refs),
    }


def judge(
    key: tuple[int, str],
    entries: list[tuple[Record, list[str]]],
    want: Record,
    original: Record,
) -> Record:
    assert len(entries) <= len(CELLS)
    cell: Record = {
        "trial_name": NAMES[key],
        "world": key[0],
        "arm": key[1],
        "status": "missing",
        "fields": [],
        "original": native_repeat.side(original),
        "fresh": None,
        "roles": {role: dict.fromkeys(ROLE_CHECKS, False) for role in ROLES},
        "reward": False,
        "graded": False,
    }
    if not entries:
        return cell
    if len(entries) > 1:
        reasons = {"duplicate_cell"}.union(*(found for _, found in entries))
        return cell | {"status": "invalid", "fields": sorted(reasons)}
    row, found = entries[0]
    bad = set(native_panel.row_mismatches(row, want)) | set(found)
    if not isinstance(row["seats"], dict) or row["seats"].keys() != set(ROLES):
        bad.add("seat_set")
    agreed = None if bad & UNSAFE else native_repeat.agreement(row, original)
    if agreed is not None:
        bad |= {f"archive.{name}" for name, ok in agreed["fields"].items() if not ok}
        bad |= {
            f"archive.seats.{role}.{name}"
            for role, checks in agreed["seats"].items()
            for name, ok in checks.items()
            if not ok
        }
    status = "invalid" if bad & INVALID else "mismatch" if bad else "reproduced"
    cell |= {"status": status, "fields": sorted(bad), "fresh": shown(row)}
    if status == "invalid":
        return cell
    cell["roles"] = {role: role_checks(row, role, want, original) for role in ROLES}
    cell["reward"] = not bad & REWARD and all(
        native_panel.same(
            native_repeat.at(row, native_repeat.FIELDS[name]),
            native_repeat.at(original, native_repeat.FIELDS[name]),
        )
        for name in ARCHIVE_REWARD
    )
    cell["graded"] = "reward_valid" not in bad
    return cell


def summary(cells: list[Record]) -> Record:
    assert len(cells) == len(CELLS)
    statuses = [cell["status"] for cell in cells]
    seats = [cell["roles"][role] for cell in cells for role in ROLES]
    agreement: Record = {
        label: {"required": len(seats), "agree": sum(seat[check] for seat in seats)}
        for check, label in ROLE_CHECKS.items()
    }
    agreement["rewards"] = {
        "required": len(CELLS),
        "graded": sum(cell["graded"] for cell in cells),
        "agree": sum(cell["reward"] for cell in cells),
    }
    return {
        "cells": {
            "registered": len(CELLS),
            "present": len(CELLS) - statuses.count("missing"),
            **{
                name: statuses.count(name)
                for name in ("reproduced", "mismatch", "invalid", "missing")
            },
        },
        "agreement": agreement,
        "missing": [
            cell["trial_name"] for cell in cells if cell["status"] == "missing"
        ],
    }


def score(data: Path, bundle: Path, new: Path) -> Record:
    root, rows = sources(data, bundle)
    archived, expected = guarded("invalid_pinned_data", native_panel.originals, root)
    raw, receipts = guarded("invalid_new_bundle", read_new, new)
    shaped: dict[int, Record] = {
        index: cast(Record, value)
        for index, value in enumerate(receipts)
        if isinstance(value, dict) and value.keys() == native_panel.RECEIPT_KEYS
    }
    found = clashes(shaped, rows)
    placed: dict[tuple[int, str], list[int]] = {}
    unplaced: list[Record] = []
    for index in range(len(receipts)):
        key = cell_of(shaped[index]) if index in shaped else None
        if key is None:
            reason = "cell" if index in shaped else "receipt_keys"
            unplaced.append(
                {"index": index, "reasons": sorted([reason, *found.get(index, [])])}
            )
        else:
            placed.setdefault(key, []).append(index)
    cells = [
        judge(
            key,
            [(shaped[i], found.get(i, [])) for i in placed.get(key, [])],
            expected[key],
            rows[key],
        )
        for key in CELLS
    ]
    ids = [
        row["trial_id"]
        for row in shaped.values()
        if native_panel.uuid_ok(row["trial_id"])
    ]
    report: Record = {
        "schema": SCORE_SCHEMA,
        "original_bundle_sha256": native_repeat.RELEASED_BUNDLE,
        "bundle_sha256": native_stage.digest(raw),
        **summary(cells),
        "identity": {
            "fresh_trial_ids": len(ids),
            "distinct": len(set(ids)),
            "shared_with_originals": len(
                set(ids) & {row["trial_id"] for row in rows.values()}
            ),
            "results_shared_with_originals": sum(
                "original_result" in r for r in found.values()
            ),
            "regenerated": list(native_repeat.REGENERATED),
        },
        "unplaced": unplaced,
        "rows": cells,
    }
    return verdict(report, {key: shaped[placed[key][0]] for key in placed}, archived)


def verdict(report: Record, fresh: Archive, archived: Record) -> Record:
    counts = report["cells"]
    if report["unplaced"] or counts["invalid"]:
        return report | {"status": "invalid"}
    if counts["mismatch"]:
        return report | {"status": "mismatch"}
    if counts["missing"]:
        return report | {"status": "incomplete"}
    assert counts["reproduced"] == len(CELLS) and fresh.keys() == set(CELLS)
    paired = native_panel.paired(fresh, archived)
    if not paired["passed"]:
        return report | {
            "status": "mismatch",
            "paired_checks": paired["archived"]["checks"],
        }
    keep = ("worlds", "primary", "descriptive", "archived")
    return report | {"status": "complete", "paired": {key: paired[key] for key in keep}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3.12 -BS -m examples.seatlab.market_handoff.native_repeat_panel"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("collect", "score"):
        sub = commands.add_parser(command)
        sub.add_argument("data", type=Path)
        sub.add_argument("original_bundle", type=Path)
        if command == "collect":
            sub.add_argument("trial_roots", type=Path, nargs="*")
        else:
            sub.add_argument("new_bundle", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "collect":
            code, report = collect(args.data, args.original_bundle, args.trial_roots)
        else:
            report = score(args.data, args.original_bundle, args.new_bundle)
            code = 0 if report["status"] == "complete" else 1
    except Malformed as exc:
        failure: Record = {
            "schema": SCHEMA,
            "status": "malformed",
            "reason": exc.reason,
        }
        if exc.index is not None:
            failure["input"] = exc.index
        sys.stderr.write(native_stage.canonical(failure).decode("ascii") + "\n")
        return 2
    stream = sys.stdout if code == 0 or args.command == "score" else sys.stderr
    stream.write(native_stage.canonical(report).decode("ascii") + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
