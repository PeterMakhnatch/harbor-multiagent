import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.decision_receipt import decision_verify as verify
from examples.ship_inflight.replay import PLACEHOLDER_RUN

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "examples" / "seatlab" / "decision_receipt" / "decision_verify.py"
INFLIGHT = ROOT / "examples" / "ship_inflight" / "inflight_verify.py"
STOCK = ROOT / "examples" / "tasks" / "ship-handover" / "tests" / "verify.py"
PUBLIC_V1 = json.loads(
    (ROOT / "examples" / "ship_inflight" / "samples" / "public-cuts.json").read_text(
        encoding="utf-8"
    )
)
V1_CUTS: dict[str, dict[str, Any]] = {
    row["ticket"]: {**row["cut"], "run": PLACEHOLDER_RUN} for row in PUBLIC_V1["rows"]
}
V1_REWARDS: dict[str, int] = {
    row["ticket"]: row["native"]["reward"] for row in PUBLIC_V1["rows"]
}
RETURNED = {
    "si-1c1135b3a2a365be": "original",
    "si-d6a136ca7e4bf8d1": "draft",
    "si-24618ead1522e201": "draft",
    "si-8d37d0777f34c365": "original",
    "si-58eb44feac2e10ee": "draft",
    "si-56eb464e42a3b592": "original",
    "si-5b7b9dbc720d7132": "original",
    "si-3c1dd43fb19073e1": "draft",
}
NO_DECISION = ("si-42d32f515443a2d3", "si-93b4d29020345eb5")
BLIND = ("si-8b457c4e64fa243d", "si-c7aa9cf61cbbb83e")
WITHHELD = (
    "si-3c710acdaa429092",
    "si-7570d87c985c30ea",
    "si-16628251379828d3",
    "si-a05885b6627797a5",
)
REPLACE_ORIGINAL = "si-1c1135b3a2a365be"
ADDED = {"schema", verify.CITATION, "citation_status"}


def fabricated_wire(ticket: str, cited: object) -> dict[str, Any]:
    return {
        **copy.deepcopy(V1_CUTS[ticket]),
        "schema": verify.CUT_SCHEMA,
        verify.CITATION: cited,
    }


def late_return(ticket: str) -> dict[str, Any]:
    cut = fabricated_wire(ticket, None)
    order = {"handoff": 0, "resolve": 1, "inbox": 2}
    events = sorted(cut["events"], key=lambda event: order[event["kind"]])
    cut["events"] = [{**event, "seq": seq} for seq, event in enumerate(events, 1)]
    return cut


def business(observation: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in observation.items() if key not in ADDED}


def staged(tmp_path: Path) -> Path:
    tests = tmp_path / "tests"
    tests.mkdir()
    for source, name in (
        (SOURCE, "decision_verify.py"),
        (INFLIGHT, "inflight_verify.py"),
        (STOCK, "verify.py"),
    ):
        shutil.copyfile(source, tests / name)
    return tests / "decision_verify.py"


