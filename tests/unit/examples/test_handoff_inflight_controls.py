import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from examples.seat_handoffs import inflight_controls
from examples.ship_inflight.replay import PLACEHOLDER_RUN

ROOT = Path(__file__).resolve().parents[3]
SAMPLES = ROOT / "examples" / "ship_inflight" / "samples"
QUESTION = SAMPLES / "question.json"
PUBLIC = json.loads((SAMPLES / "public-cuts.json").read_text(encoding="utf-8"))
BLIND = "si-8b457c4e64fa243d"
SETTLES_BLIND = "si-c7aa9cf61cbbb83e"
WITHHELD = "si-3c710acdaa429092"
JOIN_ORIGINAL = "si-56eb464e42a3b592"
IDLE = "si-42d32f515443a2d3"
REPLACE = "si-1c1135b3a2a365be"
Json = dict[str, Any]


def pack(tmp_path: Path, edit: Any = None) -> Path:
    data = copy.deepcopy(PUBLIC)
    rows = {row["ticket"]: row for row in data["rows"]}
    if edit is not None:
        edit(data, rows)
    path = tmp_path / "pack.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def run(
    tmp_path: Path,
    boundary: str = "returned",
    edit: Any = None,
    question: Path = QUESTION,
) -> tuple[int, Json | None]:
    path = pack(tmp_path, edit)
    done = subprocess.run(
        [
            sys.executable,
            "-BS",
            "-m",
            "examples.seat_handoffs.inflight_controls",
            str(question),
            str(path),
            "--boundary",
            boundary,
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode, json.loads(done.stdout) if done.stdout else None


def record(report: Json, ticket: str) -> Json:
    return next(r for r in report["records"] if r["ticket"] == ticket)


def drop(*tickets: str) -> Any:
    def edit(data: Json, rows: dict[str, Json]) -> None:
        data["rows"] = [row for row in data["rows"] if row["ticket"] not in tickets]

    return edit


def keys(value: object) -> set[str]:
    found: set[str] = set()
    stack: list[object] = [value]
    for _ in range(100_000):
        if not stack:
            return found
        item = stack.pop()
        if isinstance(item, dict):
            found.update(key for key in item if isinstance(key, str))
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    raise AssertionError("report is unbounded")


def test_public_source_counts_and_identity(tmp_path: Path) -> None:
    code, report = run(tmp_path)
    assert code == 0 and report is not None
    totals = report["totals"]
    assert totals["registered"] == totals["present"] == totals["replayed"] == 16
    assert (totals["positive"], totals["contrast"], totals["no_commit"]) == (8, 6, 2)
    assert (
        totals["returned_before_commit"] == 8
        and totals["returned_absent_at_commit"] == 6
    )
    assert totals["returned_after_commit"] == 0 and totals["no_commit_recorded"] == 2
    assert report["complete_source"] is True and report["holes"] == []
    assert report["distinguishable"] is True and report["missing"] == []
    assert [s["members"] for s in report["shapes"]] == [16]
    assert report["shapes"][0]["acknowledgements"] is False
    assert report["source"]["label"] == "scripted controls"
    assert report["source"]["replay_basis"] == "public_frozen_cut"
    assert report["source"]["identity"] == "native_ticket"
    assert report["source"]["pack_sha256"].startswith("sha256:")
    assert [r["ticket"] for r in report["records"]] == [
        r["ticket"] for r in PUBLIC["rows"]
    ]
    assert {r["native_seats"]["outgoing"] for r in report["records"]} == {"returned"}


def test_report_withholds_runs_scores_and_events(tmp_path: Path) -> None:
    _, report = run(tmp_path)
    assert report is not None
    raw = json.dumps(report)
    assert PLACEHOLDER_RUN not in raw
    assert not keys(report) & {
        "run",
        "reward",
        "reward_valid",
        "grade_before_drain",
        "outcome",
        "events",
        "settled",
        "drained",
        "expected",
        "replace",
    }


def test_none_inbox_is_absent_not_returned(tmp_path: Path) -> None:
    _, report = run(tmp_path)
    assert report is not None
    withheld = record(report, WITHHELD)
    assert withheld["status"] == "contrast"
    assert withheld["evidence"]["sent"] == "recorded"
    assert (
        withheld["evidence"]["receipt"] == "absent"
        and withheld["evidence"]["timing"] is None
    )
    assert record(report, REPLACE)["evidence"]["timing"] == "before"


def test_no_commit_is_never_contrast(tmp_path: Path) -> None:
    _, report = run(tmp_path)
    assert report is not None
    idle = record(report, IDLE)
    assert idle["status"] == "no_commit" and idle["evidence"]["commit"] == "absent"


def test_acknowledged_is_unsupported_even_after_return(tmp_path: Path) -> None:
    code, report = run(tmp_path, "acknowledged")
    assert code == 1 and report is not None
    assert report["totals"]["ack_unsupported"] == 16
    assert report["totals"]["positive"] == report["totals"]["contrast"] == 0
    assert report["missing"] == ["positive", "contrast"]
    assert record(report, REPLACE)["evidence"]["receipt"] == "confirmed"
    assert report["complete_source"] is True


def test_return_after_commit_is_contrast(tmp_path: Path) -> None:
    def edit(data: Json, rows: dict[str, Json]) -> None:
        events = rows[JOIN_ORIGINAL]["cut"]["events"]
        events[1:] = [
            {"seq": 2, "kind": "resolve", "choice": "original"},
            {"seq": 3, "kind": "inbox", "choice": "original"},
        ]

    _, report = run(tmp_path, edit=edit)
    assert report is not None
    joined = record(report, JOIN_ORIGINAL)
    assert joined["status"] == "contrast" and joined["evidence"]["timing"] == "after"
    assert report["totals"]["returned_after_commit"] == 1


def test_swapped_fate_is_a_hole_not_a_control(tmp_path: Path) -> None:
    def edit(data: Json, rows: dict[str, Json]) -> None:
        rows[BLIND]["cut"], rows[SETTLES_BLIND]["cut"] = (
            rows[SETTLES_BLIND]["cut"],
            rows[BLIND]["cut"],
        )

    code, report = run(tmp_path, edit=edit)
    assert code == 0 and report is not None
    assert {record(report, t)["status"] for t in (BLIND, SETTLES_BLIND)} == {
        "world_mismatch"
    }
    assert record(report, BLIND)["evidence"] is None
    assert report["complete_source"] is False and len(report["holes"]) == 2
    assert report["totals"]["contrast"] == 4 and report["shapes"][0]["members"] == 14


def test_contradicting_empty_inbox_is_invalid_not_absent(tmp_path: Path) -> None:
    def edit(data: Json, rows: dict[str, Json]) -> None:
        rows[REPLACE]["cut"]["events"][1]["choice"] = "none"

    _, report = run(tmp_path, edit=edit)
    assert report is not None
    replaced = record(report, REPLACE)
    assert replaced["status"] == "cut_invalid" and replaced["evidence"] is None
    assert report["totals"]["contrast"] == 6 and report["totals"]["positive"] == 7


def test_unqualified_seats_are_holes(tmp_path: Path) -> None:
    def edit(data: Json, rows: dict[str, Json]) -> None:
        rows[REPLACE]["native"]["seats"]["relief"] = "raised"
        rows[WITHHELD]["native"]["seats"]["outgoing"] = None

    _, report = run(tmp_path, edit=edit)
    assert report is not None
    assert {record(report, t)["status"] for t in (REPLACE, WITHHELD)} == {
        "seats_unqualified"
    }
    assert report["holes"] == [
        {"ticket": t, "status": "seats_unqualified"} for t in (REPLACE, WITHHELD)
    ]


def test_missing_tickets_mark_holes(tmp_path: Path) -> None:
    code, report = run(tmp_path, edit=drop(IDLE, WITHHELD))
    assert code == 0 and report is not None
    assert report["totals"]["missing"] == 2 and report["totals"]["present"] == 14
    assert (
        record(report, IDLE)["status"] == "missing"
        and record(report, IDLE)["topology"] is None
    )
    assert report["complete_source"] is False


def test_all_missing_is_not_distinguishable(tmp_path: Path) -> None:
    code, report = run(tmp_path, edit=drop(*(r["ticket"] for r in PUBLIC["rows"])))
    assert code == 1 and report is not None
    assert report["totals"]["missing"] == 16 and len(report["holes"]) == 16
    assert report["shapes"] == [] and report["missing"] == ["positive", "contrast"]


def test_without_contrasts_the_gate_fails(tmp_path: Path) -> None:
    _, full = run(tmp_path)
    assert full is not None
    contrasts = [r["ticket"] for r in full["records"] if r["status"] == "contrast"]
    code, report = run(tmp_path, edit=drop(*contrasts))
    assert code == 1 and report is not None
    assert report["missing"] == ["contrast"] and report["totals"]["positive"] == 8


@pytest.mark.parametrize(
    "edit",
    [
        lambda data, rows: data.update(question_sha256="sha256:" + "0" * 64),
        lambda data, rows: data.update(extra=1),
        lambda data, rows: rows[BLIND]["cut"].update(run=PLACEHOLDER_RUN),
        lambda data, rows: rows[BLIND].update(ticket="si-0000000000000000"),
        lambda data, rows: data["rows"].append(copy.deepcopy(rows[BLIND])),
    ],
)
def test_malformed_pack_exits_two(tmp_path: Path, edit: Any) -> None:
    code, report = run(tmp_path, edit=edit)
    assert code == 2 and report is None


def test_swapped_question_exits_two(tmp_path: Path) -> None:
    question = json.loads(QUESTION.read_text(encoding="utf-8"))
    question["cases"][0]["fate"], question["cases"][1]["fate"] = (
        question["cases"][1]["fate"],
        question["cases"][0]["fate"],
    )
    path = tmp_path / "question.json"
    path.write_text(json.dumps(question), encoding="utf-8")
    code, report = run(tmp_path, question=path)
    assert code == 2 and report is None


def test_unparsable_pack_exits_two(tmp_path: Path) -> None:
    path = tmp_path / "pack.json"
    path.write_text("{not json", encoding="utf-8")
    assert (
        inflight_controls.main([str(QUESTION), str(path), "--boundary", "returned"])
        == 2
    )
