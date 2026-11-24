"""Every reader of trial files refuses a link out of the trial (fork PR #4 review, F3): interaction,
verifier output, file content, the artifacts manifest, and both folder listings."""

from pathlib import Path

from fastapi.testclient import TestClient

from harbor.viewer.server import _open_trial_file, _read_trial_bytes, create_app

SECRET = "outside-secret-7f3a"
TRIAL = "/api/jobs/job-1/trials/trial-1"


def _trial(tmp_path: Path) -> tuple[TestClient, Path, Path]:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text(SECRET)
    (outside / "secret.json").write_text(f'{{"key": "{SECRET}"}}')
    trial = tmp_path / "jobs" / "job-1" / "trial-1"
    for folder in ("verifier", "artifacts", "agent", "user-agent"):
        (trial / folder).mkdir(parents=True)
    return TestClient(create_app(tmp_path / "jobs")), trial, outside


def test_verifier_output_does_not_follow_a_link_out(tmp_path: Path):
    client, trial, outside = _trial(tmp_path)
    (trial / "verifier" / "test-stdout.txt").symlink_to(outside / "secret.txt")
    (trial / "verifier" / "reward.json").symlink_to(outside / "secret.json")
    response = client.get(f"{TRIAL}/verifier-output")
    assert response.status_code == 200
    assert SECRET not in response.text
    assert response.json()["stdout"] is None


def test_interaction_does_not_follow_a_link_out(tmp_path: Path):
    client, trial, outside = _trial(tmp_path)
    (trial / "user-agent" / "trajectory.json").symlink_to(outside / "secret.json")
    (trial / "agent" / "bridge-trajectory.json").symlink_to(outside / "secret.json")
    response = client.get(f"{TRIAL}/interaction")
    assert SECRET not in response.text


def test_file_content_through_a_link_out_is_refused(tmp_path: Path):
    client, trial, outside = _trial(tmp_path)
    (trial / "agent" / "notes.txt").symlink_to(outside / "secret.txt")
    (trial / "agent" / "linked").symlink_to(outside, target_is_directory=True)
    for path in ("agent/notes.txt", "agent/linked/secret.txt"):
        response = client.get(f"{TRIAL}/files/{path}")
        assert response.status_code == 403, path
        assert SECRET not in response.text


def test_listings_and_the_artifacts_manifest_skip_links_out(tmp_path: Path):
    client, trial, outside = _trial(tmp_path)
    (trial / "agent" / "linked").symlink_to(outside, target_is_directory=True)
    (trial / "artifacts" / "linked").symlink_to(outside, target_is_directory=True)
    (trial / "artifacts" / "manifest.json").symlink_to(outside / "secret.json")
    (trial / "artifacts" / "kept.txt").write_text("inside")
    files = client.get(f"{TRIAL}/files").text
    artifacts = client.get(f"{TRIAL}/artifacts")
    assert "secret" not in files
    assert SECRET not in artifacts.text and "secret.txt" not in artifacts.text
    assert artifacts.json()["manifest"] is None
    assert "kept.txt" in artifacts.text


def test_a_folder_swapped_for_a_link_after_the_check_is_refused(tmp_path: Path):
    """The race: the path resolves inside the trial, then a folder on it becomes a link out."""
    _client, trial, outside = _trial(tmp_path)
    (trial / "agent" / "logs").mkdir()
    (trial / "agent" / "logs" / "secret.txt").write_text("inside, before the swap")
    target = (trial / "agent" / "logs" / "secret.txt").resolve()
    assert _read_trial_bytes(trial, target) == b"inside, before the swap"
    (trial / "agent" / "logs" / "secret.txt").unlink()
    (trial / "agent" / "logs").rmdir()
    (trial / "agent" / "logs").symlink_to(outside, target_is_directory=True)
    assert _open_trial_file(trial, target) is None
