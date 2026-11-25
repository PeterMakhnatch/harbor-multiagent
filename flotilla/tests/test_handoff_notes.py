from collections import Counter

from test_handoff import EXT, FAMILY
from test_handoff_notes_fixture import NOTES


def test_real_note_measures():
    agreement = Counter()
    for number, _, lots, crossings in NOTES:
        measured = []
        for shift, label, note, pairs, truth in crossings:
            pending = dict(pair.split("=") for pair in pairs.split())
            measures = FAMILY.note_measures(note, lots.split(), pending, set(truth.split()))
            assert measures["pairs_kept"] is True and not measures["problems"], (number, shift, note)
            assert not measures["missing_bindings"] and not measures["unparsed_bindings"]
            predicted = "K" if measures["lots_exact"] else "S" if measures["lots_kept"] else "B" if measures["lots_pointer"] else "?"
            agreement[label, predicted] += 1
            measured.append({**measures, "shift": shift, "pending": len(pending), "note_unparsed": False})
        summary = EXT.crossing_metrics(measured)
        first_loss = next((shift for shift, label, *_ in crossings if label != "K"), None)
        assert summary["first_loss_shift"] == first_loss
        assert summary["pairs_kept"] == len(crossings)
        assert summary["note_unparsed"] == 0
    assert agreement == {("K", "K"): 56, ("B", "B"): 6, ("S", "S"): 4}


def test_real_notes_readable_by_scripted_successor():
    for number, _, lots, crossings in NOTES:
        for shift, label, note, pairs, truth in crossings:
            pending = dict(pair.split("=") for pair in pairs.split())
            universe = set(lots.split())
            bindings, possible, problems = FAMILY.parse_note(note, universe, set(pending))
            assert not problems and bindings == pending, (number, shift, note)
            if label == "B":
                assert possible == universe
            elif label == "K":
                assert possible == set(truth.split())
            else:
                assert possible > set(truth.split())
