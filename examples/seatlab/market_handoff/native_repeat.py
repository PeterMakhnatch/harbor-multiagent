from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from examples.seatlab.market_handoff import (
    decisions,
    native_panel,
    native_receipt,
    native_stage,
)

SCHEMA = "seatlab.market-native-repeat/v1"
RELEASED_BUNDLE = "a694d6841e5d1512c7a2570e579c1bbf17535b3b7c702f91efa5cb1d9d5f402e"
ROLES = native_panel.ROLES
FIELDS = {
    "task_name": ("task_name",),
    "target": ("prefix_fingerprint",),
    "payoffs": ("sha256", "payoffs"),
    "choices": ("choices",),
    "grade": ("grade",),
    "multiple": ("multiple",),
}
SEAT_FIELDS = {
    "source": "source",
    "served_model": "served_model",
    "pick": "pick",
    "input": "view_sha256",
    "prompt": "prompt_sha256",
}
REGENERATED = (
    "trial_id",
    "trial_uri",
    "timestamps",
    "config",
    "sha256.config",
    "sha256.result",
)
FAILURES = (OSError, ValueError, KeyError, IndexError, TypeError, RecursionError)
Record = dict[str, Any]


class InvalidSource(ValueError):
    pass


def archive(data: Path, bundle_path: Path) -> dict[tuple[int, str], Record]:
    raw = native_receipt.read_bytes(bundle_path.expanduser().resolve(strict=True))
    if native_stage.digest(raw) != RELEASED_BUNDLE:
        decisions.fail("bundle: differs from the released native panel")
    report = native_panel.score(data, bundle_path)
    if not (
        report.get("status") == "complete"
        and report.get("passed") is True
        and report.get("bundle_sha256") == RELEASED_BUNDLE
        and report["cells"]["complete"] == len(native_panel.CELLS)
    ):
        decisions.fail("bundle: released native panel no longer scores complete")
    bundle = native_receipt.record(decisions.decode(raw.decode("utf-8")), "bundle")
    rows = {(cell["world"], cell["arm"]): cell["receipt"] for cell in bundle["cells"]}
    ids = {row["trial_id"] for row in rows.values()}
    if rows.keys() != set(native_panel.CELLS) or len(ids) != len(native_panel.CELLS):
        decisions.fail("bundle: expected 24 unique released native trial IDs")
    return rows


def fresh_receipt(data: Path, out: Path) -> Record:
    out = out.expanduser()
    if out.is_symlink() or not out.is_dir():
        decisions.fail("FRESH_TRIAL_ROOT: expected a real trial directory")
    row = native_receipt.receipt(out)
    if row["kind"] != "replay":
        decisions.fail(f"{row['trial_name']}: fresh trial is not a replay")
    world, arm = row["world"], row["arm"]
    assert row["trial_name"] == native_panel.NAMES[(world, arm)]
    bad = native_panel.stage_conflicts(out.resolve(strict=True), data, world, arm)
    if bad:
        decisions.fail(f"{row['trial_name']}: fresh stage differs in {', '.join(bad)}")
    return row


def at(row: Record, path: tuple[str, ...]) -> object:
    assert 0 < len(path) <= 2
    value: object = row
    for key in path:
        assert isinstance(value, dict)
        value = value[key]
    return value


def agreement(fresh: Record, original: Record) -> Record:
    fields = {
        name: native_panel.same(at(fresh, path), at(original, path))
        for name, path in FIELDS.items()
    }
    seats = {
        role: {
            name: native_panel.same(
                fresh["seats"][role][key], original["seats"][role][key]
            )
            for name, key in SEAT_FIELDS.items()
        }
        for role in ROLES
    }
    checks = list(fields.values())
    checks += [ok for row in seats.values() for ok in row.values()]
    assert len(checks) == len(FIELDS) + len(ROLES) * len(SEAT_FIELDS)
    return {
        "fields": fields,
        "seats": seats,
        "required": len(checks),
        "agree": sum(checks),
    }


def side(row: Record) -> Record:
    return {key: row[key] for key in ("trial_id", "choices", "grade")}


def refusal(fresh: Record, rows: dict[tuple[int, str], Record]) -> Record | None:
    for key, row in rows.items():
        if (
            row["trial_id"] == fresh["trial_id"]
            or row["sha256"]["result"] == fresh["sha256"]["result"]
        ):
            return {
                "schema": SCHEMA,
                "status": "refused",
                "reason": "same_native_trial",
                "trial_name": fresh["trial_name"],
                "archive": {
                    "bundle_sha256": RELEASED_BUNDLE,
                    "trial_name": native_panel.NAMES[key],
                    "trial_id": row["trial_id"],
                },
                "fresh": {"trial_id": fresh["trial_id"]},
            }
    return None


def compare(data: Path, bundle: Path, out: Path) -> Record:
    try:
        root = native_panel.pinned(data)
    except FAILURES as exc:
        raise InvalidSource("invalid_pinned_data") from exc
    try:
        rows = archive(root, bundle)
    except FAILURES as exc:
        raise InvalidSource("invalid_released_bundle") from exc
    try:
        fresh = fresh_receipt(root, out)
    except FAILURES as exc:
        raise InvalidSource("invalid_fresh_trial") from exc
    refused = refusal(fresh, rows)
    if refused is not None:
        return refused
    key = (fresh["world"], fresh["arm"])
    original = rows[key]
    agreed = agreement(fresh, original)
    mismatches = sorted(
        [name for name, ok in agreed["fields"].items() if not ok]
        + [
            f"seats.{role}.{name}"
            for role, row in agreed["seats"].items()
            for name, ok in row.items()
            if not ok
        ]
    )
    assert (agreed["agree"] == agreed["required"]) == (not mismatches)
    return {
        "schema": SCHEMA,
        "status": "mismatch" if mismatches else "reproduced",
        "trial_name": fresh["trial_name"],
        "world": key[0],
        "arm": key[1],
        "archive": {"bundle_sha256": RELEASED_BUNDLE} | side(original),
        "fresh": side(fresh),
        "agreement": agreed,
        "mismatches": mismatches,
        "raw_evidence_bytes_match": fresh["sha256"]["evidence"]
        == original["sha256"]["evidence"],
        "regenerated": list(REGENERATED),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3.12 -BS -m examples.seatlab.market_handoff.native_repeat"
    )
    parser.add_argument("data", type=Path)
    parser.add_argument("original_bundle", type=Path)
    parser.add_argument("fresh_trial_root", type=Path)
    args = parser.parse_args(argv)
    try:
        report = compare(args.data, args.original_bundle, args.fresh_trial_root)
    except InvalidSource as exc:
        failure = {"schema": SCHEMA, "status": "invalid", "reason": str(exc)}
        sys.stderr.write(native_stage.canonical(failure).decode("ascii") + "\n")
        return 2
    sys.stdout.write(native_stage.canonical(report).decode("ascii") + "\n")
    return 0 if report["status"] == "reproduced" else 1


if __name__ == "__main__":
    raise SystemExit(main())
