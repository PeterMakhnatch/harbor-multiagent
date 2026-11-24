import hashlib
import os
import stat
from pathlib import Path

import pytest

from examples.seatlab.decision_receipt import materialize as decision
from examples.ship_inflight import materialize as inflight

SEAT_FILES = (
    "environment/seat/Dockerfile",
    "instructions/outgoing.md",
    "instructions/relief.md",
)


def private_parent(tmp_path: Path) -> Path:
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    return parent


def snapshot(root: Path) -> dict[str, tuple[int, bytes]]:
    return {
        str(path.relative_to(root)): (
            stat.S_IMODE(path.lstat().st_mode),
            path.read_bytes() if path.is_file() else b"",
        )
        for path in sorted(root.rglob("*"))
    }


@pytest.mark.unit
def test_materialize_stages_decision_task_with_identity(tmp_path: Path) -> None:
    parent = private_parent(tmp_path)
    out = decision.target(str(parent / "ship-decision-receipt"))
    record = decision.materialize(out)
    assert os.listdir(parent) == [out.name]
    assert record["schema"] == decision.SCHEMA
    assert record["task"] == "harbor/ship-decision-receipt"
    assert record["path"] == str(out)
    assert record["base"] == decision.BASE and record["parent_private"] is True
    assert record["base_files"] == decision.BASE_FILES
    assert stat.S_IMODE(out.lstat().st_mode) == 0o755
    dirs, files = inflight.walk(out)
    assert dirs == sorted(inflight.STOCK_DIRS)
    assert files == sorted(decision.FINAL_MODES) and len(files) == 16
    entries = record["files"]
    assert isinstance(entries, dict) and sorted(entries) == files
    digests: dict[str, str] = {}
    for relative in files:
        path = out / relative
        mode = stat.S_IMODE(path.lstat().st_mode)
        executable = relative in ("solution/solve.sh", "tests/test.sh")
        assert mode == (0o755 if executable else 0o644)
        digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        assert entries[relative] == {"mode": f"{mode:04o}", "sha256": digest}
        digests[relative] = digest
    assert record["tree"] == decision.tree_digest(decision.FINAL_MODES, digests)


@pytest.mark.unit
def test_seat_files_and_selected_scripts(tmp_path: Path) -> None:
    out = private_parent(tmp_path) / "task"
    decision.materialize(decision.target(str(out)))
    base = inflight.compose(inflight.load_stock())
    for relative in SEAT_FILES:
        assert (out / relative).is_file()
    for relative in decision.UNCHANGED:
        assert (out / relative).read_bytes() == base[relative]
    assert (out / "environment/decision_world.py").read_bytes() == (
        decision.PACKAGE_DIR / "decision_world.py"
    ).read_bytes()
    assert (out / "tests/decision_verify.py").read_bytes() == (
        decision.PACKAGE_DIR / "decision_verify.py"
    ).read_bytes()
    task = (out / "task.toml").read_text()
    assert 'name = "harbor/ship-decision-receipt"\n' in task
    assert "[agents.outgoing]" in task and "[agents.relief]" in task
    compose = (out / "environment/docker-compose.yaml").read_text()
    assert '["python3", "/app/decision_world.py"]' in compose
    assert (
        compose.replace("decision_world", "inflight_world")
        == base["environment/docker-compose.yaml"].decode()
    )
    assert "decision_world.py /app/" in (out / "environment/Dockerfile").read_text()
    assert (
        (out / "tests/test.sh")
        .read_text()
        .endswith("exec python3 -I /tests/decision_verify.py\n")
    )
    relief = (out / "instructions/relief.md").read_text()
    assert relief.count(decision.CITE_SENTENCE) == 1
    assert (
        relief.replace(decision.CITE_SENTENCE, "")
        == base["instructions/relief.md"].decode()
    )
    assert b'"cite"' not in (out / "solution/solve.sh").read_bytes()
    readme = (out / "README.md").read_text()
    assert "harbor/ship-decision-receipt" in readme and "harbor/ship-inflight" in readme
    assert "verifier/ship-decision-receipt-cut.json" in readme


@pytest.mark.unit
@pytest.mark.parametrize("existing", ["file", "empty", "full"])
def test_existing_output_is_refused_untouched(tmp_path: Path, existing: str) -> None:
    parent = private_parent(tmp_path)
    out = parent / "task"
    if existing == "file":
        out.write_bytes(b"keep\n")
    else:
        out.mkdir(mode=0o750)
        if existing == "full":
            (out / "keep.txt").write_bytes(b"keep\n")
    before = snapshot(parent)
    with pytest.raises(ValueError):
        decision.materialize(out)
    assert decision.main([str(out)]) == 1
    assert snapshot(parent) == before


@pytest.mark.unit
def test_output_claimed_during_staging_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = private_parent(tmp_path)
    out = parent / "task"
    original = decision.fill

    def racing_fill(stage: decision.Stage, files: dict[str, bytes]) -> dict[str, str]:
        staged = original(stage, files)
        out.mkdir(mode=0o700)
        (out / "theirs.txt").write_bytes(b"theirs\n")
        return staged

    monkeypatch.setattr(decision, "fill", racing_fill)
    with pytest.raises(FileExistsError):
        decision.materialize(out)
    assert os.listdir(parent) == [out.name]
    assert os.listdir(out) == ["theirs.txt"]


@pytest.mark.unit
def test_output_inside_checkout_is_refused() -> None:
    out = decision.CHECKOUT / "ship-decision-receipt-refused"
    assert not os.path.lexists(out)
    with pytest.raises(ValueError, match="outside the checkout"):
        decision.materialize(out)
    assert not os.path.lexists(out)
