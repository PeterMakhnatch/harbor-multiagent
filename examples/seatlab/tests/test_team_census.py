import importlib
import json
import sys
import types
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

with pytest.MonkeyPatch.context() as patch:
    patch.syspath_prepend(str(Path(__file__).resolve().parent))
    patch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    driver = importlib.import_module("team_driver")
    census = importlib.import_module("team_census")
    test_report_helpers = importlib.import_module("test_team_report")

_JOB_ID = "1aa78f1f-fb60-4ac5-9912-b352cb673e4f"
_TRIAL_A = "10e6cc6c-3107-4ad4-86cf-b91d3951b498"
_TRIAL_B = "22e6cc6c-3107-4ad4-86cf-b91d3951b499"
_TRIAL_C = "33e6cc6c-3107-4ad4-86cf-b91d3951b400"


def _raw_trial(
    source_trial_dir: str,
    *,
    trial_id: str | None = _TRIAL_A,
    job_id: str | None = None,
    primary_reward: float | None = 1.0,
    reward_valid: bool | None = True,
    inventory: dict[str, int | None] | None = None,
    dispatch: dict[str, int | None] | None = None,
) -> Any:
    return census.RawTrialSnapshot(
        source_trial_dir=source_trial_dir,
        trial_id=trial_id,
        job_id=job_id,
        primary_reward=primary_reward,
        reward_valid=reward_valid,
        started_at="2026-11-19T00:00:00Z",
        finished_at="2026-11-19T00:00:05Z",
        seat_usage={
            "inventory": inventory
            if inventory is not None
            else {"n_input_tokens": 0, "n_cache_tokens": 0, "n_output_tokens": 0},
            "dispatch": dispatch
            if dispatch is not None
            else {"n_input_tokens": 0, "n_cache_tokens": 0, "n_output_tokens": 0},
        },
    )


def _slot(
    cell_id: str,
    *,
    seed: int = 2001,
    condition: str = "fresh",
    pairing: str = "SS",
    attempted: bool = True,
    team_status: str = "measured",
    team_error: str | None = None,
    verified_trial_id: str | None = _TRIAL_A,
    source_trial_dir: str | None = "/private/study/cells/s2001-fresh-SS/trials/t1",
    raw_trial: Any = None,
    raw_load_error: str | None = None,
) -> Any:
    return census.SlotObservation(
        cell_id=cell_id,
        seed=seed,
        condition=condition,
        pairing=pairing,
        model_actors=pairing.count("M"),
        attempted=attempted,
        team_status=team_status,
        team_error=team_error,
        verified_trial_id=verified_trial_id,
        source_trial_dir=source_trial_dir,
        raw_trial=raw_trial,
        raw_load_error=raw_load_error,
    )


