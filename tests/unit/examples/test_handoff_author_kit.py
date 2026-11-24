import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from examples.seat_handoffs import kit

REPO = Path(__file__).resolve().parents[3]
CORE = b"import json\nVALUE = 1\n"
PRODUCER = b"from .core import VALUE\n"
Json = dict[str, Any]


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    (root / "core.py").write_bytes(CORE)
    (root / "producer.py").write_bytes(PRODUCER)
    return root


@pytest.fixture
def dest(tmp_path: Path) -> Path:
    root = tmp_path / "context"
    root.mkdir()
    return root


def run(capsys, command: str, dest: Path, source: Path) -> tuple[int, Json]:
    code = kit.main([command, str(dest), "--json"], source=source)
    return code, json.loads(capsys.readouterr().out)


def states(report: Json) -> dict[str, str]:
    return {member["name"]: member["state"] for member in report["members"]}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_install_copies_exactly_three_members(capsys, source, dest):
    code, report = run(capsys, "install", dest, source)
    package = dest / "handoff_v1"
    expected = {"__init__.py": b"", "core.py": CORE, "producer.py": PRODUCER}
    hashes = {name: sha256(data) for name, data in expected.items()}
    listing = "".join(f"{hashes[name]}  {name}\n" for name in sorted(hashes))
    assert (code, report["status"], report["action"]) == (0, "match", "installed")
    assert sorted(path.name for path in dest.iterdir()) == ["handoff_v1"]
    assert sorted(path.name for path in package.iterdir()) == sorted(expected)
    assert {n: (package / n).read_bytes() for n in expected} == expected
    assert {m["name"]: m["found_sha256"] for m in report["members"]} == hashes
    assert {m["found_mode"] for m in report["members"]} == {"0644"}
    assert report["package_sha256"] == sha256(listing.encode())
    assert report["found_package_sha256"] == report["package_sha256"]
    assert package.stat().st_mode & 0o777 == 0o755
    assert (package / "core.py").stat().st_mode & 0o777 == 0o644


def test_repeated_install_is_idempotent(capsys, source, dest):
    run(capsys, "install", dest, source)
    before = (dest / "handoff_v1" / "core.py").stat().st_mtime_ns
    code, report = run(capsys, "install", dest, source)
    assert (code, report["status"]) == (0, "match")
    assert report["action"] == "already_installed"
    assert (dest / "handoff_v1" / "core.py").stat().st_mtime_ns == before


def test_check_reports_absent_kit_without_writing(capsys, source, dest):
    code, report = run(capsys, "check", dest, source)
    assert (code, report["status"], report["package_state"]) == (1, "absent", "absent")
    assert report["found_package_sha256"] is None
    assert list(dest.iterdir()) == []


def test_changed_member_is_reported_never_overwritten_or_executed(
    capsys, source, dest, tmp_path
):
    run(capsys, "install", dest, source)
    marker = tmp_path / "imported"
    changed = f"open({str(marker)!r}, 'w').close()\n".encode()
    (dest / "handoff_v1" / "core.py").write_bytes(changed)
    code, report = run(capsys, "check", dest, source)
    assert (code, report["status"]) == (1, "drift")
    assert states(report)["core.py"] == "mismatch"
    assert report["found_package_sha256"] != report["package_sha256"]
    code, report = run(capsys, "install", dest, source)
    assert (code, report["status"], report["action"]) == (1, "drift", "refused")
    assert (dest / "handoff_v1" / "core.py").read_bytes() == changed
    assert not marker.exists()


def test_missing_member_and_changed_mode_are_drift(capsys, source, dest):
    run(capsys, "install", dest, source)
    (dest / "handoff_v1" / "producer.py").unlink()
    (dest / "handoff_v1" / "core.py").chmod(0o755)
    code, report = run(capsys, "check", dest, source)
    assert (code, report["status"]) == (1, "drift")
    assert states(report) == {
        "__init__.py": "match",
        "core.py": "mode",
        "producer.py": "missing",
    }
    assert report["found_package_sha256"] is None


def test_unexpected_entries_are_drift_but_pycache_is_ignored(capsys, source, dest):
    run(capsys, "install", dest, source)
    package = dest / "handoff_v1"
    (package / "extra.py").write_bytes(b"")
    code, report = run(capsys, "check", dest, source)
    assert (code, report["unexpected"]) == (1, ["extra.py"])
    (package / "extra.py").unlink()
    (package / "__pycache__").mkdir()
    (package / "__pycache__" / "core.cpython-312.pyc").write_bytes(b"cache")
    code, report = run(capsys, "check", dest, source)
    assert (code, report["status"], report["unexpected"]) == (0, "match", [])


def test_symlinked_pycache_is_unexpected(capsys, source, dest, tmp_path):
    run(capsys, "install", dest, source)
    (tmp_path / "elsewhere").mkdir()
    (dest / "handoff_v1" / "__pycache__").symlink_to(tmp_path / "elsewhere")
    code, report = run(capsys, "check", dest, source)
    assert (code, report["unexpected"]) == (1, ["__pycache__"])


