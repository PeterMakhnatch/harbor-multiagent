import io
import json
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from examples.seatlab.committee import ballot_packet, contract
from examples.seatlab.tests import test_committee_analysis as native
from examples.seatlab.tests.test_committee_contract import _dossier, _section


def _source(tmp_path):
    study = tmp_path / "study"
    study.mkdir(mode=0o700)
    trials = []
    for index in range(6):
        trial = native._fixture(
            study,
            index,
            0,
            "novelty",
            n_private=1,
            choices=("Option_A", "Option_B", "Option_C", "Option_A"),
        )
        trials.append(trial)
        events = [
            json.loads(line)
            for line in (trial / native._WORLD).read_text(encoding="utf-8").splitlines()
        ]
        chat = {
            "unique_speakers": list(native._ROLES),
            "messages": [
                {**event["payload"], "seq": event["seq"]}
                for event in events
                if event["event_type"] == "chat_message"
            ],
        }
        result_path = trial / "result.json"
        result = native._read(result_path)
        for i, seat in enumerate(result["seat_results"]):
            source_prompt = contract.vote_prompt(_dossier(i + 1), chat, index)
            digest = native._digest(source_prompt)
            seat["agent_result"]["metadata"]["turns"]["vote"]["prompt_sha256"] = digest
            receipt_path = native._receipt_path(trial, i)
            receipt = native._read(receipt_path)
            receipt["turns"]["vote"]["prompt_sha256"] = digest
            native._write(receipt_path, receipt)
        native._write(result_path, result)
    spend_dir = native._ledger(
        tmp_path,
        [
            (
                f"hp-m{index}-r0-novelty-{phase}",
                role,
                native._FINISHED,
                native._SERVED,
                3,
                20,
                5,
                103,
                133,
            )
            for index in range(6)
            for role in native._ROLES
            for phase in ("share", "vote")
        ],
    )
    return study, spend_dir, trials


def _write_raw(path, raw):
    path.write_bytes(raw)
    return path, sha256(raw).hexdigest()


def _write_packet(path, packet):
    raw = (json.dumps(packet, sort_keys=True, separators=(",", ":")) + "\n").encode()
    return _write_raw(path, raw)


def bound_packet(tmp_path: Path) -> tuple[Path, str]:
    """Write a packet committed to fixture native actions and settled spend."""
    study, spend_dir, _ = _source(tmp_path)
    return _write_packet(
        tmp_path / "ballots.json",
        ballot_packet.export_packet(study, spend_dir=spend_dir),
    )


@pytest.fixture
def packet(tmp_path):
    return ballot_packet.read_packet(*bound_packet(tmp_path))


def test_peer_projection_uses_original_evidence_and_actual_blinded_ballots(packet):
    index = 4
    team = packet["teams"][index]
    assert team["cell_id"] == "hp-m4-r0-novelty"
    assert [row["first"]["choice"] for row in team["seats"]] == ["Z", "X", "Y", "Z"]
    for own, first in enumerate(team["seats"]):
        peers = [row for i, row in enumerate(team["seats"]) if i != own]
        frames = {
            arm: ballot_packet.prompt(packet, index, own, arm)
            for arm in ("private", "choices", "cited")
        }
        controls = {
            tuple(
                line
                for line in frame.splitlines()
                if not line.startswith("Peer ballots: ")
            )
            for frame in frames.values()
        }
        assert len(controls) == 1
        for arm, frame in frames.items():
            assert _section(frame, "Your first ballot") == first["first"]
            assert _section(frame, "Peer ballots") == [
                {
                    "seat": peer["seat"],
                    "choice": None if arm == "private" else peer["first"]["choice"],
                    "cited": peer["first"]["cited"] if arm == "cited" else None,
                }
                for peer in peers
            ]
            assert {row["fact"] for row in _section(frame, "Dossier")["facts"]} == {
                native._label(index, fid)
                for fid in (*native._SHARED, native._PRIVATE[own])
            }
            assert [row["fact"] for row in _section(frame, "Submissions")] == sorted(
                row["share"] for row in team["seats"]
            )
            for private_value in (
                "Option_",
                "analyst_",
                "F_PRIV_",
                "F_SHARED_",
                "hp-m",
                "novelty",
                "reward",
                "source_prompt_sha256",
                "source_action_sha256",
                "Correct answer",
                "private/archive",
            ):
                assert private_value not in frame


def test_decode_never_accepts_peer_private_citations_or_non_ballot_fields(packet):
    first = packet["teams"][0]["seats"][0]["first"]
    assert ballot_packet.decode(packet, 0, 0, json.dumps(first)) == first
    peer_private = native._label(0, native._PRIVATE[1])
    peers = _section(ballot_packet.prompt(packet, 0, 0, "cited"), "Peer ballots")
    assert peer_private in peers[0]["cited"]
    alias = first["cited"][0]
    for text in (
        json.dumps({"choice": "X", "cited": [peer_private]}),
        json.dumps({"choice": "X", "cited": [alias, alias]}),
        json.dumps(first | {"reward": 1}),
        '{"choice":"X","choice":"Y","cited":["K1"]}',
        json.dumps(first) + " {}",
        json.dumps(first) + " " * 8192,
    ):
        with pytest.raises(ballot_packet.InvalidBallot):
            ballot_packet.decode(packet, 0, 0, text)
    with pytest.raises(ValueError, match="map index"):
        ballot_packet.decode(packet, -1, 0, json.dumps(first))
    with pytest.raises(ValueError, match="seat index"):
        ballot_packet.prompt(packet, 0, True, "private")


