import json
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.decision_receipt import card_audit, card_inputs, card_render
from examples.seatlab.workbench.__main__ import main as workbench

DRAFT_FAILS = "si-24618ead1522e201"
DRAFT_SETTLES = "si-58eb44feac2e10ee"
REPLACE_ORIGINAL = "si-1c1135b3a2a365be"
REPLACE_DRAFT = "si-d6a136ca7e4bf8d1"
REPLACE_ORIGINAL_FAILS = "si-8d37d0777f34c365"
RUN_A = "0b6c3a52-6f0e-4b1d-9a8e-2f4c5d6e7a81"
RUN_B = "5d2e7f10-3a4b-4c6d-8e9f-0a1b2c3d4e5f"
RUN_C = "9a8b7c6d-5e4f-4a3b-9c2d-1e0f2a3b4c5d"
SECRET = "PRIVATE-7731"
Json = dict[str, Any]


def event(seq: int, seat: str, kind: str, choice: str) -> Json:
    return {"seq": seq, "seat": seat, "kind": kind, "choice": choice}


def raw_events(*pairs: tuple[str, str]) -> list[Json]:
    return [
        {"seq": seq, "kind": kind, "choice": choice}
        for seq, (kind, choice) in enumerate(pairs, 1)
    ]


RETURNED = raw_events(
    ("handoff", "original"), ("inbox", "original"), ("resolve", "close")
)
OLD_INFLIGHT = {
    "schema": "harbor.ship-inflight.cut/v1",
    "run": RUN_A,
    "fate": "settles",
    "channel": "deliver",
    "events": RETURNED,
    "replace": None,
    "settled": [],
    "drained": ["original"],
}
NEW_CITED = {
    **OLD_INFLIGHT,
    "schema": "harbor.ship-decision-receipt.cut/v1",
    "run": RUN_B,
    "cited_at_decision": "original",
}
WRONG_CITATION = {**NEW_CITED, "cited_at_decision": "draft"}
STOCK_FRESH = {
    "schema": "harbor.ship-handover.cut/v1",
    "run": RUN_C,
    "prior": "committed",
    "channel": "deliver",
    "events": raw_events(
        ("handoff", "original"), ("inbox", "original"), ("resolve", "fresh")
    ),
    "effects": ["original", "fresh"],
}


def save(tmp_path: Path, name: str, cut: object) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(cut), encoding="utf-8")
    return path


