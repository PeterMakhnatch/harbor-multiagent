import json
from dataclasses import replace
from uuid import uuid4

import pytest

from examples.seatlab.committee.accounting import account, main
from examples.seatlab.committee.schedule import ROLES
from examples.seatlab.tests.test_committee_analysis import (
    _fixture,
    _ledger,
    _read,
    _write,
)

_SHARE = (23, 3, 5)
_BOTH = (36, 3, 7)
_FIELDS = ("n_input_tokens", "n_cache_tokens", "n_output_tokens")


@pytest.fixture
def raw_reader():
    return pytest.importorskip("evallab.results")


def _trial(root, *, index=0, repeat=0, arm="open", complete=True):
    trial = _fixture(root, index, repeat, arm, n_private=1)
    result = _read(trial / "result.json")
    result["trial_uri"] = trial.resolve().as_uri()
    for seat in result["seat_results"]:
        seat["agent_result"].update(
            zip(_FIELDS, _BOTH if complete else _SHARE, strict=True)
        )
    # Native seat order need not equal the schedule order.
    result["seat_results"].reverse()
    if not complete:
        result.update(reward_valid=False, verifier_result=None)
        _write(
            trial.parent.parent / "outcome.json",
            {
                "schema": "seatlab.committee-launch/v1",
                "returncode": 0,
                "error": "native_trial_incomplete",
            },
        )
    _write(trial / "result.json", result)
    return trial, result


def _calls(trial, *, complete=True):
    cell = trial.parent.parent.name
    for role in ROLES:
        key = (role, "2026-11-20T00:00:00+00:00", "DeepSeek-V4.1-Flash-1017")
        yield (f"{cell}-share", *key, 3, 20, 5, 103, 133)
        if complete:
            yield (f"{cell}-vote", *key, 0, 13, 2, 107, 107)


def _usage(counts):
    return dict(zip(_FIELDS, counts, strict=True))


def test_raw_reader_preserves_explicit_zero_and_counts_paid_failed_turns(
    tmp_path, raw_reader, capsys
):
    study = tmp_path / "private study"
    complete, result = _trial(study)
    failed, _ = _trial(study, index=5, repeat=1, complete=False)
    ledger = _ledger(
        tmp_path,
        [
            *_calls(complete),
            *_calls(failed, complete=False),
            (
                "unregistered",
                "secret-role",
                "fixture",
                None,
                None,
                None,
                None,
                None,
                None,
            ),
        ],
    )

    assert main(["--study", str(study), "--spend-dir", str(ledger)]) == 0
    text = capsys.readouterr().out
    report = json.loads(text)
    assert report["scope"]["arms"] == ["open", "novelty"]
    assert report["scope"]["latent_worlds"] == 1
    assert report["summary"] == {
        "scheduled_cells": 24,
        "present_cells": 2,
        "absent_cells": 22,
        "unreadable_cells": 0,
        "bound_cells": 2,
        "accepted_cells": 1,
        "mismatch_cells": 0,
        "matched_seats": 4,
        "partial_matched_seats": 4,
        "matched_rewards": 1,
    }
    accepted, *_, incomplete, absent = report["cells"]
    assert accepted["native_reward"] == accepted["accepted_action_reward"] == 0.0
    assert accepted["reward_status"] == "matched"
    seat = accepted["seats"][0]
    assert seat["seat"] == "S1"
    assert seat["usage_status"] == "matched"
    assert seat["native_usage"] == seat["direct_usage"] == _usage(_BOTH)
    assert seat["share"]["private"] is True
    assert seat["share"]["fact"].startswith("K")
    assert seat["vote"]["choice"] in "XYZ"
    assert incomplete["cell_id"] == "hp-m5-r1-open"
    assert incomplete["analysis_status"] == "failed"
    assert (
        incomplete["native_reward"] is None
        and incomplete["accepted_action_reward"] is None
    )
    assert all("vote" not in row and "share" not in row for row in incomplete["seats"])
    assert all(row["usage_status"] == "partial_matched" for row in incomplete["seats"])
    assert absent["raw_result"] == "absent"
    assert absent["seats"][0]["native_usage"] == _usage((None, None, None))
    totals = report["totals"]
    assert (
        totals["native_usage"]
        == totals["spend"]["direct_usage"]
        == _usage((236, 24, 48))
    )
    assert totals["native_observed_seats"] == _usage((8, 8, 8))
    assert totals["spend"]["recorded_calls"] == totals["spend"]["settled_calls"] == 12
    assert totals["spend"]["settled_usd_low"] == "0.000001252"
    assert totals["spend"]["settled_usd_high"] == "0.000001492"
    assert totals["spend"]["unresolved_holds"] == 0
    assert totals["spend"]["actual_usd"] is None
    for private in (
        str(study),
        result["trial_uri"],
        result["id"],
        "analyst_1",
        "F_PRIV_1",
        "secret-role",
        "unregistered",
    ):
        assert private not in text


