import io
import json
import stat
import sys
from dataclasses import replace
from fractions import Fraction
from itertools import product

import pytest

from examples.seatlab.receiver_memory import provenance, revised

SEED = 7
PRICE = Fraction(1, 4)
CANARY = "private-action-canary-843"


@pytest.fixture(scope="module")
def episodes():
    return {sender: revised.cases(SEED, sender) for sender in revised.SENDERS}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def test_offer_retains_real_slot_and_varies_only_one_ascii_tag(episodes):
    for case in episodes["truthful"]:
        reports = case.history[-2]["reply"]["reports"]
        for index, age in enumerate(provenance.AGES):
            widths = set()
            common = []
            for arm, coin in product(provenance.ARMS, provenance.COINS):
                data = provenance.offer(case, age, arm, coin, PRICE)
                value = json.loads(data)
                assert data == _canonical(value)
                assert set(value) == {
                    "schema",
                    "protocol",
                    "instruction",
                    "current",
                    "note",
                    "price",
                }
                assert value["instruction"] == case.instruction
                assert value["current"] == case.history[-1]
                assert value["price"] == "1/4"
                tag = {"version": str(index + 1), "erased": "?", "sham": coin}[arm]
                assert value["note"] == {"slot": reports[index]["slot"], "tag": tag}
                assert b"additional_read" not in data and b'"reports":[' not in data
                widths.add(len(data))
                value["note"]["tag"] = "?"
                common.append(value)
            assert len(widths) == 1
            assert all(value == common[0] for value in common)
    confirm, changed = episodes["truthful"]
    for arm, coin in product(provenance.ARMS, provenance.COINS):
        assert provenance.offer(
            confirm, "oldest", arm, coin, PRICE
        ) == provenance.offer(changed, "oldest", arm, coin, PRICE)
        assert provenance.offer(
            confirm, "newest", arm, coin, PRICE
        ) != provenance.offer(changed, "newest", arm, coin, PRICE)


def test_reread_returns_actual_native_reply_even_with_incorrect_sender(episodes):
    for sender, cases in episodes.items():
        for case in cases:
            data = provenance.reread(case)
            result = json.loads(data)
            assert data == _canonical(result)
            assert set(result) == {"schema", "protocol", "inbox"}
            assert result["inbox"] == case.refresh
            slot = result["inbox"]["reply"]["reports"][-1]["slot"]
            assert case.scores[slot] == int(
                sender == "truthful" or case.name == "confirm"
            )
    first, changed = episodes["repeat"]
    assert provenance.reread(first) == provenance.reread(changed)
    for age, arm, coin in product(provenance.AGES, provenance.ARMS, provenance.COINS):
        assert provenance.offer(first, age, arm, coin, PRICE) == provenance.offer(
            changed, age, arm, coin, PRICE
        )
        assert first.scores != changed.scores


def test_offer_uses_prefix_not_refresh_or_scores(episodes):
    case = episodes["truthful"][1]
    poisoned = replace(case, refresh={}, scores={CANARY: 999})
    assert provenance.offer(
        poisoned, "newest", "version", "1", PRICE
    ) == provenance.offer(case, "newest", "version", "1", PRICE)
    with pytest.raises(ValueError):
        provenance.reread(poisoned)
    correct = case.history[-2]["reply"]["reports"]
    corrupt = {**case.history[-2], "reply": {"reports": list(reversed(correct))}}
    wrong_ack = {**case.history[-1], "body": case.history[1]["body"]}
    for prefix in (
        (*case.history[:-2], corrupt, case.history[-1]),
        (*case.history[:-1], wrong_ack),
        case.history[:-1],
    ):
        with pytest.raises(ValueError):
            provenance.offer(
                replace(case, history=prefix), "newest", "version", "2", PRICE
            )


@pytest.mark.parametrize(
    "price", [-1, 0.5, Fraction(-1, 2), Fraction(2), Fraction(1, 1000001)]
)
def test_offer_rejects_inexact_or_out_of_range_price(episodes, price):
    with pytest.raises(ValueError):
        provenance.offer(episodes["truthful"][0], "oldest", "erased", "1", price)


def _args(path, sender="truthful", world="replace", price="1/4"):
    return [
        "--seed",
        str(SEED),
        "--sender",
        sender,
        "--world",
        world,
        "--age",
        "oldest",
        "--arm",
        "sham",
        "--coin",
        "2",
        "--price",
        price,
        "--outcome",
        str(path),
    ]


@pytest.fixture
def cli(episodes, monkeypatch):
    def load(seed, sender):
        assert seed == SEED
        return episodes[sender]

    monkeypatch.setattr(provenance, "cases", load)
    return provenance.main


def _stdin(monkeypatch, raw):
    monkeypatch.setattr(
        sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
    )


