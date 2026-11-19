"""Export native calendar decisions to the portable SeatLab JSON Lines format.

The leader view contains only its actual proposals, including submitted start
reports. It contains no private busy ticks or gold. Gold and common feasible
starts are offline answer data, not policy input. Native feasibility is null
when the leader chose no time. Offline feasibility is a boolean: did the action
produce a feasible time? Thus a deliberate null scores false, not unknown.

The data comes from BUPT-GAMMA/MASBench (MIT, Copyright (c) 2026 Qizhi Chu).
Include the accompanying UPSTREAM-LICENSE.txt when redistributing the tape.
"""

import argparse
import json
import os
import tempfile
from collections.abc import Iterator
from itertools import chain
from pathlib import Path
from typing import Any

import study
from cases import member_starts


def _read_local(path: Path) -> bytes:
    if path.resolve() != path:
        raise ValueError("symlink in local evidence path")
    return path.read_bytes()


def _registered_inputs(root, report):
    # collect verified the registered plan and source. Rebuild the cell paths
    # with the same programmatic plan, never with native result paths or URIs.
    try:
        cases = study._object(_read_local(root / "source.json"))["cases"]
        plan = study._plan(
            report["study_id"],
            cases,
            list(report["summary"]),
            report["shard"],
            report["cohort_cases"],
        )
        hashes = {row["case_id"]: row["case_sha256"] for row in report["cells"]}
        if plan["case_hashes"] != hashes:
            raise ValueError("source hash mismatch")
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        raise ValueError(
            "registered calendar snapshot changed after collection"
        ) from None
    cells = {(cell["case_id"], cell["mode"]): cell for cell in plan["cells"]}
    return cases, cells


def _bound_decision(root, cell, case, row):
    observed = row["observed"]
    _, native = study._paths(root, cell)
    try:
        raw = _read_local(native / study.NATIVE_FILES["world"])
        if study._sha(raw) != observed["evidence_sha256"]["world"]:
            raise ValueError("world artifact hash mismatch")
        world = study._object(raw)
    except (KeyError, OSError, RuntimeError, ValueError):
        raise ValueError("native world evidence changed after collection") from None

    view = {"role": "m1", "mode": world["mode"], "reports": world["reports"]}
    view_digest = study._sha(study._canonical(view))
    if (
        world["acknowledged"] is not True
        or world["proposal_sha256"] != view_digest
        or observed["proposal_sha256"] != view_digest
    ):
        raise ValueError("native leader acknowledgment does not match the proposals")

    return {
        "schema": "seatlab.decision/v1",
        "key": f"calendar:{cell['case_id'].replace('/', '-')}:{cell['mode']}",
        "role": view["role"],
        "native_trial_id": row["native_id"],
        "basis": "bound",
        "view": view,
        "view_sha256": view_digest,
        "observed": {"action": world["choice"]},
        "answers": {
            "kind": "int-range",
            "min": 0,
            "max": case["horizon"] - 1,
            "include_null": True,
            "optimal": sorted(case["gold"]),
            "feasible": member_starts(
                chain.from_iterable(case["members"].values()),
                duration=case["duration"],
                horizon=case["horizon"],
                slots_per_day=case["slots_per_day"],
            ),
        },
    }


def _decisions(root, report, verified) -> Iterator[dict[str, Any]]:
    if not verified:
        return
    cases, cells = _registered_inputs(root, report)
    for row in verified:
        case_id = row["case_id"]
        yield _bound_decision(root, cells[case_id, row["mode"]], cases[case_id], row)


def _publish(out: Path, records: Iterator[dict[str, Any]]) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=out.parent,
            prefix=".calendar-tape-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            for record in records:
                stream.write(study._canonical(record))
                stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, out)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def export_tape(root: Path, out: Path) -> dict[str, int]:
    """Publish one decision per verified trial or an empty tape if none verify.

    Attempted includes incomplete cells. Excluded counts all planned cells
    without a decision, including unattempted cells. A post-collection change
    to the case snapshot or world evidence prevents publication of the tape.
    """
    if os.path.lexists(out):
        raise FileExistsError("--out must be a new file")
    try:
        root = root.resolve(strict=True)
        report = study.collect(root)
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        raise ValueError("invalid or unreadable native study registration") from None

    rows = report["cells"]
    # collect rejects all claims to a duplicated native UUID.
    verified = [row for row in rows if row["status"] == "verified"]
    _publish(out, _decisions(root, report, verified))
    return {
        "attempted": sum(row["status"] != "unattempted" for row in rows),
        "verified": len(verified),
        "exported": len(verified),
        "excluded": len(rows) - len(verified),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--native", required=True, type=Path, help="private study root")
    parser.add_argument("--out", required=True, type=Path, help="new JSON Lines file")
    args = parser.parse_args(argv)
    try:
        summary = export_tape(args.native, args.out)
    except FileExistsError:
        parser.exit(1, "--out must be a new file\n")
    except OSError:
        parser.exit(1, "could not publish calendar decision tape\n")
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(summary, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
