import json
from functools import cache
from typing import Any

import pytest

from examples.seatlab.physical_dispatch import certificate
from examples.seatlab.physical_dispatch.freshness import certify_freshness
from examples.seatlab.physical_dispatch.stage_labels import summarize_versions

Record = dict[str, Any]

CLASSES = ("current", "expired", "future", "expired_and_future", "other_key", "empty")
SEEDS = 64


def at(cards: list[Record], key: str, epoch: int) -> Record:
    (card,) = [c for c in cards if c["key"] == key and c["start"] <= epoch <= c["end"]]
    return card


def world(case: Record) -> tuple[Record, int, Record, Record]:
    order = next(b for b in case["briefs"] if b["id"] == case["realized"])
    epoch = order["epoch"]
    crates = {i: at(case["registrar"], i, epoch) for i in order["items"]}
    bays = {i: at(case["depot"], crates[i]["value"], epoch) for i in order["items"]}
    return order, epoch, crates, bays


def lineage(case: Record, item: str) -> list[Record]:
    crates = world(case)[2]
    return [at(case["depot"], crates[item]["value"], e) for e in range(3)]


def search(epoch: int, keep) -> tuple[int, Record, str] | None:
    for seed in range(SEEDS):
        case = certificate.make_case(seed, "fork", layout="draw")
        order, realized, _, _ = world(case)
        if realized != epoch:
            continue
        for item in order["items"]:
            if keep(lineage(case, item)):
                return seed, case, item
    return None


@cache
def setup() -> dict[str, Any]:
    found = search(1, lambda cards: len({c["value"] for c in cards}) == 3)
    assert found is not None, "no epoch-1 draw links prior and future depot records"
    seed, case, target = found
    order, epoch, crates, bays = world(case)
    (other,) = [i for i in order["items"] if i != target]
    past, now, later = lineage(case, target)
    assert epoch == 1 and past["end"] < epoch < later["start"]
    assert (
        now["id"] == bays[target]["id"]
        and len({past["id"], now["id"], later["id"]}) == 3
    )
    linked = {crates[target]["value"], crates[other]["value"]}
    spare = at(case["depot"], min(set(case["crates"]) - linked), epoch)
    stranger = at(
        case["registrar"], min(set(case["exhibits"]) - set(order["items"])), epoch
    )
    return {
        "seed": seed,
        "case": case,
        "items": list(order["items"]),
        "target": target,
        "other": other,
        "past": past,
        "now": now,
        "later": later,
        "spare": spare,
        "stranger": stranger,
        "registrar": sorted(crates[i]["id"] for i in order["items"]),
        "truth": {i: bays[i]["value"] for i in order["items"]},
    }


def actions(
    case: Record, registrar: list[str], depot: list[str], routes=None, slips=None
) -> list[Record]:
    out = [
        {"op": "request", "slips": [case["realized"]] if slips is None else slips},
        {"op": "file", "ids": registrar},
        {"op": "file", "ids": depot},
    ]
    return out if routes is None else [*out, {"op": "commit", "routes": routes}]


def play(case: Record, steps: list[Record]) -> Record:
    session = certificate.Session(case, "linked")
    for role, action in zip(("loader", "registrar", "depot", "loader"), steps):
        session.apply(role, action)
    return session.snapshot()


def hidden(case: Record) -> set[str]:
    labels = {*case["exhibits"], *case["crates"], *case["bays"], case["realized"]}
    labels |= {b["id"] for b in case["briefs"]}
    return labels | {c["id"] for c in case["registrar"] + case["depot"]}


def report(cut: Record) -> tuple[Record, Record]:
    frozen = json.dumps(cut, sort_keys=True)
    native = certificate.grade(cut)
    out = summarize_versions(cut)
    assert json.dumps(cut, sort_keys=True) == frozen
    assert out["accepted_actions"] == len(cut["events"])
    text = json.dumps(out, sort_keys=True)
    assert not any(label in text for label in hidden(cut["case"]))
    return out, native


def tally(*names: str) -> dict[str, int]:
    out = dict.fromkeys(CLASSES, 0)
    for name in names:
        out[name] += 1
    return out


def row(reg, dep, agenda, chain=False, expired=None, current=None, correct=None):
    return {
        "registrar": reg,
        "depot": dep,
        "on_depot_agenda": agenda,
        "current_chain": chain,
        "choice_matches_expired_depot_chain": expired,
        "choice_matches_expired_registrar_current_depot": None,
        "choice_matches_current_chain": current,
        "physically_correct": correct,
    }


