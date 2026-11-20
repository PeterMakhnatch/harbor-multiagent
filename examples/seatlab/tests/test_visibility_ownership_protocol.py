import io
import json
from dataclasses import replace
from itertools import product

import pytest

from examples.seatlab.visibility import ownership_protocol as protocol
from examples.seatlab.visibility.boxes_packet import Case
from examples.seatlab.visibility.intent_packet import World
from examples.seatlab.visibility.intent_protocol import Block, blocks


@pytest.fixture
def report():
    return json.loads(protocol.RESULTS.read_bytes())


@pytest.fixture
def world():
    return World(
        instance=1,
        seats=(
            Case(1, ((6, 1, True), (5, 1, True)), (22, 53), 7, False),
            Case(1, ((22, 3, False),), (5, 6, 53), 8, True),
            Case(1, ((53, 6, True),), (5, 6, 22), 8, False),
        ),
    )


def test_seeds_are_original_proposals_for_every_registered_block():
    choices = protocol.seeds()
    assert len(choices) == 36
    assert choices.keys() == {block.id for block in blocks()}
    assert choices["bi-i01-m0-l0-r0"] == (2, 12, 22)
    assert choices["bi-i01-m1-l2-r0"] == (41, 50, 41)


def test_seeds_reject_different_size_and_same_size_tampering(tmp_path, monkeypatch):
    raw = protocol.RESULTS.read_bytes()
    assert len(raw) == 92_678
    path = tmp_path / "report.json"
    monkeypatch.setattr(protocol, "RESULTS", path)
    changed = raw.replace(b'"verified"', b'"modified"', 1)
    assert changed != raw and len(changed) == len(raw)
    for data in (raw + b"\n", changed):
        path.write_bytes(data)
        with pytest.raises(ValueError, match="SHA-256"):
            protocol.seeds()


@pytest.mark.parametrize("variation", ["missing", "extra", "duplicate", "unknown"])
def test_seed_registration_requires_every_block_exactly_once(report, variation):
    rows = report["blocks"]
    if variation == "missing":
        rows.pop()
    elif variation == "extra":
        rows.append(rows[0])
    elif variation == "duplicate":
        rows[-1] = rows[0]
    else:
        rows[-1]["id"] = "bi-i09-m0-l0-r0"
    with pytest.raises(ValueError, match="registered proposal blocks|proposal block"):
        protocol._seed_choices(report)


@pytest.mark.parametrize(
    "field,value",
    [
        ("instance", 2),
        ("instance", True),
        ("numbering", 1),
        ("rotation", 2),
        ("repetition", 1),
    ],
)
def test_seed_id_must_match_coordinates(report, field, value):
    report["blocks"][0][field] = value
    with pytest.raises(ValueError, match="coordinates"):
        protocol._seed_choices(report)


def test_seeds_require_verified_barrier_and_proposal_evidence(report):
    row = report["blocks"][0]
    row["proposal_barrier"] = "violated"
    with pytest.raises(ValueError, match="barrier"):
        protocol._seed_choices(report)
    row["proposal_barrier"] = "verified"
    row["calls"]["proposal"]["A"], row["calls"]["proposal"]["B"] = (
        row["calls"]["proposal"]["B"],
        row["calls"]["proposal"]["A"],
    )
    with pytest.raises(ValueError, match="accepted and settled for its label"):
        protocol._seed_choices(report)


@pytest.mark.parametrize("unusable", [[1, 2], [1, 2, True], [0, 2, 3], [1, 2.0, 61]])
def test_seed_choices_are_exactly_three_legal_integers(report, unusable):
    report["blocks"][0]["proposals"]["choices"] = unusable
    with pytest.raises(ValueError, match="box proposals|box must be an integer"):
        protocol._seed_choices(report)


def test_only_proposal_choices_leave_seed_parser(report):
    original = protocol.seeds()
    for row in report["blocks"]:
        row["arms"] = {"masked": {"choices": [60, 60, 60]}}
        row["calls"] = {"proposal": row["calls"]["proposal"]}
    assert protocol._seed_choices(report) == original


def test_cli_plan_has_only_public_hashes_and_ordered_run_ids(capsys):
    assert protocol.main(["plan"]) == 0
    out = json.loads(capsys.readouterr().out)
    expected = [
        f"bo-i{instance:02d}-m{numbering}-l{rotation}-r{repetition}-{arm}"
        for repetition, rank, numbering, rotation in product(
            range(2), range(3), range(2), range(3)
        )
        for instance in [(1, 2, 6)[rank]]
        for arm in (
            ("owner", "neutral")
            if (repetition + rank + numbering + rotation) % 2
            else ("neutral", "owner")
        )
    ]
    assert len(set(expected)) == 72
    assert out == {
        "packet_sha256": "a633e7fc6673036bca0989957396250f8db8b77f816be64b4f084dc210423e34",
        "results_sha256": "8b80f2330fcf0f93cd877226cc2ce30087b8cf48cefc88fc52cffdeecc312bc2",
        "runs": expected,
    }


