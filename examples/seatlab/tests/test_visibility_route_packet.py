import json
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from examples.seatlab.visibility import listener, route_packet
from examples.seatlab.visibility.crucible import RunRecord


def _encode(value: object) -> bytes:
    return (json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n").encode()


def _load(path: Path, raw: bytes) -> tuple[route_packet.Case, ...]:
    path.write_bytes(raw)
    return route_packet.load(path, sha256(raw).hexdigest())


@pytest.fixture
def packet() -> dict[str, Any]:
    cases = []
    for policy in range(3):
        for rnd in range(1, 7):
            base = (rnd - 1) * 12
            cases.append(
                {
                    "policy": policy,
                    "round": rnd,
                    "trial_sha256": sha256(f"trial-{policy}".encode()).hexdigest(),
                    "source_sha256": sha256(f"ledger-{policy}".encode()).hexdigest(),
                    "reason": None,
                    "reports": [
                        [[75, 60], [65, 60]],
                        [[75, 66], [65, 71]],
                        [[60, 66], [60, 71]],
                    ],
                    "truth": [[55, 60, 66], [65, 60, 71]],
                    "best": 1,
                    "truth_seqs": [base + 2, base + 4, base + 6],
                    "report_seqs": [base + 9, base + 7, base + 8],
                    "fetch_seq": base + 10,
                    "decision_seq": base + 11,
                }
            )
    return {"schema": "seatlab.visibility-route-packet/v1", "cases": cases}


def test_digest_catches_numeric_change_that_preserves_best(tmp_path, packet):
    path = tmp_path / "packet.json"
    raw = _encode(packet)
    first = _load(path, raw)[0]
    assert first.truth == ((55, 60, 66), (65, 60, 71))
    assert first.best == 1
    assert first.report_seqs == (9, 7, 8)
    packet["cases"][0]["reports"][0][0][0] = 76
    path.write_bytes(_encode(packet))
    with pytest.raises(ValueError, match="unexpected route packet SHA-256"):
        route_packet.load(path, sha256(raw).hexdigest())


def test_case_reorder_rejected_even_with_new_digest(tmp_path, packet):
    cases = packet["cases"]
    cases[0], cases[1] = cases[1], cases[0]
    with pytest.raises(ValueError, match="policy or round order"):
        _load(tmp_path / "packet.json", _encode(packet))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("policy", False, "invalid packet integer"),
        ("reports", [[[101, 60], [65, 60]]] * 3, "invalid packet integer"),
        ("best", True, "invalid packet integer"),
        ("truth", [[65, 60, 71], [55, 60, 66]], "disagree with best route"),
        ("truth", [[55, 60, 66], [54, 61, 66]], "tied physical route scores"),
        ("decision_seq", 10, "invalid packet integer"),
        ("report_seqs", [9, 7, 10], "invalid packet integer"),
        ("report_seqs", [9, 7, 7], "reused event reference"),
        ("truth_seqs", [4, 2, 6], "not in ledger order"),
        ("truth_seqs", [2, 4, 14], "reused event reference"),
        ("trial_sha256", "raw-trial-id", "invalid SHA-256 commitment"),
        ("reason", "raw text", "unknown ineligible reason"),
        ("reason", "missing_observation_truth", "inconsistent ineligible case"),
        ("text", "not allowlisted", "unexpected packet fields"),
    ],
)
def test_structural_tamper_rejected_with_new_digest(
    tmp_path, packet, field, value, message
):
    packet["cases"][0][field] = value
    with pytest.raises(ValueError, match=message):
        _load(tmp_path / "packet.json", _encode(packet))


@pytest.mark.parametrize("key", ["trial_sha256", "source_sha256"])
def test_fixed_distinct_sources(tmp_path, packet, key):
    path = tmp_path / "packet.json"
    cases = packet["cases"]
    cases[6][key] = cases[0][key]
    with pytest.raises(ValueError, match="source changes within a policy"):
        _load(path, _encode(packet))
    for case in cases[6:12]:
        case[key] = cases[0][key]
    with pytest.raises(ValueError, match="source repeats across policies"):
        _load(path, _encode(packet))


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        (b'"best":1,"best":1', "duplicate packet key"),
        (b'"best":NaN', "invalid packet number"),
        (b'"best":1e999', "invalid packet number"),
        (b'"best": 1', "not canonical JSON"),
    ],
)
def test_strict_packet_json(tmp_path, packet, replacement, message):
    raw = _encode(packet).replace(b'"best":1', replacement, 1)
    with pytest.raises(ValueError, match=message):
        _load(tmp_path / "packet.json", raw)


