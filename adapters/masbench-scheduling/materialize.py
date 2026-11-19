"""Build a native Harbor scheduling task from one MASBench case."""

import argparse
import json
import shutil
from pathlib import Path
from string import Template

from cases import load_case

from harbor.models.task.task import Task

_TEMPLATE = Path(__file__).resolve().with_name("template")
_ROLES = ("m1", "m2", "m3", "m4", "m5")
_POLICIES = {
    "full": "Report every feasible start.",
    "first8": "Report only the first 8 feasible starts, or all if there are fewer.",
}
_TASK_FILES = (
    "task.toml",
    "environment/Dockerfile",
    "environment/docker-compose.yaml",
    "environment/calendar_world.py",
    "environment/player/Dockerfile",
    "solution/solve.sh",
    "solution/client.py",
    "tests/test.sh",
    "tests/verify.py",
)


def _write_file(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o755 if path.suffix == ".sh" else 0o644)


def materialize(dataset: Path, case_id: str, mode: str, output: Path) -> Path:
    """Create and validate a task at an unused path under a private directory."""
    if mode not in _POLICIES:
        raise ValueError(f"unsupported mode: {mode!r}; choose full or first8")

    output = output.parent.resolve() / output.name
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"output already exists: {output}")

    case_json = json.dumps(
        {"mode": mode, "case": load_case(dataset, case_id)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    common = Template((_TEMPLATE / "instructions/common.md").read_text("utf-8"))
    scheduler = (_TEMPLATE / "instructions/scheduler.md").read_text("utf-8")

    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if output.parent.stat().st_mode & 0o077:
        raise PermissionError(f"output parent must be private (0700): {output.parent}")

    output.mkdir(mode=0o700)
    try:
        for name in (
            "instructions",
            "environment",
            "environment/player",
            "solution",
            "tests",
        ):
            directory = output / name
            directory.mkdir()
            directory.chmod(0o755)
        for name in _TASK_FILES:
            _write_file(output / name, (_TEMPLATE / name).read_bytes())
        _write_file(output / "environment/case.json", case_json)
        for role in _ROLES:
            instruction = common.substitute(
                role=role,
                mode=mode,
                policy=_POLICIES[mode],
                scheduler=scheduler if role == "m1" else "",
            )
            _write_file(output / f"instructions/{role}.md", instruction.encode("utf-8"))
        Task(output)
        output.chmod(0o755)
    except BaseException:
        shutil.rmtree(output)
        raise
    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Keep generated tasks outside version control. Set task.path in a Harbor "
            "trial config to the printed path and bind m1 through m5 to participants. "
            "The task is offline; use participants such as oracle. "
            "Start the trial with harbor trial start -c TRIAL_CONFIG."
        ),
    )
    parser.add_argument(
        "--dataset", type=Path, required=True, help="MASBench checkout root"
    )
    parser.add_argument("--case", required=True, help="source case ID, for example 4/1")
    parser.add_argument("--mode", choices=_POLICIES, required=True)
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="unused task directory under a private (0700) parent directory",
    )
    args = parser.parse_args()
    try:
        output = materialize(args.dataset, args.case, args.mode, args.out)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(output)


if __name__ == "__main__":
    main()
