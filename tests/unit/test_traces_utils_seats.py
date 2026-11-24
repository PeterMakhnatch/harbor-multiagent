import json
from pathlib import Path

import pytest

from harbor.utils.traces_utils import export_traces

Dataset = pytest.importorskip("datasets").Dataset


_START = "2026-10-19T15:00:00Z"
_JOB_ID = "0a10ef24-8532-4e28-a3d6-e2faa7879e59"
_SUBAGENT = "summarization-1-summary"


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _write_trajectory(
    agent_dir: Path,
    prompt: str,
    answer: str,
    *,
    filename: str = "trajectory.json",
    continuation: str | None = None,
) -> None:
    data = {
        "agent": {"name": "terminus-2", "model_name": "atif-model"},
        "steps": [
            {"source": "user", "message": prompt},
            {"source": "agent", "message": answer},
        ],
    }
    if continuation is not None:
        data["continued_trajectory_ref"] = continuation
    _write_json(agent_dir / filename, data)


def _write_subagent(agent_dir: Path) -> None:
    _write_trajectory(
        agent_dir, "Summarize", "Summary", filename=f"trajectory.{_SUBAGENT}.json"
    )


def _seat_result(
    seat: str, agent: str, provider: str, *, under_test: bool = False
) -> dict:
    return {
        "seat": seat,
        "under_test": under_test,
        "agent_info": {
            "name": agent,
            "version": "test-version",
            "model_info": {"name": f"{seat}-model", "provider": provider},
        },
    }


def _write_seat_trial(job_dir: Path) -> Path:
    trial_dir = job_dir / "nested" / "seat-trial"
    solver_dir = trial_dir / "seats" / "solver" / "agent"
    guide_dir = trial_dir / "seats" / "guide" / "agent"
    _write_trajectory(
        solver_dir, "Solve the task", "Solution", continuation="trajectory.next.json"
    )
    _write_trajectory(
        solver_dir, "Continue", "Continuation", filename="trajectory.next.json"
    )
    _write_trajectory(guide_dir, "Guide the solver", "Guidance")
    _write_subagent(solver_dir)
    _write_subagent(guide_dir)

    (trial_dir / "seats" / "observer" / "agent").mkdir(parents=True)
    for seat in ("unmatched", "undeclared", "ambiguous"):
        _write_trajectory(trial_dir / "seats" / seat / "agent", "Ignore", "Ignore")
    verifier_dir = trial_dir / "verifier"
    verifier_dir.mkdir()
    (verifier_dir / "test-stdout.txt").write_text("Shared verifier output")

    solver = _seat_result("solver", "terminus-2", "solver-provider", under_test=True)
    guide = _seat_result("guide", "fx", "guide-provider")
    guide["exception_info"] = {"exception_type": "AgentTimeoutError"}
    ambiguous = _seat_result("ambiguous", "terminus-2", "ambiguous-provider")
    _write_json(
        trial_dir / "result.json",
        {
            "id": "22222222-2222-4222-8222-222222222222",
            "trial_name": trial_dir.name,
            "task_name": "seated-task",
            "started_at": _START,
            "finished_at": "2026-10-19T15:01:00Z",
            "config": {
                "job_id": _JOB_ID,
                "participants": [
                    {"seat": "solver", "name": "terminus-2"},
                    {"seat": "guide", "name": "fx", "model_name": "guide-config-model"},
                    {"seat": "observer", "name": "nop"},
                    {"seat": "unmatched", "name": "terminus-2"},
                    {"seat": "ambiguous", "name": "terminus-2"},
                ],
            },
            "agent_info": solver["agent_info"],
            "seat_results": [
                guide,
                solver,
                {
                    "seat": "observer",
                    "under_test": False,
                    "agent_info": {"name": "nop"},
                },
                _seat_result("undeclared", "terminus-2", "undeclared-provider"),
                ambiguous,
                ambiguous,
            ],
            "verifier_result": {"rewards": {"reward": 1.0}},
        },
    )
    _write_json(
        job_dir / "result.json",
        {
            "stats": {
                "evals": {
                    "seated-task": {
                        "reward_stats": {"reward": {"0.0": [trial_dir.name]}}
                    }
                }
            }
        },
    )
    return trial_dir