def test_packet_size_cap_applies_before_json_parse(tmp_path):
    with pytest.raises(ValueError, match="packet exceeds the size cap"):
        _load(tmp_path / "packet.json", b" " * (route_packet._MAX_PACKET_BYTES + 1))


_RECEIVER = "decision_maker"
_FIELDS = {
    "scout_1": ("safety", "latency"),
    "scout_2": ("safety", "reliability"),
    "scout_3": ("reliability", "latency"),
}
_WORLD = {
    "route_alpha": {"latency": 55, "reliability": 60, "safety": 66},
    "route_beta": {"latency": 65, "reliability": 60, "safety": 71},
}


def _events(policy: str, first: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(rnd, kind, actor, visibility, recipients, **payload):
        seq = len(rows) + 1
        rows.append(
            {
                "seq": seq,
                "task_family": "epistemic-crucible",
                "condition": policy,
                "seed": 42,
                "event_type": kind,
                "actor": actor,
                "authenticated": actor != "world",
                "visibility": visibility,
                "recipients": recipients,
                "payload": {"round": rnd, **payload},
            }
        )
        return seq

    for rnd in (2, 1, 3, 4, 5, 6):
        report_seqs = []
        for seat, fields in _FIELDS.items():
            read = add(rnd, "observation_read", seat, "world_audit", [])
            scores = {
                route: {field: data[field] for field in fields}
                for route, data in _WORLD.items()
            }
            add(
                rnd,
                "observation_truth",
                "world",
                "world_private",
                [seat],
                seat=seat,
                attributes=list(fields),
                read_seq=read,
                **scores,
            )
            if first and rnd == 3 and seat == "scout_1":
                rows[read - 1]["seed"] = 43
            if first and rnd == 4 and seat == "scout_1":
                rows.append({**rows[-1], "seq": len(rows) + 1})
            claim = {route: data.copy() for route, data in scores.items()}
            if seat == "scout_3":
                claim["route_alpha"]["latency"] = 75
            report_seqs.append(
                add(
                    rnd,
                    "scout_report",
                    seat,
                    "public",
                    [_RECEIVER],
                    seat=seat,
                    claim=claim,
                    note="PRIVATE_NOTE",
                )
            )
        refs = report_seqs[:2] if first and rnd == 1 else report_seqs
        add(
            rnd,
            "message_read",
            _RECEIVER,
            "world_audit",
            [],
            channel="round_reports",
            msg_seqs=refs,
        )
        add(rnd, "decision", _RECEIVER, "world_audit", [], choice="route_alpha")
    return rows


def test_archive_export_uses_first_scheduled_uuid_and_only_scoped_truth(
    tmp_path, monkeypatch
):
    root = tmp_path.resolve()
    records = []
    selected = []
    for index, policy in enumerate(listener.POLICIES):
        uuids = [str(UUID(int=index * 10 + n)) for n in (1, 2)]
        first = min(
            uuids,
            key=lambda uid: sha256(f"seatlab-listener-v1:{uid}".encode()).digest(),
        )
        for uid in reversed(uuids):
            trial = root / uid
            events = trial / "artifacts/logs/world/events.jsonl"
            events.parent.mkdir(parents=True)
            raw = b"".join(_encode(row) for row in _events(policy, uid == first))
            events.write_bytes(raw)
            records.append(
                RunRecord(
                    trial, listener.FAMILY, policy, uid, (), True, frozenset(), ()
                )
            )
            if uid == first:
                selected.append((uid, sha256(raw).hexdigest()))
    monkeypatch.setattr(listener, "run_records", lambda _: iter(records))
    raw = route_packet.export_packet(root)
    cases = _load(root / "packet.json", raw)
    assert raw == route_packet.export_packet(root)
    assert all(
        secret not in raw
        for secret in (b"PRIVATE_NOTE", b"route_alpha", str(root).encode())
    )
    for policy, (uid, digest) in enumerate(selected):
        assert uid.encode() not in raw
        first, second, wrong_seed, repeated_read, fifth, sixth = cases[
            policy * 6 : policy * 6 + 6
        ]
        assert first.reason == "incomplete_predecision_fetch"
        assert first.trial_sha256 == sha256(uid.encode()).hexdigest()
        assert first.source_sha256 == digest
        assert second.truth == ((55, 60, 66), (65, 60, 71))
        assert second.best == 1  # Reports and the archived choice do not set the key.
        assert second.reports == (
            ((75, 60), (65, 60)),
            ((55, 66), (65, 71)),
            ((60, 66), (60, 71)),
        )
        assert second.report_seqs == (9, 3, 6)
        assert wrong_seed.reason == repeated_read.reason == "invalid_observation_truth"
        assert fifth.best == sixth.best == 1
