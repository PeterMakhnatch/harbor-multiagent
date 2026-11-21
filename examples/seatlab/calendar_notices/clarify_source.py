import sys
from pathlib import Path

from ..calendar_case import _Parser, _wire
from ..calendar_inquiry import _write_owner


def _export(
    trial: Path, spend_dir: Path, out: Path, *, cohort_sha256: str | None = None
) -> None:
    from ..calendar_case_source import load

    source = load(
        trial,
        spend_dir,
        include_sender_table=True,
        cohort_sha256=cohort_sha256,
    )
    if source._sender_table is None:
        raise ValueError("clarification requires an empty checked view")
    _write_owner(
        out,
        _wire(
            {
                "schema": "seatlab.calendar-clarification-sender/v1",
                "trial_uuid": source.trial_uuid,
                "table": source._sender_table,
            }
        ),
    )


def main() -> int:
    parser = _Parser(allow_abbrev=False)
    parser.add_argument("trial", type=Path)
    parser.add_argument("--spend-dir", required=True, type=Path)
    parser.add_argument("--cohort-sha256")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        _export(args.trial, args.spend_dir, args.out, cohort_sha256=args.cohort_sha256)
    except (ImportError, OSError, ValueError):
        print("Cannot export calendar clarification sender.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