def rows(s: Record, target: Record, other: Record) -> list[Record]:
    return [target if item == s["target"] else other for item in s["items"]]


def main_depot(s: Record) -> list[str]:
    return [s["past"]["id"], s["later"]["id"], s["spare"]["id"]]


@pytest.mark.parametrize("count", (0, 1, 2))
def test_prefixes_reveal_no_order_evidence_or_grade(count: int) -> None:
    s = setup()
    other = certificate.make_case((s["seed"] + 1) % SEEDS, "fork", layout="draw")
    cuts = [
        play(s["case"], actions(s["case"], s["registrar"], main_depot(s))[:count]),
        play(s["case"], actions(s["case"], [], [], slips=[])[:count]),
        play(other, actions(other, [], [])[:count]),
    ]
    reports = []
    for cut in cuts:
        out, native = report(cut)
        assert native["status"] != "complete"
        assert out["order_revealed"] is False and out["accepted_actions"] == count
        assert [out[k] for k in ("committed", "registrar", "depot")] == [None] * 3
        assert out["on_depot_agenda"] is None and out["items"] is None
        reports.append(out)
    assert reports[0] == reports[1] == reports[2]


def test_revealed_order_without_commit_keeps_choices_null() -> None:
    s = setup()
    out, native = report(
        play(s["case"], actions(s["case"], s["registrar"], main_depot(s)))
    )
    assert native["status"] != "complete"
    assert out["order_revealed"] is True and out["committed"] is False
    assert out["registrar"] == tally("current", "current")
    assert out["depot"] == tally("expired_and_future", "other_key")
    assert out["on_depot_agenda"] == 2
    assert out["items"] == rows(
        s, row("current", "expired_and_future", True), row("current", "other_key", True)
    )
    empty, _ = report(play(s["case"], actions(s["case"], [], [])))
    assert empty["registrar"] == empty["depot"] == tally("empty", "empty")
    assert empty["on_depot_agenda"] == 0 and empty["committed"] is False
    assert empty["items"] == [row("empty", "empty", False)] * 2


def commit(
    s: Record, depot: list[str], target_bay: str, registrar=None
) -> tuple[Record, Record]:
    routes = {**s["truth"], s["target"]: target_bay}
    filed = s["registrar"] if registrar is None else registrar
    out, native = report(play(s["case"], actions(s["case"], filed, depot, routes)))
    correct = sum(routes[i] == s["truth"][i] for i in s["items"])
    assert out["committed"] is True and native["status"] == "complete"
    assert (native["correct_items"], native["reward"]) == (correct, int(correct == 2))
    return out, native


def test_expired_and_future_filing_has_matching_semantics_only() -> None:
    s = setup()
    assert s["past"]["value"] != s["truth"][s["target"]] != s["later"]["value"]
    other = row("current", "other_key", True, correct=True)
    cases = (
        (s["past"]["value"], True, False),
        (s["later"]["value"], False, False),
        (s["truth"][s["target"]], False, True),
    )
    for bay, expired, correct in cases:
        out, _ = commit(s, main_depot(s), bay)
        assert out["depot"] == tally("expired_and_future", "other_key")
        assert out["registrar"] == tally("current", "current")
        target = row(
            "current", "expired_and_future", True, False, expired, None, correct
        )
        assert out["items"] == rows(s, target, other)


def test_future_only_card_is_never_an_expired_match() -> None:
    s = setup()
    other = row("current", "other_key", True, correct=True)
    out, _ = commit(s, [s["later"]["id"]], s["later"]["value"])
    assert out["depot"] == tally("future", "other_key")
    assert out["items"] == rows(s, row("current", "future", True, correct=False), other)
    out, _ = commit(s, [s["past"]["id"]], s["past"]["value"])
    assert out["depot"] == tally("expired", "other_key")
    assert out["items"] == rows(
        s, row("current", "expired", True, expired=True, correct=False), other
    )


