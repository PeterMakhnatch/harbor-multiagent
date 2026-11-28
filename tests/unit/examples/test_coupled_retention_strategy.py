import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from examples.seatlab.coupled_retention import strategy


def test_tables_are_canonical_and_contain_only_role_digest_rules():
    observations = {b"private observation\x00\xff": "Δ", b"": "A"}
    raw = strategy.table("east", observations)
    assert raw == strategy.table("east", dict(reversed(list(observations.items()))))
    expected = {
        hashlib.sha256(packet).hexdigest(): label
        for packet, label in observations.items()
    }
    assert json.loads(raw) == {
        "schema": "seatlab.private-ballot-strategy/v1",
        "role": "east",
        "rules": expected,
    }
    assert b"private observation" not in raw
    assert (
        raw
        == json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":")).encode()
    )
    assert strategy.load(raw, "east") == expected
    for packet, label in observations.items():
        assert strategy.choose(expected, packet) == label
    with pytest.raises(ValueError):
        strategy.choose(expected, b"unknown private observation")
    with pytest.raises(ValueError):
        strategy.load(raw, "west")


@pytest.mark.parametrize(
    "label", ["", " A", "A ", "two\nlines", "\x00", "\ud800", "x" * 129, 1]
)
def test_reject_invalid_action_labels(label):
    with pytest.raises(ValueError):
        strategy.table("east", {b"packet": label})
    raw = json.dumps(
        {
            "schema": strategy.SCHEMA,
            "role": "east",
            "rules": {strategy.digest(b"packet"): label},
        }
    ).encode()
    with pytest.raises(ValueError):
        strategy.load(raw, "east")


def test_observation_and_table_limits():
    packets = {index.to_bytes(2, "big"): "🟢" * 128 for index in range(256)}
    assert len(strategy.load(strategy.table("west", packets), "west")) == 256
    packets[b"another"] = "B"
    with pytest.raises(ValueError):
        strategy.table("west", packets)
    raw = json.dumps(
        {
            "schema": strategy.SCHEMA,
            "role": "west",
            "rules": {
                strategy.digest(packet): label for packet, label in packets.items()
            },
        }
    ).encode()
    with pytest.raises(ValueError):
        strategy.load(raw, "west")
    assert strategy.digest(b"\xff" * 8192) == hashlib.sha256(b"\xff" * 8192).hexdigest()
    with pytest.raises(ValueError):
        strategy.digest(b"\xff" * 8193)
    with pytest.raises(ValueError):
        strategy.table("east", {b"\xff" * 8193: "A"})
    with pytest.raises(ValueError):
        strategy.load(
            strategy.table("east", {b"": "A"}) + b" " * strategy.MAX_TABLE_BYTES, "east"
        )


def test_rejects_duplicate_alternate_and_malformed_wire_fields():
    key = strategy.digest(b"packet").encode()
    valid = strategy.table("east", {b"packet": "A"})
    for bad in (
        b"\xff",
        b"null",
        b"[]",
        b"{}",
        valid + b"{}",
        valid.replace(b'"role":"east"', b'"role":"east","role":"east"'),
        valid.replace(b'"role":"east"', b'"role":"east","source":"control"'),
        valid.replace(b'"role":"east"', b'"role":"north"'),
        valid.replace(b'"role":"east"', b'"participant":"east"'),
        valid.replace(b'"role":"east"', b'"role":null'),
        valid.replace(b'"rules":{', b'"rules2":{'),
        valid.replace(strategy.SCHEMA.encode(), b"different-schema"),
        valid.replace(key, key.upper()),
        valid.replace(key, key[:-1]),
        valid.replace(key, b"g" + key[1:]),
        valid.replace(b'"' + key + b'":"A"', b'"' + key + b'":"A","' + key + b'":"B"'),
        valid.replace(b'"A"', b"NaN"),
        valid.replace(b'"A"', b'{"action":"A"}'),
    ):
        with pytest.raises(ValueError):
            strategy.load(bad, "east")
    with pytest.raises(ValueError):
        strategy.table("north", {b"packet": "A"})


def test_standalone_private_vote_uses_exact_bytes_and_one_optional_frame(tmp_path):
    packet = b"\x00\xff\nprivate\n"
    maximum = b"\x80" * 8192
    table_path = tmp_path / "role.json"
    table_path.write_bytes(
        strategy.table("east", {packet: "B", packet[:-1]: "C", maximum: "D", b"": "E"})
    )
    command = [
        sys.executable,
        "-IS",
        str(Path(strategy.__file__).resolve()),
        "--table",
        str(table_path),
    ]

    def run(raw: bytes, *options: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [*command, *options],
            input=raw,
            capture_output=True,
            cwd=tmp_path,
            timeout=5,
            check=False,
        )

    for raw, options, expected in (
        (packet, [], {"action": "B"}),
        (packet[:-1], [], {"action": "C"}),
        (b"", [], {"action": "E"}),
        (packet + b"\n", ["--format", "slot"], {"slot": "B"}),
        (packet, ["--format", "slot"], {"slot": "C"}),
        (packet[:-1], ["--format", "slot"], {"slot": "C"}),
        (maximum, [], {"action": "D"}),
        (maximum + b"\n", ["--format", "slot"], {"slot": "D"}),
    ):
        result = run(raw, "--role", "east", *options)
        assert result.returncode == 0
        assert result.stderr == b""
        assert json.loads(result.stdout) == expected

    for raw, options in (
        (packet, ["--role", "west"]),
        (packet, ["--role", "north"]),
        (b"secret unrecognized packet", ["--role", "east"]),
        (packet + b"\n", ["--role", "east"]),
        (packet + b"\n\n", ["--role", "east", "--format", "slot"]),
        (maximum + b"\n", ["--role", "east"]),
        (maximum + b"\n\n", ["--role", "east", "--format", "slot"]),
    ):
        result = run(raw, *options)
        assert result.returncode != 0
        assert result.stdout == b""
        assert result.stderr == b"invalid private ballot strategy or observation\n"
