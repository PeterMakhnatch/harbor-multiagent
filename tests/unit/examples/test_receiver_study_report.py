import hashlib
import json
from fractions import Fraction
from itertools import product

import pytest

from examples.seatlab.receiver_memory.study.manifest import load, seal
from examples.seatlab.receiver_memory.study.report import _arm, assess


def _bounds(low, high):
    return {"lower": str(low), "upper": str(high)}


def _write(path, data):
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")
    path.chmod(0o600)


@pytest.fixture
def study(tmp_path):
    root = tmp_path / "sealed"
    pin = seal(root, 0, "truthful", "external_unverified")["sha256"]
    return root, pin, load(root, pin)["assignments"]


def _start(study, row):
    root, pin, _ = study
    _write(
        root / "attempts" / f"{row['ticket']}.start.json",
        {
            "schema": "seatlab.receiver-study.start/v1",
            "ticket": row["ticket"],
            "plan_sha256": pin,
        },
    )


def _result(study, row, slot, read=False):
    _, pin, _ = study
    return {
        "schema": "seatlab.receiver-study.result/v1",
        "ticket": row["ticket"],
        "plan_sha256": pin,
        "offer_sha256": row["offer_sha256"],
        "read_sha256": row["read_sha256"] if read else None,
        "actions": ([{"op": "read"}] if read else [])
        + [{"op": "commit", "slot": slot}],
    }


def _record(study, row, result):
    root, _, _ = study
    _write(root / "attempts" / f"{row['ticket']}.result.json", result)


def test_bounds_are_attainable_for_every_partial_small_group():
    possibilities = [(score, reads) for score in (0, 1) for reads in (0, 1)]
    partial: list[tuple[str, tuple[int, int] | None]] = [
        ("not_started", None),
        ("incomplete", None),
    ] + [("complete", outcome) for outcome in possibilities]
    for group in product(partial, repeat=3):
        completions = product(
            *[
                [outcome] if outcome is not None else possibilities
                for _, outcome in group
            ]
        )
        totals = [
            (Fraction(sum(r for r, _ in rows), 3), Fraction(sum(n for _, n in rows), 3))
            for rows in completions
        ]
        values = {
            "reward": [r for r, _ in totals],
            "reads": [n for _, n in totals],
            "utility": [r - n / 4 for r, n in totals],
        }
        assert _arm(list(group))["bounds"] == {
            metric: _bounds(min(samples), max(samples))
            for metric, samples in values.items()
        }


def test_empty_and_incomplete_are_unresolved(study):
    root, pin, rows = study
    empty = assess(root, pin)
    assert empty["source"] == "external_unverified"
    assert empty["scheduled"] == 96
    assert empty["status_counts"] == {"not_started": 96, "incomplete": 0, "complete": 0}
    assert empty["version_minus_sham"] == {
        "reward": _bounds(-1, 1),
        "reads": _bounds(-1, 1),
        "utility": _bounds(Fraction(-5, 4), Fraction(5, 4)),
    }
    _start(study, rows[0])
    pending = root / "attempts" / (".pending-" + "a" * 32)
    pending.touch(mode=0o600)
    incomplete = assess(root, pin)
    assert incomplete["status_counts"] == {
        "not_started": 95,
        "incomplete": 1,
        "complete": 0,
    }
    assert incomplete["arms"][rows[0]["arm"]]["status_counts"]["incomplete"] == 1
    assert incomplete["version_minus_sham"] == empty["version_minus_sham"]
    assert rows[0]["ticket"] not in json.dumps(incomplete)
    assert rows[0]["offer_sha256"] not in json.dumps(incomplete)