def decision(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    try:
        code = workbench(["decision", *argv])
    except SystemExit as stop:
        code = stop.code if type(stop.code) is int else 2
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_same_draft_resume_flips_grade_with_the_supplier_fate() -> None:
    left, right = card_inputs.tickets([DRAFT_FAILS, DRAFT_SETTLES])
    assert [left["ticket"], right["ticket"]] == [DRAFT_FAILS, DRAFT_SETTLES]
    assert {left["source"], right["source"]} == {"public-scripted"}
    assert [left["native_grade"], right["native_grade"]] == [1, 0]
    fails, settles = left["card"], right["card"]
    assert (
        fails["events"]
        == settles["events"]
        == [
            event(1, "outgoing", "handoff", "draft"),
            event(2, "relief", "inbox", "draft"),
            event(3, "relief", "resolve", "join-draft"),
        ]
    )
    assert (fails["world"], fails["axis"], fails["state"]) == (
        "inflight",
        "fate",
        "fails",
    )
    assert fails["citation"] == {"status": "unrecorded", "reference": None}
    assert fails["before"] == settles["before"] == {"parcels": ["draft"], "grade": 1}
    assert fails["final"] == {
        "parcels": ["draft"],
        "outcome": "exactly_one",
        "grade": 1,
    }
    assert fails["twin"] == {"state": "settles", **settles["final"]}
    assert settles["twin"] == {"state": "fails", **fails["final"]}
    assert fails["twin"]["grade"] == right["native_grade"] == 0

    comparison = card_render.report([left, right])["comparison"]
    assert comparison["events_match"] is True
    assert comparison["first_different_event"] is None
    assert comparison["state"]["same"] is False and comparison["citation"]["same"]


def test_replace_pair_first_differs_at_the_outgoing_handoff() -> None:
    envelopes = card_inputs.tickets([REPLACE_ORIGINAL, REPLACE_DRAFT])
    original, draft = (envelope["card"] for envelope in envelopes)
    assert original["state"] == draft["state"] == "settles"
    assert original["events"][2] == event(3, "relief", "resolve", "replace-original")
    assert draft["events"][2] == event(3, "relief", "resolve", "replace-draft")
    assert original["before"] == draft["before"] == {"parcels": ["fresh"], "grade": 1}
    assert original["final"] == {
        "parcels": ["fresh"],
        "outcome": "exactly_one",
        "grade": 1,
    }
    assert draft["final"] == {
        "parcels": ["fresh", "original"],
        "outcome": "duplicate",
        "grade": 0,
    }
    assert [envelope["native_grade"] for envelope in envelopes] == [1, 0]

    comparison = card_render.report(envelopes)["comparison"]
    assert comparison["comparable"] is True and comparison["events_match"] is False
    assert comparison["first_different_event"] == 1
    assert comparison["left_event"] == event(1, "outgoing", "handoff", "original")
    assert comparison["right_event"] == event(1, "outgoing", "handoff", "draft")
    assert comparison["state"]["same"] is True


def test_cli_json_is_the_report_and_text_labels_in_flight_before(
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = ("--ticket", REPLACE_ORIGINAL, "--ticket", REPLACE_DRAFT)
    code, out = decision(capsys, *argv, "--json")
    assert code == 0
    expected = card_render.report(
        card_inputs.tickets([REPLACE_ORIGINAL, REPLACE_DRAFT])
    )
    assert json.loads(out) == expected

    code, out = decision(capsys, *argv)
    assert code == 0
    assert "first difference at event 1" in out
    assert "In-flight state, settled before drain" in out
    assert "Stock baseline, staged before relief" not in out


def test_observed_pairs_are_eight_opposite_fate_twins() -> None:
    pairs = card_inputs.observed_pairs()
    assert len(pairs) == 8
    tickets = [envelope["ticket"] for pair in pairs for envelope in pair]
    assert len(tickets) == len(set(tickets)) == 16
    stable = []
    for left, right in pairs:
        a, b = left["card"], right["card"]
        assert a["world"] == b["world"] == "inflight"
        assert {a["state"], b["state"]} == {"fails", "settles"}
        assert a["channel"] == b["channel"] and a["events"] == b["events"]
        assert a["twin"] == {"state": b["state"], **b["final"]}
        assert b["twin"] == {"state": a["state"], **a["final"]}
        assert left["native_grade"] == a["final"]["grade"]
        assert right["native_grade"] == b["final"]["grade"]
        if a["final"]["grade"] == b["final"]["grade"]:
            stable.append({left["ticket"], right["ticket"]})
    assert stable == [{REPLACE_ORIGINAL, REPLACE_ORIGINAL_FAILS}]


def test_check_twins_reports_sixteen_matches_fourteen_flips(
    capsys: pytest.CaptureFixture[str],
) -> None:
    totals = {
        "pairs": 8,
        "directions_checked": 16,
        "grade_matches": 16,
        "ledger_matches": 16,
        "grade_flips": 14,
        "grade_stable": 2,
        "verdict": "matched",
    }
    result = card_audit.audit()
    assert {key: result[key] for key in totals} == totals

    code, out = decision(capsys, "--check-twins", "--json")
    assert code == 0
    payload = json.loads(out)
    assert {key: payload[key] for key in totals} == totals

    code, out = decision(capsys, "--check-twins")
    assert code == 0
    assert card_render.TWIN_LIMIT in out


def test_old_cut_is_unrecorded_and_new_cut_cites_without_an_extra_event(
    tmp_path: Path,
) -> None:
    files = [
        save(tmp_path, "old.json", OLD_INFLIGHT),
        save(tmp_path, "new.json", NEW_CITED),
    ]
    old, new = card_inputs.files(files)
    for envelope in (old, new):
        assert envelope["source"] == "supplied-cut"
        assert envelope["ticket"] is None and envelope["native_grade"] is None
    assert old["card"]["world"] == "inflight"
    assert new["card"]["world"] == "decision-receipt"
    assert old["card"]["citation"] == {"status": "unrecorded", "reference": None}
    assert new["card"]["citation"] == {
        "status": "cited_at_decision",
        "reference": "original",
    }
    assert old["card"]["events"] == new["card"]["events"]
    assert old["card"]["events"][2] == event(3, "relief", "resolve", "close")
    assert old["card"]["final"] == new["card"]["final"]

    comparison = card_render.report([old, new])["comparison"]
    assert comparison["comparable"] is True
    assert comparison["first_different_event"] is None
    assert comparison["citation"]["same"] is False


def test_citation_of_a_reference_never_returned_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        card_inputs.files([save(tmp_path, "wrong.json", WRONG_CITATION)])


def test_stock_prior_is_a_baseline_not_a_relief_event(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = save(tmp_path, "stock.json", STOCK_FRESH)
    (stock,) = card_inputs.files([path])
    card = stock["card"]
    assert (card["world"], card["axis"], card["state"]) == (
        "stock",
        "prior",
        "committed",
    )
    assert card["events"] == [
        event(1, "outgoing", "handoff", "original"),
        event(2, "relief", "inbox", "original"),
        event(3, "relief", "resolve", "fresh"),
    ]
    assert card["before"] == {"parcels": ["original"], "grade": 1}
    assert card["final"] == {
        "parcels": ["original", "fresh"],
        "outcome": "duplicate",
        "grade": 0,
    }
    assert card["twin"] == {
        "state": "dropped",
        "parcels": ["fresh"],
        "outcome": "exactly_one",
        "grade": 1,
    }

    (inflight,) = card_inputs.files([save(tmp_path, "inflight.json", OLD_INFLIGHT)])
    comparison = card_render.report([stock, inflight])["comparison"]
    assert comparison["comparable"] is False
    assert comparison["first_different_event"] is None

    code, out = decision(capsys, "--cut", str(path))
    assert code == 0
    assert "Stock baseline, staged before relief" in out
    assert "In-flight state, settled before drain" not in out


@pytest.mark.parametrize(
    "payload",
    [
        b'{"schema": "' + SECRET.encode() + b'", "run": ',
        json.dumps({**OLD_INFLIGHT, "note": SECRET}).encode(),
        json.dumps({**OLD_INFLIGHT, "channel": SECRET}).encode(),
    ],
)
def test_malformed_private_cut_does_not_echo_payload_or_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], payload: bytes
) -> None:
    path = tmp_path / f"{SECRET}.json"
    path.write_bytes(payload)
    with pytest.raises(ValueError) as caught:
        card_inputs.files([path])
    assert SECRET not in str(caught.value) and str(tmp_path) not in str(caught.value)

    code, out = decision(capsys, "--cut", str(path), "--json")
    assert code != 0
    assert SECRET not in out and str(tmp_path) not in out


@pytest.mark.parametrize("second", [OLD_INFLIGHT, {**NEW_CITED, "run": RUN_A}])
def test_one_run_supplied_as_two_files_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], second: Json
) -> None:
    files = [
        save(tmp_path, "left.json", OLD_INFLIGHT),
        save(tmp_path, "right.json", second),
    ]
    with pytest.raises(ValueError) as caught:
        card_inputs.files(files)
    assert RUN_A not in str(caught.value)

    code, out = decision(capsys, "--cut", str(files[0]), "--cut", str(files[1]))
    assert code != 0
    assert RUN_A not in out and str(tmp_path) not in out


@pytest.mark.parametrize(
    "tickets",
    [
        [],
        ["si-0000000000000000"],
        [DRAFT_FAILS, DRAFT_FAILS],
        [DRAFT_FAILS, DRAFT_SETTLES, REPLACE_DRAFT],
    ],
)
def test_ticket_selection_needs_one_or_two_distinct_registered_tickets(
    tickets: list[str],
) -> None:
    with pytest.raises(ValueError) as caught:
        card_inputs.tickets(tickets)
    assert "si-0000000000000000" not in str(caught.value)


@pytest.mark.parametrize(
    "argv",
    [
        (),
        ("--ticket", DRAFT_FAILS, "--cut", "cut.json"),
        ("--check-twins", "--ticket", DRAFT_FAILS),
        ("--ticket", DRAFT_FAILS, "--ticket", DRAFT_SETTLES, "--ticket", REPLACE_DRAFT),
    ],
)
def test_cli_refuses_mixed_or_excess_selectors(
    capsys: pytest.CaptureFixture[str], argv: tuple[str, ...]
) -> None:
    code, out = decision(capsys, *argv)
    assert code != 0
    assert "cut.json" not in out
