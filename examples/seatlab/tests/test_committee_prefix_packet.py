import hashlib
import json
import sys
from copy import deepcopy

import pytest

from examples.seatlab.committee import (
    prefix_packet,
    recommendation,
    recommendation_report,
    schedule,
)
from examples.seatlab.tests import test_committee_analysis as base
from examples.seatlab.tests import test_committee_recommendation_report as native


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def _write_packet(path, value):
    raw = _canonical(value) if isinstance(value, dict) else value
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _load_value(tmp_path, value):
    path = tmp_path / "prefix.json"
    return prefix_packet.load(path, _write_packet(path, value))


def _rehash(row):
    row["prefix_sha256"] = hashlib.sha256(_canonical(row["events"])).hexdigest()


def _serial_fixture(trial):
    rows = native._events(trial)
    role = schedule.ROLES[1]
    read = next(
        row
        for row in rows
        if row["event_type"] == "message_read" and row["actor"] == role
    )
    refs = [
        row["seq"]
        for row in rows[: read["seq"] - 1]
        if row["event_type"] == "chat_message"
    ]
    # This ready view gains an authentic faster peer's proposal. Its model
    # prompt still uses the same 4 facts, so the existing prompt hash is valid.
    assert len(refs) == len(read["payload"]["msg_seqs"]) + 1
    read["payload"]["msg_seqs"] = refs
    native._edit_commitments(
        trial, 1, lambda metadata: metadata["read_msg_seqs"].update(proposal=refs)
    )
    base._write_events(trial, rows)
    return rows


@pytest.fixture
def serial_source(tmp_path):
    study = tmp_path / "original"
    trial, calls = native._trial(study, "masked", index=1, final=native._WIN)
    original = _serial_fixture(trial)
    spend = base._ledger(tmp_path, calls)
    return study, spend, original


def test_packet_replays_only_the_exact_original_native_prefix(tmp_path, serial_source):
    study, spend, original = serial_source
    packet = prefix_packet.export_packet(study, spend_dir=spend)
    loaded = _load_value(tmp_path, packet)
    assert list(loaded) == list(range(6))
    assert [index for index, point in loaded.items() if point is not None] == [1]
    point = loaded[1]
    assert point is not None
    assert (point.map_index, point.chair) == (1, schedule.chair(1, 0))
    ninth = [row for row in original if row["event_type"] == "chat_message"][-1]
    assert point.events == tuple(
        {key: value for key, value in row.items() if key != "ts"}
        for row in original[: ninth["seq"]]
    )
    assert point.prefix_sha256 == hashlib.sha256(_canonical(point.events)).hexdigest()
    assert [step.seq for step in point.steps] == [
        row["seq"]
        for row in point.events
        if row["event_type"] in ("dossier_read", "message_read", "chat_message")
    ]
    messages = []
    for step in point.steps:
        event = original[step.seq - 1]
        assert step.role == event["actor"]
        if step.kind == "post":
            assert step.body == {"text": event["payload"]["text"]}
            assert step.expected is not None
            assert step.expected == event["payload"] | {"seq": step.seq}
            messages.append(step.expected)
        elif step.kind == "read":
            assert step.body is None
            assert event["payload"]["msg_seqs"] == [row["seq"] for row in messages]
            assert step.expected == {
                "messages": messages,
                "unique_speakers": sorted({row["seat"] for row in messages}),
            }
        else:
            assert step.kind == "dossier"
            assert step.body is step.expected is None
    assert point.board == {
        "messages": messages,
        "unique_speakers": list(schedule.ROLES),
    }
    board = recommendation.board(point.board, point.map_index, point.chair)
    assert board.proposals.keys() == set(schedule.ROLES)
    assert board.recommendation is not None
    raw = _canonical(packet)
    for forbidden in (
        '"ts"',
        '"vote"',
        '"seat_done"',
        "correct",
        "reward",
        "PRIVATE-RUN-ID",
        str(study),
        "response_sha256",
        "private fixture response",
    ):
        assert forbidden.encode() not in raw


