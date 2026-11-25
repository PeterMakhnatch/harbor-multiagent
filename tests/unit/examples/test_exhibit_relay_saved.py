import json
import unittest
from copy import deepcopy
from importlib import import_module

from examples.seatlab.exhibit_saved import packets

WORLD = "examples.tasks.exhibit-relay.environment.exhibit_relay"
Session = import_module(f"{WORLD}.engine").Session
make_case = import_module(f"{WORLD}.cases").make_case
INSTRUCTION = "Choose the depot file for this turn."
HEADER = (
    "Select the action for your current role using only the supplied information. "
    "Return one JSON object without markdown, in this form: "
    '{"op":"file","ids":["card ID", ...]}.'
)


def prefix(case, kind, mode="authored"):
    session = Session(case, mode)
    brief = session.view("loader")["briefs"][0]
    session.apply("loader", {"op": "request", "slips": [brief["id"]]})
    registrar = session.view("registrar")
    assert registrar["briefs"] == [brief]
    cards = [
        card
        for card in registrar["cards"]
        if card["key"] in brief["items"]
        and card["start"] <= brief["epoch"] <= card["end"]
    ]
    assert len(cards) == 2
    note = {
        "epoch": brief["epoch"],
        "keys": sorted(card["value"] for card in cards),
        "weight": {"correct": brief["weight"], "wrong": 9, "unknown": 0, "empty": 0}[
            kind
        ],
    }
    notes = [] if kind == "empty" else [note]
    session.apply(
        "registrar",
        {
            "op": "file",
            "ids": [card["id"] for card in cards],
            "notes": notes,
        },
    )
    return session, notes


def decode(frame):
    assert type(frame) is bytes and frame.endswith(b"\n")
    envelope = json.loads(frame)
    assert envelope.keys() == {"schema", "prompt"}
    assert envelope["schema"] == "seatlab.seat-turn/v1"
    header, marker, payload = envelope["prompt"].partition("\nInput JSON: ")
    assert marker and header == HEADER
    return json.loads(payload)


def finish(session, depot_choice, bay_choice):
    view = session.view("depot")
    ids = [view["cards"][depot_choice]["id"]]
    session.apply("depot", {"op": "file", "ids": ids})
    loader = session.view("loader")
    session.apply(
        "loader",
        {
            "op": "commit",
            "routes": {
                item: loader["bays"][bay_choice] for item in loader["order"]["items"]
            },
        },
    )
    return session.snapshot()