def test_read_offer_and_commit_occur_in_order(
    cli, episodes, tmp_path, capsysbinary, monkeypatch
):
    case = episodes["truthful"][1]
    path = tmp_path / "private-outcome.json"
    correct = case.refresh["reply"]["reports"][-1]["slot"]
    responses = [
        b'{"op":"read"}\n',
        _canonical({"op": "commit", "slot": correct}) + b"\n",
    ]
    expected = [
        provenance.offer(case, "oldest", "sham", "2", PRICE),
        provenance.reread(case),
    ]

    class Actions:
        @property
        def buffer(self):
            return self

        def readline(self, limit):
            assert limit == provenance.MAX_ACTION_BYTES + 1
            output = capsysbinary.readouterr()
            assert output.out == expected.pop(0) + b"\n"
            assert output.err == b""
            assert not path.exists()
            return responses.pop(0)

    monkeypatch.setattr(sys, "stdin", Actions())
    assert cli(_args(path)) == 0
    assert not expected and not responses
    assert capsysbinary.readouterr() == (b"", b"")
    outcome = json.loads(path.read_bytes())
    assert outcome["score"] == outcome["reads"] == 1
    assert outcome["net_utility"] == "3/4"
    assert outcome["price"] == "1/4"
    assert outcome["slot"] == correct
    assert outcome["model_calls"] == outcome["trials"] == 0
    assert "Scripted-continuation" in outcome["scope"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_immediate_commit_never_requests_reread(
    cli, episodes, tmp_path, capsysbinary, monkeypatch
):
    case = episodes["truthful"][1]
    path = tmp_path / "outcome.json"
    correct = next(slot for slot, score in case.scores.items() if score == 1)
    _stdin(monkeypatch, _canonical({"op": "commit", "slot": correct}) + b"\n")

    def no_read(case):
        pytest.fail("immediate commit requested a reread")

    monkeypatch.setattr(provenance, "reread", no_read)
    assert cli(_args(path)) == 0
    assert capsysbinary.readouterr() == (
        provenance.offer(case, "oldest", "sham", "2", PRICE) + b"\n",
        b"",
    )
    outcome = json.loads(path.read_bytes())
    assert outcome["score"] == 1 and outcome["reads"] == 0
    assert outcome["net_utility"] == "1"


def test_repeat_sender_control_grades_the_actual_read_choice(
    cli, episodes, tmp_path, capsysbinary, monkeypatch
):
    for case in episodes["repeat"]:
        path = tmp_path / f"{case.name}.json"
        reported = case.refresh["reply"]["reports"][-1]["slot"]
        _stdin(
            monkeypatch,
            b'{"op":"read"}\n' + _canonical({"op": "commit", "slot": reported}) + b"\n",
        )
        assert cli(_args(path, sender="repeat", world=case.name)) == 0
        assert capsysbinary.readouterr().out.splitlines() == [
            provenance.offer(case, "oldest", "sham", "2", PRICE),
            provenance.reread(case),
        ]
        outcome = json.loads(path.read_bytes())
        assert outcome["score"] == case.scores[reported]
        assert outcome["net_utility"] == ("3/4" if case.name == "confirm" else "-1/4")


@pytest.mark.parametrize(
    ("raw", "lines"),
    [
        (b"", 1),
        (b'{"op":"commit","slot":"A"}', 1),
        (b'{"op":"read"}\n', 2),
        (b'{"op":"read"}\n{"op":"read"}\n{"op":"commit","slot":"A"}\n', 2),
        (b'{"op":"commit","slot":"A","slot":"B"}\n', 1),
        (b'{"op":"commit","slot":NaN}\n', 1),
        (b'{"op":"commit","slot":1e999}\n', 1),
        (b'{"op":"read","slot":"A"}\n', 1),
        (b'{"op":"read"}\n{"op":"commit","slot":null}\n', 2),
        (b'{"op":"read"}\n{"op":"other"}\n', 2),
        (_canonical({"op": "commit", "slot": CANARY}) + b"\n", 1),
        (b" " * provenance.MAX_ACTION_BYTES + b'{"op":"read"}\n', 1),
    ],
)
def test_invalid_actions_are_not_scored(
    cli, tmp_path, capsysbinary, monkeypatch, raw, lines
):
    path = tmp_path / "not-created.json"
    _stdin(monkeypatch, raw)
    assert cli(_args(path)) == 2
    assert not path.exists()
    output = capsysbinary.readouterr()
    assert len(output.out.splitlines()) == lines
    assert output.err.startswith(b"receiver-memory provenance: ")
    assert CANARY.encode() not in output.out + output.err


def test_existing_outcome_and_symlink_are_not_clobbered(
    cli, tmp_path, capsysbinary, monkeypatch
):
    existing = tmp_path / "existing.json"
    existing.write_bytes(b"original")
    link = tmp_path / "link.json"
    link.symlink_to(existing)
    for path in (existing, link):
        _stdin(monkeypatch, b'{"op":"commit","slot":"A"}\n')
        assert cli(_args(path)) == 2
        output = capsysbinary.readouterr()
        assert output.out == b""
        assert existing.read_bytes() == b"original"
        assert str(path).encode() not in output.err
