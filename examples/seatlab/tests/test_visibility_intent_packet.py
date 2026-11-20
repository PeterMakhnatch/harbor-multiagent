import importlib
import json
from pathlib import Path

import pytest
import test_visibility_boxes_packet as fixture

panel = fixture.panel

with pytest.MonkeyPatch.context() as patch:
    patch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    packet = importlib.import_module("visibility.intent_packet")

_OK = (1, 2, 6)
_NO_PRIOR = (4, 5)


def _trajectory(directory, seat):
    return directory / "seats" / seat / "agent/trajectory.json"


def _history(directory, instance):
    fixture._history(directory, instance, earlier=True)
    own = fixture._SEATS[(instance - 1) % 3]
    peer1, peer2 = (seat for seat in fixture._SEATS if seat != own)
    actions: list[tuple[float, str, int]] = [
        (5, own, 1),
        (6, peer1, 1),
        (7, peer1, 11),
        (14, own, 2),
    ]
    if instance in _NO_PRIOR:
        actions.append((8, peer1, 12))
    else:
        actions.append((8, peer2, 12))
        if instance not in _OK:
            actions.extend((9 + i / 2, peer1, 31 + i) for i in range(7))
    actions.sort()
    # Same timestamp, different service order: neither open is in the prefix.
    actions.extend(((16, own, 4), (16, peer2, 42)))

    initial = json.loads(_trajectory(directory, own).read_bytes())["steps"][0]
    steps = {seat: [initial] for seat in fixture._SEATS}
    budgets = dict.fromkeys(fixture._SEATS, 9)
    events = []
    for seconds, seat, box in actions:
        budgets[seat] -= 1
        room = (box - 1) // 10 + 1
        reply = {
            "box": box,
            "room": room,
            "flag": room == 1,
            "budget_left": budgets[seat],
        }
        events.append(
            fixture._event(len(events) + 1, seconds, seat, "open", {"box": box}, reply)
        )
        # No terminal reply for the receiver's anchor. No eligible peer needs
        # a later self open or a later reply to prove its prior observations.
        if seconds < 16:
            steps[seat].append(
                fixture._agent_step(
                    len(steps[seat]) + 1, seconds + 0.25, fixture._screen(reply)
                )
            )
    events.append(
        fixture._event(
            len(events) + 1,
            28,
            None,
            "admin.close",
            {},
            {"closed": True, "events": len(events) + 1},
        )
    )
    fixture._events_file(directory / fixture._EVENT_PATH, events)
    for seat, records in steps.items():
        if seat == own or instance in _OK:
            fixture._json_file(
                _trajectory(directory, seat),
                {"schema_version": "ATIF-v1.8", "steps": records},
            )


@pytest.fixture
def joint_panel(panel, monkeypatch):
    root, selected, unselected = panel
    for instance, directory in selected.items():
        _history(directory, instance)
    receiver = root.parent / "registered-receiver.json"
    raw = fixture.packet.export(root)
    receiver.write_bytes(raw)
    monkeypatch.setattr(packet.boxes_choice, "PACKET", receiver)
    monkeypatch.setattr(packet.boxes_choice, "PACKET_SHA256", fixture._sha(raw))
    for instance, directory in selected.items():
        if instance not in _OK:
            fixture._trajectory(directory, instance).unlink()
    return root, selected, unselected


def _load(path, raw):
    path.write_bytes(raw)
    return packet.load(path, fixture._sha(raw))


def test_export_uses_service_order_and_does_not_need_a_later_witness(joint_panel):
    root, _, _ = joint_panel
    raw = packet.export(root)
    public = json.loads(raw)
    assert public["receiver_packet_sha256"] == packet.boxes_choice.PACKET_SHA256
    assert [row["status"] for row in public["worlds"]] == [
        "eligible",
        "eligible",
        "no_budget",
        "no_prior_own",
        "no_prior_own",
        "eligible",
        "no_budget",
        "no_budget",
        "no_budget",
        "no_budget",
    ]
    worlds = _load(root.parent / "intentions.json", raw)
    assert tuple(worlds) == _OK
    a, b, c = worlds[1].seats
    assert a.own == ((1, 1, True), (2, 1, True))
    assert b.own == ((1, 1, True), (11, 2, False))
    assert c.own == ((12, 2, False),)
    assert (a.peers, b.peers, c.peers) == ((1, 11, 12), (1, 2, 12), (1, 2, 11))
    assert (a.budget_left, b.budget_left, c.budget_left) == (7, 7, 8)
    assert all(case.earlier_observation for case in worlds[1].seats)
    assert b'"earlier_observation":false' in packet.boxes_choice.PACKET.read_bytes()
    for secret in (b"run000", b"private", b"timestamp", b"p1", b",42", b"[4,"):
        assert secret not in raw


@pytest.mark.parametrize("when", (16, 17, None))
def test_export_requires_each_seats_prior_terminal_witness(joint_panel, when):
    root, selected, _ = joint_panel
    path = _trajectory(selected[1], "p3")
    trajectory = json.loads(path.read_bytes())
    step = trajectory["steps"][1]
    if when is None:
        step["message"] = step.pop("observation")["results"][0]["content"]
    else:
        step["timestamp"] = fixture._stamp(when)
    fixture._json_file(path, trajectory)
    with pytest.raises(ValueError, match="registered intentions prefix for world 01"):
        packet.export(root)


def test_export_rejects_a_missing_registered_trial(joint_panel):
    root, _, unselected = joint_panel
    (unselected[1] / "result.json").unlink()
    with pytest.raises(ValueError, match="registered team-3 roster"):
        packet.export(root)


@pytest.mark.parametrize(
    ("mutation", "error"),
    (
        ("peer", "peer boxes disagree"),
        ("seat", "world or seat roster"),
        ("world", "invalid canonical intentions packet"),
        ("budget", "excluded seat evidence"),
        ("excluded_reason", "eligibility evidence"),
        ("old_cutoff", "registered receiver cutoff"),
    ),
)
def test_load_rejects_forged_public_evidence(joint_panel, mutation, error):
    root, _, _ = joint_panel
    public = json.loads(packet.export(root))
    if mutation == "peer":
        public["worlds"][0]["seats"][0]["peers"].append(42)
    elif mutation == "seat":
        public["worlds"][0]["seats"].pop()
    elif mutation == "world":
        public["worlds"].pop()
    elif mutation == "budget":
        public["worlds"][2]["seats"][0]["budget_left"] = True
    elif mutation == "excluded_reason":
        public["worlds"][2]["status"] = "no_prior_own"
    else:
        public["worlds"][2]["seats"][0]["own_boxes"][1] = 13
    with pytest.raises(ValueError, match=error):
        _load(root.parent / "mutation.json", fixture._canonical(public))


def test_load_checks_the_paid_receiver_digest(joint_panel):
    root, _, _ = joint_panel
    raw = packet.export(root)
    receiver = packet.boxes_choice.PACKET
    receiver.write_bytes(receiver.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="box packet SHA-256 does not match"):
        _load(root.parent / "intentions.json", raw)