def _write_ordinary_trial(trial_dir: Path, *, agent_name: str = "terminus-2") -> None:
    agent_dir = trial_dir / "agent"
    _write_trajectory(agent_dir, "Ordinary task", "Ordinary answer")
    _write_subagent(agent_dir)
    _write_json(
        trial_dir / "result.json",
        {
            "trial_name": trial_dir.name,
            "task_name": "ordinary-task",
            "config": {"agent": {"name": agent_name}},
            "verifier_result": {"rewards": {"reward": 1.0}},
        },
    )


@pytest.mark.parametrize("has_root_trace", [False, True])
def test_export_seats_uses_trial_provenance_and_matches_recursive_export(
    tmp_path, monkeypatch, has_root_trace
):
    trial_dir = _write_seat_trial(tmp_path)
    if has_root_trace:
        _write_trajectory(
            trial_dir / "agent", "Do not use this prompt", "Do not export"
        )
    options = {
        "include_instruction": True,
        "include_verifier_output": True,
        "merge_subagents": False,
        "success_filter": "success",
        "use_rich_progress": False,
    }

    root_export = export_traces(trial_dir, recursive=False, **options)
    job_export = export_traces(tmp_path, **options)
    monkeypatch.chdir(trial_dir)
    cwd_export = export_traces(Path("."), recursive=False, **options)

    assert isinstance(root_export, dict)
    assert isinstance(job_export, dict)
    assert isinstance(cwd_export, dict)
    assert set(root_export) == set(job_export) == {"main", _SUBAGENT}
    for name, dataset in root_export.items():
        assert isinstance(dataset, Dataset)
        assert dataset.to_list() == job_export[name].to_list()
        assert dataset.to_list() == cwd_export[name].to_list()

    main = root_export["main"]
    assert len(main) == 3
    assert list(zip(main["seat"], main["episode"], strict=True)) == [
        ("solver", "episode-0"),
        ("solver", "episode-1"),
        ("guide", "episode-0"),
    ]
    assert main["agent"][:] == ["terminus-2", "terminus-2", "fx"]
    assert main["model"][:] == ["solver-model", "solver-model", "guide-model"]
    assert main["model_provider"][:] == [
        "solver-provider",
        "solver-provider",
        "guide-provider",
    ]
    assert main["under_test"][:] == [True, True, False]
    assert main["instruction"][:] == [
        "Solve the task",
        "Solve the task",
        "Guide the solver",
    ]
    assert main[1]["conversations"][-1]["content"] == "Continuation"
    for dataset in root_export.values():
        assert set(dataset["result"]) == {"1.0"}
        assert set(dataset["verifier_output"]) == {"Shared verifier output"}
        assert set(dataset["trial_name"]) == {trial_dir.name}
        assert set(dataset["task"]) == {"seated-task"}
        assert set(dataset["trial_id"]) == {"22222222-2222-4222-8222-222222222222"}
        assert set(dataset["date"]) == {_START}
        assert set(dataset["run_id"]) == {_JOB_ID}

    subagents = root_export[_SUBAGENT]
    assert subagents["seat"][:] == ["solver", "guide"]
    assert subagents["model_provider"][:] == ["solver-provider", "guide-provider"]
    assert subagents["under_test"][:] == [True, False]
    assert subagents["instruction"][:] == ["Solve the task", "Guide the solver"]

    failure = export_traces(
        trial_dir, success_filter="failure", use_rich_progress=False
    )
    assert isinstance(failure, Dataset)
    assert len(failure) == 0

    last = export_traces(
        trial_dir, episodes="last", export_subagents=False, use_rich_progress=False
    )
    assert isinstance(last, Dataset)
    assert list(zip(last["seat"], last["episode"], strict=True)) == [
        ("solver", "episode-1"),
        ("guide", "episode-0"),
    ]


