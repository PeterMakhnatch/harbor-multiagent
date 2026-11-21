import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn, override

from ..visibility import calendar_packet as packet
from . import decision_shapes, heldout, team_source

SCHEMA = "seatlab.calendar-shape-intake/v1"
_DEFAULT_START = 48
_DEFAULT_COUNT = 24
_MAX_COUNT = 48
_MAX_UINT32 = (1 << 32) - 1
_SOURCE_ERROR = "invalid or unreadable calendar source"
_RANGE_ERROR = (
    "require start >= 48, count in 1..48, final instance <= 4294967295, "
    "and seed in 1..4294967295"
)
_Tables = tuple[packet.Table, packet.Table]
_Worlds = dict[str, _Tables]


def _add_world(
    worlds: _Worlds,
    seen: set[str],
    index: int,
    tables: _Tables,
    *,
    expected_calendar: str | None = None,
) -> None:
    calendar = heldout._calendar_digest(tables)
    world = f"w{index:02d}"
    if (
        world in worlds
        or calendar in seen
        or (expected_calendar is not None and calendar != expected_calendar)
    ):
        raise packet._Invalid(_SOURCE_ERROR)
    seen.add(calendar)
    worlds[world] = tables


def _load_stock(
    root: Path,
    cohort: heldout._Cohort,
    seen: set[str],
    *,
    manifest: tuple[Path, str] | None = None,
) -> _Worlds:
    root = heldout._check_schedule(root, cohort)
    rows = (
        heldout._pinned_manifest_rows(*manifest, cohort)
        if manifest is not None
        else None
    )
    worlds: _Worlds = {}
    for index, name in cohort.names().items():
        calendar, semantics = (
            rows[index - cohort.first] if rows is not None else (None, None)
        )
        _, tables = heldout._checked_stock(
            root / name, index, name, cohort, expected_semantics_sha256=semantics
        )
        _add_world(worlds, seen, index, tables, expected_calendar=calendar)
    return worlds


def _candidate_cohort(*, start: int, count: int, seed: int) -> heldout._Cohort:
    if (
        type(start) is not int
        or type(count) is not int
        or type(seed) is not int
        or not 1 <= count <= _MAX_COUNT
        or not _DEFAULT_START <= start <= _MAX_UINT32 - count + 1
        or not 1 <= seed <= _MAX_UINT32
    ):
        raise packet._Invalid(_RANGE_ERROR)
    _, max_text, max_messages, timeout = heldout._VISIBLE.stock_limits
    return heldout._Cohort(
        start, start + count - 1, (seed, max_text, max_messages, timeout)
    )


def analyze(
    heldout_root: Path | str,
    visible_root: Path | str,
    candidate_root: Path | str,
    *,
    seed: int,
    start: int = _DEFAULT_START,
    count: int = _DEFAULT_COUNT,
) -> dict[str, object]:
    candidate_cohort = _candidate_cohort(start=start, count=count, seed=seed)
    _, max_text, max_messages, timeout = candidate_cohort.stock_limits
    seen: set[str] = set()
    public: _Worlds = {}
    try:
        for world in packet.load(packet.PACKET, packet.PACKET_SHA256):
            _add_world(public, seen, world.index, world.tables)
        legacy = _load_stock(
            Path(heldout_root),
            heldout._LEGACY,
            seen,
            manifest=(heldout.MANIFEST, heldout.MANIFEST_SHA256),
        )
        visible = _load_stock(
            Path(visible_root),
            heldout._VISIBLE,
            seen,
            manifest=(team_source.JOIN_MANIFEST, team_source.JOIN_MANIFEST_SHA256),
        )
        candidate = _load_stock(Path(candidate_root), candidate_cohort, seen)
    except (OSError, ValueError):
        raise packet._Invalid(_SOURCE_ERROR) from None

    preceding = public | legacy
    prior = decision_shapes.panel(preceding, visible)
    prospective = decision_shapes.panel(preceding | visible, candidate)
    return {
        "schema": SCHEMA,
        "methodology": {
            "basis": "calendar_tables_only",
            "historical_generator_commit": heldout.GENERATOR_COMMIT,
            "family": "schedule",
            "seats": 2,
            "difficulty": "easy",
            "condition": "talk",
            "play_timeout_sec": int(timeout),
            "max_text": max_text,
            "max_messages": max_messages,
            "candidate_selection": "consecutive_instances_without_resampling",
            "historical_source": "pinned_packet_and_stock_manifests",
            "candidate_source_checks": {
                "checks_stock_layout": True,
                "checks_declared_metadata_and_limits": True,
                "checks_seat_tables_and_stored_answer_consistency": True,
                "authenticates_generator_origin": False,
            },
            "calendar_uniqueness": "across_all_cohorts_ignoring_seat_and_attendee_order",
        },
        "cohort_sizes": {
            "public_packet": len(public),
            "heldout": len(legacy),
            "visible": len(visible),
            "candidate": len(candidate),
        },
        "prior_visible_vs_preceding": prior,
        "candidate_vs_reference": prospective,
        "pass": prospective["pass"],
    }


class _Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        self.exit(2, "shape intake: invalid arguments; use --help\n")


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        description="Compare calendar decision shapes without team outputs.",
        allow_abbrev=False,
    )
    parser.add_argument("--heldout", required=True, type=Path)
    parser.add_argument("--visible", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--start", type=int, default=_DEFAULT_START)
    parser.add_argument("--count", type=int, default=_DEFAULT_COUNT)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        result = analyze(
            args.heldout,
            args.visible,
            args.candidate,
            seed=args.seed,
            start=args.start,
            count=args.count,
        )
    except packet._Invalid as exc:
        print(f"shape intake: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print(f"shape intake: {_SOURCE_ERROR}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
