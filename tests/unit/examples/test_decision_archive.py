import json
import os
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.decision_receipt import archive, archive_inflight
from examples.seatlab.decision_receipt import decision_verify as verify
from examples.ship_inflight.replay import PLACEHOLDER_RUN

ARCHIVED = ["stock-controls", "inflight-cuts", "two-worlds", "disconnected"]
SUPPLIED = "supplied-cuts"
REFUSED = (
    "Shipping evidence refused: check the published source files and requested cuts.\n"
)
RELEASE_KEYS = {
    "release",
    "source",
    "registered",
    "present",
    "missing",
    "accepted_decisions",
    "decision_unrecorded",
    "no_decision",
    "inbox_returns_before_decision",
    "inbox_unrecorded_at_decision",
    "empty_returns_before_decision",
    "references_before_decision",
    "citation_opportunities_measured",
    "cited_at_decision",
    "strata",
}
STRATUM_KEYS = {
    "world",
    "axis",
    "state",
    "observation",
    "rows",
    "known_rewards",
    "reward_one",
}
MEASURED = {"cited_at_decision", "returned_not_cited"}
RUN_A = "0b6c3a52-6f0e-4b1d-9a8e-2f4c5d6e7a81"
RUN_B = "5d2e7f10-3a4b-4c6d-8e9f-0a1b2c3d4e5f"
RUN_C = "9a8b7c6d-5e4f-4a3b-9c2d-1e0f2a3b4c5d"
RUN_D = "c4d5e6f7-0819-4a2b-b3c4-d5e6f708192a"
RUN_E = "e1f2a3b4-c5d6-4e7f-8091-a2b3c4d5e6f7"
RUN_F = "f0e1d2c3-b4a5-4968-8776-655443322110"
RUNS = (RUN_A, RUN_B, RUN_C, RUN_D, RUN_E, RUN_F)
RETURNED = [("handoff", "original"), ("inbox", "original"), ("resolve", "close")]


def cut(
    run: str,
    fate: str,
    channel: str,
    events: list[tuple[str, str]],
    drained: list[str],
    cited: str | None,
) -> dict[str, Any]:
    return {
        "schema": verify.CUT_SCHEMA,
        "run": run,
        "fate": fate,
        "channel": channel,
        "events": [
            {"seq": seq, "kind": kind, "choice": choice}
            for seq, (kind, choice) in enumerate(events, 1)
        ],
        "replace": None,
        "settled": [],
        "drained": drained,
        verify.CITATION: cited,
    }


CITED_SETTLES = cut(RUN_A, "settles", "deliver", RETURNED, ["original"], "original")
CITED_FAILS = cut(RUN_B, "fails", "deliver", RETURNED, [], "original")
NOT_CITED = cut(RUN_C, "settles", "deliver", RETURNED, ["original"], None)
EMPTY_INBOX = cut(
    RUN_D,
    "settles",
    "withhold",
    [("handoff", "original"), ("inbox", "none"), ("resolve", "close")],
    ["original"],
    None,
)
LATE_READ = cut(
    RUN_E,
    "fails",
    "deliver",
    [("handoff", "original"), ("resolve", "close"), ("inbox", "original")],
    [],
    None,
)
NO_DECISION = cut(
    RUN_F,
    "settles",
    "deliver",
    [("handoff", "original"), ("inbox", "original")],
    ["original"],
    None,
)
CUTS = {
    "cited-settles.json": CITED_SETTLES,
    "cited-fails.json": CITED_FAILS,
    "not-cited.json": NOT_CITED,
    "empty-inbox.json": EMPTY_INBOX,
    "late-read.json": LATE_READ,
    "no-decision.json": NO_DECISION,
}


def stratum(state: str, observation: str, rows: int, wins: int) -> dict[str, object]:
    return {
        "world": "inflight",
        "axis": "fate",
        "state": state,
        "observation": observation,
        "rows": rows,
        "known_rewards": rows,
        "reward_one": wins,
    }


