"""Family `ledger`: the log is really split, the twists defeat careless pooling, arithmetic is exact."""

from __future__ import annotations

import re
from collections import Counter

import pytest
from conftest import family, instance

LEDGER = family("ledger")
SETTINGS = [(difficulty, n_seats) for difficulty in ("easy", "hard") for n_seats in (2, 3)]
INSTANCES = range(30)
SIGN = {"deposit": 1, "withdrawal": -1, "fee": -1, "transfer-in": 1, "transfer-out": -1}


def cases():
    for difficulty, n_seats in SETTINGS:
        for i in INSTANCES:
            yield instance("ledger", i, n_seats, difficulty), LEDGER.SETTINGS[difficulty]


def test_the_log_is_split_into_disjoint_shards_that_cover_every_account():
    for inst, _setting in cases():
        solo = inst["solo_private"]
        n = solo["rows_in_log"]
        assert [row["id"] for row in solo["rows"]] == [f"T{k:02d}" for k in range(1, n + 1)]
        assert n <= LEDGER.MAX_ROWS_PER_SEAT  # even the solo seat's log fits in one instruction
        held = Counter(row["id"] for part in inst["private"].values() for row in part["rows"])
        assert held == Counter(row["id"] for row in solo["rows"])  # every row is held by exactly one seat
        openings = Counter(a for part in inst["private"].values() for a in part["openings"])
        assert openings == Counter(solo["accounts"])  # every opening balance is known to exactly one seat
        for part in inst["private"].values():
            assert len(part["rows"]) <= LEDGER.MAX_ROWS_PER_SEAT
            named = {row["account"] for row in part["rows"] if row["type"] != "reversal"}
            assert named == set(solo["accounts"])  # every account has rows in every seat's shard


def test_transfers_and_reversals_cross_seats():
    for inst, setting in cases():
        seat_of = {row["id"]: seat for seat, part in inst["private"].items() for row in part["rows"]}
        rows = {row["id"]: row for row in inst["solo_private"]["rows"]}
        legs: dict[str, list[dict]] = {}
        for row in rows.values():
            if row["type"].startswith("transfer"):
                legs.setdefault(row["transfer"], []).append(row)
        assert len(legs) == setting["transfers"]
        for pair in legs.values():
            out, into = sorted(pair, key=lambda r: r["type"] != "transfer-out")
            assert (out["type"], into["type"]) == ("transfer-out", "transfer-in")
            assert out["amount"] == into["amount"] and out["counterparty"] == into["account"]
            assert into["counterparty"] == out["account"] != into["account"]
            assert seat_of[out["id"]] != seat_of[into["id"]]
        reversals = [row for row in rows.values() if row["type"] == "reversal"]
        assert len(reversals) == setting["reversals"]
        assert len({row["reverses"] for row in reversals}) == len(reversals)  # no row is reversed twice
        for row in reversals:
            target = rows[row["reverses"]]
            assert target["id"] < row["id"] and target["type"] in ("deposit", "withdrawal", "fee")
            assert seat_of[target["id"]] != seat_of[row["id"]]
            assert set(row) == {"id", "type", "reverses"}  # it names neither the account nor the amount


def _closing(solo: dict) -> dict[str, int]:
    """An independent implementation of the goal's rules."""
    rows = {row["id"]: row for row in solo["rows"]}
    balance = dict(solo["openings"])
    for row in rows.values():
        target = rows[row["reverses"]] if row["type"] == "reversal" else None
        if target:
            balance[target["account"]] -= SIGN[target["type"]] * target["amount"]
        else:
            balance[row["account"]] += SIGN[row["type"]] * row["amount"]
    return balance


def test_the_answer_key_follows_the_rules_in_the_goal():
    for inst, _setting in cases():
        assert inst["answer_key"] == {"balances": _closing(inst["solo_private"])}


def test_no_seat_alone_gets_any_balance_right():
    for inst, _setting in cases():
        for part in inst["private"].values():
            assert LEDGER.score(LEDGER.oracle_answer([part]), inst["answer_key"])["fields_correct"] == 0


def test_careless_pooling_fails_and_careful_pooling_succeeds():
    for inst, _setting in cases():
        parts = list(inst["private"].values())
        key = inst["answer_key"]
        assert LEDGER.score(LEDGER.oracle_answer(parts), key)["score"] == 1.0
        assert LEDGER.score(LEDGER.pool_subtotals(parts), key)["score"] < 1.0
        assert LEDGER.score(LEDGER.pool_double_booked(parts), key)["score"] < 1.0
        # independently: seats that report only per-account subtotals lose the cross-seat reversals
        totals = Counter()
        for part in parts:
            totals.update(part["openings"])
            for row in part["rows"]:
                if row["type"] != "reversal":
                    totals[row["account"]] += SIGN[row["type"]] * row["amount"]
        assert LEDGER.score({"balances": dict(totals)}, key)["score"] < 1.0


def test_closing_balances_are_not_written_in_any_text():
    for inst, _setting in cases():
        shown = set()
        for text in (inst["goal"], inst["answer_format"], inst["solo_text"], *inst["private_text"].values()):
            for token in re.split(r"[\s|:;,()`{}\"]+", text):
                token = token.rstrip(".")
                if re.fullmatch(r"-?\d+", token):
                    shown.add(int(token))
        assert not shown & set(inst["answer_key"]["balances"].values())