def test_wrong_key_and_no_card_are_separate() -> None:
    s = setup()
    bay = s["past"]["value"]
    out, _ = commit(s, [], bay)
    assert out["depot"] == tally("empty", "empty")
    assert out["items"] == rows(
        s,
        row("current", "empty", True, correct=False),
        row("current", "empty", True, correct=True),
    )
    out, _ = commit(s, [s["spare"]["id"]], bay)
    assert out["depot"] == tally("other_key", "other_key")
    assert out["items"] == rows(
        s,
        row("current", "other_key", True, correct=False),
        row("current", "other_key", True, correct=True),
    )
    out, _ = commit(s, main_depot(s), bay, registrar=[s["stranger"]["id"]])
    assert out["registrar"] == tally("other_key", "other_key")
    assert out["on_depot_agenda"] == 0
    assert out["items"] == rows(
        s,
        row("other_key", "expired_and_future", False, correct=False),
        row("other_key", "other_key", False, correct=True),
    )
    out, _ = commit(s, main_depot(s), bay, registrar=[])
    assert out["registrar"] == tally("empty", "empty")
    assert all(r["choice_matches_expired_depot_chain"] is None for r in out["items"])


def test_current_chain_and_native_grade_ignore_filed_records() -> None:
    s = setup()
    _, epoch, crates, _ = world(s["case"])
    chain = sorted(
        at(s["case"]["depot"], crates[i]["value"], epoch)["id"] for i in s["items"]
    )
    assert s["now"]["id"] in chain and len(set(chain)) == 2
    filings = ([], [s["spare"]["id"]], [s["later"]["id"]], main_depot(s), chain)
    for bay in (s["truth"][s["target"]], s["past"]["value"]):
        grades = set()
        for depot in filings:
            _, native = commit(s, depot, bay)
            grades.add((native["status"], native["reward"], native["correct_items"]))
        assert len(grades) == 1
    out, native = commit(s, chain, s["truth"][s["target"]])
    assert native["reward"] == 1 and out["depot"] == tally("current", "current")
    both = row("current", "current", True, True, None, True, True)
    assert out["items"] == [both, both]


def test_expired_card_sharing_live_value_is_not_automatically_incorrect() -> None:
    found = search(2, lambda c: c[0]["value"] == c[2]["value"] != c[1]["value"])
    assert found is not None, "the native seed range must exercise recurring bay values"
    _, case, target = found
    order, epoch, crates, bays = world(case)
    past, middle, now = lineage(case, target)
    assert past["end"] < middle["start"] <= middle["end"] < epoch == now["start"]
    assert (
        past["id"] != now["id"]
        and past["value"] == now["value"] == bays[target]["value"]
    )
    truth = {i: bays[i]["value"] for i in order["items"]}
    registrar = sorted(crates[i]["id"] for i in order["items"])
    reports = {}
    for name, depot in (
        ("none", []),
        ("shared", [past["id"]]),
        ("distinct", [middle["id"]]),
        ("recurring_and_current", [past["id"], now["id"]]),
    ):
        out, native = report(play(case, actions(case, registrar, depot, truth)))
        assert (native["reward"], native["correct_items"]) == (1, 2)
        reports[name] = out["items"][order["items"].index(target)]
    assert reports["shared"] == row("current", "expired", True, False, True, None, True)
    assert reports["distinct"] == row(
        "current", "expired", True, False, False, None, True
    )
    assert reports["none"] == row("current", "empty", True, correct=True)
    assert reports["recurring_and_current"] == row(
        "current", "current", True, chain=True, expired=True, current=True, correct=True
    )


def test_matched_stale_and_unrelated_records_have_equal_value_but_different_content():
    report = certify_freshness(7)
    treatments = report["treatments"]
    stale = treatments["expired"]
    other = treatments["unrelated"]
    current = treatments["current"]
    assert report["native_commits"] == 3 * 2 * 64
    assert {
        stamp["bytes"]
        for t in treatments.values()
        for stamp in t["request_stamps"].values()
    } == {report["final_owner_request_bytes"]}
    assert stale["request_stamps"]["original"] != other["request_stamps"]["original"]
    for hidden in (stale, other):
        assert (
            hidden["request_stamps"]["original"] == hidden["request_stamps"]["swapped"]
        )
        assert (
            hidden["analysis"]["observed"] == "1/2" and hidden["depot_supported"] == 0
        )
    assert current["request_stamps"]["original"] != current["request_stamps"]["swapped"]
    assert current["analysis"]["observed"] == "1" and current["depot_supported"] == 2
    assert (
        stale["version_labels"]["depot"]["expired"]
        == other["version_labels"]["depot"]["other_key"]
        == 2
    )
    assert set(stale["stale_reference"]["native_reward"].values()) == {0}
    assert stale["stale_reference"]["correct_items"] == 0
    assert report["comparisons"]["expired_to_unrelated"]["relation"] == "equivalent"
    assert report["comparisons"]["expired_to_unrelated"]["gain"] == "0"
