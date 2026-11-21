from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "docs" / "results" / "scripts" / "liar_writes.py"
SPEC = spec_from_file_location("flotilla_liar_writes", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import {SCRIPT}")
liar_writes = module_from_spec(SPEC)
SPEC.loader.exec_module(liar_writes)


def test_paired_writer_test_uses_pairs_not_pooled_counts():
    correct = ("p1", "correct")
    deceived = ("p2", "deceived")
    assert liar_writes.paired_writer_test([(correct, deceived)] * 4) == (4, 4, 0, 0.125)
    assert liar_writes.paired_writer_test([(correct, correct), (deceived, deceived)] * 2) == (4, 0, 0, 1.0)
    assert liar_writes.paired_writer_test([]) == (0, 0, 0, None)


def test_paired_writer_test_reintroduces_corrupted_pairs_in_both_directions():
    pairs = [
        (("p1", "correct"), ("p2", "deceived")),
        (("p2", "deceived"), ("p1", "correct")),
        (("p1", "corrupted"), ("p2", "correct")),
        (("p1", "correct"), ("p2", "corrupted")),
        (("p1", "corrupted"), ("p2", "deceived")),
    ]
    assert liar_writes.paired_writer_test(pairs) == (2, 2, 0, 0.5)
    assert liar_writes.paired_writer_test(pairs, corrupted_as="deceived") == (5, 3, 1, 0.625)
    assert liar_writes.paired_writer_test(pairs, corrupted_as="correct") == (5, 3, 0, 0.25)