@pytest.mark.parametrize(
    "changed",
    [
        "native_prompt",
        "both_prompts",
        "receipt_action",
        "world_action",
        "missing_receipt",
    ],
)
def test_export_refuses_a_source_mismatch(tmp_path, changed):
    study, spend_dir, trials = _source(tmp_path)
    trial = trials[3]
    receipt_path = native._receipt_path(trial, 0)
    result_path = trial / "result.json"
    mismatch = "source team hp-m3-r0-novelty unavailable"
    if changed in ("native_prompt", "both_prompts"):
        result = native._read(result_path)
        context = result["seat_results"][0]["agent_result"]["metadata"]
        context["turns"]["vote"]["prompt_sha256"] = "f" * 64
        native._write(result_path, result)
        if changed == "both_prompts":
            receipt = native._read(receipt_path)
            receipt["turns"]["vote"]["prompt_sha256"] = "f" * 64
            native._write(receipt_path, receipt)
            mismatch = "source vote prompt hash mismatch"
    elif changed == "receipt_action":
        receipt = native._read(receipt_path)
        receipt["turns"]["vote"]["action"]["choice"] = "Option_B"
        native._write(receipt_path, receipt)
    elif changed == "world_action":
        events = [
            json.loads(line)
            for line in (trial / native._WORLD).read_text(encoding="utf-8").splitlines()
        ]
        entry = next(event for event in events if event["event_type"] == "vote")
        entry["payload"]["choice"] = "Option_C"
        native._write_events(trial, events)
    else:
        receipt_path.unlink()
    with pytest.raises(ValueError, match=mismatch):
        ballot_packet.export_packet(study, spend_dir=spend_dir)


def test_export_does_not_substitute_another_repeat_or_ledger(tmp_path):
    study, spend_dir, trials = _source(tmp_path)
    with pytest.raises(ValueError, match="original spend evidence"):
        ballot_packet.export_packet(study, spend_dir=tmp_path / "absent")
    trials[5].parents[1].rename(study / "cells" / "hp-m5-r1-novelty")
    native._fixture(study, 5, 0, "open")
    with pytest.raises(ValueError, match="source team hp-m5-r0-novelty unavailable"):
        ballot_packet.export_packet(study, spend_dir=spend_dir)


def test_verify_rejects_wrong_bytes_and_unsafe_packet_semantics(tmp_path):
    path, digest = bound_packet(tmp_path)
    raw = path.read_bytes()
    path.write_bytes(raw + b"\n")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        ballot_packet.read_packet(path, digest)
    with pytest.raises(ValueError, match="invalid ballot packet JSON"):
        duplicate = raw.replace(b'{"schema":', b'{"schema":null,"schema":', 1)
        ballot_packet.read_packet(*_write_raw(path, duplicate))
    with pytest.raises(ValueError, match="size limit"):
        ballot_packet.read_packet(*_write_raw(path, raw + b" " * 16384))

    packet = json.loads(raw)
    packet["teams"][0]["seats"][0]["first"]["choice"] = "Y"
    with pytest.raises(ValueError, match="source vote action hash mismatch"):
        ballot_packet.read_packet(*_write_packet(path, packet))
    packet = json.loads(raw)
    packet["teams"][0]["seats"][0]["private_path"] = "/private/path"
    with pytest.raises(ValueError, match="invalid source seat fields"):
        ballot_packet.read_packet(*_write_packet(path, packet))
    packet = json.loads(raw)
    packet["teams"][0]["seats"].reverse()
    with pytest.raises(ValueError, match="invalid source seat order"):
        ballot_packet.read_packet(*_write_packet(path, packet))

    path, digest = _write_raw(path, raw)
    link = tmp_path / "symlink.json"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="packet unreadable"):
        ballot_packet.read_packet(link, digest)


def test_cli_exports_verifies_and_decodes_without_private_inputs(
    tmp_path, monkeypatch, capsys
):
    study, spend_dir, _ = _source(tmp_path)

    def cli(*args):
        monkeypatch.setattr(sys, "argv", ["ballot_packet", *map(str, args)])
        ballot_packet.main()
        result = capsys.readouterr()
        assert not result.err
        return result.out

    raw = cli("export", study, "--spend-dir", spend_dir)
    packet = json.loads(raw)
    assert set(packet) == {"schema", "task_git_tree", "teams"}
    assert raw == json.dumps(packet, sort_keys=True, separators=(",", ":")) + "\n"
    assert "/private/" not in raw and "Option_" not in raw and "DeepSeek" not in raw
    path, digest = _write_raw(tmp_path / "export.json", raw.encode())
    packet_args = (path, "--expect-sha256", digest)
    assert cli("verify", *packet_args) == ""
    selection = (*packet_args, "--map", 0, "--seat", 1)
    frame = cli("prompt", *selection, "--arm", "private")
    first = _section(frame, "Your first ballot")
    monkeypatch.setattr(
        sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(first).encode()))
    )
    assert json.loads(cli("decode", *selection)) == first
