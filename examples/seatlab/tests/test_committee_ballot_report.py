import json
import sys

import pytest

from examples.seatlab.committee import analysis, ballot_packet, ballot_report, contract
from examples.seatlab.committee.ballot_schedule import Cell, grid
from examples.seatlab.committee.schedule import ROLES, TASK_TREE
from examples.seatlab.tests.test_committee_contract import _dossier, _stock_post
from examples.seatlab.visibility import deepseek


@pytest.fixture
def study(tmp_path):
    messages = [
        {"seat": role, "text": _stock_post(f"p{i}"), "seq": i}
        for i, role in enumerate(ROLES, 1)
    ]
    chat = {"messages": messages, "unique_speakers": list(ROLES)}
    teams = []
    for index in range(6):
        labels, aliases = contract._mapping(index)
        first_opts = "AAAB" if index == 1 else "ABBC"
        seats = []
        for i, opt in enumerate(first_opts, 1):
            first = {
                "choice": labels[f"Option_{opt}"],
                "cited": [aliases["F_SHARED_1"]],
            }
            dossier = _dossier(i)
            prompt = contract.vote_prompt(dossier, chat, index)
            action = contract.vote_action(json.dumps(first), dossier, chat, index)
            seats.append(
                {
                    "seat": f"S{i}",
                    "share": aliases[f"F_PRIV_{i}"],
                    "first": first,
                    "source_prompt_sha256": analysis._sha(prompt),
                    "source_action_sha256": analysis._sha(analysis._canonical(action)),
                }
            )
        teams.append(
            {"cell_id": f"hp-m{index}-r0-novelty", "map_index": index, "seats": seats}
        )
    packet = {
        "schema": "seatlab.committee-ballots/v1",
        "task_git_tree": TASK_TREE,
        "teams": teams,
    }
    path = tmp_path / "private-packet.json"
    raw = json.dumps(packet) + "\n"
    path.write_text(raw, encoding="utf-8")
    root = tmp_path / "private-study"
    (root / "cells").mkdir(parents=True)
    return path, root, analysis._sha(raw), packet


def _receipt(study, cell, vote="A", **overrides):
    _, root, packet_sha, packet = study
    seat = packet["teams"][cell.map_index]["seats"][cell.seat_index]
    ballot = {
        "choice": analysis._LABELS[cell.map_index]["ABC".index(vote)],
        "cited": seat["first"]["cited"],
    }
    text = "\n  " + json.dumps(ballot) + "\n"
    prompt = ballot_packet.prompt(packet, cell.map_index, cell.seat_index, cell.arm)
    row = {
        "schema": "seatlab.committee-peer-turn/v1",
        "cell_id": cell.cell_id,
        "packet_sha256": packet_sha,
        "status": "complete",
        "prompt_sha256": analysis._sha(prompt),
        "served_model": deepseek.MODEL,
        "response_sha256": analysis._sha(text),
        "response_text": text,
        "ballot": ballot,
    } | overrides
    path = root / "cells" / f"{cell.cell_id}.json"
    path.write_text(json.dumps(row), encoding="utf-8")
    return path


def _analyze(study):
    packet, root, expected, _ = study
    return ballot_report.analyze(packet, root, expected_sha256=expected)


def test_missing_map_does_not_cross_pair_blocks(study):
    for index in range(5):
        _receipt(study, Cell(0, index, 0, "private"), "B")
        _receipt(study, Cell(0, index, 0, "choices"), "A")
    _receipt(study, Cell(0, 5, 0, "private"))
    _receipt(study, Cell(1, 5, 0, "choices"))

    report = _analyze(study)
    assert [row["cell_id"] for row in report["cells"]] == [
        cell.cell_id for cell in grid()
    ]
    primary = report["contrasts"]["choices_minus_private"]
    assert primary["measures"]["correct"] == {
        "paired_denominator": 5,
        "treatment_numerator": 5,
        "comparison_numerator": 0,
        "supported_maps": 5,
        "effect": None,
    }
    assert primary["maps"][5]["measures"]["correct"]["effect"] is None
    assert primary["by_block"][1]["measures"]["correct"]["paired_denominator"] == 0


def test_seat_effect_weights_maps_and_group_pairs_require_four_seats(study):
    for seat in range(4):
        _receipt(study, Cell(0, 0, seat, "private"), "BBCC"[seat])
        _receipt(study, Cell(0, 0, seat, "choices"))
        _receipt(study, Cell(0, 0, seat, "cited"), "B")
        _receipt(study, Cell(1, 0, seat, "private"), "B")
        if seat < 3:
            _receipt(study, Cell(1, 0, seat, "choices"))
    for index in range(1, 6):
        for arm in ("private", "choices"):
            _receipt(study, Cell(0, index, 0, arm))

    report = _analyze(study)
    primary = report["contrasts"]["choices_minus_private"]
    correct = primary["measures"]["correct"]
    assert correct["effect"] == pytest.approx(1 / 6)
    assert correct["paired_denominator"] == 12
    assert correct["treatment_numerator"] - correct["comparison_numerator"] == 7
    assert primary["by_block"][0]["measures"]["correct"]["effect"] == pytest.approx(
        1 / 6
    )
    assert primary["by_block"][1]["measures"]["correct"]["effect"] is None
    group = primary["measures"]["correct_group_majority"]
    assert group["paired_denominator"] == 1
    assert group["effect"] is None
    assert primary["maps"][0]["measures"]["strict_group_majority"]["effect"] == 1
    assert primary["maps"][0]["measures"]["correct_group_majority"]["effect"] == 1
    partial = next(
        row
        for row in report["groups"]
        if (row["block"], row["map_index"], row["arm"]) == (1, 0, "choices")
    )
    assert partial["observed_seats"] == 3
    assert partial["strict_group_majority"] is None
    assert report["summary"]["by_arm"]["choices"]["measures"][
        "strict_group_majority"
    ] == {
        "numerator": 1,
        "denominator": 1,
    }
    secondary = report["contrasts"]["cited_minus_choices"]
    assert secondary["maps"][0]["measures"]["correct"]["effect"] == -1
    assert secondary["measures"]["correct"]["effect"] is None

    cells = {row["cell_id"]: row for row in report["cells"]}
    harmful = cells["pb-b0-m0-s1-cited"]
    assert harmful["adopted_peer_plurality"] is True
    assert harmful["switched_from_correct_to_wrong"] is True
    tied = cells["pb-b0-m0-s2-cited"]
    assert tied["peer_plurality"] == {"status": "tie", "choice": None}
    assert tied["adopted_peer_plurality"] is None
    agreed = cells["pb-b0-m1-s1-choices"]
    assert agreed["adoption_eligibility"] == "already_agrees"
    assert agreed["adopted_peer_plurality"] is None
    cited = report["summary"]["by_arm"]["cited"]["measures"]
    assert cited["adopted_peer_plurality"] == {"numerator": 2, "denominator": 2}
    assert cited["switched_from_correct_to_wrong"] == {"numerator": 1, "denominator": 1}
    assert cited["strict_group_majority"] == {"numerator": 1, "denominator": 1}
    assert cited["correct_group_majority"] == {"numerator": 0, "denominator": 1}


