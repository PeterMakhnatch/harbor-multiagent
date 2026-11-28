import json
import re
from dataclasses import replace
from typing import Any

import pytest

from examples.seatlab.decision_boundary import revised_temporal as rt

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@pytest.fixture(scope="module")
def runs() -> dict[tuple[str, str, str], rt.Launch]:
    return rt.launches(7)


def _inbox(*reports: tuple[str, str, str]) -> rt.Entry:
    reply = {"reports": [{"phase": p, "slot": s, "receipt": r} for p, s, r in reports]}
    return ("booker", "GET", "/inbox", None, 200, reply)


def _replies(entries: tuple[rt.Entry, ...]) -> list[dict[str, Any]]:
    return [entry[5] for entry in entries if entry[:3] == rt.INBOX]


def test_seed_7_shows_the_timing_only_pattern(runs) -> None:
    result = rt.report(7, runs)
    temporal = result["temporal"]
    assert result["timing_only_pattern"] is True
    assert result["native_grades"] == 12
    assert {arm: temporal["arms"][arm]["observed"] for arm in rt.temporal.ARMS} == {
        "early_commit": "1",
        "early_close": "1",
        "late_commit": "1/2",
        "late_close": "1",
    }
    assert temporal["commit_relation"] == "refinement"
    assert temporal["close_relation"] == "equivalent"
    assert temporal["late_hindsight_relation"] == "refinement"


def test_twelve_native_grades_reward_exactly_the_final_slot(runs) -> None:
    assert sorted(runs) == sorted(
        (v, t, s) for v in rt.VARIANTS for t in rt.TIMINGS for s in rt.SLOTS
    )
    for variant in rt.VARIANTS:
        early = {s: runs[(variant, "early", s)].reward for s in rt.SLOTS}
        late = {s: runs[(variant, "late", s)].reward for s in rt.SLOTS}
        assert early == late and sum(early.values()) == 1


def test_schedules_place_the_revision_around_the_commit(runs) -> None:
    for (_, timing, _), run in runs.items():
        decided = run.entries[: run.decision]
        sent = [e[3]["phase"] for e in decided if e[1:3] == ("POST", "/report")]
        assert sent == (["initial", "revision"] if timing == "early" else ["initial"])
        assert run.entries[run.decision][1:3] == ("POST", "/commit")
        assert run.entries[-1][:3] == rt.INBOX


def test_aliases_keep_order_equality_and_multiplicity(runs) -> None:
    early = rt.normalize(runs[("replace", "early", "A")].entries)
    receipts = [[r["receipt"] for r in reply["reports"]] for reply in _replies(early)]
    assert receipts == [
        ["receipt-1"],
        ["receipt-1", "receipt-2"],
        ["receipt-1", "receipt-2"],
    ]
    late = rt.normalize(runs[("replace", "late", "A")].entries)
    receipts = [[r["receipt"] for r in reply["reports"]] for reply in _replies(late)]
    assert receipts == [["receipt-1"], ["receipt-1"], ["receipt-1", "receipt-2"]]
    raw = runs[("replace", "early", "A")].entries
    for before, after in zip(raw, early, strict=True):
        if before[:3] != rt.INBOX:
            assert before == after
        else:
            strip = [
                {k: v for k, v in r.items() if k != "receipt"}
                for r in before[5]["reports"]
            ]
            assert strip == [
                {k: v for k, v in r.items() if k != "receipt"}
                for r in after[5]["reports"]
            ]


def test_receipt_reuse_across_phases_is_refused() -> None:
    with pytest.raises(ValueError, match="reused across phases"):
        rt.normalize((_inbox(("initial", "A", "x1"), ("revision", "B", "x1")),))


def test_receipt_change_between_reads_is_refused() -> None:
    with pytest.raises(ValueError, match="changed between reads"):
        rt.normalize((_inbox(("initial", "A", "x1")), _inbox(("initial", "A", "x2"))))


def test_receipt_outside_the_inbox_receipt_field_is_refused() -> None:
    echo = (
        "booker",
        "POST",
        "/ack",
        {"phase": "initial", "receipt": "x1"},
        200,
        {"ok": True},
    )
    with pytest.raises(ValueError, match="outside"):
        rt.normalize((_inbox(("initial", "A", "x1")), echo))
    with pytest.raises(ValueError, match="inbox report fields"):
        rt.normalize(
            (
                (
                    "booker",
                    "GET",
                    "/inbox",
                    None,
                    200,
                    {"reports": [{"phase": "initial"}]},
                ),
            )
        )


def test_inconsistent_decision_prefix_is_refused(runs) -> None:
    key = ("confirm", "early", "B")
    run = runs[key]
    entries = list(run.entries)
    role, method, path, body, status, reply = entries[0]
    entries[0] = (role, method, path, body, status, {**reply, "phase": "altered"})
    altered = {**runs, key: replace(run, entries=tuple(entries))}
    with pytest.raises(ValueError, match="decision prefix"):
        rt.report(7, altered)
    shifted = {**runs, key: replace(run, decision=run.decision - 1)}
    with pytest.raises(ValueError, match="decision prefix"):
        rt.report(7, shifted)


def test_no_secret_slot_or_one_off_identifier_is_disclosed(runs) -> None:
    text = json.dumps(rt.report(7, runs))
    instruction = (rt.TASK / "instructions" / "booker.md").read_bytes()
    assert UUID.search(text) is None
    assert not any(f'"{slot}"' in text for slot in rt.SLOTS)
    for run in runs.values():
        normalized = rt.normalize(run.entries)
        seen = rt.observation(instruction, normalized, len(normalized))
        assert len(run.sensitive) == 5
        for secret in run.sensitive:
            assert secret not in text
            assert secret.encode("ascii") not in seen
        assert b"reporter" not in seen.split(instruction, 1)[1]


def test_cli_prints_the_report(capsys) -> None:
    assert rt.main(["--seed", "7"]) == 0
    assert json.loads(capsys.readouterr().out)["timing_only_pattern"] is True
    assert rt.main(["--seed", str(rt.MAX_SEED + 1)]) == 2