def native_python(
    script: Path, *args: str, cwd: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-B", str(script), *args],
        cwd=cwd,
        env={},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_fixture_partition_matches_the_frozen_v1_cuts() -> None:
    assert PUBLIC_V1["schema"] == "harbor.ship-inflight.public-cuts/v1"
    tickets = {*RETURNED, *NO_DECISION, *BLIND, *WITHHELD}
    assert len(V1_CUTS) == len(tickets) == 16 and set(V1_CUTS) == tickets
    assert all(cut["schema"] == verify.inflight.CUT_SCHEMA for cut in V1_CUTS.values())
    assert all(verify.CITATION not in cut for cut in V1_CUTS.values())


@pytest.mark.parametrize("ticket", sorted(V1_CUTS))
def test_v1_cut_is_refused_as_unsupported(ticket: str) -> None:
    verify.inflight.evaluate(V1_CUTS[ticket])
    with pytest.raises(verify.CutError, match="unsupported"):
        verify.evaluate(V1_CUTS[ticket])
    for cited in (None, "original"):
        with pytest.raises(verify.CutError, match="unsupported"):
            verify.evaluate({**V1_CUTS[ticket], verify.CITATION: cited})


def test_stock_handover_schema_is_refused_as_unsupported() -> None:
    cut = {**fabricated_wire(BLIND[0], None), "schema": verify.stock.CUT_SCHEMA}
    with pytest.raises(verify.CutError, match="unsupported"):
        verify.evaluate(cut)


@pytest.mark.parametrize("ticket", sorted(V1_CUTS))
def test_uncited_status_and_business_grade_match_pinned_inflight(ticket: str) -> None:
    observation = verify.evaluate(fabricated_wire(ticket, None))
    pinned = verify.inflight.evaluate(V1_CUTS[ticket])
    assert observation["schema"] == verify.OBSERVATION_SCHEMA
    assert observation.keys() == {*pinned, verify.CITATION, "citation_status"}
    assert business(observation) == business(pinned)
    assert observation["reward"] == V1_REWARDS[ticket]
    assert observation[verify.CITATION] is None
    expected = (
        "returned_not_cited"
        if ticket in RETURNED
        else "no_decision"
        if ticket in NO_DECISION
        else "decided_without_returned_reference"
    )
    assert observation["citation_status"] == expected


def test_uncited_status_counts_over_frozen_v1_data() -> None:
    statuses = [
        verify.evaluate(fabricated_wire(ticket, None))["citation_status"]
        for ticket in V1_CUTS
    ]
    assert {status: statuses.count(status) for status in verify.STATUSES} == {
        "no_decision": 2,
        "decided_without_returned_reference": 6,
        "returned_not_cited": 8,
        "cited_at_decision": 0,
    }


@pytest.mark.parametrize(("ticket", "symbol"), sorted(RETURNED.items()))
def test_citation_alternatives_share_one_business_grade(
    ticket: str, symbol: str
) -> None:
    uncited = verify.evaluate(fabricated_wire(ticket, None))
    cited = verify.evaluate(fabricated_wire(ticket, symbol))
    assert cited["citation_status"] == "cited_at_decision"
    assert cited[verify.CITATION] == symbol
    assert business(cited) == business(uncited)
    assert cited["reward"] == uncited["reward"] == V1_REWARDS[ticket]


@pytest.mark.parametrize("ticket", NO_DECISION)
@pytest.mark.parametrize("cited", verify.CITABLE)
def test_citation_without_decision_is_rejected(ticket: str, cited: str) -> None:
    with pytest.raises(verify.CutError, match="lacks a resolve"):
        verify.evaluate(fabricated_wire(ticket, cited))


@pytest.mark.parametrize("ticket", (*BLIND, *WITHHELD))
@pytest.mark.parametrize("cited", verify.CITABLE)
def test_citation_never_returned_is_rejected(ticket: str, cited: str) -> None:
    with pytest.raises(verify.CutError, match="returned before resolve"):
        verify.evaluate(fabricated_wire(ticket, cited))


@pytest.mark.parametrize("ticket", WITHHELD)
def test_accepted_handoff_without_returned_inbox_is_not_a_reference(
    ticket: str,
) -> None:
    handoff = V1_CUTS[ticket]["events"][0]
    assert handoff["kind"] == "handoff" and handoff["choice"] == "original"
    observation = verify.evaluate(fabricated_wire(ticket, None))
    assert observation["citation_status"] == "decided_without_returned_reference"
    with pytest.raises(verify.CutError):
        verify.evaluate(fabricated_wire(ticket, "original"))


@pytest.mark.parametrize(("ticket", "symbol"), sorted(RETURNED.items()))
def test_mismatched_citation_is_rejected(ticket: str, symbol: str) -> None:
    other = next(item for item in verify.CITABLE if item != symbol)
    with pytest.raises(verify.CutError, match="differs"):
        verify.evaluate(fabricated_wire(ticket, other))


def test_reference_returned_after_decision_cannot_be_cited() -> None:
    late = late_return(REPLACE_ORIGINAL)
    assert [event["kind"] for event in late["events"]] == [
        "handoff",
        "resolve",
        "inbox",
    ]
    observation = verify.evaluate(late)
    assert observation["citation_status"] == "decided_without_returned_reference"
    assert business(observation)["reward"] == V1_REWARDS[REPLACE_ORIGINAL]
    with pytest.raises(verify.CutError, match="returned before resolve"):
        verify.evaluate({**late, verify.CITATION: "original"})


@pytest.mark.parametrize(
    "cited",
    ("fresh", "none", "Original", "", True, 0, ["original"], {"ref": "original"}),
)
def test_non_symbolic_citation_is_rejected(cited: object) -> None:
    ticket, _ = next(iter(RETURNED.items()))
    with pytest.raises(verify.CutError, match="cited_at_decision"):
        verify.evaluate(fabricated_wire(ticket, cited))


def test_omitted_or_extra_citation_field_is_malformed_not_negative() -> None:
    omitted = fabricated_wire(BLIND[0], None)
    del omitted[verify.CITATION]
    with pytest.raises(verify.CutError, match="fields"):
        verify.evaluate(omitted)
    with pytest.raises(verify.CutError, match="fields"):
        verify.evaluate({**fabricated_wire(BLIND[0], None), "acknowledged": None})


@pytest.mark.parametrize(
    "schema",
    (verify.OBSERVATION_SCHEMA, verify.inflight.OBSERVATION_SCHEMA, "", None),
)
def test_other_schemas_are_rejected(schema: object) -> None:
    with pytest.raises(verify.CutError, match="schema"):
        verify.evaluate({**fabricated_wire(BLIND[0], None), "schema": schema})


def test_pinned_ledger_replay_still_applies() -> None:
    ticket = next(iter(RETURNED))
    cut = fabricated_wire(ticket, RETURNED[ticket])
    cut["drained"] = [*cut["drained"], "original"]
    with pytest.raises(verify.CutError, match="drained"):
        verify.evaluate(cut)


def test_staged_offline_mode_regrades_under_isolated_python(tmp_path: Path) -> None:
    script = staged(tmp_path)
    ticket, symbol = next(iter(RETURNED.items()))
    cut = tmp_path / "cut.json"
    cut.write_text(json.dumps(fabricated_wire(ticket, symbol)), encoding="utf-8")
    assert 0 < cut.stat().st_size <= verify.stock.MAX_CUT_BYTES
    done = native_python(script, str(cut), cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == verify.evaluate(fabricated_wire(ticket, symbol))


def test_staged_offline_mode_refuses_v1_and_duplicate_keys(tmp_path: Path) -> None:
    script = staged(tmp_path)
    old = tmp_path / "v1.json"
    old.write_text(json.dumps(V1_CUTS[BLIND[0]]), encoding="utf-8")
    done = native_python(script, str(old), cwd=tmp_path)
    assert done.returncode == 1 and done.stdout == "" and "unsupported" in done.stderr
    doubled = tmp_path / "doubled.json"
    wire = json.dumps(fabricated_wire(BLIND[0], None))
    doubled.write_text(
        wire[:-1] + ', "cited_at_decision": "original"}', encoding="utf-8"
    )
    done = native_python(script, str(doubled), cwd=tmp_path)
    assert done.returncode == 1 and done.stdout == "" and "no grade" in done.stderr


def test_staged_native_mode_needs_the_expected_world(tmp_path: Path) -> None:
    script = staged(tmp_path)
    done = native_python(script, cwd=tmp_path)
    assert done.returncode == 1 and done.stdout == ""
    assert "expected world is missing" in done.stderr
    done = native_python(script, "a", "b", cwd=tmp_path)
    assert done.returncode == 2 and "usage" in done.stderr
