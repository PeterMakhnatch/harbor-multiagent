import json
from collections import Counter

import pytest

from examples.seatlab.partner_switch import engine, panel


NAMES = ("zeta", "alpha")


def _rule(reverse: bool, payload: bytes, *, invert_decoder: bool = False) -> str:
    data = json.loads(payload)
    cycle = data["active"][::-1] if reverse else data["active"]
    successor = dict(zip(cycle, cycle[1:] + cycle[:1], strict=True))
    own = set(data["own"])
    if data["stage"] == "report":
        missing = next(slot for slot in cycle if slot not in own)
        return successor[missing]
    peer = data["peer"]
    excluded = (
        successor[peer]
        if invert_decoder
        else next(slot for slot in cycle if successor[slot] == peer)
    )
    remaining = own - {excluded}
    return min(remaining) if len(remaining) == 1 else peer


@pytest.mark.parametrize("start", [0, 23])
def test_native_partner_switch_changes_actual_votes(start: int) -> None:
    cases = engine.cases(start)
    report_calls: Counter[tuple[str, str, bytes]] = Counter()
    vote_calls: Counter[tuple[str, str, bytes]] = Counter()

    def report(name: str, role: str, packet: bytes) -> str:
        assert json.loads(packet)["stage"] == "report"
        assert json.loads(packet)["role"] == role
        report_calls[name, role, packet] += 1
        return _rule(name == "alpha", packet)

    def vote(name: str, role: str, packet: bytes) -> str:
        assert json.loads(packet)["stage"] == "vote"
        assert json.loads(packet)["role"] == role
        vote_calls[name, role, packet] += 1
        return _rule(name == "alpha", packet)

    inputs = panel.report_inputs(cases, NAMES)
    schedule = panel.deliver(cases, NAMES, report)
    result = panel.score(schedule, vote)
    assert report_calls == Counter(
        (name, role, packet)
        for (name, role), packets in inputs.items()
        for packet in packets
    )
    assert vote_calls == Counter(
        (name, role, packet)
        for (name, role), packets in panel.vote_inputs(schedule).items()
        for packet in packets
    )
    assert result == {
        "order": list(NAMES),
        "cases": 6,
        "native": {
            "matrix": [["1", "1/2"], ["1/2", "1"]],
            "home_mean": "1",
            "mixed_mean": "1/2",
        },
        "frozen_home_vote": {
            "matrix": [["1", "1"], ["1", "1"]],
            "home_mean": "1",
            "mixed_mean": "1",
        },
        "mixed_overestimate": "1/2",
        "changed_peer_views": 24,
        "changed_votes": 12,
        "observed_role_opportunities": 24,
        "report_histograms": {
            name: {role: dict.fromkeys(cases[0].active, 2) for role in engine.ROLES}
            for name in NAMES
        },
        "matched_report_histograms": True,
        "report_decisions": 12,
        "vote_decisions": 36,
    }


def test_same_report_rule_is_neutral_with_distinct_team_names() -> None:
    cases = engine.cases(0)

    def choice(name: str, role: str, packet: bytes) -> str:
        return _rule(False, packet)

    schedule = panel.deliver(cases, NAMES, choice)
    result = panel.score(schedule, choice)
    assert (
        result["native"]
        == result["frozen_home_vote"]
        == {
            "matrix": [["1", "1"], ["1", "1"]],
            "home_mean": "1",
            "mixed_mean": "1",
        }
    )
    assert result["mixed_overestimate"] == "0"
    assert result["changed_peer_views"] == result["changed_votes"] == 0
    assert result["observed_role_opportunities"] == 24
    assert result["report_decisions"] == 12
    assert result["vote_decisions"] == 24
    assert result["matched_report_histograms"]


def test_switch_counts_only_received_reports() -> None:
    cases = engine.cases(0)

    def report(name: str, role: str, packet: bytes) -> str:
        return _rule(name == "alpha" and role == "east", packet)

    def vote(name: str, role: str, packet: bytes) -> str:
        return min(json.loads(packet)["own"])

    result = panel.score(panel.deliver(cases, NAMES, report), vote)
    assert result["changed_peer_views"] == 12
    assert result["changed_votes"] == 0
    assert result["vote_decisions"] == 30
    assert result["native"] == result["frozen_home_vote"]
    assert result["mixed_overestimate"] == "0"
    assert result["matched_report_histograms"]


def test_frozen_comparison_can_underestimate() -> None:
    cases = engine.cases(0)

    def choice(name: str, role: str, packet: bytes) -> str:
        return _rule(name == "alpha", packet, invert_decoder=True)

    result = panel.score(panel.deliver(cases, NAMES, choice), choice)
    assert result["native"]["mixed_mean"] == "1"
    assert result["frozen_home_vote"]["mixed_mean"] == "1/2"
    assert result["mixed_overestimate"] == "-1/2"
    assert result["native"]["home_mean"] == result["frozen_home_vote"]["home_mean"]


@pytest.mark.parametrize("names", [("a",), ("a", "a"), ("a", "a/b"), tuple("abcdefg")])
def test_invalid_team_roster(names: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        panel.report_inputs(engine.cases(0), names)
