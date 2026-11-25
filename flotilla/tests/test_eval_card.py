"""Zero-cost card integration sweeps use built tasks, not a replacement scorer."""

import json
import subprocess
import sys

from conftest import FLOTILLA


def card(tmp_path, family, *options):
    result = subprocess.run(
        [sys.executable, str(FLOTILLA / "eval_card.py"), "--family", family,
         "--instances", "2", "--schedule-seeds", "2", "--jobs", "2", "--seed", "7236499753218553",
         "--out", str(tmp_path), *options], capture_output=True, text=True, check=False, timeout=90)
    data = json.loads((tmp_path / f"{family}.card.json").read_text()) if (
        tmp_path / f"{family}.card.json").is_file() else None
    assert data is not None, result.stderr
    checks = {row["name"]: row["status"] for row in data["checks"]}
    matrix = {(row["level"], row["arm"], row["policy"]): row for row in data["matrix"]}
    return result, data, checks, matrix


def test_fixture_card_sweep(tmp_path):
    result, data, checks, matrix = card(tmp_path, "kit_fixture", "--levels", "canary,heavy")
    assert result.returncode == 0, data["checks"]
    assert data["passed"]
    for name in ("solvable", "easy alone", "floor", "separation", "determinism", "schedule independence",
                 "no leak", "pairs", "twin rule"):
        assert checks[name] == "PASS", data["checks"]
    assert checks["dose response"] == checks["chance"] == checks["one seat is not enough"] == "NA"
    for level in ("canary", "heavy"):
        assert matrix[level, "talk-control", "naive"]["min"] == 1
        assert matrix[level, "talk-trap", "naive"]["mean"] == 0
        assert matrix[level, "talk-trap", "careful"]["n"] == 4
    assert all(row["same"] for row in data["replays"])
    assert all(pair["edit_chars"] == 0 for pair in data["static"]["pairs"])
    assert all(row["mean_input_tokens_estimate"] > 0 for row in data["load"])
    assert "## Open risks" in (tmp_path / "kit_fixture.card.md").read_text()


def test_rf1v_card_with_explicit_pair(tmp_path):
    result, data, checks, matrix = card(tmp_path, "probe", "--levels", "canary", "--variant", "verify-readfirst",
                                        "--read-first", "--arms", "talk-quiet,talk-false",
                                        "--pair", "talk-false=talk-quiet", "--policies", "careful,naive")
    assert result.returncode == 0, data["checks"]
    assert checks["separation"] == checks["pairs"] == checks["determinism"] == "PASS"
    assert checks["easy alone"] == checks["floor"] == "NA"
    assert matrix["canary", "talk-false", "naive"]["mean"] == 0
    assert matrix["canary", "talk-quiet", "naive"]["min"] == 1
    assert matrix["canary", "talk-false", "careful"]["min"] == 1
    assert data["identity"]["variant"] == "verify-readfirst"


def test_inverted_pair_fails_but_writes_both_cards(tmp_path):
    result, data, checks, _ = card(tmp_path, "kit_fixture", "--levels", "canary", "--arms",
                                   "talk-control,talk-trap", "--pair", "talk-control=talk-trap")
    assert result.returncode == 1
    assert not data["passed"]
    assert checks["separation"] == "FAIL"
    assert (tmp_path / "kit_fixture.card.md").is_file()
