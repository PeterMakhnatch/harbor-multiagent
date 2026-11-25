import base64
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from ...native_study import require, write_exclusive
from ...seat_checkpoints.materialize import dir_mode, read_plain, sync_directory
from ...ship_inflight.materialize import publish
from ..decision_boundary.core import World
from ..decision_boundary.inputs import SCHEMA, census
from ..decision_boundary.joint import ARMS
from ..exhibit_enroll import target
from .certificate import author_tables

EXPORT_SCHEMA = "seatlab.joint-handoffs.author-export/v1"
ACCESS = "private_benchmark_author"
SCOPE = "Author-only answer tables: every world lists the score of every legal dispatch. Keep these files away from participants, task images and checkouts."
STAGE_PREFIX = ".joint-author-export-"
PRIVATE_FILE = 0o600
PRIVATE_DIR = 0o700
SCALE = 1
FILES = tuple(f"{arm}.json" for arm in ARMS)


def _record(rows: list[World]) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "scale": SCALE,
        "worlds": [
            {
                "name": row.name,
                "mass": row.mass,
                "scores": row.scores,
                "observation_b64": base64.b64encode(row.observation).decode("ascii"),
            }
            for row in rows
        ],
    }


def _staged(path: Path, rows: list[World]) -> str:
    data, mode = read_plain(path)
    require(mode == PRIVATE_FILE, f"{path.name} is not private")
    require(census(data) == (SCALE, rows), f"{path.name} does not round-trip")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _write(staged: Path, arms: dict[str, list[World]]) -> dict[str, str]:
    digests: dict[str, str] = {}
    for arm, name in zip(ARMS, FILES, strict=True):
        write_exclusive(staged / name, _record(arms[arm]), PRIVATE_FILE)
        digests[name] = _staged(staged / name, arms[arm])
    require(sorted(os.listdir(staged)) == sorted(FILES), "staged files differ")
    require(dir_mode(staged) == PRIVATE_DIR, "staged directory is not private")
    return digests


def export(out: Path, start: int) -> dict[str, Any]:
    out = target(out)
    seed, arms = author_tables(start)
    reference = arms["both"]
    with tempfile.TemporaryDirectory(prefix=STAGE_PREFIX, dir=out.parent) as temporary:
        staged = Path(temporary) / "tables"
        staged.mkdir(mode=PRIVATE_DIR)
        digests = _write(staged, arms)
        sync_directory(staged)
        publish(staged, out)
    sync_directory(out.parent)
    require(dir_mode(out) == PRIVATE_DIR, "published directory is not private")
    return {
        "schema": EXPORT_SCHEMA,
        "access": ACCESS,
        "scope": SCOPE,
        "development_seed": seed,
        "path": str(out),
        "input_schema": SCHEMA,
        "scale": SCALE,
        "arms": list(ARMS),
        "worlds_per_arm": len(reference),
        "actions_per_world": len(reference[0].scores),
        "observation_classes": {
            arm: len({row.observation for row in arms[arm]}) for arm in ARMS
        },
        "files": digests,
        "model_calls": 0,
    }
