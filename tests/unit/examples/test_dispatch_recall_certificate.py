import json

import pytest

from examples.seatlab.dispatch_recall import certificate as c
from examples.seatlab.dispatch_recall.native import Session

DECISION = {
    "truthful": {"immediate": "1", "deferred": "1/2", "withheld": "1/2"},
    "wrong": {"immediate": "1", "deferred": "1/2", "withheld": "1/2"},
    "silent": {"immediate": "1/2", "deferred": "1/2", "withheld": "1/2"},
}
FOLLOW = {
    "truthful": {"immediate": "1", "deferred": "1/2", "withheld": "1/2"},
    "wrong": {"immediate": "0", "deferred": "1/2", "withheld": "1/2"},
    "silent": {"immediate": "1/2", "deferred": "1/2", "withheld": "1/2"},
}
COLLIDES = {
    "truthful": {"immediate": False, "deferred": True, "withheld": True},
    "wrong": {"immediate": False, "deferred": True, "withheld": True},
    "silent": {"immediate": True, "deferred": True, "withheld": True},
}
SPLITS = {
    "truthful": {"immediate": True, "deferred": True, "withheld": False},
    "wrong": {"immediate": True, "deferred": True, "withheld": False},
    "silent": {"immediate": False, "deferred": False, "withheld": False},
}


def _line(data: object) -> bytes:
    text = json.dumps({"ok": True, "data": data}, separators=(",", ":"))
    return text.encode() + b"\n"


def _truthful_loader_lines(
    seed: int, transition: str, timing: str, tickets: tuple[str, str] | None
) -> list[bytes]:
    session = Session(seed, transition, timing, tickets=tickets)
    first = session.view("loader")
    lines = [_line(first)]
    stage = {"op": "stage", "bay": first["initial_bay"]}
    lines.append(_line(session.apply("loader", stage)))
    lines.append(_line(session.view("loader")))
    depot = session.view("depot")
    session.apply("depot", {"op": "report", "bay": depot["current_bay"]})
    window = session.view("loader")
    lines.append(_line(window))
    commit = {"op": "commit", "bay": first["bays"][0], "ticket": window["ticket"]}
    lines.append(_line(session.apply("loader", commit)))
    late = session.view("loader")
    lines.append(_line(late))
    close = {"op": "close", "ticket": late["ticket"]}
    lines.append(_line(session.apply("loader", close)))
    return lines


@pytest.mark.parametrize("seed", [0, 1, 7, 2**63 - 1])
def test_certificate_exact_optima_controls_and_scope(seed):
    result = c.certify(seed)
    assert result["schema"] == c.SCHEMA and result["seed"] == seed
    assert result["model_calls"] == result["harbor_trials"] == 0
    assert result["native_commits"] == 3 * 3 * 2 * 2 + 1
    assert result["close_checked"] == 3 * 3 * 2 * 2
    assert result["schedule"] == "view,stage,view,view"
    assert result["bytes"] == "engine_replies"
    for sender in c.SENDERS:
        for timing in c.TIMINGS:
            arm = result["arms"][sender][timing]
            assert arm["decision"] == DECISION[sender][timing]
            assert arm["classes"] == (1 if COLLIDES[sender][timing] else 2)
            assert arm["collide"] is COLLIDES[sender][timing]
            assert arm["close_split"] is SPLITS[sender][timing]
            assert arm["follow"] == FOLLOW[sender][timing]
            assert arm["always_stay"] == arm["always_switch"] == "1/2"
            assert arm["replies"] == 4
            same = arm["stay_decision"] == arm["move_decision"]
            assert same is COLLIDES[sender][timing]
            assert (arm["stay_full"] == arm["move_full"]) is not SPLITS[sender][timing]
    assert result["arms"]["truthful"]["deferred"]["after_commit"] == "refinement"
    assert result["arms"]["truthful"]["withheld"]["after_commit"] == "equivalent"
    assert result["contrasts"] == {
        "withheld_to_deferred": "equivalent:0",
        "deferred_to_immediate": "refinement:1/2",
        "silent_to_truthful": "refinement:1/2",
        "wrong_to_truthful": "equivalent:0",
    }
    assert result["inversion"] == {
        "wrong_decision": "1",
        "wrong_follow": "0",
        "pooled_immediate": "1/2",
        "pooled_deferred": "1/2",
        "pooled_withheld": "1/2",
    }
    assert result["missing"] == {
        "unreported_recorded": False,
        "unreported_reward": None,
        "silent_recorded": True,
        "silent_reward": None,
        "premature_ticket_rejected": True,
        "unredeemed_reward": None,
        "wrong_commit_reward": 0,
    }
    assert "engine replies only" in result["scope"]
    assert "real free team" in result["scope"]
    assert json.loads(json.dumps(result)) == result


def test_certificate_is_reproducible_for_a_held_seed():
    assert c.certify(3) == c.certify(3)
    assert c.phase_tickets(3) == c.phase_tickets(3) != c.phase_tickets(4)


def test_transcript_matches_independently_driven_wire_bytes():
    seed = 11
    tickets = c.phase_tickets(seed)
    stay = _truthful_loader_lines(seed, "stay", "deferred", tickets)
    move = _truthful_loader_lines(seed, "move", "deferred", tickets)
    assert c.transcript(seed, "stay", "deferred", "truthful", tickets, 0) == stay
    assert c.transcript(seed, "move", "deferred", "truthful", tickets, 0) == move
    assert stay[:4] == move[:4]
    assert stay[5] != move[5] and len(stay[5]) == len(move[5])
    arm = c.certify(seed)["arms"]["truthful"]["deferred"]
    for name, lines in (("stay", stay), ("move", move)):
        assert arm[f"{name}_decision"] == c._digest(b"".join(lines[:4]))
        assert arm[f"{name}_full"] == c._digest(b"".join(lines))
    for line in stay:
        assert line.endswith(b"\n") and json.loads(line)["ok"] is True
        assert c.encode_reply(json.loads(line)["data"]) == line


def test_unheld_phase_tokens_would_confound_the_collision():
    seed = 11
    stay = _truthful_loader_lines(seed, "stay", "deferred", None)
    move = _truthful_loader_lines(seed, "move", "deferred", None)
    assert stay[:3] == move[:3]
    assert stay[3] != move[3]
    left, right = json.loads(stay[3])["data"], json.loads(move[3])["data"]
    assert left.pop("ticket") != right.pop("ticket")
    assert left == right


@pytest.mark.parametrize("seed", [-1, 2**63, True, 1.0, "1"])
def test_certificate_rejects_invalid_seeds(seed):
    with pytest.raises(ValueError):
        c.certify(seed)


@pytest.mark.parametrize("index", [-1, 2, True])
def test_transcript_rejects_off_menu_commit_index(index):
    with pytest.raises(ValueError):
        c.transcript(0, "stay", "immediate", "truthful", c.phase_tickets(0), index)