def test_authenticated_concurrent_snapshot_is_not_a_serial_get(tmp_path):
    study = tmp_path / "original"
    trial, calls = native._trial(study, "masked")
    spend = base._ledger(tmp_path, calls)
    rows = native._events(trial)
    divergent = next(
        row
        for row in rows
        if row["event_type"] == "message_read" and row["actor"] == schedule.ROLES[1]
    )
    before = [
        row["seq"]
        for row in rows[: divergent["seq"] - 1]
        if row["event_type"] == "chat_message"
    ]
    assert divergent["payload"]["msg_seqs"] == before[:-1]
    report = recommendation_report.analyze(study, spend_dir=spend)
    assert [row["cell_id"] for row in report["cells"]] == ["rc-m0-r0-masked"]
    packet = prefix_packet.export_packet(study, spend_dir=spend)
    assert packet["sources"][0]["reason"] == "prefix_not_serial"
    assert _load_value(tmp_path, packet) == dict.fromkeys(range(6))


def test_another_arm_or_repetition_cannot_replace_missing_source(tmp_path):
    study = tmp_path / "original"
    shown, shown_calls = native._trial(study, "shown")
    later, later_calls = native._trial(study, "masked", rep=1)
    _serial_fixture(shown)
    _serial_fixture(later)
    spend = base._ledger(tmp_path, shown_calls + later_calls)
    report = recommendation_report.analyze(study, spend_dir=spend)
    assert {row["cell_id"] for row in report["cells"]} == {
        "rc-m0-r0-shown",
        "rc-m0-r1-masked",
    }
    packet = prefix_packet.export_packet(study, spend_dir=spend)
    assert [row["cell_id"] for row in packet["sources"]] == [
        f"rc-m{i}-r0-masked" for i in range(6)
    ]
    assert _load_value(tmp_path, packet) == dict.fromkeys(range(6))
    packet["sources"][0].update(cell_id="rc-m0-r1-masked", repetition=1)
    with pytest.raises(ValueError, match="unregistered prefix source"):
        _load_value(tmp_path, packet)


def test_spend_must_attest_registered_source(tmp_path, serial_source):
    study, _, _ = serial_source
    packet = prefix_packet.export_packet(study, spend_dir=tmp_path / "absent-ledger")
    assert packet["sources"][1]["reason"] == "source_spend_unavailable"
    assert _load_value(tmp_path, packet)[1] is None


def test_reordered_authentic_posts_fail_after_both_hashes_are_recomputed(
    tmp_path, serial_source
):
    study, spend, _ = serial_source
    packet = prefix_packet.export_packet(study, spend_dir=spend)
    row = packet["sources"][1]
    posts = [event for event in row["events"] if event["event_type"] == "chat_message"]
    proposal, advice = posts[-2:]
    assert proposal["payload"]["text"].startswith("PROPOSAL ")
    assert advice["payload"]["text"].startswith("RECOMMEND ")
    for field in ("actor", "payload"):
        proposal[field], advice[field] = advice[field], proposal[field]
    _rehash(row)
    with pytest.raises(ValueError, match="did not follow its authenticated reads"):
        _load_value(tmp_path, packet)


@pytest.mark.parametrize(
    "change", ["fact", "prose", "truth", "interleaved", "vote", "late_post", "omitted"]
)
def test_packet_semantics_are_checked_independently_of_commitments(
    tmp_path, serial_source, change
):
    study, spend, original = serial_source
    packet = prefix_packet.export_packet(study, spend_dir=spend)
    row = packet["sources"][1]
    events = row["events"]
    fact = next(event for event in events if event["event_type"] == "chat_message")
    if change == "fact":
        text = fact["payload"]["text"]
        assert '"points":9' in text
        fact["payload"]["text"] = text.replace('"points":9', '"points":99')
    elif change == "prose":
        fact["payload"]["text"] = 'FACT {"summary":"uncaptured private prose"}'
    elif change == "truth":
        truth = next(
            event for event in events if event["event_type"] == "dossier_truth"
        )
        truth["recipients"] = [schedule.ROLES[0]]
    elif change == "interleaved":
        assert events[2]["event_type"] == "dossier_truth"
        assert events[3] == fact
        truth = events[2]
        events[2] = fact | {"seq": 3}
        events[3] = truth | {"seq": 4, "payload": truth["payload"] | {"read_seq": 3}}
    elif change == "vote":
        vote = next(event for event in original if event["event_type"] == "vote")
        events[4] = prefix_packet.normalize_event(vote) | {"seq": 5}
    elif change == "late_post":
        events.append(fact | {"seq": len(events) + 1})
    else:
        events.pop(1)
    _rehash(row)
    with pytest.raises(ValueError):
        _load_value(tmp_path, packet)


