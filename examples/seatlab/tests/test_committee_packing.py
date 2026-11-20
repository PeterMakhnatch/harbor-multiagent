import json
from copy import deepcopy
from hashlib import sha256
from itertools import product

import pytest

from examples.seatlab.committee import ballot_packet, contract, packing
from examples.seatlab.committee.schedule import ROLES
from examples.seatlab.tests.test_committee_ballot_packet import bound_packet
from examples.seatlab.tests.test_committee_contract import (
    _MAPS,
    _dossier,
    _section,
    _stock_post,
)


@pytest.fixture(scope="module")
def source_packet():
    return ballot_packet.read_packet(packing.PACKET, packing.PACKET_SHA256)


@pytest.fixture(scope="module")
def duplicate_packet(tmp_path_factory):
    return ballot_packet.read_packet(
        *bound_packet(tmp_path_factory.mktemp("duplicates"))
    )


def _source_inputs(packet, map_index, seat_index):
    _, _, order = _MAPS[map_index]
    keys = {f"K{i}": key for i, key in enumerate(order.split(), 1)}
    seats = packet["teams"][map_index]["seats"]
    return _dossier(seat_index + 1), {
        "unique_speakers": list(ROLES),
        "messages": [
            {"seat": role, "seq": i, "text": _stock_post(keys[seat["share"]])}
            for i, (role, seat) in enumerate(zip(ROLES, seats, strict=True), 1)
        ],
    }


def _assert_layouts(packet, map_index, seat_index):
    separate = packing.prompt(
        packet, packing.Cell(0, map_index, seat_index, "separate")
    )
    indexed = packing.prompt(packet, packing.Cell(0, map_index, seat_index, "indexed"))
    dossier, chat = _source_inputs(packet, map_index, seat_index)
    assert (
        separate.encode()
        == contract.tally_prompt(dossier, chat, map_index, "first").encode()
    )
    assert separate.splitlines()[:1] + separate.splitlines()[3:] == (
        indexed.splitlines()[:1] + indexed.splitlines()[2:]
    )

    index = _section(indexed, "Fact index")
    original = _section(separate, "Dossier")
    submissions = _section(separate, "Submissions")
    assert index.keys() == original.keys() == {"options", "facts"}
    assert index["options"] == original["options"] == ["X", "Y", "Z"]
    rows = index["facts"]
    aliases = [row["fact"] for row in rows]
    assert aliases == sorted(set(aliases))

    dossier_by_alias = {row["fact"]: row for row in original["facts"]}
    posted_by_alias = {row["fact"]: row for row in submissions}
    assert set(aliases) == dossier_by_alias.keys() | posted_by_alias.keys()
    rebuilt_dossier, rebuilt_submissions = [], []
    for row in rows:
        fact = {k: v for k, v in row.items() if k not in {"in_dossier", "post_count"}}
        assert row["in_dossier"] is (row["fact"] in dossier_by_alias)
        assert type(row["post_count"]) is int
        assert row["post_count"] >= 0
        if row["in_dossier"]:
            rebuilt_dossier.append(fact)
        rebuilt_submissions.extend([fact] * row["post_count"])
    assert rebuilt_dossier == original["facts"]
    assert rebuilt_submissions == submissions
    return {row["fact"]: row for row in rows}


def test_grid_has_fixed_scope_ids_and_counterbalanced_order():
    cells = packing.grid()
    assert isinstance(cells, tuple)
    assert len({cell.cell_id for cell in cells}) == len(cells) == 96
    for pair, (block, map_index, seat) in zip(
        zip(cells[::2], cells[1::2], strict=True),
        product(range(2), range(6), range(4)),
        strict=True,
    ):
        arms = ("separate", "indexed")
        if (block + map_index + seat) % 2:
            arms = arms[::-1]
        for cell, arm in zip(pair, arms, strict=True):
            assert cell == packing.Cell(block, map_index, seat, arm)
            assert cell.role == ROLES[seat]
            assert cell.cell_id == f"ix-b{block}-m{map_index}-s{seat + 1}-{arm}"


def test_both_layouts_reconstruct_all_committed_source_seats(source_packet):
    for map_index, seat in product(range(6), range(4)):
        _assert_layouts(source_packet, map_index, seat)


def test_index_preserves_repeated_posts_equal_facts_and_unposted_local_fact(
    duplicate_packet,
):
    rows = _assert_layouts(duplicate_packet, 0, 1)
    assert rows["K6"] == {
        "fact": "K6",
        "option": "Y",
        "points": 10,
        "provenance": "shared",
        "in_dossier": True,
        "post_count": 3,
    }
    assert rows["K4"] == rows["K6"] | {"fact": "K4", "post_count": 0}
    assert rows["K9"] == rows["K6"] | {"fact": "K9", "post_count": 0}
    assert rows["K3"] == {
        "fact": "K3",
        "option": "X",
        "points": 9,
        "provenance": "private",
        "in_dossier": True,
        "post_count": 0,
    }
    assert rows["K2"]["provenance"] == "private"
    assert rows["K2"]["in_dossier"] is False
    assert rows["K2"]["post_count"] == 1
    assert "K5" not in rows and "K7" not in rows


