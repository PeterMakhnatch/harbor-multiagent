import importlib
import json

import pytest

PACKAGE = "examples.tasks.exhibit-relay.environment.exhibit_relay"
make_case = importlib.import_module(f"{PACKAGE}.cases").make_case
Session = importlib.import_module(f"{PACKAGE}.engine").Session
grade = importlib.import_module(f"{PACKAGE}.grading").grade
observation = importlib.import_module(f"{PACKAGE}.diagnostics").observation
MODES = ("authored", "author-masked")


def _card(case, role, key, epoch):
    return next(
        card
        for card in case[role]
        if card["key"] == key and card["start"] <= epoch <= card["end"]
    )


def _sources(case, brief):
    return [_card(case, "registrar", item, brief["epoch"]) for item in brief["items"]]


def _claim(case, brief):
    return {
        "epoch": brief["epoch"],
        "keys": sorted(card["value"] for card in _sources(case, brief)),
        "weight": brief["weight"],
    }


def _complete(session, case):
    realized = next(
        brief for brief in case["briefs"] if brief["id"] == case["realized"]
    )
    destinations = [
        _card(case, "depot", source["value"], realized["epoch"])
        for source in _sources(case, realized)
    ]
    session.apply("depot", {"op": "file", "ids": [card["id"] for card in destinations]})
    routes = dict(
        zip(realized["items"], [card["value"] for card in destinations], strict=True)
    )
    session.apply("loader", {"op": "commit", "routes": routes})
    cut = session.snapshot()
    result = grade(cut)
    report = observation(cut)
    assert result["reward"] == 1
    assert all(report[key] == value for key, value in result.items())
    return result


def test_authorship_semantics_distinct_from_documentary_support_and_grade():
    case = make_case(17, "coupling", layout="a")
    target = case["briefs"][0]
    documented = next(
        brief
        for brief in case["briefs"]
        if not set(brief["items"]) & set(target["items"])
    )
    registrar_ids = [card["id"] for card in _sources(case, documented)]
    assert not set(registrar_ids) & {card["id"] for card in _sources(case, target)}
    slips = [target["id"], documented["id"]]
    claim = _claim(case, target)
    submitted_reports = []
    grades = []
    for mode in MODES:
        for weight in (claim["weight"], 2):
            session = Session(case, mode)
            session.apply("loader", {"op": "request", "slips": slips})
            session.apply(
                "registrar",
                {
                    "op": "file",
                    "ids": registrar_ids,
                    "notes": [{**claim, "weight": weight}],
                },
            )
            report = observation(session.snapshot())["authored_handoff"]
            assert report["schema"] == "harbor.exhibit-relay.authorship/v1"
            submitted, delivered = report["submitted"], report["delivered"]
            for summary in (submitted, delivered):
                assert summary["assertion_count"] == 1
                assert summary["true_pair_count"] == 2
                assert summary["correct_pair_count"] == 1
                assert summary["irrelevant_pair_count"] == 0
                assert summary["missing_true_pair_count"] == 1
                assert summary["documented_true_pair_count"] == 1
                assert summary["claimed_documented_pair_count"] == 0
                assert summary["claimed_unfiled_true_pair_count"] == 1
                assert summary["missing_documented_true_pair_count"] == 1
            assert submitted["accurate_positive_weight_count"] == int(
                weight == claim["weight"]
            )
            assert submitted["inaccurate_positive_weight_count"] == int(
                weight != claim["weight"]
            )
            assert submitted["unknown_correct_weight_count"] == 0
            if weight == claim["weight"]:
                submitted_reports.append(submitted)
            if mode == "authored":
                assert delivered == submitted
            else:
                assert delivered["accurate_positive_weight_count"] == 0
                assert delivered["inaccurate_positive_weight_count"] == 0
                assert delivered["unknown_correct_weight_count"] == 1
                assert delivered["unknown_weight_count"] == 1
                assert session.view("depot")["bundles"] == [{**claim, "weight": 0}]
            encoded = json.dumps(report)
            assert all(crate not in encoded for crate in case["crates"])
            grades.append(_complete(session, case))
            assert observation(session.snapshot())["authored_handoff"] == report
    assert submitted_reports[0] == submitted_reports[1]
    assert all(result == grades[0] for result in grades)


@pytest.mark.parametrize("mode", MODES)
def test_missing_sender_and_explicit_empty_submission(mode):
    case = make_case(17, "coupling")
    target = case["briefs"][0]
    session = Session(case, mode)
    assert observation(session.snapshot())["authored_handoff"] is None
    session.apply("loader", {"op": "request", "slips": [target["id"]]})
    assert observation(session.snapshot())["authored_handoff"] is None
    session.apply("registrar", {"op": "file", "ids": [], "notes": []})
    report = observation(session.snapshot())["authored_handoff"]
    assert report["submitted"] == report["delivered"]
    summary = report["submitted"]
    assert summary["assertion_count"] == summary["correct_pair_count"] == 0
    assert summary["unknown_weight_count"] == summary["documented_true_pair_count"] == 0
    assert summary["true_pair_count"] == summary["missing_true_pair_count"] == 1


@pytest.mark.parametrize("mode", MODES)
def test_claims_outside_nominated_forecast(mode):
    case = make_case(17, "coupling")
    target, other = case["briefs"][:2]
    session = Session(case, mode)
    session.apply("loader", {"op": "request", "slips": [target["id"]]})
    off_epoch = {**_claim(case, target), "epoch": (target["epoch"] + 1) % 3}
    session.apply(
        "registrar",
        {"op": "file", "ids": [], "notes": [_claim(case, other), off_epoch]},
    )
    report = observation(session.snapshot())["authored_handoff"]
    for summary in (report["submitted"], report["delivered"]):
        assert summary["assertion_count"] == summary["irrelevant_pair_count"] == 2
        assert summary["correct_pair_count"] == 0
        assert summary["claimed_unfiled_true_pair_count"] == 0
        assert summary["missing_true_pair_count"] == 1


def test_baseline_omits_authored_handoff():
    case = make_case(17, "coupling")
    session = Session(case, "bundled")
    session.apply("loader", {"op": "request", "slips": [case["briefs"][0]["id"]]})
    session.apply("registrar", {"op": "file", "ids": []})
    report = observation(session.snapshot())
    assert set(report) == {
        "schema",
        "mode",
        "case_commitment",
        "profile",
        "handoff",
        *grade(session.snapshot()),
    }
