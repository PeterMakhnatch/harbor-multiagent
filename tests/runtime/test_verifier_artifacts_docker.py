import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from harbor.models.environment_type import EnvironmentType
from harbor.models.trial.artifact_manifest import ArtifactManifestEntry
from harbor.models.trial.config import (
    AgentConfig,
    EnvironmentConfig,
    Participant,
    TaskConfig,
    TrialConfig,
)
from harbor.trial.trial import Trial

pytestmark = [pytest.mark.runtime, pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.parametrize("mode", ["single-shared", "single-separate", "seat-shared"])
async def test_verifier_output_artifacts_are_collected(
    tmp_path: Path, mode: str
) -> None:
    task = tmp_path / "artifact-phase"
    for name in ("environment", "solution", "tests"):
        (task / name).mkdir(parents=True)
    (task / "environment/Dockerfile").write_text("FROM ubuntu:24.04\n")
    (task / "solution/solve.sh").write_text(
        "#!/bin/bash\necho play >/tmp/play-output.txt\n"
    )
    (task / "tests/test.sh").write_text(
        "#!/bin/bash\nset -eu\n"
        'test "$(cat /tmp/play-output.txt)" = play\n'
        "echo changed >/tmp/play-output.txt\n"
        "echo log >/logs/verifier/late.txt\n"
        "echo report >/tmp/verifier-output.txt\n"
        "echo 1 >/logs/verifier/reward.txt\n"
    )
    task_config = """\
artifacts = [
  "/tmp/play-output.txt",
  "/logs/verifier/late.txt",
  { source = "/tmp/verifier-output.txt", destination = "reports/output.txt" },
  "/tmp/never-created.txt",
]
"""
    seats = mode == "seat-shared"
    if seats:
        (task / "instructions").mkdir()
        (task / "instructions/p1.md").write_text("Do nothing.\n")
        (task / "environment/docker-compose.yaml").write_text(
            "services:\n"
            "  main:\n"
            '    command: ["sh", "-c", "echo play >/tmp/play-output.txt; sleep infinity"]\n'
            "  p1:\n"
            "    image: ubuntu:24.04\n"
            '    command: ["sleep", "infinity"]\n'
            "    depends_on: [main]\n"
        )
        task_config += "\n[agents.p1]\n"
    else:
        (task / "instruction.md").write_text("Create the play output.\n")
    if mode == "single-separate":
        task_config += '\n[verifier]\nenvironment_mode = "separate"\n'
    (task / "task.toml").write_text(task_config)

    config = TrialConfig(
        task=TaskConfig(path=task),
        trial_name=f"artifact-phase-{hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12]}",
        trials_dir=tmp_path / "trials",
        environment=EnvironmentConfig(type=EnvironmentType.DOCKER),
        agent=AgentConfig() if seats else AgentConfig(name="oracle"),
        participants=[Participant(seat="p1", name="oracle")] if seats else [],
    )
    async with asyncio.timeout(180):
        trial = await Trial.create(config)
        result = await trial.run()

    assert result.exception_info is None, result.exception_info
    assert result.verifier_result is not None
    assert result.verifier_result.rewards == {"reward": 1.0}
    assert bool(result.seat_results) == seats
    manifest = [
        ArtifactManifestEntry.model_validate(entry)
        for entry in json.loads(trial.paths.artifacts_manifest_path.read_text())
    ]
    entries = {entry.source: (entry.status, entry.phase) for entry in manifest}
    assert len(manifest) == len(entries) == 5
    assert entries["/tmp/play-output.txt"] == ("ok", "pre_verifier")
    assert entries["/logs/verifier/late.txt"] == ("ok", "post_verifier")
    assert entries["/tmp/verifier-output.txt"] == ("ok", "post_verifier")
    assert entries["/tmp/never-created.txt"] == ("failed", None)

    artifacts = trial.paths.artifacts_dir
    assert (artifacts / "tmp/play-output.txt").read_text() == "play\n"
    assert (artifacts / "logs/verifier/late.txt").read_text() == "log\n"
    assert (artifacts / "reports/output.txt").read_text() == "report\n"
    assert (trial.paths.verifier_dir / "late.txt").read_text() == "log\n"
