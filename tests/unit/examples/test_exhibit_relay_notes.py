import importlib
import json
from copy import deepcopy
from itertools import combinations

import pytest

PACKAGE = "examples.tasks.exhibit-relay.environment.exhibit_relay"
make_case = importlib.import_module(f"{PACKAGE}.cases").make_case
notes_module = importlib.import_module(f"{PACKAGE}.notes")
normalize_notes = notes_module.normalize_notes
deliver_notes = notes_module.deliver_notes
audit_notes = notes_module.audit_notes
SEEDS = (0, 17, 983, 2**63 - 1)
SUMMARY_FIELDS = (
    "assertion_count",
    "accurate_positive_weight_count",
    "inaccurate_positive_weight_count",
    "unknown_weight_count",
    "missing_true_pair_count",
    "irrelevant_pair_count",
    "correct_pair_count",
)


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def row(epoch, keys, weight):
    return {"epoch": epoch, "keys": list(keys), "weight": weight}


def fixture(seed, layout):
    case = make_case(seed, "coupling", layout=layout)
    slips = sorted(brief["id"] for brief in case["briefs"])
    registrar_ids = set()
    truth = []
    for brief in case["briefs"]:
        epoch = brief["epoch"]
        sources = []
        for item in brief["items"]:
            matches = [
                card
                for card in case["registrar"]
                if card["key"] == item and card["start"] <= epoch <= card["end"]
            ]
            assert len(matches) == 1
            sources.append(matches[0])
        registrar_ids.update(card["id"] for card in sources)
        truth.append(
            row(epoch, sorted(card["value"] for card in sources), brief["weight"])
        )
    assert len(registrar_ids) == 4
    assert len(truth) == 4
    return (
        case,
        slips,
        sorted(registrar_ids),
        sorted(truth, key=lambda r: (r["epoch"], r["keys"])),
    )


def summary(case, slips, registrar_ids, notes):
    inputs = (case, slips, registrar_ids, notes)
    before = deepcopy(inputs)
    result = audit_notes(*inputs)
    assert inputs == before
    return tuple(result[field] for field in SUMMARY_FIELDS)


@pytest.mark.parametrize("seed", SEEDS)
def test_masked_authored_twins_hide_only_weights(seed):
    case, _, _, truth = fixture(seed, "a")
    twin, _, _, other_truth = fixture(seed, "b")
    assert case["crates"] == twin["crates"]
    assert truth != other_truth
    honest = normalize_notes(truth, case["crates"])
    mistaken = normalize_notes(other_truth, case["crates"])
    for source in (honest, mistaken):
        before = deepcopy(source)
        visible = deliver_notes(source, disclose=True)
        masked = deliver_notes(source, disclose=False)
        assert visible == before
        assert masked == [{**r, "weight": 0} for r in before]
        assert len(canonical_bytes(visible)) == len(canonical_bytes(masked))
        assert source == before
        visible[0]["keys"].reverse()
        masked[0]["keys"].clear()
        assert source == before
    assert deliver_notes(honest, disclose=False) == deliver_notes(
        mistaken, disclose=False
    )
    assert deliver_notes(mistaken, disclose=True) == other_truth
    assert deliver_notes(mistaken, disclose=True) != truth


@pytest.mark.parametrize("count", range(5))
def test_normalize_zero_through_four_rows(count):
    case, _, _, truth = fixture(17, "a")
    crates = case["crates"]
    expected = [{**r, "weight": w} for r, w in zip(truth[:count], (0, 9, 1, 4))]
    raw = [{**r, "keys": list(reversed(r["keys"]))} for r in reversed(expected)]
    before = deepcopy((raw, crates))
    normalized = normalize_notes(raw, crates)
    assert normalized == expected
    assert (raw, crates) == before
    assert normalize_notes(normalized, crates) == expected
    if normalized:
        normalized[0]["keys"].clear()
        assert (raw, crates) == before
    assert deliver_notes([], disclose=True) == deliver_notes([], disclose=False) == []


def test_normalize_distinct_epochs_and_public_crates_without_truth():
    case = make_case(17, "coupling", layout="a")
    crates = case["crates"]
    keys = sorted(crates[:2])
    expected = [row(0, keys, 0), row(1, keys, 9), row(2, keys, 3)]
    assert normalize_notes(list(reversed(expected)), crates) == expected
    assert deliver_notes(expected, disclose=True) == expected


