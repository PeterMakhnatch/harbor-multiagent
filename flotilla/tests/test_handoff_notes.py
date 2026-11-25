from collections import Counter

from test_handoff import EXT, FAMILY
from test_handoff_notes_fixture import NOTES

POINTER_NOTES = [
    (True, "B95 returned, B50 delivered: bad lot is among B95's lots. Combine with B82/B72 results."),
    (True, "Bad lot is in B00 (16 lots). B79 or B10 returning pins it to B00 overlap."),
    (True, "Bad lot is one of B54's 16 lots (see /log). B79 and B87 each hold half of them."),
    (True, "Bad lot in B50 (B63 lots clear). Exactly one of B02/B46 returns. Bad in B50∩B02 or B50∩B46."),
    (True, "Bad lot in B45&B04, not B81. B93 ret=>lot in B45&B93; B57 ret=>B45&B57."),
    (True, "Bad lot in B57. Open: R05=B72, R09=B87. Bad in B57&B72: L00 L01 L26 L36 L40 L49 L50 L84; else B57&B87."),
    (True, "Pending B12=R07; Bad lot in B12."),
    (False, "Bad lot in B95: 08 11 13 19 22 23 34 40 49 55 59 65 79 85 90 99. B82 has 11 13 22 23 49 59 79 90."),
    (False, "S1: B31 returned so bad lot is one of B31 lots: 03 07 14 20 21 36 40 50 51 64 70 71 79 86 92 94."),
    (False, "Bad in B10:09,15,18,22,37,38,40,59,71,75,77,79,80,81,84,99."),
    (False, "B36 returned, B05 delivered => bad in B36 lots 11,22,28,34,43,46,49,51,55,58,62,78,79,83,85,99."),
    (False, "Bad in B95&B36: L34 L55 L85 L99 or B95's B82 lots among L11 L22 L49 L51 L58 L62 L79 L83."),
    (False, "Bad in B34: L01 04 05 21 26 36 44 46 54 57 59 65 69 75 81 95 (&B24)."),
    (False, "Bad lot in B88 set: L13 19 29 33."),
    (False, "bad lot in 14 20 21 50 51 70 79 92. Open refs: R69=B23 R90=B48 R10=B26 R68=B12."),
]


def test_pointer_reading_round6():
    for expected, note in POINTER_NOTES:
        assert FAMILY.lot_pointer(note) is expected, note


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
    assert all(FAMILY.lot_pointer(note) is (label == "B") for *_, crossings in NOTES
               for _, label, note, *_ in crossings if label in "BS")


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
