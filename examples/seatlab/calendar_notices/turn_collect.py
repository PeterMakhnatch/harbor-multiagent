import argparse
import json
import stat
from pathlib import Path
from typing import Any

from .. import calendar_turn_diff
from . import repeat_summary, turn_summary
from .visible_replay_summary import _unique_object


def _trial_directory(job: Path, run: str) -> Path:
    path = job
    for component in ("cells", run, "trials", run):
        path = path / component
        if not stat.S_ISDIR(path.lstat().st_mode):
            raise ValueError(
                "enrolled trial path must contain only physical directories"
            )
    return path


def collect(receipt: Any, job: Path, spend_dir: Path) -> dict[str, Any]:
    repeat_summary.summarize(receipt)
    rows = {
        (row["world"], row["arm"], row["repetition"]): row for row in receipt["teams"]
    }
    root = job.expanduser().resolve()
    pairs = []
    for world in repeat_summary._WORLDS:
        for arm in repeat_summary._ARMS:
            left, right = rows[world, arm, 1], rows[world, arm, 2]
            if left["status"] != "complete" or right["status"] != "complete":
                continue
            try:
                r1 = _trial_directory(root, left["run"])
                r2 = _trial_directory(root, right["run"])
                pair = calendar_turn_diff.compare(r1, r2, spend_dir)
            except (OSError, ValueError):
                raise ValueError(
                    f"cannot compare enrolled trials for {world}/{arm}"
                ) from None
            pairs.append(pair)
    panel = {"schema": turn_summary.PANEL_SCHEMA, "pairs": pairs}
    turn_summary.summarize(receipt, panel)
    return panel


def _read_receipt(path: Path) -> Any:
    limit = repeat_summary._MAX_RECEIPT_BYTES
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("oversize native repeatability receipt")
    return json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Collect comparisons from enrolled native calendar repeats.",
        allow_abbrev=False,
    )
    parser.add_argument("receipt", type=Path, metavar="NATIVE_RECEIPT")
    parser.add_argument("--job", type=Path, required=True, metavar="JOB")
    parser.add_argument("--spend-dir", type=Path, required=True, metavar="DIR")
    args = parser.parse_args(argv)
    try:
        receipt = _read_receipt(args.receipt)
    except (OSError, ValueError, RecursionError):
        parser.error("cannot read a bounded native repeatability receipt")
    try:
        panel = collect(receipt, args.job, args.spend_dir)
    except (OSError, ValueError, TypeError, LookupError, RuntimeError):
        parser.error("cannot collect a validated native calendar turn panel")
    print(json.dumps(panel, separators=(",", ":"), allow_nan=False))


if __name__ == "__main__":
    main()
