import json

import pytest
from conftest import FLOTILLA, load

RUN = load(FLOTILLA / "run_wave.py")


@pytest.mark.parametrize("model", [None, "openrouter/test/model"])
@pytest.mark.parametrize(("status", "valid"), [(None, True), ("0\n", True), ("126", False), ("bad", False)])
def test_oracle_startup_errors_invalidate_the_trial_but_a_played_zero_counts(tmp_path, model, status, valid):
    cell = {"instance": 0, "arm": "team", "model": model, "scripted": ["s2"]}
    participants = [{"seat": "s1", "name": "terminus-2" if model else "oracle", "model_name": model},
                    {"seat": "s2", "name": "oracle", "model_name": None}]
    result = {"config": {"participants": participants},
              "seat_results": [{"seat": row["seat"], "agent_info": {"name": row["name"]}}
                               for row in participants]}
    verifier = tmp_path / "verifier"
    verifier.mkdir()
    (verifier / "score.json").write_text(json.dumps({"instance": 0, "condition": "team", "reward": 0.0}))
    (verifier / "world-state.json").write_text("{}")
    if model:
        model_dir = tmp_path / "seats" / "s1" / "agent"
        model_dir.mkdir(parents=True)
        (model_dir / "exit-code.txt").write_text("1")
    oracle_dir = tmp_path / "seats" / "s2" / "agent"
    oracle_dir.mkdir(parents=True)
    if status is not None:
        (oracle_dir / "exit-code.txt").write_text(status)
    reason = RUN.trial_problem(tmp_path, result, cell)
    if valid:
        assert reason is None
    else:
        assert "s2" in reason and ("126" if status == "126" else "malformed") in reason


@pytest.mark.parametrize("seat", ["../outside", "s/1", "", None])
def test_invalid_participant_cannot_select_an_unrelated_marker(tmp_path, seat):
    assert RUN.exit_marker_problem(tmp_path, seat) == "an oracle participant seat name is malformed"
