import importlib
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from harbor.models.job.config import DatasetConfig, JobConfig
from harbor.models.job.result import JobResult, JobStats
from harbor.models.trial.config import AgentConfig, Participant, TaskConfig
from harbor.viewer.server import create_app


def _write_job(
    tmp_path: Path,
    config: JobConfig | None = None,
    *,
    job_name: str = "job-with-progress",
    started_at: datetime | None = None,
) -> Path:
    job_dir = tmp_path / job_name
    job_dir.mkdir()
    started_at = started_at or datetime(2026, 4, 28, 9, 0, tzinfo=timezone.utc)
    result = JobResult(
        id=uuid4(),
        started_at=started_at,
        updated_at=datetime(2026, 4, 28, 9, 5, tzinfo=timezone.utc),
        n_total_trials=4,
        stats=JobStats.from_counts(
            n_total_trials=4,
            n_completed_trials=2,
            n_running_trials=1,
            n_errored_trials=1,
            n_retries=1,
        ),
    )
    config = config or JobConfig(job_name=job_name)
    (job_dir / "result.json").write_text(result.model_dump_json(indent=4))
    (job_dir / "config.json").write_text(config.model_dump_json(indent=4))
    return job_dir


@pytest.mark.unit
def test_job_endpoint_exposes_progress_stats(tmp_path: Path) -> None:
    _write_job(tmp_path)
    client = TestClient(create_app(tmp_path))

    response = client.get("/api/jobs/job-with-progress")

    assert response.status_code == 200
    body = response.json()
    assert body["n_total_trials"] == 4
    assert body["updated_at"] == "2026-04-28T09:05:00Z"
    assert "status" not in body
    assert body["stats"]["n_completed_trials"] == 2
    assert body["stats"]["n_running_trials"] == 1
    assert body["stats"]["n_pending_trials"] == 1
    assert body["stats"]["n_errored_trials"] == 1
    assert body["stats"]["n_retries"] == 1


@pytest.mark.unit
def test_jobs_list_uses_progress_counts(tmp_path: Path) -> None:
    _write_job(tmp_path)
    client = TestClient(create_app(tmp_path))

    response = client.get("/api/jobs")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["n_total_trials"] == 4
    assert item["n_completed_trials"] == 2
    assert item["n_errored_trials"] == 1
    assert "status" not in item


@pytest.mark.unit
def test_jobs_list_sources_include_dataset_task_and_path_labels(
    tmp_path: Path,
) -> None:
    _write_job(
        tmp_path,
        JobConfig(
            job_name="job-with-progress",
            datasets=[DatasetConfig(name="terminal-bench")],
            tasks=[
                TaskConfig(name="harbor/package-task"),
                TaskConfig(path=Path("examples/tasks/local-task")),
            ],
        ),
    )
    client = TestClient(create_app(tmp_path))

    response = client.get("/api/jobs")

    assert response.status_code == 200
    assert response.json()["items"][0]["datasets"] == [
        "harbor/package-task",
        "local-task",
        "terminal-bench",
    ]

    search_response = client.get("/api/jobs", params={"q": "local-task"})
    assert search_response.status_code == 200
    assert search_response.json()["total"] == 1


@pytest.mark.unit
def test_jobs_list_and_filters_use_native_participant_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = "missing_viewer_test_agents"
    import_path = f"{module}.custom:SeatAgent"
    shadowed_path = f"{module}.named:NamedSeatAgent"
    for job_name, config in (
        (
            "native",
            JobConfig(
                agents=[
                    AgentConfig(
                        name="oracle",
                        model_name="unused-provider/unused-model",
                    ),
                ],
                participants=[
                    Participant(
                        seat="first",
                        name="named-agent",
                        import_path=shadowed_path,
                        model_name="local-model",
                    ),
                    Participant(
                        seat="second",
                        import_path=import_path,
                        model_name="seat-provider/chat/v1",
                    ),
                    Participant(
                        seat="third",
                        import_path=import_path,
                        model_name="seat-provider/chat/v1",
                    ),
                ],
            ),
        ),
        (
            "single",
            JobConfig(agents=[AgentConfig(name="codex", model_name="openai/gpt-5")]),
        ),
        ("single-custom", JobConfig(agents=[AgentConfig(import_path=import_path)])),
    ):
        _write_job(tmp_path, config, job_name=job_name)
    importer = Mock(wraps=importlib.import_module)
    monkeypatch.setattr(importlib, "import_module", importer)
    client = TestClient(create_app(tmp_path))

    response = client.get("/api/jobs")
    assert response.status_code == 200
    items = {item["name"]: item for item in response.json()["items"]}
    assert set(items) == {"native", "single", "single-custom"}
    response = client.get("/api/jobs/filters")
    assert response.status_code == 200
    filters = response.json()

    expected_labels = {
        "agents": [
            ("codex", "single"),
            (import_path, "native"),
            ("named-agent", "native"),
        ],
        "providers": [("openai", "single"), ("seat-provider", "native")],
        "models": [
            ("chat/v1", "native"),
            ("gpt-5", "single"),
            ("local-model", "native"),
        ],
    }
    for key, labels in expected_labels.items():
        assert filters[key] == [{"value": label, "count": 1} for label, _ in labels]
        for name, item in items.items():
            assert item[key] == [label for label, job in labels if job == name]
        for label, job in labels:
            for params in ({key.removesuffix("s"): label}, {"q": label.upper()}):
                response = client.get("/api/jobs", params=params)
                assert response.status_code == 200
                assert [item["name"] for item in response.json()["items"]] == [job]

    for key, label in (
        ("agent", "oracle"),
        ("provider", "unused-provider"),
        ("model", "unused-model"),
        ("agent", shadowed_path),
    ):
        for params in ({key: label}, {"q": label}):
            response = client.get("/api/jobs", params=params)
            assert response.status_code == 200
            assert response.json()["total"] == 0
    assert not any(
        (call.args[0] if call.args else call.kwargs["name"]).startswith(module)
        for call in importer.call_args_list
    )


@pytest.mark.unit
def test_jobs_list_keeps_unknown_starts_last(tmp_path: Path) -> None:
    _write_job(
        tmp_path,
        job_name="aware-job",
        started_at=datetime(2026, 4, 28, 9, 0, tzinfo=timezone.utc),
    )
    legacy_dir = _write_job(
        tmp_path,
        job_name="naive-job",
        started_at=datetime(2026, 4, 28, 10, 0),
    )
    legacy_path = legacy_dir / "result.json"
    legacy_bytes = legacy_path.read_bytes()
    client = TestClient(create_app(tmp_path))

    response = client.get("/api/jobs")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["name"] for item in items] == ["aware-job", "naive-job"]
    assert items[1]["started_at"] == json.loads(legacy_bytes)["started_at"]
    assert legacy_path.read_bytes() == legacy_bytes
