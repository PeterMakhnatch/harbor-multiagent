import json
from pathlib import Path

import pytest

from harbor.analyze.analyzer import assemble_analyze_task
from harbor.analyze.models import load_rubric

TEMPLATE = (
    "Analyze {trial_path}.\n"
    "Trajectory: {trial_path}/agent/trajectory.json\n\n"
    "{task_section}\n\n{criteria_guidance}"
)


def _assemble(trial_dir: Path, task_dir: Path, dest: Path) -> str:
    assemble_analyze_task(
        trial_dir=trial_dir,
        task_dir=task_dir,
        rubric=load_rubric(None),
        template=TEMPLATE,
        output_schema={"type": "object"},
        dest=dest,
    )
    return (dest / "instruction.md").read_text()


def _make_trial(tmp_path: Path, result: dict) -> Path:
    trial_dir = tmp_path / "trial__seat"
    trial_dir.mkdir()
    (trial_dir / "trial.log").write_text("")
    (trial_dir / "result.json").write_text(json.dumps(result))
    return trial_dir


def _make_task(tmp_path: Path) -> Path:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "task.toml").write_text("")
    return task_dir


@pytest.mark.unit
def test_assemble_analyze_task_surfaces_seat_trajectories_and_instructions(tmp_path):
    trial_dir = _make_trial(tmp_path, {"task_name": "t", "seat_results": []})
    for seat in ("chef0", "chef1"):
        agent_dir = trial_dir / "seats" / seat / "agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "trajectory.json").write_text("{}")
    task_dir = _make_task(tmp_path)
    (task_dir / "instructions").mkdir()
    for seat in ("chef0", "chef1"):
        (task_dir / "instructions" / f"{seat}.md").write_text(f"Cook as {seat}.")

    dest = tmp_path / "wrapper"
    text = _assemble(trial_dir, task_dir, dest)

    assert (dest / "environment" / "trial" / "seats" / "chef0").is_dir()
    assert (dest / "environment" / "trial" / "seats" / "chef1").is_dir()
    assert "/app/trial/seats/chef0/agent/trajectory.json" in text
    assert "/app/trial/seats/chef1/agent/trajectory.json" in text
    assert text.index("chef0/agent/trajectory.json") < text.index(
        "chef1/agent/trajectory.json"
    )
    assert "seat_results" in text
    assert "/app/task/instructions/" in text
    assert "/app/task/instruction.md" not in text


@pytest.mark.unit
def test_assemble_analyze_task_discovers_seats_from_result_json_when_dirs_absent(
    tmp_path,
):
    trial_dir = _make_trial(
        tmp_path,
        {"task_name": "t", "seat_results": [{"seat": "p1"}, {"seat": "p2"}]},
    )
    task_dir = _make_task(tmp_path)
    (task_dir / "instruction.md").write_text("Play.")

    text = _assemble(trial_dir, task_dir, tmp_path / "wrapper")

    assert "seats/p1/agent/trajectory.json" in text
    assert "seats/p2/agent/trajectory.json" in text


@pytest.mark.unit
def test_assemble_analyze_task_preserves_single_agent_prompt(tmp_path):
    trial_dir = _make_trial(tmp_path, {"task_name": "t"})
    task_dir = _make_task(tmp_path)
    (task_dir / "instruction.md").write_text("Do the thing.")

    text = _assemble(trial_dir, task_dir, tmp_path / "wrapper")

    assert "/app/task/instruction.md" in text
    assert "/app/trial/agent/trajectory.json" in text
    assert "seats/" not in text
