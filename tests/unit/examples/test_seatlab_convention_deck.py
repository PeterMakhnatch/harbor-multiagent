import hashlib
import json
import re

import pytest

from examples.seatlab.convention_crossplay import ballot_deck
from examples.seatlab.convention_crossplay.loader import digest


def _json(value: object) -> bytes:
    return json.dumps(value).encode("utf-8") + b"\n"


def _reject(manifest: bytes, requests: bytes, pin: str) -> None:
    with pytest.raises(ValueError, match="^invalid role ballot deck or pin$") as exc:
        ballot_deck.parse(manifest, requests, pin)
    assert exc.value.__suppress_context__ or exc.value.__context__ is None


def _repin(manifest: bytes, requests: bytes, **updates) -> bytes:
    data = json.loads(manifest)
    data.update(requests_sha256=digest(requests), **updates)
    return _json(data)


def test_exact_text_and_wire_fields() -> None:
    packets = {
        b"",
        b"  secret\r\nsecond\tline\n",
        b"\x00\x01",
        "é e\u0301 😀\u2028\u2029".encode(),
        b"\x00" * 8192,
    }
    manifest, requests = ballot_deck.make("east", ("A", "B"), packets, "slot")
    assert json.loads(manifest) == {
        "schema": "seatlab.role-ballot-deck/v1",
        "role": "east",
        "actions": ["A", "B"],
        "response_field": "slot",
        "requests_sha256": digest(requests),
        "count": len(packets),
    }
    assert requests.endswith(b"\n")
    assert b"\r" not in requests
    rows = [json.loads(line) for line in requests.split(b"\n")[:-1]]
    expected = {}
    for row in rows:
        assert set(row) == {"id", "prompt_sha256", "prompt"}
        assert re.fullmatch(r"[0-9a-f]{32}", row["id"])
        packet = row["prompt"].encode("utf-8")
        assert row["prompt_sha256"] == hashlib.sha256(packet).hexdigest()
        expected[row["id"]] = row["prompt_sha256"], packet
    assert len(rows) == len(expected) == len(packets)
    assert {packet for _, packet in expected.values()} == packets
    assert ballot_deck.parse(manifest, requests, digest(manifest)) == (
        "east",
        ("A", "B"),
        "slot",
        expected,
    )


def test_full_deck_and_fresh_ids_per_deck() -> None:
    packets = {str(i).encode() for i in range(256)}
    actions = tuple(str(i) for i in range(16))
    decks = [ballot_deck.make("west", actions, packets, "action") for _ in range(2)]
    rosters = [
        ballot_deck.parse(manifest, requests, digest(manifest))
        for manifest, requests in decks
    ]
    for role, labels, field, roster in rosters:
        assert (role, labels, field) == ("west", actions, "action")
        assert len(roster) == 256
        assert {packet for _, packet in roster.values()} == packets
    assert set(rosters[0][3]).isdisjoint(rosters[1][3])


@pytest.mark.parametrize(
    "packets",
    [
        {b"\xff"},
        {b"\xed\xa0\x80"},
        {b"x" * 8193},
        set(),
        {str(i).encode() for i in range(257)},
    ],
)
def test_make_rejects_invalid_packets(packets) -> None:
    with pytest.raises(ValueError, match="^invalid role ballot deck or pin$"):
        ballot_deck.make("east", ("A",), packets, "slot")


@pytest.mark.parametrize(
    "updates",
    [
        {"role": "north"},
        {"role": True},
        {"response_field": "vote"},
        {"actions": []},
        {"actions": ["A", "A"]},
        {"actions": [" A"]},
        {"actions": ["A\nB"]},
        {"actions": ["\ud800"]},
        {"actions": ["x" * 129]},
        {"actions": [42]},
        {"actions": list(map(str, range(17)))},
    ],
)
def test_rejects_illegal_role_actions_or_response_field(updates) -> None:
    manifest, requests = ballot_deck.make("east", ("A",), {b"private"}, "slot")
    changed = _repin(manifest, requests, **updates)
    _reject(changed, requests, digest(changed))
    values = json.loads(changed)
    with pytest.raises(ValueError, match="^invalid role ballot deck or pin$"):
        ballot_deck.make(
            values["role"],
            tuple(values["actions"]),
            {b"private"},
            values["response_field"],
        )


def test_rejects_stale_pins_and_counts() -> None:
    manifest, requests = ballot_deck.make("east", ("A",), {b"private"}, "slot")
    _reject(manifest + b" ", requests, digest(manifest))
    _reject(manifest, requests, digest(manifest).upper())
    _reject(manifest, requests + b" ", digest(manifest))
    for count in (True, 0, 2, 257, 1.0):
        changed = _repin(manifest, requests, count=count)
        _reject(changed, requests, digest(changed))
    changed = _repin(manifest, requests, extra="private")
    _reject(changed, requests, digest(changed))


@pytest.mark.parametrize(
    "updates",
    [
        {"id": "A" * 32},
        {"id": "short"},
        {"id": True},
        {"prompt_sha256": "0" * 64},
        {"prompt_sha256": "sha256:" + "0" * 64},
        {"prompt": "edited"},
        {"prompt": "\ud800"},
        {"prompt": None},
        {"prompt": "x" * 8193},
        {"extra": "private"},
    ],
)
def test_rejects_malformed_requests_even_with_current_pins(updates) -> None:
    manifest, requests = ballot_deck.make("west", ("A",), {b"private"}, "action")
    row = json.loads(requests)
    row.update(updates)
    bad_requests = _json(row)
    changed = _repin(manifest, bad_requests)
    _reject(changed, bad_requests, digest(changed))


def test_rejects_duplicate_packets_and_ids() -> None:
    manifest, requests = ballot_deck.make("west", ("A",), {b"one", b"two"}, "slot")
    first, second = [json.loads(line) for line in requests.splitlines()]
    for duplicate in (
        dict(second, id=first["id"]),
        dict(second, prompt=first["prompt"], prompt_sha256=first["prompt_sha256"]),
    ):
        altered = _json(first) + _json(duplicate)
        changed = _repin(manifest, altered)
        _reject(changed, altered, digest(changed))


def test_rejects_invalid_json_and_delimiters() -> None:
    manifest, requests = ballot_deck.make("east", ("A",), {b"private"}, "slot")
    for malformed in (
        requests.replace(b'"id":', b'"id":"duplicate","id":', 1),
        requests.replace(b'"prompt":', b'"prompt":NaN,"other":', 1),
        requests.replace(b'"prompt":', b'"prompt":' + b"[" * 2000, 1),
        b"\xff" + requests,
        requests[:-1],
        requests + b"\n",
        requests.replace(b"\n", b"\r\n"),
    ):
        changed = _repin(manifest, malformed)
        _reject(changed, malformed, digest(changed))
    for malformed in (
        manifest.replace(b'"role":', b'"role":"west","role":', 1),
        manifest.replace(b'"count":1', b'"count":NaN'),
        b"\xff" + manifest,
    ):
        _reject(malformed, requests, digest(malformed))


def test_rejects_oversized_documents() -> None:
    manifest, requests = ballot_deck.make("east", ("A",), {b"private"}, "slot")
    big_manifest = manifest.ljust(ballot_deck.MAX_MANIFEST_BYTES + 1, b" ")
    _reject(big_manifest, requests, digest(big_manifest))
    big_requests = requests.ljust(ballot_deck.MAX_REQUESTS_BYTES + 1, b" ")
    changed = _repin(manifest, big_requests)
    _reject(changed, big_requests, digest(changed))
