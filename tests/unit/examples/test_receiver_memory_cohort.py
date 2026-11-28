from itertools import permutations

import pytest

from examples.seatlab.receiver_memory.cohort import (
    MAX_SEED,
    _matched,
    balanced_seeds,
    cohort,
)


def test_cohort_requires_six_rotations_without_reusing_development_seeds():
    dev, holdout = balanced_seeds(0), balanced_seeds(200)
    assert set(dev) == set(holdout) == set(permutations("ABC", 2))
    assert len(set(dev.values()) | set(holdout.values())) == 12
    with pytest.raises(ValueError, match="insufficient"):
        balanced_seeds(MAX_SEED)


@pytest.mark.parametrize("label", ["version", "erased", "sham"])
def test_equal_length_rejects_a_second_substituted_field(label):
    samples = {"version": b"route/A/1", "erased": b"route/A/?", "sham": b"route/A/2"}
    _matched(samples, "oldest", "2")
    samples[label] = samples[label].replace(b"/A/", b"/B/")
    with pytest.raises(ValueError, match="outside"):
        _matched(samples, "oldest", "2")


@pytest.mark.parametrize("sender", ["truthful", "repeat"])
def test_age_has_value_only_with_a_faithful_source(sender):
    result = cohort(200, sender)
    assert result["rows_per_arm"] == 48
    assert result["native_grades"] == 72
    arms = result["arms"]
    public = {
        arm: policies["by-tag"]
        for arm, policies in result["public_wire_policies"].items()
    }
    assert public["version"]["reads"] == public["sham"]["reads"] == "1/2"
    assert public["erased"]["reads"] == "1"
    if sender == "truthful":
        assert arms["version"]["adaptive"] == {
            "reward": "1",
            "reads": "1/2",
            "utility": "7/8",
        }
        assert arms["version"]["never"]["reward"] == "3/4"
        assert arms["version"]["always"]["reward"] == "1"
        for arm in ("erased", "sham"):
            assert arms[arm]["adaptive"] == {
                "reward": "3/4",
                "reads": "0",
                "utility": "3/4",
            }
        assert result["version_vs_sham_utility"] == "1/8"
        assert public["version"]["reward"] == "1"
        assert public["sham"]["reward"] == "7/8"
        assert result["by_tag_version_vs_sham_utility"] == "1/8"
    else:
        for arm in arms.values():
            assert arm["never"]["reward"] == arm["always"]["reward"] == "1/2"
            assert arm["adaptive"] == {"reward": "1/2", "reads": "0", "utility": "1/2"}
        assert result["version_vs_sham_utility"] == "0"
        assert all(row["reward"] == "1/2" for row in public.values())
        assert result["by_tag_version_vs_sham_utility"] == "0"
    assert result["version_benefit"] is (sender == "truthful")
