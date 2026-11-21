import json
from itertools import product

import pytest

from examples.seatlab.visibility.route_summary import summarize

_PRIMARY = "complementary_minus_duplicate_evidence"

type _Row = dict[str, int | str | bool | None]


@pytest.fixture
def register() -> list[_Row]:
    return [
        {
            "policy": policy,
            "round": round_number,
            "rotation": rotation,
            "swap": swap,
            "rep": rep,
            "lens": lens,
            "channel": channel,
            "status": "unrun",
            "label": None,
            "best": "B" if swap else "A",
            "review_vote": None,
            "exact": None,
        }
        for policy, round_number, rotation, swap, rep, lens, channel in product(
            range(3),
            range(1, 7),
            range(3),
            range(2),
            range(2),
            ("duplicate", "complementary"),
            ("vote", "evidence"),
        )
    ]


def _score(row: _Row, correct: bool) -> None:
    row["label"] = "A" if (row["best"] == "A") == correct else "B"
    row["status"] = "valid"


def test_complete_deltas_weight_policies_equally_and_keep_ties(register):
    for row in register:
        if row["channel"] != "evidence" or row["round"] != 1:
            continue
        if row["policy"] == 2:
            if row["rotation"] == 2:
                if row["swap"] == row["rep"] == 0:
                    _score(row, True)  # A scored tie, not a missing block.
                continue
            if row["rotation"] == 1 and row["swap"] == 1:
                continue
            _score(row, row["lens"] == "duplicate")  # Six deltas of -1.
        elif row["rotation"] == row["swap"] == row["rep"] == 0:
            _score(row, row["lens"] == "complementary")  # One +1 per policy.

    result = summarize(register)
    assert result == summarize(list(reversed(register)))
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    assert result["primary_contrast"] == _PRIMARY
    primary = result["contrasts"][_PRIMARY]
    assert primary["complete_blocks"] == 9
    assert primary["complete_ties"] == 1
    assert primary["known_scored_cells"] == 18
    assert [s["complete_blocks"] for s in primary["by_policy"].values()] == [1, 1, 7]
    assert primary["by_policy"]["2"]["paired_complete_delta"] == pytest.approx(-6 / 7)
    assert primary["paired_complete_delta"] == pytest.approx(8 / 21)
    assert primary["paired_complete_delta"] > 0  # Pooling blocks gives -4/9.

    for row in register:
        if row["policy"] == 1 and row["lens"] == "duplicate":
            row.update(status="unrun", label=None)
    partial = summarize(register)["contrasts"][_PRIMARY]
    assert partial["complete_blocks"] == 8
    assert partial["by_policy"]["1"]["paired_complete_delta"] is None
    assert partial["paired_complete_delta"] is None


def test_missing_cells_use_signed_bounds_over_the_full_register(register):
    for row in register:
        if row["policy"] == 1:
            row.update(best=None, status="ineligible")
        elif row["round"] == 1 and row["rotation"] == row["swap"] == row["rep"] == 0:
            if row["channel"] == "evidence":
                if row["policy"] == 0 and row["lens"] == "complementary":
                    _score(row, True)
                if row["policy"] == 2 and row["lens"] == "duplicate":
                    _score(row, False)  # Known score 0 still tightens the lower bound.

    result = summarize(register)
    assert result["population"] == {
        "registered_source_policies": 3,
        "source_policies_with_known_rounds": 2,
        "source_policies_with_scored_decisions": 2,
        "registered_rounds": 18,
        "known_source_rounds": 12,
        "rounds_with_scored_decisions": 2,
        "registered_blocks": 216,
        "known_source_blocks": 144,
        "registered_reviewers": 432,
        "registered_decisions": 864,
    }
    primary = result["contrasts"][_PRIMARY]
    assert primary["registered_blocks"] == 216
    assert primary["registered_scored_cells"] == 432
    assert primary["known_scored_cells"] == 2
    assert primary["complete_blocks"] == 0
    assert primary["paired_complete_delta"] is None
    assert primary["worst_delta"] == pytest.approx(-214 / 216)
    assert primary["best_delta"] == 1
    assert primary["by_policy"]["1"]["known_scored_cells"] == 0
    assert primary["by_policy"]["1"]["worst_delta"] == -1
    assert primary["by_policy"]["1"]["best_delta"] == 1
    interaction = result["contrasts"]["interaction"]
    assert interaction["registered_scored_cells"] == 864
    assert interaction["worst_delta"] == pytest.approx(-430 / 216)
    assert interaction["best_delta"] == 2
    assert result["arms"]["duplicate"]["evidence"]["accuracy"] == 0
    unknown = result["by_policy"]["1"]["arms"]["duplicate"]["evidence"]
    assert unknown["status_counts"] == {"ineligible": 72}
    assert unknown["accuracy"] is None