def test_texts_show_every_held_row_and_opening():
    inst = instance("ledger", 0, 2, "easy")
    for seat, part in inst["private"].items():
        text = inst["private_text"][seat]
        for row in part["rows"]:
            line = next(line for line in text.splitlines() if line.startswith(f"| {row['id']} |"))
            if row["type"] == "reversal":
                assert f"cancels row {row['reverses']}" in line
            else:
                assert f"| {row['type']} | {row['account']} | {row['amount']} |" in line
        for account, value in part["openings"].items():
            assert f"- {account}: {value}" in text


P1 = {"accounts": ["A", "B"], "rows_in_log": 7, "openings": {"A": 100}, "rows": [
    {"id": "T01", "type": "deposit", "account": "A", "amount": 30},
    {"id": "T03", "type": "transfer-out", "account": "A", "amount": 40, "transfer": "X1", "counterparty": "B"},
    {"id": "T05", "type": "withdrawal", "account": "B", "amount": 20},
    {"id": "T06", "type": "reversal", "reverses": "T02"},
]}
P2 = {"accounts": ["A", "B"], "rows_in_log": 7, "openings": {"B": 50}, "rows": [
    {"id": "T02", "type": "fee", "account": "B", "amount": 5},
    {"id": "T04", "type": "transfer-in", "account": "B", "amount": 40, "transfer": "X1", "counterparty": "A"},
    {"id": "T07", "type": "reversal", "reverses": "T01"},
]}


def test_rules_on_a_hand_made_log():
    # A: 100 + 30 (T01) - 40 (T03) - 30 (T07 cancels T01) = 60
    # B: 50 - 5 (T02) + 40 (T04) - 20 (T05) + 5 (T06 cancels T02) = 70
    assert LEDGER.oracle_answer([P1, P2]) == {"balances": {"A": 60, "B": 70}}
    assert LEDGER.oracle_answer([P2, P1, P1]) == {"balances": {"A": 60, "B": 70}}  # order and repeats
    assert LEDGER.oracle_answer([P1]) == {"balances": {"A": 90, "B": -20}}  # T06's target is unknown
    assert LEDGER.pool_subtotals([P1, P2]) == {"balances": {"A": 90, "B": 65}}
    assert LEDGER.pool_double_booked([P1, P2]) == {"balances": {"A": 20, "B": 110}}


KEY = {"balances": {"Alder": 1240, "Birch": -75, "Cedar": 0}}


@pytest.mark.parametrize(("answer", "correct"), [
    ({"balances": {"Alder": 1240, "Birch": -75, "Cedar": 0}}, 3),
    ({"Alder": 1240, "Birch": -75, "Cedar": 0}, 3),  # without the "balances" wrapper
    ({"Balances": {" alder ": "1240", "BIRCH": " -75 ", "cedar": "0"}}, 3),  # case, whitespace, strings
    ({"balances": {"Alder": "1,240", "Birch": "−75", "Cedar": "+0"}}, 3),  # separators, U+2212 minus
    ({"balances": {"Alder": 1240.0, "Birch": "-75.00", "Cedar": -0.0}}, 3),  # integral floats
    ({"balances": {"Alder": 1240, "alder": "1240", "Birch": -75}}, 2),  # agreeing duplicates count
    ({"balances": {"Alder": 1240, "alder": 1241, "Birch": -75}}, 1),  # conflicting duplicates do not
    ({"balances": {"Alder": 1239, "Birch": 75, "Cedar": False}}, 0),  # False is not 0
    ({"balances": {"Alder": 1240.5, "Birch": "-75.5", "Cedar": "0.01"}}, 0),  # no rounding
    ({"balances": {"Alder": "1.240", "Birch": "-7 5", "Cedar": "zero"}}, 0),
    ({"balances": {"Alder": "1240 units", "Birch": "$-75", "Cedar": [0]}}, 0),
    ({"balances": {"Alder": float("nan"), "Birch": float("-inf"), "Cedar": None}}, 0),
    ({"balances": {"Alder": "12,40", "Birch": "-,75", "Cedar": "1" * 40}}, 0),
    ({"balances": {"Alder": 1240, "Zed": 5}}, 1),  # unknown accounts are ignored
    ({"balances": [1240, -75, 0]}, 0),
    (None, 0),
])
def test_score_is_forgiving_about_format_but_never_about_values(answer, correct):
    result = LEDGER.score(answer, KEY)
    assert (result["fields_correct"], result["fields_total"]) == (correct, 3)
    assert result["score"] == correct / 3


def test_careless_pooling_distribution_is_reported(capsys):
    """Prints the share of accounts right for each strategy (run with -s to see it)."""
    for difficulty, n_seats in SETTINGS:
        tallies = {"one seat": Counter(), "subtotals": Counter(), "double-booked": Counter()}
        for i in INSTANCES:
            inst = instance("ledger", i, n_seats, difficulty)
            parts, key = list(inst["private"].values()), inst["answer_key"]
            for part in parts:
                tallies["one seat"][round(LEDGER.score(LEDGER.oracle_answer([part]), key)["score"], 2)] += 1
            tallies["subtotals"][round(LEDGER.score(LEDGER.pool_subtotals(parts), key)["score"], 2)] += 1
            tallies["double-booked"][round(LEDGER.score(LEDGER.pool_double_booked(parts), key)["score"], 2)] += 1
        print(difficulty, n_seats, {name: dict(sorted(t.items())) for name, t in tallies.items()})
        assert max(tallies["subtotals"]) < 1.0 and max(tallies["double-booked"]) < 1.0