INFLIGHT_RELEASE = {
    "release": "inflight-cuts",
    "source": archive.SELECTION["inflight-cuts"],
    "registered": 16,
    "present": 16,
    "missing": 0,
    "accepted_decisions": 14,
    "decision_unrecorded": 0,
    "no_decision": 2,
    "inbox_returns_before_decision": 12,
    "inbox_unrecorded_at_decision": 0,
    "empty_returns_before_decision": 4,
    "references_before_decision": 8,
    "citation_opportunities_measured": 0,
    "cited_at_decision": None,
    "strata": [
        stratum("fails", "decided_without_reference", 3, 1),
        stratum("fails", "no_decision", 1, 0),
        stratum("fails", "returned_citation_unrecorded", 4, 3),
        stratum("settles", "decided_without_reference", 3, 2),
        stratum("settles", "no_decision", 1, 1),
        stratum("settles", "returned_citation_unrecorded", 4, 2),
    ],
}
SUPPLIED_RELEASE = {
    "release": SUPPLIED,
    "source": archive.SELECTION[SUPPLIED],
    "registered": None,
    "present": 6,
    "missing": None,
    "accepted_decisions": 5,
    "decision_unrecorded": 0,
    "no_decision": 1,
    "inbox_returns_before_decision": 4,
    "inbox_unrecorded_at_decision": 0,
    "empty_returns_before_decision": 1,
    "references_before_decision": 3,
    "citation_opportunities_measured": 3,
    "cited_at_decision": 2,
    "strata": [
        stratum("fails", "cited_at_decision", 1, 0),
        stratum("fails", "decided_without_reference", 1, 0),
        stratum("settles", "cited_at_decision", 1, 1),
        stratum("settles", "decided_without_reference", 1, 1),
        stratum("settles", "no_decision", 1, 1),
        stratum("settles", "returned_not_cited", 1, 1),
    ],
}


def saved(directory: Path, name: str, value: object) -> Path:
    path = directory / name
    path.write_bytes(json.dumps(value).encode("utf-8"))
    return path


def supplied(directory: Path) -> list[Path]:
    return [saved(directory, name, value) for name, value in CUTS.items()]


def flags(paths: list[Path]) -> list[str]:
    return [item for path in paths for item in ("--receipt-cut", str(path))]


