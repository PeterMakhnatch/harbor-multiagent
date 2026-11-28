import json
from copy import deepcopy
from typing import Any

import pytest

from examples.seatlab.receiver_memory import provenance, wire_policy


def _offer(slot: str = "A", tag: str = "2") -> dict[str, Any]:
    return {
        "schema": provenance.SCHEMA,
        "protocol": provenance.PROTOCOL,
        "instruction": "A public instruction.",
        "current": {
            "method": "POST",
            "path": "/ack",
            "status": 200,
            "body": {"phase": "revision", "receipt": "receipt-2"},
            "reply": {"ok": True},
        },
        "note": {"slot": slot, "tag": tag},
        "price": "1/4",
    }


def _reply(initial: str = "A", revision: str = "C") -> dict[str, Any]:
    return {
        "schema": provenance.SCHEMA,
        "protocol": provenance.COMMIT_PROTOCOL,
        "inbox": {
            "method": "GET",
            "path": "/inbox",
            "status": 200,
            "body": None,
            "reply": {
                "reports": [
                    {"phase": "initial", "slot": initial, "receipt": "receipt-1"},
                    {"phase": "revision", "slot": revision, "receipt": "receipt-2"},
                ]
            },
        },
    }


def _encode(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


@pytest.mark.parametrize(
    ("policy", "tag", "expected"),
    [
        ("never", "1", {"op": "commit", "slot": "B"}),
        ("never", "2", {"op": "commit", "slot": "B"}),
        ("never", "?", {"op": "commit", "slot": "B"}),
        ("always", "1", {"op": "read"}),
        ("always", "2", {"op": "read"}),
        ("always", "?", {"op": "read"}),
        ("by-tag", "1", {"op": "read"}),
        ("by-tag", "2", {"op": "commit", "slot": "B"}),
        ("by-tag", "?", {"op": "read"}),
    ],
)
def test_first_uses_public_retained_note_and_tag(policy, tag, expected):
    data = _offer("B", tag)
    assert wire_policy.first(_encode(data), policy) == expected
    assert wire_policy.first(json.dumps(data).encode("ascii"), policy) == expected


@pytest.mark.parametrize("slot, tag", [("A", "1"), ("C", "2")])
@pytest.mark.parametrize("policy", wire_policy.POLICIES)
def test_matching_version_and_sham_offers_have_same_action(slot, tag, policy):
    version = _encode(_offer(slot, tag))
    sham = _encode(deepcopy(_offer(slot, tag)))
    assert version == sham
    assert wire_policy.first(version, policy) == wire_policy.first(sham, policy)


@pytest.mark.parametrize("initial, revision", [("A", "C"), ("C", "A"), ("B", "B")])
def test_second_commits_reported_revision_even_when_slots_repeat(initial, revision):
    assert wire_policy.first(_encode(_offer("A", "?")), "by-tag") == {"op": "read"}
    assert wire_policy.second(_encode(_reply(initial, revision))) == {
        "op": "commit",
        "slot": revision,
    }


@pytest.mark.parametrize(
    "key", ["seed", "sender", "arm", "age", "coin", "case", "payoffs", "answer"]
)
def test_first_rejects_private_fields(key):
    offer = _offer()
    offer[key] = "not on the wire"
    with pytest.raises(ValueError):
        wire_policy.first(_encode(offer), "always")


def test_first_rejects_duplicate_and_nonfinite_values():
    data = _encode(_offer())
    for invalid in (
        data.replace(b'"tag":"2"', b'"tag":"2","tag":"1"'),
        data.replace(b'"price":"1/4"', b'"price":NaN'),
        data.replace(b'"status":200', b'"status":1e999'),
        data.replace(b'"slot":"A"', b'"slot":"A","seed":42'),
        data + b" " * wire_policy.MAX_BYTES,
    ):
        with pytest.raises(ValueError):
            wire_policy.first(invalid, "never")


@pytest.mark.parametrize(
    "path, value",
    [
        (("note", "slot"), "D"),
        (("note", "tag"), 2),
        (("current", "body", "phase"), "initial"),
        (("current", "body", "receipt"), "receipt-1"),
        (("current", "reply", "ok"), 1),
        (("price",), "1/0"),
        (("schema",), "another-version"),
    ],
)
def test_first_rejects_invalid_nested_fields(path, value):
    offer = _offer()
    target = offer
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        wire_policy.first(_encode(offer), "always")


def test_second_rejects_swapped_report_order():
    reply = _reply()
    reply["inbox"]["reply"]["reports"].reverse()
    with pytest.raises(ValueError):
        wire_policy.second(_encode(reply))


@pytest.mark.parametrize(
    "key, value",
    [("phase", "initial"), ("receipt", "receipt-1"), ("slot", "D"), ("arm", "sham")],
)
def test_second_rejects_invalid_revision(key, value):
    reply = _reply()
    reply["inbox"]["reply"]["reports"][1][key] = value
    with pytest.raises(ValueError):
        wire_policy.second(_encode(reply))


def test_second_rejects_duplicate_keys_and_private_fields():
    reply = _reply()
    data = _encode(reply).replace(b'"slot":"C"', b'"slot":"C","slot":"A"')
    with pytest.raises(ValueError):
        wire_policy.second(data)
    reply["seed"] = 0
    with pytest.raises(ValueError):
        wire_policy.second(_encode(reply))
