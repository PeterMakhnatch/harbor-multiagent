"""Export a calibration bundle from retained native Harbor calendar trials.

The source arm is full disclosure. The target arm is the separate first8
trial. The bundle includes upstream MASBench attribution and must be treated
as offline evidence: only sources.jsonl is for replay, and only the views in
that tape are policy inputs.
"""

import argparse
import ctypes
import errno
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

import export_tape
import study

PAIR_SCHEMA = "seatlab.calibration/v1"
ARMS = (("source", "full"), ("target", "first8"))
LICENSE = Path(__file__).with_name("UPSTREAM-LICENSE.txt")


@dataclass(frozen=True)
class _Decision:
    native_id: str
    sha256: str
    line: bytes


class _ExportSummary(TypedDict):
    pairs: int
    verified: dict[str, int]
    missing: dict[str, int]
    sha256: dict[str, str]


def _decision_key(case_id: str, mode: str) -> str:
    return f"calendar:{case_id.replace('/', '-')}:{mode}"


def _registered_study(root: Path):
    try:
        return study.collect(root)
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        raise ValueError("invalid or unreadable native study registration") from None


def _read_decisions(root: Path, tape: Path, report) -> dict[tuple[str, str], _Decision]:
    # Use the native tape exporter's receipt and acknowledgment checks. Cross-check
    # its independent collection against the rows that supply the case digests.
    export_tape.export_tape(root, tape)
    export_tape._registered_inputs(root, report)
    expected = {
        _decision_key(row["case_id"], row["mode"]): row
        for row in report["cells"]
        if row["status"] == "verified"
    }
    decisions = {}
    with tape.open("rb") as stream:
        for line in stream:
            record = study._object(line)
            key = record.get("key")
            row = expected.pop(key, None) if isinstance(key, str) else None
            canonical = study._canonical(record)
            if (
                row is None
                or line != canonical + b"\n"
                or record.get("native_trial_id") != row["native_id"]
                or record.get("view_sha256") != row["observed"]["proposal_sha256"]
                or record.get("observed") != {"action": row["observed"]["choice"]}
            ):
                raise ValueError(
                    "native decision tape does not match collected receipts"
                )
            decisions[row["case_id"], row["mode"]] = _Decision(
                row["native_id"], study._sha(canonical), line
            )
    if expected:
        raise ValueError("native decision tape is missing a verified trial")
    return decisions


def _collect(roots: Sequence[Path], scratch: Path):
    hashes = {}
    decisions = {}
    planned = set()
    study_ids = set()
    native_ids = set()
    for index, root in enumerate(roots):
        report = _registered_study(root)
        study_id = study._uuid(report["study_id"])
        if study_id in study_ids:
            raise ValueError("duplicate native study UUID")
        study_ids.add(study_id)
        for row in report["cells"]:
            case_id, mode = row["case_id"], row["mode"]
            cell = (case_id, mode)
            if cell in planned:
                raise ValueError(
                    f"duplicate registered calendar cell: {case_id} {mode}"
                )
            planned.add(cell)
            case_hash = row["case_sha256"]
            if hashes.setdefault(case_id, case_hash) != case_hash:
                raise ValueError(f"registered case SHA-256 mismatch: {case_id}")
            if (native_id := row.get("native_id")) is not None:
                if native_id in native_ids:
                    raise ValueError("duplicate native trial UUID")
                native_ids.add(native_id)
        decisions.update(
            _read_decisions(root, scratch / f"study-{index}.jsonl", report)
        )
    return hashes, decisions


def _pinned_hashes(dataset: Path, registered: dict[str, str], all_cases: bool):
    try:
        available = study._source_case_ids(dataset)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        raise ValueError(
            f"--dataset must be the intact MASBench checkout at {study.DATASET_COMMIT}"
        ) from None
    if not registered.keys() <= set(available):
        raise ValueError(
            "native plan contains a case outside the pinned MASBench corpus"
        )
    result = {}
    for case_id in available if all_cases else registered:
        try:
            case_hash = study._sha(study._canonical(study.load_case(dataset, case_id)))
        except (OSError, RuntimeError, ValueError):
            raise ValueError(
                f"could not read pinned MASBench case: {case_id}"
            ) from None
        if registered.get(case_id, case_hash) != case_hash:
            raise ValueError(
                f"registered case does not match pinned MASBench: {case_id}"
            )
        result[case_id] = case_hash
    return result