def run_json(argv: list[str], capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    assert archive.main([*argv, "--json"]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    report: dict[str, Any] = json.loads(out)
    return report


def refused(argv: list[str], capsys: pytest.CaptureFixture[str], private: Path) -> None:
    assert archive.main([*argv, "--json"]) == 2
    out, err = capsys.readouterr()
    assert out == "" and err == REFUSED
    assert str(private) not in err


def by_name(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    names = [group["release"] for group in report["releases"]]
    assert len(set(names)) == len(names)
    return {group["release"]: group for group in report["releases"]}


def strata_total(group: dict[str, Any], observation: str) -> int:
    return sum(
        row["rows"] for row in group["strata"] if row["observation"] == observation
    )


def check_archived(group: dict[str, Any]) -> None:
    assert (
        set(group) == RELEASE_KEYS
        and group["source"] == archive.SELECTION[group["release"]]
    )
    registered, present, missing = (
        group["registered"],
        group["present"],
        group["missing"],
    )
    assert type(registered) is int and type(missing) is int and 0 < registered <= 256
    assert (
        present + missing == registered == sum(row["rows"] for row in group["strata"])
    )
    assert group["citation_opportunities_measured"] == 0
    assert group["cited_at_decision"] is None
    assert not MEASURED & {row["observation"] for row in group["strata"]}
    assert (
        strata_total(group, "returned_citation_unrecorded")
        == group["references_before_decision"]
    )
    assert strata_total(group, "no_decision") == group["no_decision"]
    assert strata_total(group, "decision_unrecorded") == group["decision_unrecorded"]
    assert strata_total(group, "missing_result") == missing
    assert (
        strata_total(group, "reference_unrecorded")
        == group["inbox_unrecorded_at_decision"]
    )
    returns = (
        group["empty_returns_before_decision"] + group["references_before_decision"]
    )
    assert group["inbox_returns_before_decision"] == returns
    for row in group["strata"]:
        assert set(row) == STRATUM_KEYS
        assert row["axis"] == ("prior" if row["world"] == "stock" else "fate")
        assert row["state"] in archive.WORLD_STATES[row["world"]]
        assert 0 <= row["known_rewards"] <= row["rows"]
        assert (row["reward_one"] is None) == (row["known_rewards"] == 0)
        if row["observation"] == "missing_result":
            assert row["known_rewards"] == 0


def test_four_archived_releases_stay_separate_with_unmeasured_citations(
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = run_json([], capsys)
    assert set(report) == {"schema", "releases", "limits"}
    assert report["schema"] == archive.SCHEMA
    assert report["limits"] == list(archive.LIMITS)
    assert [group["release"] for group in report["releases"]] == ARCHIVED
    for group in report["releases"]:
        check_archived(group)
    assert any(group["references_before_decision"] > 0 for group in report["releases"])


def test_inflight_release_distinguishes_empty_returns_from_references(
    capsys: pytest.CaptureFixture[str],
) -> None:
    inflight = by_name(run_json([], capsys))["inflight-cuts"]
    assert inflight == INFLIGHT_RELEASE
    decided_without = strata_total(inflight, "decided_without_reference")
    assert decided_without == 6 and inflight["empty_returns_before_decision"] == 4
    assert inflight == archive.release("inflight-cuts", archive_inflight.collect())


def test_supplied_cuts_report_citation_apart_from_both_fates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    groups = by_name(run_json(flags(supplied(tmp_path)), capsys))
    assert list(groups) == [*ARCHIVED, SUPPLIED]
    assert groups[SUPPLIED] == SUPPLIED_RELEASE
    cited = [
        row
        for row in groups[SUPPLIED]["strata"]
        if row["observation"] == "cited_at_decision"
    ]
    assert [(row["state"], row["reward_one"]) for row in cited] == [
        ("fails", 0),
        ("settles", 1),
    ]


def test_supplied_cuts_do_not_change_or_join_archived_releases(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    alone = run_json([], capsys)
    joined = run_json(flags(supplied(tmp_path)), capsys)
    assert joined["releases"][: len(ARCHIVED)] == alone["releases"]
    assert joined["limits"] == alone["limits"] and set(joined) == set(alone)
    group = joined["releases"][-1]
    assert group["registered"] is None and group["missing"] is None
    assert not {"world", "axis_state", "ticket", "run", "path"} & set(group)


def test_reports_carry_no_paths_names_or_identities(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = flags(supplied(tmp_path))
    raw = json.dumps(run_json(argv, capsys))
    assert archive.main(argv) == 0
    text, err = capsys.readouterr()
    assert err == ""
    for output in (raw, text):
        assert str(tmp_path) not in output and tmp_path.name not in output
        assert not any(name in output for name in CUTS)
        assert not any(run in output for run in RUNS)
        assert PLACEHOLDER_RUN not in output and "si-" not in output


def test_ordering_of_supplied_cuts_does_not_change_the_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = supplied(tmp_path)
    forward = run_json(flags(paths), capsys)
    backward = run_json(flags(paths[::-1]), capsys)
    assert forward == backward


def foreign(directory: Path) -> Path:
    earlier = {key: value for key, value in NOT_CITED.items() if key != verify.CITATION}
    return saved(
        directory, "earlier.json", {**earlier, "schema": verify.inflight.CUT_SCHEMA}
    )


def linked(directory: Path) -> Path:
    link = directory / "link.json"
    os.symlink(saved(directory, "target.json", NOT_CITED), link)
    return link


def copied(directory: Path) -> Path:
    path = directory / "copy.json"
    path.write_bytes(json.dumps(CITED_SETTLES, indent=1).encode("utf-8"))
    return path


def conflicting(directory: Path) -> Path:
    return saved(directory, "conflict.json", {**NOT_CITED, "run": RUN_A})


def garbled(directory: Path) -> Path:
    path = directory / "garbled.json"
    path.write_bytes(b'{"schema": 1, "schema": 2}')
    return path


@pytest.mark.parametrize(
    "bad",
    [
        lambda directory: directory / "gone.json",
        linked,
        foreign,
        copied,
        conflicting,
        garbled,
        lambda directory: directory,
    ],
)
def test_one_bad_cut_refuses_the_whole_report_without_partial_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], bad: Any
) -> None:
    good = saved(tmp_path, "cited-settles.json", CITED_SETTLES)
    broken = bad(tmp_path)
    refused(flags([good, broken]), capsys, tmp_path)
    refused(flags([broken, good]), capsys, tmp_path)


def test_abbreviated_option_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = saved(tmp_path, "cited-settles.json", CITED_SETTLES)
    with pytest.raises(SystemExit) as error:
        archive.main(["--receipt", str(good), "--json"])
    assert error.value.code == 2
    assert capsys.readouterr().out == ""


def test_changed_inflight_release_refuses_every_release(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    changed = tmp_path / "public-cuts.json"
    changed.write_bytes(archive_inflight.PACK.read_bytes() + b"\n")
    monkeypatch.setattr(archive_inflight, "PACK", changed)
    refused(flags(supplied(tmp_path)), capsys, tmp_path)


def test_archived_row_claiming_a_citation_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = archive_inflight.collect()
    index = next(i for i, row in enumerate(rows) if row["inbox"] == "original")
    rows[index] = {**rows[index], "citation": "at_decision"}
    monkeypatch.setattr(archive.archive_inflight, "collect", lambda: rows)
    refused([], capsys, tmp_path)


def test_missing_archived_release_refuses_the_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    releases = archive.archive_legacy.collect()
    del releases["two-worlds"]
    monkeypatch.setattr(archive.archive_legacy, "collect", lambda: releases)
    refused(flags(supplied(tmp_path)), capsys, tmp_path)