def _line(text, prefix):
    return next(
        line.removeprefix(prefix)
        for line in text.splitlines()
        if line.startswith(prefix)
    )


@pytest.mark.parametrize("numbering", [0, 1])
@pytest.mark.parametrize("label,seat", [(0, 1), (1, 2), (2, 0)])
def test_policy_prompts_change_only_one_rule_and_map_each_role(
    world, numbering, label, seat
):
    block = Block(1, numbering, 2, 0)
    proposals = (11, 11, 44)
    neutral = protocol.prompt(world, block, label, "neutral", proposals)
    owner = protocol.prompt(world, block, label, "owner", proposals)
    diffs = [
        (a, b)
        for a, b in zip(neutral.splitlines(), owner.splitlines(), strict=True)
        if a != b
    ]
    assert diffs == [
        (
            "Each proposal is a suggestion. No label has priority. You may keep or change your box.",
            (
                "Each proposed box belongs to its proposer with the earliest label (A before B before C). "
                "Keep your proposal if you own it. Otherwise choose a box no seat proposed."
            ),
        )
    ]
    assert (
        "This is a team convention, not a service restriction. All legal boxes remain available."
        in owner
    )
    assert (
        "Choose exactly 1 box to open next, at the same time as your teammates."
        in owner
    )
    assert owner.endswith('Return JSON with only the integer field "box".')
    assert _line(owner, "Your label: ") == "ABC"[label]
    assert json.loads(_line(owner, "Sealed proposals: ")) == (
        {"A": 50, "B": 50, "C": 17} if numbering else {"A": 11, "B": 11, "C": 44}
    )
    case = world.seats[seat]
    assert int(_line(owner, "Your remaining opens: ")) == case.budget_left
    assert json.loads(_line(owner, "Your previous results, oldest first: ")) == [
        {"box": 61 - box, "room": 7 - room, "flag": flag}
        if numbering
        else {"box": box, "room": room, "flag": flag}
        for box, room, flag in case.own
    ]
    assert json.loads(
        _line(owner, "Box numbers previously opened by teammates: ")
    ) == sorted(61 - box if numbering else box for box in case.peers)


@pytest.mark.parametrize("arm", ["neutral", "owner"])
def test_peer_flag_results_do_not_affect_prompt(world, arm):
    block = Block(1, 1, 1, 0)  # B is the first physical seat.
    own, p2, p3 = world.seats
    changed = replace(
        world,
        seats=(
            own,
            replace(p2, own=((22, 3, True),)),
            replace(p3, own=((53, 6, False),)),
        ),
    )
    assert protocol.prompt(world, block, 1, arm, (60, 11, 10)) == protocol.prompt(
        changed, block, 1, arm, (60, 11, 10)
    )


def test_prompt_rejects_a_different_world(world):
    with pytest.raises(ValueError, match="wrong world"):
        protocol.prompt(world, Block(2, 0, 0, 0), 0, "owner", (1, 2, 3))


def test_cli_prompt_uses_registered_seeds_and_one_role(monkeypatch, capsys, world):
    block = Block(1, 1, 2, 0)
    monkeypatch.setattr(protocol.intent_packet, "load", lambda: {1: world})
    assert protocol.main(["prompt", "bo-i01-m1-l2-r0-owner", "--seat", "C"]) == 0
    result = capsys.readouterr()
    assert result.err == ""
    assert result.out == protocol.prompt(world, block, 2, "owner", (41, 50, 41))


@pytest.mark.parametrize(
    "run,response,original",
    [
        ("bo-i01-m0-l0-r0-neutral", b'{"box":6}', 6),
        ("bo-i01-m1-l2-r0-neutral", b'{"box":20}', 41),  # A proposed it.
        ("bo-i01-m1-l2-r0-owner", b'{"box":20}', 41),
        ("bo-i01-m1-l2-r0-owner", b'{"box":55}', 6),  # A repeat remains legal.
        ("bo-i01-m1-l2-r0-owner", b'{"box":1,"box":2}', None),
    ],
)
def test_cli_decode_does_not_enforce_the_convention(
    monkeypatch, capsys, run, response, original
):
    with io.TextIOWrapper(io.BytesIO(response)) as stdin:
        monkeypatch.setattr(protocol.sys, "stdin", stdin)
        code = protocol.main(["decode", run])
    result = capsys.readouterr()
    if original is None:
        assert code == 1
        assert not result.out
    else:
        assert code == 0
        assert json.loads(result.out) == {"box": original}