def test_contrast_directions_on_complete_quartets(register):
    for row in register:
        same = row["lens"] == "duplicate"
        _score(row, same == (row["channel"] == "vote"))

    contrasts = summarize(register)["contrasts"]
    expected = {
        _PRIMARY: 1,
        "complementary_minus_duplicate_vote": -1,
        "evidence_minus_vote_duplicate": -1,
        "evidence_minus_vote_complementary": 1,
        "interaction": 2,
    }
    for name, delta in expected.items():
        contrast = contrasts[name]
        assert contrast["complete_blocks"] == 216
        assert contrast["paired_complete_delta"] == delta
        assert contrast["worst_delta"] == contrast["best_delta"] == delta


def test_swap_and_repeat_pairs_compare_physical_routes(register):
    for row in register:
        if row["policy"] != 0 or row["round"] != 1:
            continue
        if row["lens"] == "duplicate" and row["channel"] == "evidence":
            if row["rotation"] == 0:
                label = "A" if row["swap"] == row["rep"] else "B"
                row.update(status="valid", label=label)
            elif row["rotation"] == 1 and row["swap"] == row["rep"] == 0:
                row.update(status="valid", label="A")  # Has no valid partner.
        if row["lens"] == "complementary" and row["channel"] == "vote":
            if row["rotation"] == row["rep"] == 0:
                row.update(status="valid", label="A")  # Same letter, different route.

    result = summarize(register)
    arm = result["arms"]["duplicate"]["evidence"]
    assert arm["label_counts"] == {"A": 3, "B": 2}
    assert arm["valid_decisions"] == 5
    assert arm["accuracy"] == pytest.approx(3 / 5)
    assert arm["label_swap_pairs"] == {
        "registered_pairs": 108,
        "known_source_pairs": 108,
        "valid_pairs": 2,
        "physical_agreement": 2,
        "physical_disagreement": 0,
    }
    assert arm["repeat_pairs"]["valid_pairs"] == 2
    assert arm["repeat_pairs"]["physical_disagreement"] == 2
    policy_arm = result["by_policy"]["0"]["arms"]["duplicate"]["evidence"]
    assert policy_arm["repeat_pairs"]["registered_pairs"] == 36
    other = result["arms"]["complementary"]["vote"]["label_swap_pairs"]
    assert other["valid_pairs"] == other["physical_disagreement"] == 1


def test_transcription_counts_each_shared_reviewer_once(register):
    for row in register:
        if (
            row["policy"] == row["rotation"] == row["swap"] == 0
            and row["round"] == 1
            and row["lens"] == "duplicate"
        ):
            if row["rep"] == 0:
                row.update(review_vote="A", exact=False)
                _score(row, row["channel"] == "vote")
            else:
                row.update(review_vote="B", exact=True)

    result = summarize(register)
    assert result["reviews_by_lens"]["duplicate"] == {
        "registered_reviewers": 216,
        "known_source_reviewers": 216,
        "known_recommendations": 2,
        "known_transcriptions": 2,
        "exact_transcriptions": 1,
        "incorrect_transcriptions": 1,
        "exact_fraction": 0.5,
    }
    arms = result["arms"]["duplicate"]
    assert arms["vote"]["recommendation_following"] == {
        "comparable": 1,
        "followed": 1,
        "fraction": 1.0,
    }
    assert arms["evidence"]["recommendation_following"] == {
        "comparable": 1,
        "followed": 0,
        "fraction": 0.0,
    }
    assert result["reviews_by_lens"]["complementary"]["exact_fraction"] is None


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"policy": False}, "policy index"),
        ({"round": True}, "round index"),
        ({"rotation": False}, "rotation index"),
        ({"swap": False}, "swap index"),
        ({"rep": 0.0}, "rep index"),
        ({"lens": "Duplicate"}, "lens"),
        ({"channel": "message"}, "channel"),
        ({"status": "valid"}, "status and label"),
        ({"status": "invalid", "label": "A"}, "status and label"),
        ({"status": "valid", "label": "A", "best": None}, "unknown source"),
        ({"best": None, "review_vote": "A"}, "unknown source"),
        ({"best": None, "exact": False}, "unknown source"),
        ({"status": "bad status"}, "status category"),
        ({"status": "x" * 65}, "status category"),
        ({"exact": 0}, "exact flag"),
        ({"review_vote": "A"}, "shared reviewer"),
        ({"exact": False}, "shared reviewer"),
    ],
)
def test_rejects_row_aliases_and_contradictions(register, changes, message):
    register[0].update(changes)
    with pytest.raises(ValueError, match=message):
        summarize(register)


def test_rejects_inconsistent_source_keys_after_reversing_the_display_swap(register):
    for row in register:
        if row["policy"] == 0 and row["round"] == 1 and row["swap"] == 1:
            row["best"] = "A"  # Maps to original B; all other rows map to original A.
    with pytest.raises(ValueError, match="inconsistent answer key"):
        summarize(register)


def test_rejects_missing_or_aliased_registered_rows(register):
    with pytest.raises(ValueError, match="864 registered decision rows"):
        summarize(register[1:])
    with pytest.raises(ValueError, match="duplicate registered decision row"):
        summarize([register[1].copy(), *register[1:]])
    del register[0]["exact"]
    with pytest.raises(ValueError, match="missing a required field"):
        summarize(register)