class _FakeArrowTable:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def to_pylist(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def to_arrow_table(self) -> _FakeArrowTable:
        return _FakeArrowTable(self._rows)


class _FakeConnection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.closed = False

    def execute(self, _sql: str) -> _FakeResult:
        return _FakeResult(self._rows)

    def close(self) -> None:
        self.closed = True


@contextmanager
def _fake_evallab_modules(
    monkeypatch: pytest.MonkeyPatch,
    repo_root: Path,
    *,
    load_trial_fn: Any,
    connect_trials_fn: Any,
) -> Any:
    evallab_pkg = types.ModuleType("evallab")
    evallab_pkg.__file__ = str(repo_root / "src" / "evallab" / "__init__.py")
    evallab_pkg.__path__ = [str(repo_root / "src" / "evallab")]

    results_mod = types.ModuleType("evallab.results")
    results_mod.load_trial = load_trial_fn  # type: ignore[attr-defined]

    storage_pkg = types.ModuleType("evallab.storage")
    storage_pkg.__path__ = [str(repo_root / "src" / "evallab" / "storage")]

    trials_mod = types.ModuleType("evallab.storage.trials")
    trials_mod.connect_trials = connect_trials_fn  # type: ignore[attr-defined]

    evallab_pkg.results = results_mod  # type: ignore[attr-defined]
    evallab_pkg.storage = storage_pkg  # type: ignore[attr-defined]
    storage_pkg.trials = trials_mod  # type: ignore[attr-defined]

    for name, module in (
        ("evallab", evallab_pkg),
        ("evallab.results", results_mod),
        ("evallab.storage", storage_pkg),
        ("evallab.storage.trials", trials_mod),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    yield


def test_null_vs_zero_usage_and_counts_preserve_missing() -> None:
    zero_slot = _slot(
        "s2001-fresh-SS",
        pairing="SS",
        verified_trial_id=_TRIAL_A,
        source_trial_dir="/private/study/t-a",
        raw_trial=_raw_trial(
            "/private/study/t-a",
            trial_id=_TRIAL_A,
            primary_reward=0.0,
            inventory={"n_input_tokens": 0, "n_cache_tokens": 0, "n_output_tokens": 0},
            dispatch={"n_input_tokens": 0, "n_cache_tokens": 0, "n_output_tokens": 0},
        ),
    )
    partial_slot = _slot(
        "s2001-fresh-SM",
        pairing="SM",
        verified_trial_id=_TRIAL_B,
        source_trial_dir="/private/study/t-b",
        raw_trial=_raw_trial(
            "/private/study/t-b",
            trial_id=_TRIAL_B,
            primary_reward=0.75,
            inventory={
                "n_input_tokens": None,
                "n_cache_tokens": None,
                "n_output_tokens": None,
            },
            dispatch={
                "n_input_tokens": 120,
                "n_cache_tokens": 0,
                "n_output_tokens": None,
            },
        ),
    )
    unattempted_slot = _slot(
        "s2003-fresh-MM",
        seed=2003,
        pairing="MM",
        attempted=False,
        team_status="not_attempted",
        verified_trial_id=None,
        source_trial_dir=None,
        raw_trial=None,
    )

    result = census.compare_census([zero_slot, partial_slot, unattempted_slot], [])
    slots = {row["cell_id"]: row for row in result["slots"]}

    # Zero is a known numeric value, never collapsed into null.
    assert slots["s2001-fresh-SS"]["native_shared_reward"] == 0.0
    assert slots["s2001-fresh-SS"]["seats"]["inventory"] == {
        "n_input_tokens": 0,
        "n_cache_tokens": 0,
        "n_output_tokens": 0,
    }
    # Missing stays null and is never coerced to 0.
    assert slots["s2001-fresh-SM"]["seats"]["inventory"]["n_input_tokens"] is None
    assert slots["s2001-fresh-SM"]["seats"]["dispatch"]["n_cache_tokens"] == 0
    assert slots["s2001-fresh-SM"]["seats"]["dispatch"]["n_output_tokens"] is None
    assert slots["s2003-fresh-MM"]["registered_team_status"] == "unavailable"
    assert slots["s2003-fresh-MM"]["native_shared_reward"] is None
    assert slots["s2003-fresh-MM"]["seats"]["dispatch"]["n_input_tokens"] is None

    # Shared reward is never duplicated onto per-role records.
    for row in result["slots"]:
        for role in ("inventory", "dispatch"):
            assert "reward" not in row["seats"][role]
            assert "cost_usd" not in row["seats"][role]

    inv_usage = result["role_usage"]["inventory"]
    assert inv_usage["slots_with_known_input_tokens"] == 1
    assert inv_usage["slots_with_null_input_tokens"] == 2
    assert inv_usage["known_input_tokens"] == 0

    disp_usage = result["role_usage"]["dispatch"]
    assert disp_usage["slots_with_known_input_tokens"] == 2
    assert disp_usage["slots_with_null_input_tokens"] == 1
    assert disp_usage["known_input_tokens"] == 120
    assert disp_usage["slots_with_known_cache_tokens"] == 2
    assert disp_usage["known_cache_tokens"] == 0
    assert disp_usage["slots_with_known_output_tokens"] == 1
    assert disp_usage["slots_with_null_output_tokens"] == 2
    assert disp_usage["known_output_tokens"] == 0

    # All-null metric aggregates to null, never 0.
    all_null = census.compare_census([unattempted_slot], [])
    assert all_null["role_usage"]["inventory"]["known_input_tokens"] is None
    assert all_null["role_usage"]["inventory"]["slots_with_known_input_tokens"] == 0
    assert all_null["role_usage"]["inventory"]["slots_with_null_input_tokens"] == 1


def test_reused_native_trial_id_is_rejected_and_not_counted_as_measured() -> None:
    first = _slot(
        "s2001-fresh-SS",
        verified_trial_id=_TRIAL_A,
        source_trial_dir="/private/study/t1",
        raw_trial=_raw_trial(
            "/private/study/t1",
            trial_id=_TRIAL_A,
            job_id=_JOB_ID,
            primary_reward=1.0,
            inventory={"n_input_tokens": 10, "n_cache_tokens": 0, "n_output_tokens": 5},
        ),
    )
    second = _slot(
        "s2001-fresh-SM",
        pairing="SM",
        verified_trial_id=_TRIAL_A,
        source_trial_dir="/private/study/t2",
        raw_trial=_raw_trial(
            "/private/study/t2",
            trial_id=_TRIAL_A,
            job_id=_JOB_ID,
            primary_reward=1.0,
            inventory={"n_input_tokens": 20, "n_cache_tokens": 0, "n_output_tokens": 8},
        ),
    )
    census_rows = [
        census.CensusRowSnapshot(
            source_trial_dir="/private/study/t1",
            trial_id=_TRIAL_A,
            job_id=_JOB_ID,
            reward=1.0,
            projection_error=None,
            raw_trial_id_present=True,
        )
    ]

    result = census.compare_census([first, second], census_rows)
    assert result["counts"]["measured"] == 0
    assert result["counts"]["unavailable"] == 2
    assert result["counts"]["invalid"] == 2
    assert result["counts"]["with_reused_native_trial_id"] == 2
    assert result["counts"]["with_native_shared_reward"] == 0
    assert result["counts"]["census_matched_by_uuid"] == 0
    assert result["counts"]["census_matched_by_job_and_uuid"] == 0
    assert result["role_usage"]["inventory"]["known_input_tokens"] is None

    for row in result["slots"]:
        assert row["registered_team_status"] == "unavailable"
        assert row["detail_status"] == "invalid"
        assert row["detail_reason"] == "duplicate_native_trial_id"
        assert row["native_trial_id"] == _TRIAL_A
        assert row["native_trial_id_reused"] is True
        assert row["native_shared_reward"] is None
        assert row["census"]["uuid_matches"] == 0
        assert row["census"]["job_uuid_matches"] == 0
        assert row["census"]["reward"] is None


def test_invalid_attempt_is_never_a_score_even_if_raw_has_primary_reward() -> None:
    invalid_slot = _slot(
        "s2003-fresh-MM",
        seed=2003,
        pairing="MM",
        attempted=True,
        team_status="invalid",
        team_error="model_invalid_or_refused_decisions",
        verified_trial_id=_TRIAL_A,
        source_trial_dir="/private/study/invalid",
        raw_trial=_raw_trial(
            "/private/study/invalid",
            trial_id=_TRIAL_A,
            job_id=None,
            primary_reward=0.5,
            reward_valid=False,
            inventory={
                "n_input_tokens": 1258,
                "n_cache_tokens": 0,
                "n_output_tokens": 574,
            },
            dispatch={
                "n_input_tokens": 1512,
                "n_cache_tokens": 0,
                "n_output_tokens": 381,
            },
        ),
    )
    # Even if a raw trial somehow had reward_valid=True, invalid team_status blocks scoring.
    invalid_with_raw_valid = _slot(
        "s2003-fresh-MS",
        seed=2003,
        pairing="MS",
        attempted=True,
        team_status="invalid",
        team_error="native_world_mismatch",
        verified_trial_id=_TRIAL_B,
        source_trial_dir="/private/study/invalid-2",
        raw_trial=_raw_trial(
            "/private/study/invalid-2",
            trial_id=_TRIAL_B,
            job_id=_JOB_ID,
            primary_reward=1.0,
            reward_valid=True,
        ),
    )
    census_rows = [
        census.CensusRowSnapshot(
            source_trial_dir="/private/study/invalid",
            trial_id=_TRIAL_A,
            job_id=None,
            reward=None,
            projection_error="unknown native identity; no projection attempted",
            raw_trial_id_present=True,
        ),
        census.CensusRowSnapshot(
            source_trial_dir="/private/study/invalid-2",
            trial_id=_TRIAL_B,
            job_id=_JOB_ID,
            reward=1.0,
            projection_error=None,
            raw_trial_id_present=True,
        ),
    ]

    result = census.compare_census([invalid_slot, invalid_with_raw_valid], census_rows)
    assert result["counts"]["measured"] == 0
    assert result["counts"]["unavailable"] == 2
    assert result["counts"]["invalid"] == 2
    assert result["counts"]["with_native_shared_reward"] == 0
    assert result["counts"]["census_rewards_matching_native"] == 0

    rows = {row["cell_id"]: row for row in result["slots"]}
    first = rows["s2003-fresh-MM"]
    assert first["registered_team_status"] == "unavailable"
    assert first["detail_reason"] == "model_invalid_or_refused_decisions"
    assert first["native_shared_reward"] is None
    assert first["census"]["error"] == "unknown_native_identity"
    # Known seat token usage from the invalid attempt is still accounted for honestly.
    assert first["seats"]["inventory"]["n_input_tokens"] == 1258
    assert first["seats"]["dispatch"]["n_output_tokens"] == 381

    second = rows["s2003-fresh-MS"]
    assert second["registered_team_status"] == "unavailable"
    assert second["native_shared_reward"] is None
    assert second["census"]["reward"] == 1.0
    assert second["census"]["reward_matches_native"] is False


def test_path_only_match_is_not_job_uuid_join_and_rejects_synthetic_census_id() -> None:
    standalone_path = "/private/study/cells/s2001-fresh-SS/trials/seatlab-team-abc"
    job_backed_path = "/private/study/cells/s2001-fresh-SM/trials/seatlab-team-def"
    synthetic_path = "/private/study/cells/s2001-fresh-MS/trials/seatlab-team-ghi"

    standalone = _slot(
        "s2001-fresh-SS",
        verified_trial_id=_TRIAL_A,
        source_trial_dir=standalone_path,
        raw_trial=_raw_trial(
            standalone_path, trial_id=_TRIAL_A, job_id=None, primary_reward=0.75
        ),
    )
    job_backed = _slot(
        "s2001-fresh-SM",
        pairing="SM",
        verified_trial_id=_TRIAL_B,
        source_trial_dir=job_backed_path,
        raw_trial=_raw_trial(
            job_backed_path, trial_id=_TRIAL_B, job_id=_JOB_ID, primary_reward=0.5
        ),
    )
    synthetic_target = _slot(
        "s2001-fresh-MS",
        pairing="MS",
        verified_trial_id=_TRIAL_C,
        source_trial_dir=synthetic_path,
        raw_trial=_raw_trial(
            synthetic_path, trial_id=_TRIAL_C, job_id=None, primary_reward=1.0
        ),
    )

    census_rows = [
        # Standalone trial: exact path + UUID match, null job_id -> NOT a job+uuid join.
        census.CensusRowSnapshot(
            source_trial_dir=standalone_path,
            trial_id=_TRIAL_A,
            job_id=None,
            reward=None,
            projection_error="unknown native identity; no projection attempted",
            raw_trial_id_present=True,
        ),
        # Real job-backed control copied to a different path: job+uuid join succeeds without path match.
        census.CensusRowSnapshot(
            source_trial_dir="/private/other-root/copied-job/seatlab-team-def",
            trial_id=_TRIAL_B,
            job_id=_JOB_ID,
            reward=0.5,
            projection_error=None,
            raw_trial_id_present=True,
        ),
        # Synthetic non-UUID census trial ID at exact path -> flagged as path_identity_conflict.
        census.CensusRowSnapshot(
            source_trial_dir=synthetic_path,
            trial_id=None,
            job_id=None,
            reward=1.0,
            projection_error="some unanticipated error string",
            raw_trial_id_present=True,
        ),
    ]

    result = census.compare_census(
        [standalone, job_backed, synthetic_target], census_rows
    )
    rows = {row["cell_id"]: row for row in result["slots"]}

    sa = rows["s2001-fresh-SS"]
    assert sa["native_job_id"] is None
    assert sa["census"]["path_matches"] == 1
    assert sa["census"]["uuid_matches"] == 1
    assert sa["census"]["job_uuid_matches"] == 0
    assert sa["census"]["path_identity_conflict"] is False
    assert sa["census"]["reward"] is None
    assert sa["census"]["error"] == "unknown_native_identity"

    jb = rows["s2001-fresh-SM"]
    assert jb["native_job_id"] == _JOB_ID
    assert jb["census"]["path_matches"] == 0
    assert jb["census"]["uuid_matches"] == 1
    assert jb["census"]["job_uuid_matches"] == 1
    assert jb["census"]["reward"] == 0.5
    assert jb["census"]["reward_matches_native"] is True
    assert jb["census"]["error"] is None

    syn = rows["s2001-fresh-MS"]
    assert syn["native_trial_id"] == _TRIAL_C
    assert syn["census"]["path_matches"] == 1
    assert syn["census"]["uuid_matches"] == 0
    assert syn["census"]["job_uuid_matches"] == 0
    assert syn["census"]["path_identity_conflict"] is True
    assert syn["census"]["reward"] is None
    assert syn["census"]["error"] == "unknown_projection_error"

    assert result["counts"]["census_matched_by_path"] == 2
    assert result["counts"]["census_matched_by_uuid"] == 2
    assert result["counts"]["census_matched_by_job_and_uuid"] == 1
    assert result["counts"]["census_path_identity_conflicts"] == 1
    assert result["counts"]["census_errors_by_code"] == {
        "unknown_native_identity": 1,
        "unknown_projection_error": 1,
    }

    # Public JSON must not leak local paths.
    encoded = json.dumps(result)
    assert "/private/" not in encoded
    assert "source_trial_dir" not in encoded


def test_normalize_trial_record_handles_datamodel_objects_and_raw_dicts(
    tmp_path: Path,
) -> None:
    trial_dir = (tmp_path / "trial-a").resolve()
    trial_dir.mkdir()
    started = datetime(2026, 11, 19, 12, 0, 0, tzinfo=UTC)
    finished = datetime(2026, 11, 19, 12, 1, 0, tzinfo=UTC)

    record_from_attr = SimpleNamespace(
        id=UUID(_TRIAL_A),
        primary_reward=0.5,
        result={
            "id": UUID(_TRIAL_A),
            "reward_valid": True,
            "started_at": started,
            "finished_at": finished,
            "config": {"job_id": UUID(_JOB_ID)},
        },
        seat_results=[
            SimpleNamespace(
                seat="inventory",
                agent_result=SimpleNamespace(
                    n_input_tokens=0,
                    n_cache_tokens=0,
                    n_output_tokens=0,
                    cost_usd=0.0,
                ),
            ),
            SimpleNamespace(
                seat="dispatch",
                agent_result=SimpleNamespace(
                    n_input_tokens=42,
                    n_cache_tokens=None,
                    n_output_tokens=7,
                    cost_usd=0.01,
                ),
            ),
        ],
    )
    snap = census.normalize_trial_record(record_from_attr, trial_dir)
    assert snap.trial_id == _TRIAL_A
    assert snap.job_id == _JOB_ID
    assert snap.primary_reward == 0.5
    assert snap.reward_valid is True
    assert snap.started_at == "2026-11-19T12:00:00+00:00"
    assert snap.finished_at == "2026-11-19T12:01:00+00:00"
    assert snap.seat_usage["inventory"] == {
        "n_input_tokens": 0,
        "n_cache_tokens": 0,
        "n_output_tokens": 0,
    }
    assert snap.seat_usage["dispatch"] == {
        "n_input_tokens": 42,
        "n_cache_tokens": None,
        "n_output_tokens": 7,
    }

    # Stock Eval Lab TrialRecord has no .seat_results attr and stores seats in .result["seat_results"].
    record_from_raw = SimpleNamespace(
        id="synthetic-non-uuid-id",
        primary_reward=None,
        result={
            "id": "synthetic-non-uuid-id",
            "config": {"job_id": None},
            "seat_results": [
                {
                    "seat": "inventory",
                    "agent_result": {
                        "n_input_tokens": 11,
                        "n_cache_tokens": 2,
                        "n_output_tokens": 3,
                    },
                }
            ],
        },
    )
    snap_raw = census.normalize_trial_record(record_from_raw, trial_dir)
    assert snap_raw.trial_id is None
    assert snap_raw.job_id is None
    assert snap_raw.primary_reward is None
    assert snap_raw.seat_usage["inventory"]["n_input_tokens"] == 11
    assert snap_raw.seat_usage["dispatch"]["n_input_tokens"] is None


def test_main_rejects_nonempty_scratch_dir_and_prints_public_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = (tmp_path / "study").resolve()
    plan = driver._register(root)
    measured_cell = test_report_helpers._cell(plan, "SS", seed=2001, condition="fresh")
    test_report_helpers.NativeFixture(root, plan, measured_cell).write()

    fake_repo = (tmp_path / "fake-evallab").resolve()
    (fake_repo / "src" / "evallab").mkdir(parents=True)

    nonempty = (tmp_path / "nonempty").resolve()
    nonempty.mkdir()
    (nonempty / "marker.txt").write_text("existing", encoding="utf-8")

    def fake_load_trial(trial_dir: Path) -> Any:
        raw = json.loads((trial_dir / "result.json").read_text(encoding="utf-8"))
        rewards = (raw.get("verifier_result") or {}).get("rewards") or {}
        primary = rewards.get("reward")
        return SimpleNamespace(
            id=str(raw["id"]),
            primary_reward=float(primary) if primary is not None else None,
            result=raw,
            config={},
        )

    def fake_connect_trials(
        *,
        repo_root: Path,
        roots: list[Path],
        derived_root: Path,
        read_only: bool = False,
    ) -> tuple[_FakeConnection, dict[str, Any]]:
        assert read_only is True
        return _FakeConnection([]), {"n_rows": 0}

    with _fake_evallab_modules(
        monkeypatch,
        fake_repo,
        load_trial_fn=fake_load_trial,
        connect_trials_fn=fake_connect_trials,
    ):
        with pytest.raises(SystemExit) as excinfo:
            census.main(
                [
                    "--study",
                    str(root),
                    "--evallab-root",
                    str(fake_repo),
                    "--scratch-dir",
                    str(nonempty),
                ]
            )
        assert excinfo.value.code == 2

        exit_code = census.main(
            ["--study", str(root), "--evallab-root", str(fake_repo)]
        )
    assert exit_code == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["schema"] == census.SCHEMA
    assert parsed["counts"]["registered"] == 16
    assert parsed["counts"]["measured"] == 1
    assert parsed["counts"]["unavailable"] == 15


def test_inspect_study_passes_only_existing_positive_dirs_and_read_only_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "study").resolve()
    plan = driver._register(root)
    measured_cell = test_report_helpers._cell(plan, "SS", seed=2001, condition="fresh")
    invalid_cell = test_report_helpers._cell(plan, "MM", seed=2003, condition="fresh")

    measured_fixture = test_report_helpers.NativeFixture(root, plan, measured_cell)
    measured_fixture.write()

    invalid_fixture = test_report_helpers.NativeFixture(root, plan, invalid_cell)
    invalid_dispatch = invalid_fixture.seat("dispatch")
    invalid_dispatch["exception_info"] = {
        "exception_message": f"secret {root} raw model text sk-ant-secret"
    }
    invalid_dispatch["agent_result"]["metadata"].update(
        stage="invalid_or_refused_decisions", view_sha256=None
    )
    invalid_fixture.native.update(reward_valid=False, verifier_result=None)
    invalid_fixture.write()

    fake_repo = (tmp_path / "fake-evallab").resolve()
    (fake_repo / "src" / "evallab").mkdir(parents=True)
    scratch = (tmp_path / "scratch-new").resolve()

    seen_connect: dict[str, Any] = {}
    loaded_paths: list[Path] = []

    def fake_load_trial(trial_dir: Path) -> Any:
        loaded_paths.append(trial_dir)
        raw = json.loads((trial_dir / "result.json").read_text(encoding="utf-8"))
        rewards = (raw.get("verifier_result") or {}).get("rewards") or {}
        primary = rewards.get("reward")
        return SimpleNamespace(
            id=str(raw["id"]),
            primary_reward=float(primary) if primary is not None else None,
            result=raw,
            config={},
        )

    def fake_connect_trials(
        *,
        repo_root: Path,
        roots: list[Path],
        derived_root: Path,
        read_only: bool = False,
    ) -> tuple[_FakeConnection, dict[str, Any]]:
        seen_connect.update(
            repo_root=repo_root,
            roots=list(roots),
            derived_root=derived_root,
            read_only=read_only,
        )
        rows = []
        for trial_dir in roots:
            raw = json.loads((trial_dir / "result.json").read_text(encoding="utf-8"))
            rows.append(
                {
                    "source_trial_dir": str(trial_dir.resolve()),
                    "trial_id": raw["id"],
                    "job_id": None,
                    "reward": None,
                    "projection_error": "unknown native identity; no projection attempted",
                }
            )
        return _FakeConnection(rows), {"n_rows": len(rows)}

    with _fake_evallab_modules(
        monkeypatch,
        fake_repo,
        load_trial_fn=fake_load_trial,
        connect_trials_fn=fake_connect_trials,
    ):
        output = census.inspect_study(root, fake_repo, scratch)

    expected_dirs = [measured_fixture.trial.resolve(), invalid_fixture.trial.resolve()]
    assert seen_connect["repo_root"] == fake_repo
    assert seen_connect["read_only"] is True
    assert seen_connect["derived_root"] == scratch
    assert seen_connect["roots"] == expected_dirs
    assert loaded_paths == expected_dirs
    assert scratch.is_dir() and list(scratch.iterdir()) == []

    assert output["counts"]["registered"] == 16
    assert output["counts"]["attempted"] == 2
    assert output["counts"]["measured"] == 1
    assert output["counts"]["unavailable"] == 15
    assert output["counts"]["invalid"] == 1
    assert output["counts"]["not_attempted"] == 14
    assert output["counts"]["census_matched_by_path"] == 2
    assert output["counts"]["census_matched_by_uuid"] == 2
    assert output["counts"]["census_matched_by_job_and_uuid"] == 0
    assert output["counts"]["census_errors_by_code"] == {"unknown_native_identity": 2}

    by_cell = {row["cell_id"]: row for row in output["slots"]}
    assert by_cell[measured_cell["cell_id"]]["registered_team_status"] == "measured"
    assert by_cell[measured_cell["cell_id"]]["native_shared_reward"] == 1.0
    assert by_cell[invalid_cell["cell_id"]]["registered_team_status"] == "unavailable"
    assert by_cell[invalid_cell["cell_id"]]["native_shared_reward"] is None
    assert (
        by_cell[invalid_cell["cell_id"]]["detail_reason"]
        == "model_invalid_or_refused_decisions"
    )

    serialized = json.dumps(output)
    assert str(root) not in serialized
    assert str(fake_repo) not in serialized
    assert str(scratch) not in serialized
    assert "sk-ant-secret" not in serialized
    assert "raw model text" not in serialized
    assert "cost_usd" not in serialized
    assert "estimated_cost_usd" not in serialized
    assert "source_trial_dir" not in serialized
