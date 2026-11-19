"""What a trial's task is: its label, instance and condition (CONTRACT.md 2).

A readable task name says it: ``flotilla/<label>-<iii>-<condition>``, the name of every task built
before opaque names, and of ``build_tasks.py --readable-names``. An opaque name (``flotilla/t-<12 hex>``,
the default) says nothing, because a seat can read the Docker names that Harbor derives from it. For an
opaque name the identity comes from, in this order:

1. ``task.toml`` ``[metadata]`` in the trial's task folder (``task_id.path`` in result.json), when the
   folder still holds the task of that name;
2. a build manifest given to the analysis (``--manifest FILE``, or ``DIR/manifest.json`` of ``--tasks DIR``);
3. the build manifest next to the task folder (``manifest.json`` at the build root of
   ``<root>/<label>/<task>``).

A manifest maps every task name built at its root to the task's label, instance, condition and seats, and
keeps the names of folders that a later build replaced. Only JSON and TOML files are read.
"""

from __future__ import annotations

import functools
import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONDITIONS = ("solo", "silent", "talk", "talk-liar", "talk-warned", "talk-liar-warned")
TASK_NAME = re.compile(
    r"^flotilla/(?P<label>[a-z0-9_]+(?:\.[a-z0-9_]+)?)-(?P<instance>\d{3})-(?P<condition>solo|silent|talk|talk-liar|talk-warned|talk-liar-warned)$"
)
LABEL = re.compile(r"^[a-z0-9_]+(?:\.[a-z0-9_-]+)?$")
MANIFEST = "manifest.json"


@dataclass(frozen=True)
class Identity:
    label: str  # <family> or <family>.<variant>
    instance: int
    condition: str


def identity(label: Any, instance: Any, condition: Any) -> Identity | None:
    """An ``Identity`` of valid values, else None."""
    if not isinstance(label, str) or not LABEL.match(label) or condition not in CONDITIONS:
        return None
    if isinstance(instance, bool) or not isinstance(instance, int) or instance < 0:
        return None
    return Identity(label, instance, condition)


def from_name(task_name: str) -> Identity | None:
    """The identity that a readable task name says."""
    match = TASK_NAME.match(task_name)
    return Identity(match["label"], int(match["instance"]), match["condition"]) if match else None


def from_task_folder(folder: Path, task_name: str) -> Identity | None:
    """The identity in ``task.toml`` ``[metadata]``, if the folder holds the task of this name."""
    try:
        config = tomllib.loads((folder / "task.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    task = config.get("task") if isinstance(config.get("task"), dict) else {}
    meta = config.get("metadata") if isinstance(config.get("metadata"), dict) else {}
    family, variant = meta.get("family"), meta.get("variant") or ""
    if task.get("name") != task_name or not isinstance(family, str) or not isinstance(variant, str):
        return None  # another task (a later build at this path) or not a Flotilla task folder
    return identity(f"{family}.{variant}" if variant else family, meta.get("instance"), meta.get("condition"))


@dataclass(frozen=True)
class Manifest:
    names: dict[str, Identity]  # task name -> identity
    folders: dict[Identity, list[Path]]  # identity -> its task folders, newest build first
    problem: str | None = None  # why the file cannot be used; None when it can
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)  # task name -> its entry as written


def read_manifest(path: Path) -> Manifest:
    """A build manifest (cached until the file changes); an empty one with a ``problem`` when it cannot be
    used."""
    try:
        stat = path.stat()
    except OSError:
        return Manifest({}, {}, "manifest not found")
    return _read_manifest(str(path), stat.st_mtime_ns, stat.st_size)


@functools.lru_cache(maxsize=64)
def _read_manifest(path: str, _mtime_ns: int, _size: int) -> Manifest:
    try:
        data = json.loads(Path(path).read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return Manifest({}, {}, f"manifest unreadable: {type(exc).__name__}")
    tasks = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(tasks, dict):
        return Manifest({}, {}, "not a Flotilla build manifest (no tasks object)")
    names: dict[str, Identity] = {}
    entries: dict[str, dict[str, Any]] = {}
    built: dict[Identity, list[tuple[str, Path]]] = {}
    for name, entry in tasks.items():
        known = (
            identity(entry.get("label"), entry.get("instance"), entry.get("condition"))
            if isinstance(entry, dict)
            else None
        )
        if known is None:
            continue
        names[str(name)] = known
        entries[str(name)] = entry
        if isinstance(entry.get("path"), str):
            built.setdefault(known, []).append(
                (str(entry.get("built") or ""), Path(path).parent / entry["path"])
            )
    folders = {
        key: [folder for _at, folder in sorted(rows, key=lambda row: row[0], reverse=True)]
        for key, rows in built.items()
    }
    return Manifest(names, folders, entries=entries)


def rebuilt_folders(root: Path, task: Identity) -> list[Path]:
    """The folders that the manifest at a build root lists for the same task, newest build first: a task
    folder rebuilt since the run has another opaque name."""
    return read_manifest(root / MANIFEST).folders.get(task, [])


class Names:
    """Resolves task names to identities for one analysis; ``problems`` holds (path, reason) for each given
    manifest that cannot be used."""

    def __init__(self, manifests: list[str | Path] | None = None) -> None:
        self.given: dict[str, Identity] = {}
        self.problems: list[tuple[str, str]] = []
        for raw in manifests or []:
            manifest = read_manifest(Path(raw))
            if manifest.problem:
                self.problems.append((str(raw), manifest.problem))
            self.given.update(manifest.names)

    def identity(self, task_name: str, task_path: str | None) -> Identity | None:
        readable = from_name(task_name)
        if readable is not None or not task_name.startswith("flotilla/"):
            return readable
        folder = Path(task_path) if task_path else None
        if folder is not None and (found := from_task_folder(folder, task_name)) is not None:
            return found
        if task_name in self.given:
            return self.given[task_name]
        for root in (folder.parent, folder.parent.parent) if folder is not None else ():
            if (found := read_manifest(root / MANIFEST).names.get(task_name)) is not None:
                return found
        return None
