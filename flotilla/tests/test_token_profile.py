"""Offline token accounting from known CLI log formats and real simulated task bundles."""

import json
import subprocess
import sys

import pytest
from conftest import FLOTILLA, load


@pytest.fixture
def tool(monkeypatch):
    monkeypatch.syspath_prepend(str(FLOTILLA))
    return load(FLOTILLA / "token_profile.py")


def log(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("startup notice\n" + "\n".join(json.dumps(event) for event in events))
    return path


def codex(total, cached, out):
    return {"type": "turn.completed", "usage": {"input_tokens": total, "cached_input_tokens": cached,
            "output_tokens": out, "reasoning_output_tokens": 4}}


def claude(total, cached, written, out):
    return {"type": "result", "usage": {"input_tokens": total, "cache_read_input_tokens": cached,
            "cache_creation_input_tokens": written, "output_tokens": out}}


def test_codex_sums_completed_turns_not_reasoning_twice(tmp_path, tool):
    path = log(tmp_path / "seat.log", [codex(1000, 800, 25), codex(300, 200, 10),
                                      {"type": "item.completed", "usage": {"output_tokens": 99999}}])
    assert tool.log_tokens(path)[0] == {"miss": 300, "hit": 1000, "out": 35}


def test_claude_final_usage_includes_cache_writes_and_ignores_intermediate_usage(tmp_path, tool):
    final = claude(12, 1000, 200, 100)
    path = log(tmp_path / "seat.log", [{"type": "assistant", "message": {"usage": final["usage"]}}, final])
    assert tool.log_tokens(path)[0] == {"miss": 212, "hit": 1000, "out": 100}


@pytest.mark.parametrize("events", [[{"type": "other", "usage": {"input_tokens": 100}}],
                                    [codex(10, 100, 1)], [codex(0, 0, 0)],
                                    [codex(10, 0, 1), {"type": "turn.failed"}],
                                    [claude(10, 0, 0, 1), claude(20, 0, 0, 2)]])
def test_unknown_or_incomplete_usage_is_not_a_profile(tmp_path, tool, events):
    with pytest.raises(ValueError):
        tool.log_tokens(log(tmp_path / "seat.log", events))


def test_playtest_profile_reports_number_of_external_seats_per_role_and_unknown_fails(tmp_path, tool, capsys):
    task = tmp_path / "digest" / "t-example"
    task.mkdir(parents=True)
    (task / "task.toml").write_text('[metadata]\ndifficulty="standard"\ncondition="team"\n')
    dirs = []
    for i, out in enumerate((20, 40)):
        run = tmp_path / f"run-{i}"
        log(run / "seats/p1/seat.log", [codex(1000, 400, out)])
        (run / "result.json").write_text(json.dumps({"task": str(task), "seats": {
            "p1": {"mode": "external", "exit_code": 0}, "p2": {"mode": "scripted"}}}))
        dirs.append(str(run))
    argv = ["--family", "digest", "--level", "standard", "--from-playtest", *dirs]
    assert tool.main(argv) == 0
    rows = json.loads(capsys.readouterr().out)["token_profiles"]
    assert rows == [{"kind": "digest", "level": "standard", "arm": "team", "role": "p1", "n": 2,
                     "miss": 600, "hit": 400, "out": 30, "uncertainty": 2,
                     "source": "Codex turn.completed usage"}]
    log(tmp_path / "run-1/seats/p1/seat.log", [{"type": "unrecognized"}])
    assert tool.main(argv) == 1
    captured = capsys.readouterr()
    assert not captured.out and "unknown or ambiguous log format" in captured.err


def test_calibration_uses_per_turn_growth_and_reports_exact_error(tool):
    rows = [tool.FitSample(context=100, turns=t, first=120, total=120 * t + 30 * t * (t - 1) // 2,
                           cached=(120 * t + 30 * t * (t - 1) // 2) // 2, out=10 * t) for t in (2, 3, 5)]
    model = tool.fit(rows)
    assert model == tool.TurnModel(20, 30, 0.5, 10)
    assert tool.fit_errors(rows, model) == {"hit": 0, "miss": 0, "out": 0}
    assert model.predict(200, 4) == {"miss": 530, "hit": 530, "out": 40}


def test_model_turns_reads_real_built_fixture_and_runs_careful_policy(tmp_path):
    build = subprocess.run([sys.executable, str(FLOTILLA / "build_tasks.py"), "--family", "kit_fixture",
                            "--difficulty", "heavy", "--conditions", "solo,talk-control", "--instances", "0",
                            "--out", str(tmp_path)], check=True, capture_output=True, text=True)
    assert build.returncode == 0
    tasks = sorted((tmp_path / "kit_fixture").glob("t-*"))
    result = subprocess.run([sys.executable, str(FLOTILLA / "token_profile.py"), "--family", "kit_fixture",
                             "--level", "heavy", "--model-turns", *map(str, tasks)],
                            check=True, capture_output=True, text=True)
    rows = json.loads(result.stdout)["token_profiles"]
    assert {(row["arm"], row["role"]) for row in rows} == {("solo", "p1"), ("talk-control", "p2")}
    assert all(row["n"] == 1 and row["out"] > 0 for row in rows)
    assert "world calls/turn" in rows[0]["source"] and "WAPE" in result.stderr