class ExhibitSavedTests(unittest.TestCase):
    def check_receipt(self, receipt, case):
        public = json.dumps(receipt, sort_keys=True)
        for crate in case["crates"]:
            self.assertNotIn(crate, public)
        for brief in case["briefs"]:
            self.assertNotIn(brief["id"], public)
        for field in (
            "realized",
            "reward",
            "correct_items",
            "registrar_supported",
            "depot_supported",
            "chains_supported",
            "dispatch_supported",
        ):
            self.assertNotIn(f'"{field}"', public)

    def check_pair(self, session, notes):
        cut = session.snapshot()
        original = deepcopy(cut)
        frames, receipt = packets(cut, INSTRUCTION)
        self.assertEqual(cut, original)
        self.assertEqual(set(frames), {"authored", "author-masked"})
        left, right = frames["authored"], frames["author-masked"]
        authored, masked = decode(left), decode(right)
        expected_view = session.view("depot")
        self.assertEqual(expected_view["bundles"], notes)
        self.assertEqual(expected_view["bundle_source"], "registrar")
        self.assertEqual(expected_view["phase"], "depot")
        self.assertEqual(expected_view["turn"], "depot")
        self.assertEqual(expected_view["briefs"], [])
        self.assertIsNone(expected_view["order"])
        self.assertEqual(expected_view["registrar_file"], [])
        self.assertEqual(expected_view["depot_file"], [])
        expected = {
            "instruction": INSTRUCTION,
            "view": expected_view,
            "previous_action": None,
        }
        self.assertEqual(authored, expected)
        masked_expected = deepcopy(expected)
        masked_expected["view"]["bundles"] = [{**note, "weight": 0} for note in notes]
        self.assertEqual(masked, masked_expected)
        self.assertEqual(len(left), len(right))
        changes = [(a, b) for a, b in zip(left, right, strict=True) if a != b]
        self.assertCountEqual(
            changes, [(48 + note["weight"], 48) for note in notes if note["weight"]]
        )
        self.assertEqual(left == right, all(note["weight"] == 0 for note in notes))
        case = cut["case"]
        self.check_receipt(receipt, case)
        for brief in case["briefs"]:
            self.assertNotIn(brief["id"].encode(), left)
            self.assertNotIn(brief["id"].encode(), right)
        for card in case["registrar"]:
            self.assertNotIn(card["id"].encode(), left)
            self.assertNotIn(card["id"].encode(), right)
        return frames

    def test_partial_registration_preserves_sender_claim(self):
        for seed in (7, 31):
            for layout in ("a", "b"):
                for kind in ("correct", "wrong", "unknown", "empty"):
                    with self.subTest(seed=seed, layout=layout, kind=kind):
                        case = make_case(seed, "coupling", layout=layout)
                        session, notes = prefix(case, kind)
                        actions = [
                            event["action"] for event in session.snapshot()["events"]
                        ]
                        self.assertEqual(len(actions[0]["slips"]), 1)
                        self.assertEqual(len(actions[1]["ids"]), 2)
                        self.assertEqual(actions[1]["notes"], notes)
                        if kind == "wrong":
                            self.assertNotEqual(
                                notes[0]["weight"], case["briefs"][0]["weight"]
                            )
                        self.check_pair(session, notes)

    def test_no_registrar_is_not_an_empty_submission(self):
        case = make_case(7, "coupling")
        session = Session(case, "authored")
        session.apply("loader", {"op": "request", "slips": []})
        with self.assertRaises(ValueError):
            packets(session.snapshot(), INSTRUCTION)
        session.apply("registrar", {"op": "file", "ids": [], "notes": []})
        self.check_pair(session, [])

    def test_frozen_packet_ignores_later_actions(self):
        case = make_case(7, "coupling", layout="a")
        first, notes = prefix(case, "wrong")
        expected = self.check_pair(first, notes)
        second, _ = prefix(case, "wrong")
        third, _ = prefix(case, "wrong")
        first_cut = finish(first, 0, 0)
        second_cut = finish(second, 1, 0)
        third_cut = finish(third, 0, 1)
        self.assertNotEqual(first_cut["events"][2], second_cut["events"][2])
        self.assertNotEqual(first_cut["events"][3], third_cut["events"][3])
        for cut in (first_cut, second_cut, third_cut):
            frames, receipt = packets(cut, INSTRUCTION)
            self.assertEqual(frames, expected)
            self.check_receipt(receipt, case)

    def test_frozen_packet_ignores_lottery_and_source_mask(self):
        case = make_case(31, "coupling", layout="b")
        original, notes = prefix(case, "wrong")
        expected = self.check_pair(original, notes)
        alternate = deepcopy(case)
        alternate["realized"] = next(
            brief["id"] for brief in case["briefs"] if brief["id"] != case["realized"]
        )
        changed_lottery, _ = prefix(alternate, "wrong")
        masked_source, _ = prefix(case, "wrong", "author-masked")
        self.assertEqual(
            original.snapshot()["events"], changed_lottery.snapshot()["events"]
        )
        self.assertEqual(
            original.snapshot()["events"], masked_source.snapshot()["events"]
        )
        self.assertNotEqual(
            original.view("depot")["bundles"], masked_source.view("depot")["bundles"]
        )
        for session in (changed_lottery, masked_source):
            frames, receipt = packets(session.snapshot(), INSTRUCTION)
            self.assertEqual(frames, expected)
            self.check_receipt(receipt, session.snapshot()["case"])