def _write_bundle(stage: Path, hashes: dict[str, str], decisions) -> _ExportSummary:
    pairs = []
    verified = {side: 0 for side, _ in ARMS}
    with (
        (stage / "decisions.jsonl").open("wb") as tape,
        (stage / "sources.jsonl").open("wb") as sources,
    ):
        for case_id in sorted(hashes, key=study._case_key):
            pair: dict[str, str | dict[str, str | None]] = {
                "id": f"calendar:{case_id.replace('/', '-')}"
            }
            for side, mode in ARMS:
                decision = decisions.get((case_id, mode))
                pair[side] = {
                    "key": _decision_key(case_id, mode),
                    "case_sha256": hashes[case_id],
                    "native_trial_id": decision.native_id if decision else None,
                    "decision_sha256": decision.sha256 if decision else None,
                }
                if decision is not None:
                    verified[side] += 1
                    tape.write(decision.line)
                    if side == "source":
                        sources.write(decision.line)
            pairs.append(pair)
    (stage / "pairs.json").write_bytes(
        study._canonical({"schema": PAIR_SCHEMA, "pairs": pairs}) + b"\n"
    )
    (stage / LICENSE.name).write_bytes(LICENSE.read_bytes())
    digests = {}
    for name in ("decisions.jsonl", "sources.jsonl", "pairs.json", LICENSE.name):
        with (stage / name).open("rb") as stream:
            digests[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "pairs": len(pairs),
        "verified": verified,
        "missing": {side: len(pairs) - count for side, count in verified.items()},
        "sha256": digests,
    }


def _rename_new_directory(stage: Path, out: Path) -> None:
    # POSIX rename can replace an empty directory. Require an exclusive rename
    # so consumers see all files at once and we cannot overwrite even in a race.
    if os.name == "nt":
        os.rename(stage, out)
        return
    source, destination = os.fsencode(stage), os.fsencode(out)
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        status = rename(source, destination, 0x4)  # RENAME_EXCL
    elif sys.platform == "linux" and hasattr(libc, "renameat2"):
        rename = libc.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        status = rename(
            -100, source, -100, destination, 1
        )  # AT_FDCWD, RENAME_NOREPLACE
    else:
        raise OSError(errno.ENOTSUP, "atomic exclusive directory rename is unavailable")
    if status != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def export_pairs(
    native: Sequence[Path],
    out: Path,
    *,
    all_cases: bool = False,
    dataset: Path | None = None,
) -> _ExportSummary:
    """Publish one immutable bundle, retaining missing planned arms in the manifest."""
    if os.path.lexists(out):
        raise FileExistsError("--out must be a new directory")
    if not native:
        raise ValueError("at least one native study is required")
    if all_cases and dataset is None:
        raise ValueError("--all-cases requires --dataset")
    try:
        roots = [root.resolve(strict=True) for root in native]
    except (OSError, RuntimeError):
        raise ValueError("invalid or unreadable native study registration") from None
    if len(set(roots)) != len(roots):
        raise ValueError("duplicate native study root")
    out = out.parent.resolve(strict=True) / out.name
    if dataset is not None:
        try:
            dataset = dataset.resolve(strict=True)
        except (OSError, RuntimeError):
            raise ValueError("--dataset must be a readable MASBench checkout") from None
    if any(out.is_relative_to(root) for root in roots) or (
        dataset is not None and out.is_relative_to(dataset)
    ):
        raise ValueError("--out must be outside the native studies and dataset")

    with tempfile.TemporaryDirectory(dir=out.parent, prefix=".calendar-pairs-") as tmp:
        scratch = Path(tmp)
        hashes, decisions = _collect(roots, scratch)
        if dataset is not None:
            hashes = _pinned_hashes(dataset, hashes, all_cases)
        stage = scratch / "bundle"
        stage.mkdir()
        summary = _write_bundle(stage, hashes, decisions)
        _rename_new_directory(stage, out)
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", action="append", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path, help="new bundle directory")
    parser.add_argument("--all-cases", action="store_true", help="plan all 200 cases")
    parser.add_argument("--dataset", type=Path, help="pinned public MASBench checkout")
    args = parser.parse_args(argv)
    try:
        summary = export_pairs(
            args.native, args.out, all_cases=args.all_cases, dataset=args.dataset
        )
    except FileExistsError:
        parser.exit(1, "--out must be a new directory\n")
    except OSError:
        parser.exit(1, "could not publish calendar calibration bundle\n")
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