@pytest.mark.parametrize("chunk_size", [None, 1, 2])
@pytest.mark.parametrize("merge_subagents", [False, True])
def test_export_preserves_seat_columns_after_ordinary_rows(
    tmp_path, chunk_size, merge_subagents
):
    ordinary = tmp_path / "ordinary-trial"
    _write_ordinary_trial(ordinary)
    trial_dir = _write_seat_trial(tmp_path)

    exported = export_traces(
        tmp_path,
        chunk_size=chunk_size,
        merge_subagents=merge_subagents,
        use_rich_progress=False,
    )

    if merge_subagents:
        assert isinstance(exported, Dataset)
        datasets = {
            source: [row for row in exported if row["trace_source"] == source]
            for source in ("main", _SUBAGENT)
        }
    else:
        assert isinstance(exported, dict)
        datasets = {source: dataset.to_list() for source, dataset in exported.items()}
    expected_seats = {
        "main": ["solver", "solver", "guide"],
        _SUBAGENT: ["solver", "guide"],
    }
    for source, seats in expected_seats.items():
        rows = datasets[source]
        assert rows[0]["trial_name"] == ordinary.name
        assert rows[0]["seat"] is None
        assert rows[0]["under_test"] is None
        assert rows[0]["trial_id"] is None
        assert [row["trial_name"] for row in rows[1:]] == [trial_dir.name] * len(seats)
        assert [row["seat"] for row in rows[1:]] == seats
        assert {row["trial_id"] for row in rows[1:]} == {
            "22222222-2222-4222-8222-222222222222"
        }
        assert [row["under_test"] for row in rows[1:]] == [
            seat == "solver" for seat in seats
        ]

    ordinary_export = export_traces(
        ordinary, export_subagents=False, use_rich_progress=False
    )
    assert isinstance(ordinary_export, Dataset)
    assert ordinary_export[0]["model"] == "atif-model"
    assert "seat" not in ordinary_export.column_names
    assert "under_test" not in ordinary_export.column_names


def test_export_preserves_ordinary_non_atif_error(tmp_path):
    _write_ordinary_trial(tmp_path, agent_name="nop")

    with pytest.raises(NotImplementedError, match="nop.*ATIF"):
        export_traces(tmp_path, use_rich_progress=False)


def test_export_preserves_recursive_step_discovery(tmp_path):
    trial_dir = tmp_path / "step-trial"
    _write_json(
        trial_dir / "result.json",
        {
            "trial_name": trial_dir.name,
            "task_name": "step-task",
            "config": {"agent": {"name": "terminus-2"}},
            "verifier_result": {"rewards": {"reward": 1.0}},
        },
    )
    for step in ("prepare", "finish"):
        _write_trajectory(trial_dir / "steps" / step / "agent", step, "Done")

    exported = export_traces(tmp_path, use_rich_progress=False)

    assert isinstance(exported, Dataset)
    assert len(exported) == 2
    assert {row["conversations"][0]["content"] for row in exported} == {
        "prepare",
        "finish",
    }
    assert set(exported["trial_name"]) == {trial_dir.name}
    assert set(exported["result"]) == {"1.0"}
    assert "seat" not in exported.column_names


@pytest.mark.parametrize("has_partial_result", [False, True])
def test_export_does_not_inherit_job_metadata_for_unfinished_seats(
    tmp_path, has_partial_result
):
    trial_dir = tmp_path / "unfinished-trial"
    (trial_dir / "agent").mkdir(parents=True)
    config = {"participants": [{"seat": "solver", "name": "terminus-2"}]}
    _write_json(trial_dir / "config.json", config)
    if has_partial_result:
        _write_json(
            trial_dir / "result.json",
            {
                "config": config,
                "finished_at": None,
                "seat_results": [
                    _seat_result(
                        "solver", "terminus-2", "solver-provider", under_test=True
                    )
                ],
            },
        )
    _write_trajectory(trial_dir / "seats" / "solver" / "agent", "Solve", "Incomplete")
    _write_json(
        tmp_path / "result.json",
        {"config": {"agent": {"name": "terminus-2"}}, "stats": {}},
    )

    for root in (trial_dir, tmp_path):
        exported = export_traces(root, use_rich_progress=False)
        assert isinstance(exported, Dataset)
        assert len(exported) == 0
