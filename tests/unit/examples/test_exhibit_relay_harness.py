import re
from pathlib import Path

import pytest

from examples.native_study import MAX_TASK_BYTES
from examples.seatlab.exhibit_harness import HARNESS_PATHS, fingerprint


def author_tree(root: Path) -> None:
    for relative in HARNESS_PATHS:
        source = root / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(relative.encode("ascii"))


def test_fingerprint_checkout_simulation(tmp_path: Path) -> None:
    root = tmp_path / "author"
    author_tree(root)
    original = fingerprint(root)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", original)

    (root / "README.md").write_text("unrelated documentation", encoding="utf-8")
    assert fingerprint(root) == original

    moved = root.rename(tmp_path / "moved")
    logic = moved / "examples/seatlab/exhibit_owner.py"
    logic.chmod(0o600)
    assert fingerprint(moved) == original

    logic.write_bytes(b"changed logic")
    assert fingerprint(moved) != original
    logic.unlink()
    with pytest.raises(FileNotFoundError):
        fingerprint(moved)


def test_fingerprint_rejects_oversized_source(tmp_path: Path) -> None:
    author_tree(tmp_path)
    source = tmp_path / HARNESS_PATHS[-1]
    with source.open("wb") as handle:
        handle.truncate(MAX_TASK_BYTES + 1)
    with pytest.raises(ValueError, match="exceed"):
        fingerprint(tmp_path)