@pytest.mark.parametrize("bad_weight", (-1, 10, None, True, False, 1.0, "1", []))
def test_invalid_weight_is_not_unknown(bad_weight):
    crates = make_case(0, "coupling")["crates"]
    with pytest.raises(ValueError):
        normalize_notes([row(0, crates[:2], bad_weight)], crates)
    assert normalize_notes([row(0, crates[:2], 0)], crates)[0]["weight"] == 0


@pytest.mark.parametrize("bad_epoch", (-1, 3, None, True, False, 1.0, "1"))
def test_invalid_epoch(bad_epoch):
    crates = make_case(0, "coupling")["crates"]
    with pytest.raises(ValueError):
        normalize_notes([row(bad_epoch, crates[:2], 1)], crates)


def test_invalid_rows_and_duplicate_bounds():
    case, _, _, truth = fixture(17, "a")
    crates = case["crates"]
    keys = crates[:2]
    valid = row(0, keys, 1)
    other_pair = next(
        list(pair)
        for pair in combinations(sorted(crates), 2)
        if all(list(pair) != r["keys"] for r in truth)
    )
    invalid = (
        None,
        {},
        "notes",
        (valid,),
        [None],
        [1],
        [{"epoch": 0, "keys": keys}],
        [{**valid, "comment": "extra"}],
        [row(0, [], 1)],
        [row(0, keys[:1], 1)],
        [row(0, crates[:3], 1)],
        [row(0, [keys[0], keys[0]], 1)],
        [row(0, [keys[0], "UNKNOWN"], 1)],
        [row(0, [keys[0], 1], 1)],
        [{**valid, "keys": tuple(keys)}],
        [valid, row(0, reversed(keys), 9)],
        [valid, deepcopy(valid)],
        truth + [row(0, other_pair, 1)],
    )
    for raw in invalid:
        before = deepcopy((raw, crates))
        with pytest.raises(ValueError):
            normalize_notes(raw, crates)
        assert (raw, crates) == before


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("layout", ("a", "b"))
def test_operator_audit_distinguishes_correct_mistaken_and_abstaining(seed, layout):
    case, slips, registrar_ids, truth = fixture(seed, layout)
    wrong_layout = "b" if layout == "a" else "a"
    _, _, _, wrong = fixture(seed, wrong_layout)
    unknown = [{**r, "weight": 0} for r in truth]
    assert summary(case, slips, registrar_ids, truth) == (4, 4, 0, 0, 0, 0, 4)
    assert summary(case, slips, registrar_ids, wrong) == (4, 0, 4, 0, 0, 0, 4)
    assert summary(case, slips, registrar_ids, unknown) == (4, 0, 0, 4, 0, 0, 4)
    assert summary(case, slips, registrar_ids, []) == (0, 0, 0, 0, 4, 0, 0)
    mixed = [truth[0], wrong[1], unknown[2]]
    assert summary(case, slips, registrar_ids, mixed) == (3, 1, 1, 1, 1, 0, 3)


def test_operator_audit_separates_nominations_from_documentary_support():
    case, _slips, registrar_ids, _truth = fixture(983, "a")
    brief = case["briefs"][0]
    epoch = brief["epoch"]
    sources = [
        card
        for card in case["registrar"]
        if card["key"] in brief["items"] and card["start"] <= epoch <= card["end"]
    ]
    assert len(sources) == 2
    target = row(epoch, sorted(card["value"] for card in sources), brief["weight"])
    selected = sorted(card["id"] for card in sources)
    assert summary(case, [brief["id"]], selected, [target]) == (1, 1, 0, 0, 0, 0, 1)
    assert summary(case, [], registrar_ids, []) == (0, 0, 0, 0, 0, 0, 0)
    assert summary(case, [], registrar_ids, [target]) == (1, 0, 0, 0, 0, 1, 0)
    partial = audit_notes(case, [brief["id"]], selected[:1], [target])
    assert (
        partial["claimed_unfiled_true_pair_count"]
        == partial["accurate_positive_weight_count"]
        == 1
    )
    assert partial["claimed_documented_pair_count"] == 0
    stale = {**target, "epoch": (epoch + 1) % 3}
    stale = normalize_notes([stale], case["crates"])
    result = summary(case, [brief["id"]], selected, stale)
    assert result[0] == result[4] == result[5] == 1
    assert result[1] == result[6] == 0
