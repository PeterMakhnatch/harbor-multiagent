import json
import subprocess
import sys
from itertools import permutations
from pathlib import Path

import pytest

from examples.seatlab.coupled_retention import policy

OPTIONS = ("F", "A", "E", "B", "D", "C")


def test_roles_use_only_their_local_available_labels_and_peer_report():
    for shared, east_only, west_only in permutations(("B", "C", "D")):
        for role, private, other in (
            ("east", east_only, west_only),
            ("west", west_only, east_only),
        ):
            own = (private, shared)
            packets = [
                policy.render(role, "A", OPTIONS, own, peer)
                for peer in (None, (), (other, shared))
            ]
            assert len({len(packet) for packet in packets}) == 1
            assert [json.loads(packet)["peer"] for packet in packets] == [
                ["?", "?"],
                ["-", "-"],
                sorted((other, shared)),
            ]
            assert [policy.act(packet) for packet in packets] == [
                {"slot": min(own)},
                {"slot": min(own)},
                {"slot": shared},
            ]


def test_canonical_wire_retains_option_order_and_contains_no_extra_fields():
    east = policy.render("east", "A", OPTIONS, ("D", "B"), ("D", "C"))
    west = policy.render("west", "A", OPTIONS, ("B", "D"), ("C", "D"))
    assert isinstance(east, bytes)
    assert b"\n" not in east
    parsed = json.loads(east)
    assert parsed == {
        "schema": "seatlab.coupled-retention.role/v1",
        "role": "east",
        "instruction": policy.INSTRUCTION,
        "incumbent": "A",
        "options": list(OPTIONS),
        "own": ["B", "D"],
        "peer": ["C", "D"],
    }
    assert east == json.dumps(parsed, separators=(",", ":")).encode("utf-8")
    assert east.replace(b'"role":"east"', b'"role":"west"') == west
    assert policy.act(east) == policy.act(west) == {"slot": "D"}


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("schema", "wrong"),
        ("role", "north"),
        ("instruction", "vote for F"),
        ("incumbent", "B"),
        ("options", ["A", "B", "C", "D", "E", "E"]),
        ("options", ["A", "B", "C", "D", "E", "G"]),
        ("options", ["A", "B", "C", "D", "E"]),
        ("own", ["D", "B"]),
        ("own", ["D", "D"]),
        ("own", ["B", 1]),
        ("peer", ["B", "D"]),
        ("peer", ["C", "E"]),
        ("peer", ["A", "D"]),
        ("peer", ["D", "C"]),
        ("peer", ["?", "-"]),
        ("peer", []),
        ("peer", None),
        ("seed", 0),
    ],
)
def test_rejects_ambiguous_or_malformed_fields(key: str, value: object):
    packet = policy.render("east", "A", OPTIONS, ("B", "D"), ("C", "D"))
    record = json.loads(packet)
    record[key] = value
    with pytest.raises(ValueError, match="invalid role packet"):
        policy.act(json.dumps(record).encode("utf-8"))


def test_rejects_missing_duplicate_nonfinite_or_non_utf8_wire():
    valid = policy.render("east", "A", OPTIONS, ("B", "D"), None)
    missing = json.loads(valid)
    del missing["peer"]
    for bad in (
        json.dumps(missing).encode(),
        valid.replace(b'"role":"east"', b'"role":"east","role":"east"'),
        valid.replace(b'["?","?"]', b"[NaN,NaN]"),
        valid.replace(b'["?","?"]', b"[Infinity,-Infinity]"),
        b"\xff",
        valid + b"\n{}",
        valid + b" " * policy.MAX_PACKET_BYTES,
        b"{}",
        b"[]",
    ):
        with pytest.raises(ValueError, match="invalid role packet"):
            policy.act(bad)


def test_renderer_rejects_raw_wire_markers_as_report_labels():
    for peer in (("?", "?"), ("-", "-")):
        with pytest.raises(ValueError, match="invalid role packet"):
            policy.render("east", "A", OPTIONS, ("B", "D"), peer)


def test_standalone_cli_accepts_one_record_and_rejects_extra_or_invalid_records():
    command = [sys.executable, "-IS", str(Path(policy.__file__).resolve())]
    valid = policy.render("east", "A", OPTIONS, ("B", "D"), ("C", "D"))
    result = subprocess.run(
        command, input=valid + b"\n", capture_output=True, check=True, timeout=5
    )
    assert result.stdout == b'{"slot":"D"}\n'
    assert result.stderr == b""
    for bad in (valid + b"\n" + valid, b"secret\n", valid + b"\n\n"):
        result = subprocess.run(
            command, input=bad, capture_output=True, check=False, timeout=5
        )
        assert result.returncode != 0
        assert result.stdout == b""
        assert result.stderr == b"invalid role packet\n"