def test_native_audit_is_not_reselected_after_admission(
    tmp_path, serial_source, monkeypatch
):
    study, spend, original = serial_source
    trial = study / "cells/rc-m1-r0-masked/trials/committee-rc-m1-r0-masked"
    analyze = recommendation_report.analyze

    def replace_after_admission(root, *, spend_dir):
        report = analyze(root, spend_dir=spend_dir)
        original[0]["ts"] = "2026-11-20T00:00:01+00:00"
        base._write_events(trial, original)
        return report

    monkeypatch.setattr(recommendation_report, "analyze", replace_after_admission)
    packet = prefix_packet.export_packet(study, spend_dir=spend)
    assert packet["sources"][1]["reason"] == "source_audit_not_bound"
    assert _load_value(tmp_path, packet)[1] is None


def test_packet_needs_exact_canonical_bytes_and_no_duplicate_keys(
    tmp_path, serial_source
):
    study, spend, original = serial_source
    packet = prefix_packet.export_packet(study, spend_dir=spend)
    path = tmp_path / "prefix.json"
    digest = _write_packet(path, packet)
    spaced = _canonical(packet) + b"\n"
    spaced_digest = _write_packet(path, spaced)
    with pytest.raises(ValueError, match="packet SHA-256 mismatch"):
        prefix_packet.load(path, digest)
    with pytest.raises(ValueError, match="canonical JSON"):
        prefix_packet.load(path, spaced_digest)
    duplicated = _canonical(packet).replace(b'"seq":1,', b'"seq":1,"seq":1,', 1)
    with pytest.raises(ValueError, match="invalid prefix packet JSON"):
        prefix_packet.load(path, _write_packet(path, duplicated))
    row = packet["sources"][1]
    row["prefix_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="prefix SHA-256 mismatch"):
        prefix_packet.load(path, _write_packet(path, packet))
    event = original[0]
    assert prefix_packet.normalize_event(event) == prefix_packet.normalize_event(
        event | {"ts": "2026-11-20T00:00:02+00:00"}
    )
    with pytest.raises(ValueError):
        prefix_packet.normalize_event(event | {"ts": "private raw text"})
    with pytest.raises(ValueError):
        prefix_packet.normalize_event(event | {"extra": "private raw text"})


def test_check_dossier_validates_facts_but_ignores_uncaptured_descriptions():
    role = schedule.ROLES[0]
    dossier = recommendation_report._dossier(role)
    for fact in dossier["shared_facts"]:
        fact.update(kind="positive_criterion", text="uncaptured description")
    dossier["private_facts"][0].update(
        kind="critical_flaw", text="uncaptured description"
    )
    dossier["rubric"] = "uncaptured description"
    prefix_packet.check_dossier(role, dossier)
    with pytest.raises(ValueError):
        prefix_packet.check_dossier(schedule.ROLES[1], dossier)
    for field, value in [
        ("points", True),
        ("kind", "major_strength"),
        ("option", "Option_C"),
    ]:
        altered = deepcopy(dossier)
        altered["private_facts"][0][field] = value
        with pytest.raises(ValueError):
            prefix_packet.check_dossier(role, altered)
    with pytest.raises(ValueError):
        prefix_packet.check_dossier(role, dossier | {"other_seat_dossier": {}})


def test_cli_exports_canonical_bytes_and_verifies_only_counts(
    tmp_path, serial_source, monkeypatch, capsysbinary
):
    study, spend, _ = serial_source
    monkeypatch.setattr(
        sys, "argv", ["prefix_packet", "export", str(study), "--spend-dir", str(spend)]
    )
    prefix_packet.main()
    result = capsysbinary.readouterr()
    assert result.err == b""
    assert result.out == _canonical(json.loads(result.out))
    path = tmp_path / "prefix.json"
    digest = _write_packet(path, result.out)
    monkeypatch.setattr(
        sys, "argv", ["prefix_packet", "verify", str(path), "--expect-sha256", digest]
    )
    prefix_packet.main()
    result = capsysbinary.readouterr()
    assert result.err == b""
    assert json.loads(result.out) == {"eligible_sources": 1, "ineligible_sources": 5}