def test_receipts_bind_packet_prompt_model_response_and_ballot(
    study, monkeypatch, capsys
):
    path, root, expected, packet = study
    private_prompt = ballot_packet.prompt(packet, 0, 0, "private")
    bad_text = "private-response-raw"
    cases = [
        ({"cell_id": "pb-b0-m1-s1-private"}, "cell_id_mismatch"),
        ({"packet_sha256": "0" * 64}, "packet_hash_mismatch"),
        ({"prompt_sha256": analysis._sha(private_prompt)}, "prompt_hash_mismatch"),
        ({"served_model": deepseek.REVISION.upper()}, "served_model_invalid"),
        ({"response_sha256": "0" * 64}, "response_hash_mismatch"),
        ({"ballot": {"choice": "Z", "cited": ["K1"]}}, "ballot_mismatch"),
        (
            {"response_text": bad_text, "response_sha256": analysis._sha(bad_text)},
            "response_ballot_invalid",
        ),
    ]
    invalid = {}
    for i, (overrides, reason) in enumerate(cases):
        cell = Cell(0, i // 4, i % 4, "choices")
        _receipt(study, cell, **overrides)
        invalid[cell.cell_id] = reason
    rejected = Cell(0, 2, 0, "choices")
    _receipt(
        study,
        rejected,
        status="invalid_ballot",
        response_text=bad_text,
        response_sha256=analysis._sha(bad_text),
        ballot=None,
    )
    invalid[rejected.cell_id] = "invalid_ballot"
    started = Cell(0, 2, 1, "choices")
    _receipt(
        study,
        started,
        status="started",
        served_model=None,
        response_text=None,
        response_sha256=None,
        ballot=None,
    )
    linked = Cell(0, 2, 2, "choices")
    receipt_path = _receipt(study, linked)
    target = root / "outside-cells.json"
    receipt_path.rename(target)
    receipt_path.symlink_to(target)
    invalid[linked.cell_id] = "unsafe_evidence_path"
    valid = Cell(0, 2, 3, "choices")
    _receipt(study, valid, served_model=deepseek.REVISION.lower())
    _receipt(study, Cell(1, 2, 3, "choices"), served_model=deepseek.REVISION)
    duplicate = Cell(0, 3, 0, "choices")
    duplicate_path = _receipt(study, duplicate)
    raw = duplicate_path.read_text(encoding="utf-8")
    duplicate_path.write_text(raw[:-1] + ', "status": "complete"}', encoding="utf-8")
    invalid[duplicate.cell_id] = "receipt_malformed"
    (root / "cells" / "unregistered.json").write_text(bad_text, encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        ["ballot_report", str(path), str(root), "--expect-sha256", expected],
    )
    ballot_report.main()
    output = capsys.readouterr()
    assert not output.err
    report = json.loads(output.out)
    assert {
        row["cell_id"]: row["reason"]
        for row in report["cells"]
        if row["status"] == "invalid"
    } == invalid
    cells = {row["cell_id"]: row for row in report["cells"]}
    assert cells[started.cell_id]["status"] == "incomplete"
    assert cells[valid.cell_id]["status"] == "complete"
    assert cells["pb-b0-m0-s1-private"]["status"] == "missing"
    for private in (
        bad_text,
        str(root),
        "Your first ballot",
        "analyst_1",
        "api.deepseek.com",
        "outside-cells",
        "response_text",
        "unregistered",
    ):
        assert private not in output.out

    changed = path.read_text(encoding="utf-8") + "\n"
    path.write_text(changed, encoding="utf-8")
    with pytest.raises(ValueError):
        _analyze(study)
    current = ballot_report.analyze(path, root, expected_sha256=analysis._sha(changed))
    same_slot = next(row for row in current["cells"] if row["cell_id"] == valid.cell_id)
    assert same_slot["reason"] == "packet_hash_mismatch"


def test_rejects_linked_cells_directory(study):
    _, root, _, _ = study
    slot = Cell(0, 0, 0, "private")
    _receipt(study, slot)
    cells = root / "cells"
    target = root / "other"
    cells.rename(target)
    cells.symlink_to(target, target_is_directory=True)

    report = _analyze(study)
    assert {row["reason"] for row in report["cells"]} == {"unsafe_evidence_path"}
