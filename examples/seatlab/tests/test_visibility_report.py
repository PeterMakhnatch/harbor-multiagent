import json
from pathlib import Path

import pytest

from examples.seatlab.visibility import __main__ as report
from examples.seatlab.visibility.core import Event, Trial


def _trial(path, key="private-trial"):
    return Trial(
        key,
        "control/team",
        ("reader", "writer"),
        (
            Event("send", "writer", (("writer", "private-id"),)),
            Event("action", "reader"),
            Event("fetch", "reader", (("world", "keep"), ("writer", "private-id"))),
            Event("fetch", "reader", (("writer", "private-id"),)),
            Event("action", "writer"),
        ),
        {"reader": "complete", "writer": "complete"},
        True,
        (path,),
    )


def test_public_report_counts_trials_not_fetches_or_other_seats_actions(
    tmp_path, monkeypatch
):
    private = tmp_path / "private-archive.json"
    private.write_text("hidden example content")
    monkeypatch.setitem(
        report._POPULATIONS, "synthetic", (1, lambda _: [_trial(private)])
    )
    result = report.replay(tmp_path, "synthetic")
    assert result["multi_seat_trials"] == 1
    assert result["projection"]["trials_with_changed_fetch"] == 1
    assert result["projection"]["trials_with_changed_fetch_before_action"] == 0
    arm = result["arms"]["control/team"]
    assert arm["receipts"]["fetch"]["events"] == 2
    assert arm["receipts"]["fetch"]["sum_seat_unique_messages"] == 1
    assert arm["receipts"]["bound"]["events"] == 0
    text = json.dumps(result)
    for retained in [
        str(tmp_path),
        "private-trial",
        "private-id",
        "hidden example content",
    ]:
        assert retained not in text


@pytest.mark.parametrize("change", ["bytes", "source_set"])
def test_source_change_refuses_public_report(tmp_path, monkeypatch, change):
    original = tmp_path / "original"
    alternate = tmp_path / "alternate"
    original.write_bytes(b"original")
    alternate.write_bytes(b"original")
    reads = 0

    def load(_: Path):
        nonlocal reads
        reads += 1
        if reads == 1:
            yield _trial(original)
        elif change == "source_set":
            yield _trial(alternate)
        else:
            original.write_bytes(b"changed")
            yield _trial(original)

    monkeypatch.setitem(report._POPULATIONS, "synthetic", (1, load))
    with pytest.raises(ValueError, match="source evidence changed"):
        report.replay(tmp_path, "synthetic")