def test_changing_all_first_ballots_does_not_change_either_prompt(
    source_packet, tmp_path
):
    changed = deepcopy(source_packet)
    for i, seat in enumerate(changed["teams"][0]["seats"]):
        dossier, chat = _source_inputs(source_packet, 0, i)
        choice = {"X": "Y", "Y": "Z", "Z": "X"}[seat["first"]["choice"]]
        seat["first"] = {"choice": choice, "cited": ["K1"]}
        action = contract.vote_action(json.dumps(seat["first"]), dossier, chat, 0)
        raw_action = json.dumps(action, sort_keys=True, separators=(",", ":")).encode()
        seat["source_action_sha256"] = sha256(raw_action).hexdigest()
    raw = json.dumps(changed).encode()
    path = tmp_path / "changed.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="packet SHA-256 mismatch"):
        ballot_packet.read_packet(path, packing.PACKET_SHA256)
    verified = ballot_packet.read_packet(path, sha256(raw).hexdigest())

    for seat, arm in product(range(4), ("separate", "indexed")):
        cell = packing.Cell(0, 0, seat, arm)
        prompt = packing.prompt(verified, cell)
        assert prompt == packing.prompt(source_packet, cell)
        assert prompt == packing.prompt(verified, packing.Cell(1, 0, seat, arm))
        for private in ("Your first ballot", "Peer ballots", "hp-m", "ix-b", "Option_"):
            assert private not in prompt


@pytest.mark.parametrize("arm", ("separate", "indexed"))
def test_score_keeps_wrong_totals_and_distinguishes_native_and_visible_best(
    duplicate_packet, arm
):
    cell = packing.Cell(0, 0, 1, arm)
    wrong = '{"choice":"Y","cited":["K6","K3"],"totals":{"Z":-1,"Y":-20,"X":100}}'
    assert packing.score(duplicate_packet, cell, wrong) == {
        "status": "accepted",
        "choice": "Y",
        "cited": ["K6", "K3"],
        "totals": {"Z": -1, "Y": -20, "X": 100},
        "exact_totals": False,
        "correct_choice": False,
        "correct_visible_choice": True,
        "order_followed": False,
    }
    exact = '{"totals":{"X":19,"Y":21,"Z":10},"choice":"X","cited":["K2"]}'
    assert packing.score(duplicate_packet, cell, exact) == {
        "status": "accepted",
        "choice": "X",
        "cited": ["K2"],
        "totals": {"X": 19, "Y": 21, "Z": 10},
        "exact_totals": True,
        "correct_choice": True,
        "correct_visible_choice": False,
        "order_followed": True,
    }


def test_score_remaps_optimum_to_original_blind_labels(source_packet):
    cell = packing.Cell(1, 5, 3, "indexed")
    exact = '{"totals":{"X":10,"Y":12,"Z":28},"choice":"Z","cited":["K5"]}'
    result = packing.score(source_packet, cell, exact)
    assert result["status"] == "accepted"
    assert result["exact_totals"] is True
    assert result["correct_choice"] is True
    assert result["correct_visible_choice"] is True


@pytest.mark.parametrize(
    "text",
    (
        None,
        "",
        "not JSON",
        '{"choice":"X","cited":["K2"]}',
        '{"totals":{"X":true,"Y":21,"Z":10},"choice":"X","cited":["K2"]}',
        '{"totals":{"X":19.0,"Y":21,"Z":10},"choice":"X","cited":["K2"]}',
        '{"totals":{"X":19,"X":0,"Y":21,"Z":10},"choice":"X","cited":["K2"]}',
        '{"totals":{"X":19,"Y":21,"Z":10},"choice":"Option_A","cited":["K2"]}',
        '{"totals":{"X":19,"Y":21,"Z":10},"choice":"X","cited":"K2"}',
        '{"totals":{"X":19,"Y":21,"Z":10},"choice":"X","cited":[]}',
        '{"totals":{"X":19,"Y":21,"Z":10},"choice":"X","cited":["K2","K2"]}',
        '{"totals":{"X":19,"Y":21,"Z":10},"choice":"X","cited":["K5"]}',
    ),
)
def test_missing_or_malformed_generation_has_no_vote_or_correctness(
    duplicate_packet, text
):
    assert packing.score(duplicate_packet, packing.Cell(0, 0, 1, "indexed"), text) == {
        "status": "invalid_response",
        "choice": None,
        "cited": None,
        "totals": None,
        "exact_totals": None,
        "correct_choice": None,
        "correct_visible_choice": None,
        "order_followed": None,
    }
