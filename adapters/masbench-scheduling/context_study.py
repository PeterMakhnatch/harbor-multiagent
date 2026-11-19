"""Run the registered calendar context-field audit on an exported native episode set."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import stat
import tempfile
from pathlib import Path
from uuid import UUID

from seatlab import tape as wire
from seatlab.episodes import read_episodes
from seatlab.sufficiency import audit_context

SCHEMA = "seatlab.calendar-context-study/v1"
PROTOCOL = "adapters/masbench-scheduling/context-audit-protocol.md"
ROLE = "m1"
STRATA = (2, 3, 4, 5)
FIT_NUMBERS = (1, 2, 3, 4)
HELDOUT_NUMBERS = (5, 6)
FIT_CASES = tuple(f"{stratum}/{number}" for stratum in STRATA for number in FIT_NUMBERS)
HELDOUT_CASES = tuple(
    f"{stratum}/{number}" for stratum in STRATA for number in HELDOUT_NUMBERS
)
EXPECTED_CASES = tuple(
    f"{stratum}/{number}"
    for stratum in STRATA
    for number in (*FIT_NUMBERS, *HELDOUT_NUMBERS)
)
EXPECTED_CASE_SET = frozenset(EXPECTED_CASES)

PROJECTIONS: tuple[tuple[str, tuple[str, ...] | None], ...] = (
    ("Full", None),
    ("Role only", ()),
    ("Round", ("/round",)),
    ("Request bound", ("/bound",)),
    ("Participant replies", ("/replies",)),
    ("Exchange", ("/round", "/bound", "/replies")),
)

_INDEX_FIELDS = frozenset({"case_id", "status", "native_trial_id", "episode_id"})
_WITNESS_LIMIT = 1


def _require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_uuid(value: object) -> str:
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError("invalid canonical native_trial_id UUID")
    try:
        parsed = UUID(value)
    except ValueError:
        raise ValueError("invalid canonical native_trial_id UUID") from None
    _require(str(parsed) == value, "invalid canonical native_trial_id UUID")
    return value


def _expected_episode_id(case_id: str) -> str:
    stratum, number = case_id.split("/")
    return f"calendar:{stratum}-{number}:cursor:{ROLE}"


def _checked_source_dir(path: Path) -> Path:
    try:
        mode = path.lstat().st_mode
    except OSError:
        raise ValueError("source export directory is unreadable") from None
    _require(not stat.S_ISLNK(mode), "source export directory cannot be a symlink")
    _require(stat.S_ISDIR(mode), "source export must be a directory")
    return path


def _read_regular_file(root: Path, name: str) -> bytes:
    target = root / name
    try:
        mode = target.lstat().st_mode
        _require(not stat.S_ISLNK(mode), f"source export {name} cannot be a symlink")
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            _require(
                stat.S_ISREG(os.fstat(stream.fileno()).st_mode),
                f"source export {name} must be a regular file",
            )
            return stream.read()
    except ValueError:
        raise
    except OSError:
        raise ValueError(f"source export {name} is missing or unreadable") from None


def _parse_index(raw: bytes) -> dict[str, tuple[str, str]]:
    try:
        parsed = wire.decode(raw)
    except wire.TapeError:
        raise ValueError("source index.json is invalid JSON") from None
    _require(isinstance(parsed, list), "source index.json must be a JSON array")
    assert isinstance(parsed, list)

    indexed: dict[str, tuple[str, str]] = {}
    native_ids: set[str] = set()
    episode_ids: set[str] = set()
    for row in parsed:
        _require(isinstance(row, dict), "index entry must be a JSON object")
        assert isinstance(row, dict)
        _require(
            row.keys() == _INDEX_FIELDS,
            "index entry must contain only verified registration fields",
        )
        _require(
            row["status"] == "verified", "all 24 registered cases must be verified"
        )
        case_id = row["case_id"]
        _require(
            isinstance(case_id, str) and case_id in EXPECTED_CASE_SET,
            "unexpected or malformed case_id in source index",
        )
        assert isinstance(case_id, str)
        _require(case_id not in indexed, "duplicate case_id in source index")

        native_id = _canonical_uuid(row["native_trial_id"])
        _require(
            native_id not in native_ids, "duplicate native_trial_id in source index"
        )

        episode_id = row["episode_id"]
        _require(
            isinstance(episode_id, str) and episode_id == _expected_episode_id(case_id),
            "indexed episode_id does not match calendar:{stratum}-{num}:cursor:m1",
        )
        assert isinstance(episode_id, str)
        _require(episode_id not in episode_ids, "duplicate episode_id in source index")

        indexed[case_id] = (native_id, episode_id)
        native_ids.add(native_id)
        episode_ids.add(episode_id)

    _require(
        indexed.keys() == EXPECTED_CASE_SET,
        "source index must contain exactly the 24 registered cases",
    )
    return indexed


def _raw_episode_lines(episodes_bytes: bytes) -> list[bytes]:
    lines: list[bytes] = []
    stream = io.BytesIO(episodes_bytes)
    while True:
        line = stream.readline()
        if not line:
            break
        lines.append(line if line.endswith(b"\n") else line + b"\n")
    _require(bool(lines), "source episodes.jsonl contains no episodes")
    return lines


def _load_and_split_episodes(
    episodes_bytes: bytes,
    indexed: dict[str, tuple[str, str]],
    work_dir: Path,
) -> tuple[Path, Path]:
    raw_lines = _raw_episode_lines(episodes_bytes)
    source_copy = work_dir / "source-episodes.jsonl"
    source_copy.write_bytes(episodes_bytes)

    expected_by_episode_id = {
        episode_id: (case_id, native_id)
        for case_id, (native_id, episode_id) in indexed.items()
    }

    by_case: dict[str, bytes] = {}
    seen_trials: set[str] = set()
    seen_cases: set[str] = set()
    try:
        for index, episode in enumerate(read_episodes(source_copy)):
            episode_id = episode["id"]
            _require(
                episode_id in expected_by_episode_id,
                "episode id is not registered in source index",
            )
            case_id, expected_native_id = expected_by_episode_id[episode_id]
            _require(case_id not in by_case, "duplicate episode for registered case")
            _require(episode["role"] == ROLE, "episode role must be m1")
            _require(episode["closed"] is True, "episode must be closed")
            native_id = _canonical_uuid(episode["native_trial_id"])
            _require(
                native_id == expected_native_id,
                "episode native_trial_id does not match source index",
            )
            _require(
                native_id not in seen_trials,
                "duplicate native_trial_id across episodes",
            )
            case_digest = episode["case_sha256"]
            _require(
                case_digest not in seen_cases,
                "duplicate case_sha256 across episodes",
            )
            seen_trials.add(native_id)
            seen_cases.add(case_digest)
            by_case[case_id] = raw_lines[index]
    except wire.TapeError:
        raise ValueError("invalid episode record in source episodes.jsonl") from None

    _require(
        by_case.keys() == EXPECTED_CASE_SET,
        "source episodes.jsonl must match all 24 registered cases one-to-one",
    )

    fit_path = work_dir / "fit.jsonl"
    heldout_path = work_dir / "heldout.jsonl"
    fit_path.write_bytes(b"".join(by_case[case_id] for case_id in FIT_CASES))
    heldout_path.write_bytes(b"".join(by_case[case_id] for case_id in HELDOUT_CASES))
    return fit_path, heldout_path


def _write_new_json(path: Path, report: dict[str, object]) -> None:
    if os.path.lexists(path):
        raise ValueError("output path must not already exist")
    payload = (
        json.dumps(
            report,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
        + b"\n"
    )
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=".context-study-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temp_path = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp_path, path)
    except FileExistsError:
        raise ValueError("output path must not already exist") from None
    except OSError:
        raise ValueError("cannot write output report") from None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def run_study(source_export: Path, out: Path) -> dict[str, object]:
    """Validate a 24-case native export, run all 6 registered audits, and write JSON."""
    out = Path(out)
    if os.path.lexists(out):
        raise ValueError("output path must not already exist")
    if not out.parent.is_dir():
        raise ValueError("output parent directory does not exist")
    root = _checked_source_dir(Path(source_export))
    index_bytes = _read_regular_file(root, "index.json")
    episodes_bytes = _read_regular_file(root, "episodes.jsonl")

    indexed = _parse_index(index_bytes)
    source_index_sha256 = _sha256_bytes(index_bytes)
    source_episodes_sha256 = _sha256_bytes(episodes_bytes)

    with tempfile.TemporaryDirectory(prefix="seatlab-calendar-context-") as tmp:
        work_dir = Path(tmp)
        fit_path, heldout_path = _load_and_split_episodes(
            episodes_bytes, indexed, work_dir
        )
        rows: list[dict[str, object]] = []
        for name, fields in PROJECTIONS:
            try:
                audit = audit_context(
                    fit_path,
                    fields=fields,
                    heldout=heldout_path,
                    witnesses=_WITNESS_LIMIT,
                )
            except (ValueError, OSError):
                raise ValueError(
                    f"context audit failed for projection {name}"
                ) from None
            rows.append(
                {
                    "representation": name,
                    "fields": audit["fields"],
                    "fit": audit["fit"],
                    "heldout": audit["heldout"],
                }
            )

    report: dict[str, object] = {
        "schema": SCHEMA,
        "protocol": PROTOCOL,
        "role": ROLE,
        "source": {
            "index_sha256": source_index_sha256,
            "episodes_sha256": source_episodes_sha256,
            "verified_cases": len(EXPECTED_CASES),
        },
        "split": {
            "fit_cases": list(FIT_CASES),
            "heldout_cases": list(HELDOUT_CASES),
        },
        "representations": rows,
    }
    _write_new_json(out, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source_export",
        type=Path,
        metavar="SOURCE_EXPORT",
        help="directory containing verified index.json and episodes.jsonl",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        metavar="NEW.json",
        help="new JSON output path (must not already exist)",
    )
    args = parser.parse_args(argv)
    try:
        run_study(args.source_export, args.out)
    except ValueError as exc:
        parser.exit(1, f"context_study: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
