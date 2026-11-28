"""File collection and content hashing for task packages.

Shared logic used by both the publisher and the `harbor dataset add` command.
"""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

import pathspec

from harbor.models.task.config import TaskConfig
from harbor.models.task.file_filter import TaskFileFilter
from harbor.models.task.paths import TaskPaths
from harbor.utils.file_selection import select_files

DEFAULT_IGNORES = [
    "__pycache__/",
    "*.pyc",
    ".DS_Store",
    "*.swp",
    "*.swo",
    "*~",
]


class Packager:
    """Collects task files and computes content hashes."""

    @staticmethod
    def collect_files(task_dir: Path) -> list[Path]:
        """Collect publishable files from a task directory."""
        task_dir = task_dir.resolve()
        paths = TaskPaths(task_dir)
        file_filter = TaskFileFilter(task_dir)

        files: list[Path] = []

        # Single files
        for single in (
            paths.config_path,
            paths.instruction_path,
            paths.readme_path,
            paths.trajectory_path,
        ):
            if single.exists():
                files.append(single)

        # Recursive directories. steps_dir covers per-step instruction.md,
        # tests/, solution/, workdir/, and setup.sh for multi-step tasks; it
        # simply doesn't exist for single-step tasks.
        for directory in (
            paths.environment_dir,
            paths.tests_dir,
            paths.solution_dir,
            paths.steps_dir,
        ):
            if directory.exists():
                selection = select_files(directory, exclude=file_filter.is_excluded)
                for relative_path in selection.relative_paths:
                    p = directory / relative_path
                    if p.is_file():
                        files.append(p)

        # Apply gitignore / default ignore filtering
        if paths.gitignore_path.exists():
            spec = pathspec.PathSpec.from_lines(
                "gitignore", paths.gitignore_path.read_text().splitlines()
            )
        else:
            spec = pathspec.PathSpec.from_lines("gitignore", DEFAULT_IGNORES)

        files.extend(Packager._seat_instruction_files(paths, spec, file_filter))
        files = [
            f for f in files if not spec.match_file(f.relative_to(task_dir).as_posix())
        ]

        files.sort(key=lambda p: p.relative_to(task_dir).as_posix())
        return files

    @staticmethod
    def _seat_instruction_files(
        paths: TaskPaths,
        spec: pathspec.PathSpec[pathspec.Pattern],
        file_filter: TaskFileFilter,
    ) -> list[Path]:
        config_path = paths.config_path
        if not config_path.exists() and not config_path.is_symlink():
            return []
        if not config_path.resolve().is_relative_to(paths.task_dir):
            raise ValueError(f"Input paths must stay within the task: {config_path}")
        if not config_path.is_file():
            raise ValueError(f"task.toml must be a regular file: {config_path}")
        try:
            with config_path.open("rb") as handle:
                raw_config = tomllib.load(handle)
        except tomllib.TOMLDecodeError as error:
            raise ValueError(
                f"Invalid task.toml in {paths.task_dir}: {error}"
            ) from error
        if "agents" not in raw_config:
            return []
        seats = TaskConfig.model_validate({"agents": raw_config["agents"]}).agents
        if seats and Packager._is_ignored(paths, spec, config_path):
            raise ValueError(
                "task.toml declares [agents] seats but is excluded by task "
                "ignore rules; it is required to publish the task."
            )
        seat_files: list[Path] = []
        if seats:
            file_filter.require_included(config_path)
        for seat in seats:
            path = paths.seat_instruction_path(seat)
            relative_path = path.relative_to(paths.task_dir).as_posix()
            paths.validate_input_tree(path)
            if not path.is_file():
                raise FileNotFoundError(
                    f"Seat {seat!r} has no instruction: expected {relative_path}"
                )
            file_filter.require_included(path)
            if Packager._is_ignored(paths, spec, path):
                raise ValueError(
                    f"Seat {seat!r} instruction {relative_path} is excluded by "
                    "task ignore rules; it is required to publish the task."
                )
            seat_files.append(path)
        return seat_files

    @staticmethod
    def _is_ignored(
        paths: TaskPaths, spec: pathspec.PathSpec[pathspec.Pattern], path: Path
    ) -> bool:
        return any(
            spec.match_file(candidate.relative_to(paths.task_dir).as_posix())
            for candidate in (path, path.resolve())
        )

    @staticmethod
    def compute_file_hash(file_path: Path) -> str:
        """Compute SHA-256 digest for a single file."""
        digest = hashlib.sha256()
        with file_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def compute_content_hash(task_dir: Path) -> tuple[str, list[Path]]:
        """Collect files and compute SHA-256 content hash for a task directory.

        Returns (content_hash, files).
        """
        task_dir = task_dir.resolve()
        files = Packager.collect_files(task_dir)
        outer = hashlib.sha256()
        for f in files:
            rel = f.relative_to(task_dir).as_posix()
            file_hash = Packager.compute_file_hash(f)
            outer.update(f"{rel}\0{file_hash}\n".encode())
        return outer.hexdigest(), files
