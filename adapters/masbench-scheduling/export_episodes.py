"""Export verified native cursor trials as portable m1 episodes."""

import argparse
from collections.abc import Iterable
from pathlib import Path

import interactive
import study
from template.environment.calendar_world import role_view

LICENSE_NAME = "UPSTREAM-LICENSE.txt"
CASE_FILE = "environment/case.json"


def _digest(value):
    return study._sha(study._canonical(value))


def _frame(turn, view, expected_sha256, action):
    view_sha256 = _digest(view)
    study._require(view_sha256 == expected_sha256, "native view digest mismatch")
    # The broker binds each accepted query and choice to its prior delivered view.
    return {
        "turn": turn,
        "basis": "bound",
        "view": view,
        "view_sha256": view_sha256,
        "observed": {"action": action},
    }


def _episode(case, native_id, world):
    rounds = world["rounds"]
    frames = [
        _frame(
            1,
            role_view(case, "cursor", "m1"),
            world["initial_view_sha256"]["m1"],
            f"query:{rounds[0]['bound']}",
        )
    ]
    choice = world["choice"]
    terminal = None if choice is None else f"choose:{choice}"
    for number, row in enumerate(rounds, 1):
        action = (
            f"query:{rounds[number]['bound']}" if number < len(rounds) else terminal
        )
        view = {key: row[key] for key in ("round", "bound", "replies")}
        frames.append(_frame(number + 1, view, row["bundle_sha256"], action))
    study._require(
        frames[-1]["view_sha256"] == world["choice_view_sha256"],
        "native choice view digest mismatch",
    )
    case_name = case["case_id"].replace("/", "-")
    episode = {
        "schema": "seatlab.episode/v1",
        "id": f"calendar:{case_name}:cursor:m1",
        "case_sha256": _digest(case),
        "role": "m1",
        "native_trial_id": native_id,
        "closed": True,
        "frames": frames,
    }
    return {**episode, "episode_sha256": _digest(episode)}


def _selected_episode(
    root, registered_out, cell, case, task_hashes, attempt, native_id
):
    try:
        cell_root, native = study._paths(root, cell)
        relative = cell_root.relative_to(root)
        study._require(
            attempt is not None
            and interactive._read_file(root, (relative / "attempt.json").as_posix())
            == attempt,
            "attempt receipt differs from its snapshot",
        )
        files = interactive._native_files(root, native)
        study._require(
            files.keys() == interactive.NATIVE_FILES.keys()
            and interactive._digests(files) == study._object(attempt)["native_sha256"],
            "native files differ from the attempt receipt",
        )
        result = study._object(files["result"])
        _, registered_native = study._paths(registered_out, cell)
        study._require(
            study._uuid(result["id"]) == native_id
            and result["trial_name"] == cell["trial_name"]
            and result["trial_uri"] == registered_native.as_uri(),
            "native trial identity mismatch",
        )
        case_data = interactive._read_file(
            root, (relative / "task" / CASE_FILE).as_posix()
        )
        study._require(
            case_data == study._canonical({"mode": "cursor", "case": case})
            and study._sha(case_data) == task_hashes[CASE_FILE],
            "task case differs from registration",
        )
        return _episode(case, native_id, study._object(files["world"]))
    except (*interactive.EVIDENCE_ERRORS, IndexError):
        raise ValueError("verified native evidence changed or is unreadable") from None


def _collect_root(path):
    try:
        root = interactive._root(Path(path))
        plan_bytes = interactive._read_file(root, "plan.json")
        source_bytes = interactive._read_file(root, "source.json")
        plan = study._object(plan_bytes)
        source = study._object(source_bytes)
        study_id = plan["study_id"]
        study._uuid(study_id)
        case_ids = [cell["case_id"] for cell in plan["cells"]]
        for case_id in case_ids:
            study._case_key(case_id)
        hashes = plan["task_sha256"]
        rebuilt = interactive._plan(study_id, source, case_ids, hashes)
        study._require(plan == rebuilt, "invalid registered cell identities")
        cells = {cell["case_id"]: cell for cell in rebuilt["cells"]}
        registered_out = Path(source["out_path"])
        cases = source["cases"]
        attempts = {}
        for case_id, cell in cells.items():
            cell_root, _ = study._paths(root, cell)
            receipt = (cell_root / "attempt.json").relative_to(root).as_posix()
            try:
                attempts[case_id] = interactive._read_file(root, receipt)
            except (OSError, ValueError):
                attempts[case_id] = None
        rows = interactive.collect(root)["cells"]
    except (*interactive.EVIDENCE_ERRORS, ImportError, RuntimeError, SyntaxError):
        raise ValueError("invalid native root registration") from None

    try:
        study._require(
            interactive._read_file(root, "plan.json") == plan_bytes
            and interactive._read_file(root, "source.json") == source_bytes,
            "native registration differs from its snapshot",
        )
    except (OSError, ValueError):
        raise ValueError("native registration changed during collection") from None

    for row in rows:
        case_id = row["case_id"]
        index_row = {"case_id": case_id, "status": row["status"]}
        if "reason" in row:
            index_row["reason"] = row["reason"]
        native_id = row.get("native_id")
        if native_id is not None:
            index_row["native_trial_id"] = native_id
        episode = None
        if row["status"] == "verified":
            episode = _selected_episode(
                root,
                registered_out,
                cells[case_id],
                cases[case_id],
                hashes[case_id],
                attempts[case_id],
                native_id,
            )
            index_row["episode_id"] = episode["id"]
        yield index_row, episode


def export_episodes(roots: Iterable[Path], out: Path) -> None:
    """Write an index of planned cases and episodes only for verified trials."""
    out = Path(out)
    try:
        out.lstat()
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        raise ValueError("cannot check episode output path") from None
    else:
        raise ValueError("output must be a new directory")

    entries = {}
    native_ids = set()
    for root in roots:
        for row, episode in _collect_root(root):
            case_id = row["case_id"]
            if case_id in entries:
                raise ValueError("duplicate case ID")
            if "native_trial_id" in row:
                native_id = row["native_trial_id"]
                if native_id in native_ids:
                    raise ValueError("duplicate native trial UUID")
                native_ids.add(native_id)
            entries[case_id] = row, episode
    if not entries:
        raise ValueError("at least one native root is required")

    try:
        license_bytes = interactive._read_file(interactive.ADAPTER, LICENSE_NAME)
        study._require(bool(license_bytes.strip()), "empty upstream license")
    except (OSError, ValueError):
        raise ValueError("upstream license is missing, unreadable, or empty") from None

    ordered = [entries[case_id] for case_id in sorted(entries, key=study._case_key)]
    index_data = study._canonical([row for row, _ in ordered]) + b"\n"
    episode_data = b"".join(
        study._canonical(episode) + b"\n"
        for _, episode in ordered
        if episode is not None
    )
    try:
        out.mkdir()
        (out / "episodes.jsonl").write_bytes(episode_data)
        (out / "index.json").write_bytes(index_data)
        (out / LICENSE_NAME).write_bytes(license_bytes)
    except FileExistsError:
        raise ValueError("output must be a new directory") from None
    except OSError:
        raise ValueError("cannot write episode output") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--native", required=True, action="append", type=Path, metavar="ROOT"
    )
    parser.add_argument("--out", required=True, type=Path, metavar="NEW_DIR")
    args = parser.parse_args(argv)
    try:
        export_episodes(args.native, args.out)
    except ValueError as exc:
        parser.exit(1, f"export_episodes: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
