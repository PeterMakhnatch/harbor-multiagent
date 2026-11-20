from pathlib import Path

import pytest

from examples.seatlab.tests.test_visibility_calendar_packet import _briefing, _source
from examples.seatlab.visibility import calendar_packet as packet
from examples.seatlab.visibility import calendar_replica_source as replica

_P1, _P2, _ADMIN = b"a" * 32, b"b" * 32, b"c" * 32


def _pin_packet(
    path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tables: tuple[packet.Table, packet.Table],
    original_hash: str = "0" * 64,
) -> None:
    data = packet._canonical(
        (packet.World(0, original_hash, tables),)
        + tuple(packet.World(index, f"{index:064x}", tables) for index in range(1, 12))
    )
    path.write_bytes(data)
    monkeypatch.setattr(packet, "PACKET", path)
    monkeypatch.setattr(packet, "PACKET_SHA256", packet._digest(data))


@pytest.fixture
def task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    task = _source(tmp_path.resolve(), 0)
    payload = task / "environment/task.json"
    payload.write_bytes(payload.read_bytes().rstrip(b"\r\n") + b"\n")
    compose = task / "environment/docker-compose.yaml"
    raw = compose.read_bytes().replace(
        b'  main:\n    environment:\n      FLOTILLA_SEATS: "p1,p2"\n'
        b'      FLOTILLA_CONDITION: "talk"\n',
        b'  main:\n    command: ["fixture"]\n    environment:\n'
        b'      FLOTILLA_CONDITION: "talk"\n      FLOTILLA_SEATS: "p1,p2"\n'
        b'      FLOTILLA_TOKENS: "p1:%s,p2:%s"\n'
        b'      FLOTILLA_ADMIN: "%s"\n' % (_P1, _P2, _ADMIN),
    )
    raw = raw.replace(b'"not-for-export"', b'"' + _P2 + b'"', 1)
    raw = raw.replace(b'"not-for-export"', b'"' + _P1 + b'"', 1)
    compose.write_bytes(
        raw.replace(_P1, b"1" * 32).replace(_P2, b"2" * 32).replace(_ADMIN, b"0" * 32)
    )
    monkeypatch.setattr(replica, "_SEMANTICS_SHA256", (packet._source_hashes(task)[0],))
    compose.write_bytes(raw)
    tables = (
        packet.normalize_briefing(_briefing("p1")),
        packet.normalize_briefing(_briefing("p2")),
    )
    _pin_packet(task.parent / "packet.json", monkeypatch, tables)
    return task


def test_rekey_changes_native_identity_only(task: Path) -> None:
    first = replica.verify(task, 0)
    assert first.native_sha256 == packet._source_hashes(task)[0]
    compose = task / "environment/docker-compose.yaml"
    raw = compose.read_bytes()
    compose.write_bytes(
        raw.replace(_P1, b"d" * 32).replace(_P2, b"e" * 32).replace(_ADMIN, b"f" * 32)
    )
    second = replica.verify(task, 0)
    assert second.native_sha256 == packet._source_hashes(task)[0]
    assert second.native_sha256 != first.native_sha256
    assert second.semantics_sha256 == first.semantics_sha256


@pytest.mark.parametrize(
    ("relative", "before", "after"),
    [
        ("tests/test.sh", b"fixture", b"Fixture"),
        ("environment/world.py", b"fixture", b"Fixture"),
        ("environment/Dockerfile", b"fixture", b"Fixture"),
        ("environment/task.json", b"\n", b"\r\n"),
        ("environment/docker-compose.yaml", b'"unused"', b'"Unused"'),
    ],
)
def test_exact_stock_bytes(
    task: Path, relative: str, before: bytes, after: bytes
) -> None:
    path = task / relative
    original = path.read_bytes()
    changed = original.replace(before, after, 1)
    assert changed != original
    path.write_bytes(changed)
    with pytest.raises(ValueError, match="pinned stock semantics"):
        replica.verify(task, 0)


@pytest.mark.parametrize(
    ("old_token", "replacement", "error"),
    [
        (_P1, b"", "missing or malformed"),
        (_P1, b'      SEAT_TOKEN: "' + b"f" * 32 + b'"\n', "inconsistent"),
        (_P1, b'      SEAT_TOKEN: "' + _P2 + b'"\n', "inconsistent"),
        (_P1, b'      SEAT_TOKEN: "' + b"g" * 32 + b'"\n', "missing or malformed"),
        (_P2, b'      SEAT_TOKEN: "' + b"b" * 31 + b'"\n', "missing or malformed"),
    ],
)
def test_role_binding_must_match(
    task: Path, old_token: bytes, replacement: bytes, error: str
) -> None:
    compose = task / "environment/docker-compose.yaml"
    line = b'      SEAT_TOKEN: "' + old_token + b'"\n'
    compose.write_bytes(compose.read_bytes().replace(line, replacement, 1))
    with pytest.raises(ValueError, match=error):
        replica.verify(task, 0)


def test_distinct_credentials(task: Path) -> None:
    compose = task / "environment/docker-compose.yaml"
    compose.write_bytes(compose.read_bytes().replace(_ADMIN, _P1, 1))
    with pytest.raises(ValueError, match="inconsistent calendar auth binding"):
        replica.verify(task, 0)


@pytest.mark.parametrize("kind", ["extra", "cache", "missing", "symlink"])
def test_stock_tree_only(task: Path, kind: str) -> None:
    if kind == "extra":
        (task / "untracked").touch()
    elif kind == "cache":
        (task / "environment/__pycache__").mkdir()
    else:
        path = task / "tests/test.sh"
        other = task.parent / "same-bytes.sh"
        other.write_bytes(path.read_bytes())
        path.unlink()
        if kind == "symlink":
            path.symlink_to(other)
    with pytest.raises(
        ValueError, match="unexpected file|incomplete|unsafe source file"
    ):
        replica.verify(task, 0)


def test_rejects_symlink_ancestor(task: Path) -> None:
    alias = task.parent / "alias"
    alias.symlink_to(task.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe replica task path"):
        replica.verify(alias / task.name, 0)


def test_checks_roles_and_distinguishes_original_checksum(
    task: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p1, p2 = packet.source_tables(task, 0)
    identity = replica.verify(task, 0)
    _pin_packet(packet.PACKET, monkeypatch, (p2, p1))
    with pytest.raises(ValueError, match="role tables differ"):
        replica.verify(task, 0)
    _pin_packet(packet.PACKET, monkeypatch, (p1, p2), identity.native_sha256)
    with pytest.raises(ValueError, match="repeats the original native checksum"):
        replica.verify(task, 0)