def test_complete_contrast_uses_native_scores_and_optional_read_price(study):
    root, pin, rows = study
    for row in rows:
        _start(study, row)
        version = row["arm"] == "version"
        slot = next(
            slot for slot, score in row["scores"].items() if score == int(version)
        )
        _record(study, row, _result(study, row, slot, read=not version))
    report = assess(root, pin)
    assert report["status_counts"] == {
        "not_started": 0,
        "incomplete": 0,
        "complete": 96,
    }
    assert report["arms"]["version"]["observed_sums"] == {
        "reward": "48",
        "reads": "0",
        "utility": "48",
    }
    assert report["arms"]["sham"]["observed_sums"] == {
        "reward": "0",
        "reads": "48",
        "utility": "-12",
    }
    assert report["version_minus_sham"] == {
        "reward": _bounds(1, 1),
        "reads": _bounds(-1, -1),
        "utility": _bounds(Fraction(5, 4), Fraction(5, 4)),
    }


def test_one_observation_updates_full_denominator(study):
    root, pin, rows = study
    row = next(row for row in rows if row["arm"] == "version")
    _start(study, row)
    slot = next(slot for slot, score in row["scores"].items() if score == 1)
    _record(study, row, _result(study, row, slot, read=True))
    report = assess(root, pin)
    assert report["arms"]["version"]["bounds"] == {
        "reward": _bounds(Fraction(1, 48), 1),
        "reads": _bounds(Fraction(1, 48), 1),
        "utility": _bounds(Fraction(-11, 48), Fraction(191, 192)),
    }


def test_result_without_claim_and_dual_artifact(study):
    root, pin, rows = study
    row = rows[0]
    result = _result(study, row, "A")
    _record(study, row, result)
    with pytest.raises(ValueError):
        assess(root, pin)
    _start(study, row)
    assert assess(root, pin)["status_counts"]["complete"] == 1
    _write(root / "attempts" / f"{row['ticket']}.result.copy.json", result)
    with pytest.raises(ValueError):
        assess(root, pin)


def test_invalid_attempt_evidence_is_not_dropped(study):
    root, pin, rows = study
    row = rows[0]
    _start(study, row)
    valid = _result(study, row, "A", read=True)
    for patch in (
        {"schema": "foreign"},
        {"ticket": rows[1]["ticket"]},
        {"plan_sha256": "0" * 64},
        {"offer_sha256": "0" * 64},
        {"read_sha256": "0" * 64},
        {"read_sha256": None},
        {"actions": [{"op": "commit", "slot": "A"}]},
        {"actions": [{"op": "read"}, {"op": "read"}]},
        {"actions": [{"op": "read"}, {"op": "commit", "slot": "D"}]},
        {"actions": [{"op": "read", "slot": "A"}, {"op": "commit", "slot": "A"}]},
        {"actions": [{"op": "commit", "slot": "A", "grade": 1}]},
        {"reward": 1},
        {"source": "scripted_control"},
    ):
        _record(study, row, valid | patch)
        with pytest.raises(ValueError):
            assess(root, pin)


def test_foreign_source_requires_original_pin(study):
    root, pin, _ = study
    plan = json.loads((root / "plan.json").read_text())
    plan["source"] = "scripted_control"
    _write(root / "plan.json", plan)
    with pytest.raises(ValueError):
        assess(root, pin)
    plan["source"] = "claimed_model_name"
    _write(root / "plan.json", plan)
    with pytest.raises(ValueError):
        assess(root, hashlib.sha256((root / "plan.json").read_bytes()).hexdigest())


def test_roster_does_not_accept_unknown_files_or_links(study):
    root, pin, rows = study
    directory = root / "attempts"
    path = directory / ".pending-"
    path.touch(mode=0o600)
    with pytest.raises(ValueError):
        assess(root, pin)
    path.unlink()
    path = directory / f"{rows[0]['ticket']}.start.json"
    path.symlink_to(root / "plan.json")
    with pytest.raises(ValueError):
        assess(root, pin)
    path.unlink()
    (directory / (".pending-" + "a" * 32)).symlink_to(root / "plan.json")
    with pytest.raises(ValueError):
        assess(root, pin)
