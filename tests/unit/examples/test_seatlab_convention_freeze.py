import json

import pytest

from examples.seatlab.convention_crossplay import ballot_freeze
from examples.seatlab.coupled_retention import strategy

PIN = "a" * 64
OTHER_PIN = "b" * 64
REQUESTS = {"id-a": (PIN, b"private packet a"), "id-b": (PIN, b"private packet b")}


def _row(request_id: str, reply: str, pin: str = PIN) -> bytes:
    return (
        json.dumps({"id": request_id, "prompt_sha256": pin, "reply": reply}).encode()
        + b"\n"
    )


def _report(**updates: int) -> dict[str, int]:
    report = {
        "expected": 2,
        "represented": 2,
        "valid": 2,
        "missing": 0,
        "duplicate_ids": 0,
        "stale_ids": 0,
        "invalid_ids": 0,
        "unknown_rows": 0,
    }
    report.update(updates)
    return report


def _freeze(*files: bytes) -> tuple[dict[str, int], bytes | None]:
    return ballot_freeze.freeze("east", ("A", "B"), "slot", REQUESTS, list(files))


def test_complete_shards_are_order_independent_and_keep_exact_choices():
    a = _row("id-a", '{"slot":"B"}')
    b = _row("id-b", '{"slot":"A"}')
    report, raw = _freeze(b, b"", a.rstrip(b"\n"))
    assert report == _report()
    assert raw is not None
    assert raw == strategy.table(
        "east", {b"private packet a": "B", b"private packet b": "A"}
    )
    assert _freeze(a + b) == (report, raw)


def test_same_choices_from_different_decks_freeze_to_same_table():
    first = ballot_freeze.freeze(
        "west",
        ("hold", "release"),
        "action",
        REQUESTS,
        [_row("id-a", '{"action":"hold"}') + _row("id-b", '{"action":"hold"}')],
    )
    second = ballot_freeze.freeze(
        "west",
        ("hold", "release"),
        "action",
        {"new-a": REQUESTS["id-a"], "new-b": REQUESTS["id-b"]},
        [_row("new-b", '{"action":"hold"}') + _row("new-a", '{"action":"hold"}')],
    )
    assert first == second
    assert first == (
        _report(),
        strategy.table("west", {packet: "hold" for _, packet in REQUESTS.values()}),
    )


@pytest.mark.parametrize("second", ['{"slot":"A"}', '{"slot":"B"}'])
def test_duplicate_expected_id_is_never_accepted(second: str):
    report, raw = _freeze(
        _row("id-a", '{"slot":"A"}'),
        _row("id-a", second) + _row("id-b", '{"slot":"B"}'),
    )
    assert report == _report(valid=1, duplicate_ids=1)
    assert raw is None


def test_all_represented_ids_are_counted_even_if_ambiguity_categories_overlap():
    report, raw = _freeze(
        _row("id-a", '{"slot":"A"}'),
        _row("id-a", '{"slot":"X"}', OTHER_PIN),
        _row("id-b", '{"slot":"B"}', OTHER_PIN),
    )
    assert report == _report(valid=0, duplicate_ids=1, stale_ids=2, invalid_ids=1)
    assert raw is None


def test_missing_empty_and_unknown_rows():
    assert _freeze(b"") == (_report(represented=0, valid=0, missing=2), None)
    assert _freeze(_row("id-a", '{"slot":"A"}')) == (
        _report(represented=1, valid=1, missing=1),
        None,
    )
    assert _freeze(
        _row("id-a", '{"slot":"A"}')
        + _row("id-b", '{"slot":"B"}')
        + _row("unknown", "not JSON")
        + _row("unknown", '{"slot":"A"}')
    ) == (_report(unknown_rows=2), None)


@pytest.mark.parametrize(
    "reply",
    [
        "",
        '```json\n{"slot":"A"}\n```',
        '{"slot":"A"}{}',
        '{"slot":"X"}',
        '{"slot":true}',
        '{"slot":NaN}',
        '{"slot":null}',
        '{"slot":"A","slot":"A"}',
        '{"slot":"A","why":"private"}',
        '{"action":"A"}',
        '[{"slot":"A"}]',
        '"A"',
    ],
)
def test_observed_malformed_vote_is_invalid_not_missing(reply: str):
    assert _freeze(_row("id-a", reply) + _row("id-b", '{"slot":"B"}')) == (
        _report(valid=1, invalid_ids=1),
        None,
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"\n",
        b"\xff",
        b"private outer payload",
        b"[]",
        b'{"id":"private outer payload"}',
        _row("bad id", "private outer payload"),
        _row("id-a", "private outer payload", "not a digest"),
        b'{"id":"id-a","id":"id-a","prompt_sha256":"'
        + PIN.encode()
        + b'","reply":"private outer payload"}',
        b'{"id":"id-a","prompt_sha256":"' + PIN.encode() + b'","reply":NaN}',
        b'{"id":"id-a","prompt_sha256":"' + PIN.encode() + b'","reply":{}}',
    ],
)
def test_corrupt_envelope_fails_without_partial_report_or_private_error(raw: bytes):
    with pytest.raises(ValueError, match="^invalid ballot reply files$") as error:
        _freeze(_row("id-a", '{"slot":"A"}'), raw)
    assert "private" not in str(error.value)
    assert error.value.__suppress_context__


def test_reply_limits_fail_closed():
    line = _row("id-a", '{"slot":"A"}')
    with pytest.raises(ValueError, match="^invalid ballot reply files$"):
        _freeze(line * ballot_freeze.MAX_REPLY_ROWS, line)
    with pytest.raises(ValueError, match="^invalid ballot reply files$"):
        _freeze(*([b""] * (ballot_freeze.MAX_REPLY_FILES + 1)))
    with pytest.raises(ValueError, match="^invalid ballot reply files$"):
        _freeze()
    with pytest.raises(ValueError, match="^invalid ballot reply files$"):
        _freeze(b" " * ballot_freeze.MAX_REPLY_BYTES, b"x")
