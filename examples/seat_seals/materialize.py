"""Stage the opt-in `harbor/shift-board-seal` task from the frozen Shift Board.

Usage: `python3 -BS -m examples.seat_seals.materialize OUT`. It reuses the
checkpoint staging: the frozen stock copy is checked against the frozen base, OUT
appears complete or not at all, and nothing existing is overwritten. The world
image gains board_world.py, mark.py, seal_world.py and seal_mark.py; the main
service runs seal_world.py and alone receives a required BOARD_SEAL. Seat
images, instructions and the verifier stay stock.
"""

import sys
from pathlib import Path

from ..seat_checkpoints import materialize as checkpoint

PACKAGE_DIR = Path(__file__).resolve().parent
SCHEMA = "harbor.seat-seal.materialized/v1"
TASK_NAME = "harbor/shift-board-seal"
ARTIFACTS = (
    "/logs/world/shift-board-id.json",
    "/logs/world/seat-cut.json",
    "/logs/world/seat-seal.json",
)
ADDED: tuple[tuple[str, Path], ...] = (
    *checkpoint.ADDED,
    ("environment/seal_world.py", PACKAGE_DIR / "seal_world.py"),
    ("environment/seal_mark.py", PACKAGE_DIR / "seal_mark.py"),
)
WORLD_FILES = ("board.py", "world.py", *(Path(path).name for path, _ in ADDED))
REVISIONS: tuple[tuple[str, str, str], ...] = (
    (
        "task.toml",
        'name = "harbor/shift-board"\n',
        f'name = "{TASK_NAME}"\n',
    ),
    (
        "task.toml",
        'artifacts = ["/logs/world/shift-board-id.json"]\n',
        "artifacts = [" + ", ".join(f'"{path}"' for path in ARTIFACTS) + "]\n",
    ),
    (
        "environment/Dockerfile",
        "COPY board.py world.py /app/\n",
        "COPY " + " ".join(WORLD_FILES) + " /app/\n",
    ),
    (
        "environment/docker-compose.yaml",
        '    command: ["python3", "/app/world.py"]\n',
        '    command: ["python3", "/app/seal_world.py"]\n'
        "    environment:\n"
        '      BOARD_SEAL: "${BOARD_SEAL:?BOARD_SEAL must be continue or seal}"\n',
    ),
)
LAYOUT = checkpoint.Layout(
    schema=SCHEMA,
    task=TASK_NAME,
    stage_prefix=".seat-seal-stage-",
    added=ADDED,
    revisions=REVISIONS,
)

assert len(set(WORLD_FILES)) == len(WORLD_FILES) == 6
assert all(path.startswith("environment/") for path, _, _ in REVISIONS[2:])
assert not any("seat/" in path or "instructions/" in path for path, _, _ in REVISIONS)


def materialize(out: Path) -> dict[str, object]:
    return checkpoint.materialize_layout(out, LAYOUT)


def main(argv: list[str]) -> int:
    return checkpoint.run(argv, LAYOUT)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