def test_symlinked_or_directory_member_is_not_followed(capsys, source, dest, tmp_path):
    run(capsys, "install", dest, source)
    target = tmp_path / "outside.py"
    target.write_bytes(CORE)
    member = dest / "handoff_v1" / "core.py"
    member.unlink()
    member.symlink_to(target)
    (dest / "handoff_v1" / "producer.py").unlink()
    (dest / "handoff_v1" / "producer.py").mkdir()
    code, report = run(capsys, "check", dest, source)
    assert code == 1
    assert states(report)["core.py"] == "symlink"
    assert states(report)["producer.py"] == "not_file"
    code, report = run(capsys, "install", dest, source)
    assert (code, report["action"]) == (1, "refused")
    assert member.is_symlink() and target.read_bytes() == CORE


def test_symlinked_package_is_drift(capsys, source, dest, tmp_path):
    run(capsys, "install", dest, source)
    other = tmp_path / "other"
    other.mkdir()
    (other / "handoff_v1").symlink_to(dest / "handoff_v1")
    code, report = run(capsys, "install", other, source)
    assert (code, report["status"], report["package_state"]) == (1, "drift", "symlink")
    assert set(states(report).values()) == {"unchecked"}
    assert (other / "handoff_v1").is_symlink()


def test_package_file_is_drift(capsys, source, dest):
    (dest / "handoff_v1").write_bytes(b"")
    code, report = run(capsys, "install", dest, source)
    assert (code, report["package_state"]) == (1, "not_dir")
    assert report["action"] == "refused"
    assert (dest / "handoff_v1").read_bytes() == b""


def test_other_creator_in_progress_is_incomplete(capsys, source, dest):
    package = dest / "handoff_v1"
    package.mkdir(mode=0o700)
    package.chmod(0o700)
    (package / "core.py").write_bytes(b"partial")
    code, report = run(capsys, "install", dest, source)
    assert (code, report["status"], report["action"]) == (1, "incomplete", "refused")
    assert report["package_state"] == "private"
    assert sorted(path.name for path in package.iterdir()) == ["core.py"]
    assert (package / "core.py").read_bytes() == b"partial"


def test_interrupted_install_is_surfaced_not_repaired(
    capsys, monkeypatch, source, dest
):
    real_write = kit._write

    def interrupted(package_fd: int, name: str, data: bytes) -> None:
        if name == "producer.py":
            raise OSError(5, "Input/output error")
        real_write(package_fd, name, data)

    monkeypatch.setattr(kit, "_write", interrupted)
    code, report = run(capsys, "install", dest, source)
    assert (code, report["status"]) == (2, "error")
    assert "incomplete" in report["error"]
    monkeypatch.undo()
    for command in ("check", "install"):
        code, report = run(capsys, command, dest, source)
        assert (code, report["status"]) == (1, "incomplete")
        assert states(report)["producer.py"] == "missing"
    package = dest / "handoff_v1"
    assert sorted(path.name for path in package.iterdir()) == ["__init__.py", "core.py"]
    assert package.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("member", ["core.py", "producer.py"])
def test_symlinked_or_missing_source_is_an_error(capsys, source, dest, member):
    (source / member).unlink()
    code, report = run(capsys, "check", dest, source)
    assert (code, report["status"]) == (2, "error")
    assert member in report["error"]
    (source / "other.py").write_bytes(CORE)
    (source / member).symlink_to(source / "other.py")
    code, report = run(capsys, "install", dest, source)
    assert (code, report["status"]) == (2, "error")
    assert list(dest.iterdir()) == []


def test_unusable_destination_is_an_error(capsys, source, tmp_path):
    code, report = run(capsys, "check", tmp_path / "missing", source)
    assert (code, report["status"]) == (2, "error")
    plain = tmp_path / "plain"
    plain.write_bytes(b"")
    code, report = run(capsys, "install", plain, source)
    assert (code, report["status"]) == (2, "error")


def test_entry_listing_is_bounded(capsys, source, dest):
    run(capsys, "install", dest, source)
    for index in range(kit.MAX_ENTRIES):
        (dest / "handoff_v1" / f"extra{index}.py").write_bytes(b"")
    code, report = run(capsys, "check", dest, source)
    assert (code, report["status"]) == (2, "error")


def test_text_output_escapes_hostile_names(capsys, source, dest):
    assert kit.main(["install", str(dest)], source=source) == 0
    text = capsys.readouterr().out
    assert text.startswith("match: ")
    assert sha256(CORE) in text and sha256(b"") in text
    (dest / "handoff_v1" / "evil\x1b[2J.py").write_bytes(b"")
    assert kit.main(["check", str(dest)], source=source) == 1
    text = capsys.readouterr().out
    assert "\x1b" not in text and "unexpected 'evil\\x1b[2J.py'" in text


@pytest.mark.parametrize("argv", [["check"], ["remove", "x"], ["check", "x", "--yes"]])
def test_invalid_usage_exits_two(argv, source):
    with pytest.raises(SystemExit) as raised:
        kit.main(argv, source=source)
    assert raised.value.code == 2


def test_public_command_installs_canonical_sources(dest):
    command = [sys.executable, "-BS", "-m", "examples.seat_handoffs.kit"]
    for action, action_name in (("install", "installed"), ("check", "checked")):
        result = subprocess.run(
            [*command, action, str(dest), "--json"],
            cwd=REPO,
            capture_output=True,
            check=False,
        )
        report = json.loads(result.stdout)
        assert (result.returncode, report["action"]) == (0, action_name)
    canonical = REPO / "examples" / "seat_handoffs"
    for name in ("core.py", "producer.py"):
        installed = (dest / "handoff_v1" / name).read_bytes()
        assert installed == (canonical / name).read_bytes()
    assert not (dest / "handoff_v1" / "__pycache__").exists()