def test_input_includes_cache_and_missing_native_counts_are_not_imputed(
    tmp_path, raw_reader
):
    trial, result = _trial(tmp_path)
    result["seat_results"][-1]["agent_result"]["n_input_tokens"] = 33  # misses only
    result["seat_results"][-2]["agent_result"]["n_cache_tokens"] = None
    _write(trial / "result.json", result)
    report = account(tmp_path, spend_dir=_ledger(tmp_path, list(_calls(trial))))
    row = report["cells"][0]
    assert row["analysis_status"] == "accepted"
    assert row["mismatches"] == ["S1:native_usage_mismatch", "S2:native_usage_mismatch"]
    assert row["seats"][0]["native_usage"]["n_input_tokens"] == 33
    assert row["seats"][1]["native_usage"]["n_cache_tokens"] is None
    assert row["seats"][1]["direct_usage"]["n_cache_tokens"] == 3
    assert report["summary"]["mismatch_cells"] == 1


def test_unavailable_ledger_does_not_replace_native_counts(
    tmp_path, raw_reader, capsys
):
    _trial(tmp_path)
    assert (
        main(["--study", str(tmp_path), "--spend-dir", str(tmp_path / "missing")]) == 1
    )
    report = json.loads(capsys.readouterr().out)
    assert report["totals"]["spend_evidence"] == "unavailable"
    assert report["totals"]["spend"] is None
    assert report["cells"][0]["seats"][0]["native_usage"] == _usage(_BOTH)
    assert report["cells"][0]["seats"][0]["usage_status"] == "unavailable"


def test_unresolved_hold_is_not_a_settled_call(tmp_path, raw_reader):
    trial, _ = _trial(tmp_path, complete=False)
    ledger = _ledger(
        tmp_path,
        [
            *_calls(trial, complete=False),
            (
                "hp-m0-r0-open-vote",
                ROLES[0],
                "fixture",
                None,
                None,
                None,
                None,
                None,
                None,
            ),
        ],
    )
    report = account(tmp_path, spend_dir=ledger)
    seat = report["cells"][0]["seats"][0]
    assert seat["usage_status"] == "unavailable"
    assert seat["turns"]["vote"]["direct"]["usage"] == _usage((None, None, None))
    assert seat["direct_usage"] == _usage(_SHARE)
    spend = report["totals"]["spend"]
    assert spend["recorded_calls"] == 5 and spend["settled_calls"] == 4
    assert (
        spend["unresolved_holds"] == 1 and spend["unresolved_hold_usd"] == "0.010000000"
    )
    assert spend["upper_usd_including_holds"] == "0.010000532"
    assert spend["exact_list_price_usd"] is None


@pytest.mark.parametrize("field", ["id", "trial_uri", "trial_name", "reward", "seat"])
def test_raw_projection_must_bind_to_native_trial_seat_and_accepted_reward(
    tmp_path, monkeypatch, raw_reader, field
):
    trial, native = _trial(tmp_path)
    stock_load = raw_reader.load_trial

    def switched(candidate):
        record = stock_load(candidate)
        if field == "reward":
            return replace(record, rewards={"reward": 1.0})
        if field == "seat":
            record.result["seat_results"][-1]["under_test"] = False
            return record
        return replace(
            record,
            result=record.result
            | {
                field: {
                    "id": str(uuid4()),
                    "trial_uri": "file:///private/other",
                    "trial_name": "other",
                }[field]
            },
        )

    monkeypatch.setattr(raw_reader, "load_trial", switched)
    report = account(tmp_path, spend_dir=_ledger(tmp_path, list(_calls(trial))))
    row = report["cells"][0]
    assert report["summary"]["mismatch_cells"] == 1
    if field == "reward":
        assert row["mismatches"] == ["raw_reward_mismatch"]
    elif field == "seat":
        assert row["mismatches"] == ["S1:raw_seat_identity_mismatch"]
    else:
        assert row["mismatches"][0] == "raw_trial_identity_mismatch"
        assert row["native_reward"] is None
    assert native["id"] not in json.dumps(report)
