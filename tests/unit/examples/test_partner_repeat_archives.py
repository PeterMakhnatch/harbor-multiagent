import json
from collections import Counter

import pytest

from examples.seatlab.partner_native import repeat_archives

_ROLE_HISTOGRAM = {
    (0, 0, 0, 0): 24,
    (0, 0, 0, 1): 4,
    (0, 0, 1, 0): 4,
    (1, 1, 0, 0): 3,
    (1, 0, 0, 0): 3,
    (0, 0, 1, 1): 3,
    (0, 1, 0, 0): 2,
    (0, 1, 1, 0): 2,
    (1, 1, 0, 1): 1,
    (0, 1, 1, 1): 1,
    (1, 0, 1, 0): 1,
}
_NOTICE_HISTOGRAM = {
    (1, 1, 1, 1): 6,
    (0, 0, 1, 1): 6,
    (0, 0, 0, 0): 3,
    (0, 1, 1, 1): 2,
    (1, 0, 1, 1): 2,
    (1, 0, 0, 1): 1,
    (1, 0, 1, 0): 1,
    (0, 0, 1, 0): 1,
    (1, 1, 0, 1): 1,
    (0, 1, 0, 0): 1,
}


def test_retained_repeat_report():
    result = repeat_archives.report()
    assert result.keys() == {"schema", "source", "studies"}
    assert result["schema"] == "seatlab.retained-repeat-audit/v1"
    assert result["source"] == "published_native_rewards"
    assert result["studies"].keys() == {"role_allocation", "calendar_notices"}
    for study, worlds, arms, digest, histogram, values in (
        (
            "role_allocation",
            12,
            ["sender", "receiver"],
            "fadde7ef1bb075367565f18012b0effcc690af3d608f2f28809099e3e4784209",
            _ROLE_HISTOGRAM,
            ("5/16", "7/32", "3/32", "1/32"),
        ),
        (
            "calendar_notices",
            24,
            ["lists", "checked"],
            "3650bd3235d8634272e3fc84560420788736f56a7d374c1a584a96f67ad555a3",
            _NOTICE_HISTOGRAM,
            ("7/16", "11/48", "5/24", "5/16"),
        ),
    ):
        entry = result["studies"][study]
        assert entry.keys() == {
            "worlds",
            "teams",
            "arms",
            "receipt_sha256",
            "scope",
            "metrics",
        }
        assert entry["worlds"] == worlds
        assert entry["arms"] == arms
        assert entry["receipt_sha256"] == digest
        assert entry["teams"] == 4 * sum(histogram.values())
        assert "not partner swaps" in entry["scope"]
        metrics = entry["metrics"]
        assert metrics["complete"] is True
        assert metrics["complete_cases"] == sum(histogram.values())
        assert metrics["stable_original_only"] == histogram.get((1, 1, 0, 0), 0)
        assert metrics["stable_replacement_only"] == histogram.get((0, 0, 1, 1), 0)
        for name, value in zip(
            (
                "cross_disagreement",
                "within_disagreement",
                "excess_disagreement",
                "replacement_minus_original_reward",
            ),
            values,
            strict=True,
        ):
            assert metrics[name] == {"value": value, "bounds": [value, value]}
    assert "not independent" in result["studies"]["role_allocation"]["scope"]
    encoded = json.dumps(result)
    for field in ("trial_id_sha256", "trial_uuid", "w24", "selected_slot", "seeds"):
        assert field not in encoded


def test_role_rows_match_all_native_proofs():
    receipt = repeat_archives._receipt(
        "role-allocation-live.json", repeat_archives._ROLE_SHA256
    )
    assert Counter(repeat_archives._role_cases(receipt)) == _ROLE_HISTOGRAM
    receipt["rows"][0]["reward"] ^= 1
    with pytest.raises(ValueError, match="does not match native grade"):
        repeat_archives._role_cases(receipt)


@pytest.mark.parametrize("field", ["native_timeline_verified", "trial_id_sha256"])
def test_role_native_proof_cannot_be_reused_or_unverified(field):
    receipt = repeat_archives._receipt(
        "role-allocation-live.json", repeat_archives._ROLE_SHA256
    )
    first = receipt["policies"][0]["native"]["arms"]
    first["peer"][field] = (
        False if field == "native_timeline_verified" else first["blind"][field]
    )
    with pytest.raises(ValueError, match="native role allocation grade"):
        repeat_archives._role_cases(receipt)


def test_notice_receipt_matches_native_rewards():
    receipt = repeat_archives._receipt(
        "calendar-native-repeatability.json", repeat_archives._NOTICE_SHA256
    )
    cases = repeat_archives._notice_cases(receipt)
    assert Counter(cases) == _NOTICE_HISTOGRAM
    assert [
        sum(case[a] != case[b] for case in cases) for a in (0, 1) for b in (2, 3)
    ] == [11, 9, 12, 10]
    receipt["teams"][1]["trial_uuid"] = receipt["teams"][0]["trial_uuid"]
    with pytest.raises(ValueError, match="duplicate native trial UUID"):
        repeat_archives._notice_cases(receipt)


def test_receipt_digest_must_match(tmp_path, monkeypatch):
    monkeypatch.setattr(repeat_archives, "_RESULTS", tmp_path)
    (tmp_path / "role-allocation-live.json").write_text("{}")
    with pytest.raises(ValueError, match="retained receipt digest mismatch"):
        repeat_archives.report()
