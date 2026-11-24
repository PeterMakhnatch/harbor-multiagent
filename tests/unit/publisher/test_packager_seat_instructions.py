import re
import shutil
import tarfile
from pathlib import Path

import pytest

from harbor.models.job.lock import build_trial_lock
from harbor.models.task.task import Task
from harbor.models.trial.config import TaskConfig, TrialConfig
from harbor.publisher.packager import Packager
from harbor.publisher.publisher import Publisher
from harbor.tasks.client import TaskDownloadResult

pytestmark = pytest.mark.unit

EXAMPLES = Path(__file__).resolve().parents[3] / "examples" / "tasks"
SEATS = ("p1", "p2")


def _copy_example(name: str, destination: Path) -> Path:
    target = destination.resolve() / name
    shutil.copytree(EXAMPLES / name, target, symlinks=True)
    return target


def _publish_offline(task_dir: Path, destination: Path) -> tuple[str, set[str], Path]:
    destination = destination.resolve()
    destination.mkdir(parents=True)
    digest, files = Packager.compute_content_hash(task_dir)
    archive = destination / "task.harbor"
    Publisher._create_archive(task_dir, files, archive)
    extracted = destination / "downloaded"
    with tarfile.open(archive, "r:gz") as tar:
        names = set(tar.getnames())
        tar.extractall(extracted, filter="data")
    return digest, names, extracted


def _lock_digest(task_dir: Path) -> str:
    lock = build_trial_lock(
        trial_config=TrialConfig(task=TaskConfig(path=task_dir)),
        task_download_result=TaskDownloadResult(
            path=task_dir, download_time_sec=0, cached=True
        ),
    )
    return lock.task.digest


def test_relocated_seat_package_keeps_digest_and_delivers_each_role(
    tmp_path: Path,
) -> None:
    expected = Packager.compute_content_hash(EXAMPLES / "hello-seats")[0]
    first = _copy_example("hello-seats", tmp_path / "first")
    second = _copy_example("hello-seats", tmp_path / "second" / "nested")

    assert Packager.compute_content_hash(first)[0] == expected
    assert Packager.compute_content_hash(second)[0] == expected
    digest, names, extracted = _publish_offline(first, tmp_path / "registry")
    assert digest == expected
    assert {name for name in names if name.startswith("instructions/")} == {
        f"instructions/{seat}.md" for seat in SEATS
    }
    assert Packager.compute_content_hash(extracted)[0] == expected
    source = Task(EXAMPLES / "hello-seats")
    downloaded = Task(extracted)
    for seat in SEATS:
        assert downloaded.seat_instruction(seat) == source.seat_instruction(seat)
    assert downloaded.seat_instruction("p1") != downloaded.seat_instruction("p2")


def test_editing_one_role_changes_digest_and_only_that_role(tmp_path: Path) -> None:
    task_dir = _copy_example("hello-seats", tmp_path)
    before = Packager.compute_content_hash(task_dir)[0]
    original = Task(EXAMPLES / "hello-seats")
    role = task_dir / "instructions" / "p2.md"
    role.write_text(role.read_text() + "\nWave before you stop.\n")

    after, _, extracted = _publish_offline(task_dir, tmp_path / "registry")

    assert after != before
    assert Packager.compute_content_hash(extracted)[0] == after
    downloaded = Task(extracted)
    assert downloaded.seat_instruction("p1") == original.seat_instruction("p1")
    assert downloaded.seat_instruction("p2") != original.seat_instruction("p2")
    assert downloaded.seat_instruction("p2") == Task(task_dir).seat_instruction("p2")


def test_trial_lock_digest_matches_published_seat_package(tmp_path: Path) -> None:
    task_dir = _copy_example("hello-seats", tmp_path)
    published, _, _ = _publish_offline(task_dir, tmp_path / "registry")

    assert _lock_digest(task_dir) == f"sha256:{published}"
    role = task_dir / "instructions" / "p1.md"
    role.write_text(role.read_text() + "\nWave before you stop.\n")
    assert _lock_digest(task_dir) != f"sha256:{published}"


def test_task_without_seats_keeps_digest_with_local_instructions_note(
    tmp_path: Path,
) -> None:
    expected = Packager.compute_content_hash(EXAMPLES / "hello-world")[0]
    task_dir = _copy_example("hello-world", tmp_path)
    notes = task_dir / "instructions"
    notes.mkdir()
    (notes / "notes.md").write_text("Local scratch notes.\n")
    (notes / "p1.md").write_text("You are p1.\n")

    digest, names, extracted = _publish_offline(task_dir, tmp_path / "registry")

    assert digest == expected
    assert not any(name.startswith("instructions/") for name in names)
    assert Task(extracted).instruction == Task(task_dir).instruction


def test_undeclared_role_note_stays_out_of_seat_package(tmp_path: Path) -> None:
    task_dir = _copy_example("hello-seats", tmp_path)
    expected = Packager.compute_content_hash(task_dir)[0]
    (task_dir / "instructions" / "p3.md").write_text("You are p3.\n")

    digest, names, _ = _publish_offline(task_dir, tmp_path / "registry")

    assert digest == expected
    assert "instructions/p3.md" not in names


@pytest.mark.parametrize(
    "required", ["task.toml", "instructions/p1.md", "instructions/p2.md"]
)
async def test_ignored_required_seat_input_refuses_publication(
    tmp_path: Path, required: str
) -> None:
    task_dir = _copy_example("hello-seats", tmp_path)
    (task_dir / ".gitignore").write_text(f"/{required}\n")
    offline_publisher = Publisher.__new__(Publisher)

    with pytest.raises(ValueError, match=re.escape(required)):
        Packager.compute_content_hash(task_dir)
    with pytest.raises(ValueError, match=re.escape(required)):
        await offline_publisher.publish_task(task_dir)
    assert (task_dir / required).is_file()
