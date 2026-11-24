import json
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.workbench import generation_details, generation_receipts
from examples.seatlab.workbench.generation_receipts import (
    MAX_LINE_BYTES,
    MAX_ROWS,
    owner,
)
from examples.seatlab.workbench.native_account import build
from examples.seatlab.workbench.seat_stream import Malformed
from examples.seatlab.workbench.team_account import main
from tests.unit.cli.test_team_account_workbench import (
    FINAL_A,
    FINAL_B,
    JOB,
    export,
    finals,
    write_job,
    write_lines,
)

Json = dict[str, Any]
SCHEMA = "seatlab.openrouter-generation-details/v1"
RETRY = "a9c8b7d6-e5f4-4a3b-8c2d-1e0f9a8b7c6d"
SECRET = "sk-or-v1-" + "7c" * 32
ORIGIN = "https://private.example/run-4c1d"
UPSTREAM = "chatcmpl-upstream-9d1e"
GEN_TEAM = "gen-team-5f2a"
GEN_ZERO = "gen-zero-5f2a"
GEN_LOOSE = "gen-loose-5f2a"
GEN_RETRY = "gen-retry-5f2a"
GEN_STRAY = "gen-stray-5f2a"
GEN_WAIT = "gen-wait-5f2a"
GEN_LATE = "gen-late-5f2a"
TOTALS = {
    GEN_TEAM: 0.0015,
    GEN_ZERO: 0,
    GEN_LOOSE: 0.002,
    GEN_RETRY: 1e-09,
    GEN_STRAY: 0.25,
}
SHARED = (
    "basis",
    "currency",
    "packets",
    "owners",
    "generations",
    "known_usd",
    "seats",
    "team",
    "owner_records_without_settled_generation",
)
PRIVATE_MODEL = "private-test-org/receipt-model"
PRIVATE = (
    SECRET,
    ORIGIN,
    UPSTREAM,
    PRIVATE_MODEL,
    "ReceiptVendor",
    *TOTALS,
    GEN_WAIT,
    GEN_LATE,
)


def detail(generation: object, total: object, **extra: object) -> Json:
    return {"data": {"id": generation, "total_cost": total, **extra}}


def ledger_rows(totals: dict[str, float]) -> list[Json]:
    rows: list[Json] = []
    for index, (generation, total) in enumerate(totals.items()):
        call = f"c{index}"
        nano = (Decimal(repr(total)) * 10**9).to_integral_value(ROUND_CEILING)
        rows.append(
            {
                "event": "started",
                "at": "2026-11-20T15:00:00Z",
                "call_id": call,
                "identity": "author",
                "model": "openai/gpt-5-mini",
                "request_sha256": "0" * 64,
                "reserved_nano": 2_000_000,
                "input_token_bound": 4096,
                "output_token_bound": 512,
            }
        )
        rows.append(
            {
                "event": "settled",
                "at": "2026-11-20T15:00:02Z",
                "call_id": call,
                "cost_nano": int(nano),
                "cost_basis": "openrouter_generation.total_cost",
                "receipt": {"id": generation, "total_cost": total},
            }
        )
    return rows


def documents() -> list[Json]:
    rows = [detail(generation, total) for generation, total in TOTALS.items()]
    rows[0]["data"].update(model=PRIVATE_MODEL, user=SECRET)
    repeat = detail(
        GEN_TEAM,
        TOTALS[GEN_TEAM],
        model=PRIVATE_MODEL,
        provider_name="ReceiptVendor",
        origin=ORIGIN,
        upstream_id=UPSTREAM,
        usage=0.9,
        upstream_inference_cost=0.9,
        user=SECRET + "-other",
    )
    return [*rows, repeat, detail(GEN_ZERO, 0.0)]


def claims() -> list[Json]:
    team = owner(FINAL_B, "author", GEN_TEAM)
    return [
        team,
        team,
        owner(FINAL_A, "author", GEN_ZERO),
        owner(RETRY, "author", GEN_RETRY),
        owner(FINAL_A, "planner", GEN_STRAY),
        owner(FINAL_A, "critic", GEN_WAIT),
        owner(RETRY, "critic", GEN_LATE),
    ]


def write_bytes(path: Path, data: bytes) -> str:
    path.write_bytes(data)
    return str(path)


def outcome(capsys: pytest.CaptureFixture[str], *argv: object) -> tuple[Any, str]:
    try:
        code = main([str(arg) for arg in argv])
    except SystemExit as stop:
        code = stop.code
    return code, capsys.readouterr().out


@pytest.fixture
def job(tmp_path: Path) -> Path:
    return write_job(tmp_path / "job", JOB, finals(JOB))


@pytest.fixture
def packets(job: Path) -> list[Json]:
    return [build(str(job))]


def test_saved_details_split_money_exactly_like_the_ledger_projection(
    packets, tmp_path
):
    owners = str(write_lines(tmp_path / "owners.jsonl", claims()))
    details = str(write_lines(tmp_path / "details.jsonl", documents()))
    ledger = str(write_lines(tmp_path / "events.jsonl", ledger_rows(TOTALS)))
    report = generation_details.reconcile(packets, details, owners)
    projection = generation_receipts.reconcile(packets, ledger, owners)
    assert report["schema"] == SCHEMA and "ledger" not in report
    assert {key: report[key] for key in SHARED} == {
        key: projection[key] for key in SHARED
    }
    assert report["documents"] == {
        "rows": 7,
        "distinct_generations": 5,
        "repeated_rows": 2,
    }
    assert report["known_usd"] == {
        "settled": "0.253500001",
        "assigned_to_team": "0.0015",
        "not_assigned": "0.252000001",
    }
    assert {kind: row["count"] for kind, row in report["generations"].items()} == {
        "matched": 2,
        "foreign_trial": 1,
        "off_roster_seat": 1,
        "unidentified": 1,
    }
    assert report["generations"]["foreign_trial"]["usd"] == "0.000000001"
    assert {
        (row["trial"], row["seat"]): (row["generations"], row["usd"])
        for row in report["team"]
    } == {(FINAL_A, "author"): (1, "0"), (FINAL_B, "author"): (1, "0.0015")}
    assert report["owner_records_without_settled_generation"] == {
        "packet_seat": 1,
        "outside_packets": 1,
    }
    serialized = json.dumps(report)
    assert not [text for text in (*PRIVATE, details, owners) if text in serialized]


def test_empty_returns_are_no_evidence_and_owner_claims_stay_pending(packets, tmp_path):
    owners = str(write_lines(tmp_path / "owners.jsonl", claims()))
    details = write_bytes(tmp_path / "details.jsonl", b"")
    ledger = write_bytes(tmp_path / "events.jsonl", b"")
    report = generation_details.reconcile(packets, details, owners)
    projection = generation_receipts.reconcile(packets, ledger, owners)
    assert {key: report[key] for key in SHARED} == {
        key: projection[key] for key in SHARED
    }
    assert "ledger" not in report and report["team"] == []
    assert report["documents"] == {
        "rows": 0,
        "distinct_generations": 0,
        "repeated_rows": 0,
    }
    assert report["known_usd"] == {
        "settled": "0",
        "assigned_to_team": "0",
        "not_assigned": "0",
    }
    assert report["seats"]["owner_record_without_settled_generation"] == 3


def test_conflicting_amount_for_one_generation_refuses_without_echo(packets, tmp_path):
    rows = [detail(GEN_TEAM, 0.0015), detail(GEN_TEAM, 0.0016)]
    details = str(write_lines(tmp_path / "details.jsonl", rows))
    with pytest.raises(Malformed) as refused:
        generation_details.reconcile(packets, details)
    assert GEN_TEAM not in str(refused.value) and details not in str(refused.value)


def test_one_generation_claimed_by_two_seats_refuses(packets, tmp_path):
    details = str(write_lines(tmp_path / "details.jsonl", [detail(GEN_TEAM, 0.0015)]))
    conflict = [owner(FINAL_B, "author", GEN_TEAM), owner(FINAL_A, "critic", GEN_TEAM)]
    owners = str(write_lines(tmp_path / "owners.jsonl", conflict))
    with pytest.raises(Malformed):
        generation_details.reconcile(packets, details, owners)


def line(row: object) -> bytes:
    return json.dumps(row).encode() + b"\n"


VALID = line(detail(GEN_TEAM, 0.0015))
CHAT = {
    "id": UPSTREAM,
    "object": "chat.completion",
    "model": "openai/gpt-5-mini",
    "choices": [{"message": {"role": "assistant", "content": SECRET}}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "cost": 0.0015},
}


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(line(CHAT), id="chat-completion"),
        pytest.param(line({"data": CHAT}), id="chat-inside-data"),
        pytest.param(line({"usage": {"cost": 0.0015}}), id="usage-only"),
        pytest.param(line({"data": {"id": GEN_TEAM, "usage": 0.0015}}), id="no-total"),
        pytest.param(
            line(detail(UPSTREAM, 0.0015, upstream_id=UPSTREAM)), id="chatcmpl-id"
        ),
        pytest.param(line({"data": {"id": GEN_TEAM, "cost": 0.0015}}), id="guess"),
        pytest.param(line({"data": {"total_cost": 0.0015}}), id="no-id"),
        pytest.param(line({**detail(GEN_TEAM, 0.0015), "user": SECRET}), id="extra"),
        pytest.param(line({"data": [GEN_TEAM, 0.0015]}), id="data-array"),
        pytest.param(line([detail(GEN_TEAM, 0.0015)]), id="array-row"),
        pytest.param(line(detail(GEN_TEAM, "0.0015")), id="string-total"),
        pytest.param(line(detail(GEN_TEAM, True)), id="boolean-total"),
        pytest.param(line(detail(GEN_TEAM, None)), id="null-total"),
        pytest.param(line(detail(GEN_TEAM, -0.0015)), id="negative"),
        pytest.param(b'{"data":{"id":"gen-a","total_cost":NaN}}\n', id="nan"),
        pytest.param(b'{"data":{"id":"gen-a","total_cost":Infinity}}\n', id="inf"),
        pytest.param(b'{"data":{"id":"gen-a","total_cost":-Infinity}}\n', id="-inf"),
        pytest.param(b'{"data":{"id":"gen-a","total_cost":1e999}}\n', id="huge"),
        pytest.param(line(detail(GEN_TEAM, 1000000000.5)), id="over-cap"),
        pytest.param(
            b'{"data":{"id":"gen-a","total_cost":0.' + b"0" * 40 + b"1}}\n",
            id="long-number",
        ),
        pytest.param(
            b'{"data":{"id":"gen-a","id":"gen-b","total_cost":0.1}}\n', id="dup-key"
        ),
        pytest.param(b'{"data":{"id":"gen-a","total_cost":0.1},"data":{}}\n', id="dup"),
        pytest.param(line(detail("gen 1", 0.0015)), id="spaced-id"),
        pytest.param(line(detail("", 0.0015)), id="empty-id"),
        pytest.param(line(detail("gen-", 0.0015)), id="bare-prefix"),
        pytest.param(line(detail("-gen", 0.0015)), id="punct-id"),
        pytest.param(line(detail("gen_a", 0.0015)), id="underscore-id"),
        pytest.param(line(detail("gen-a:b", 0.0015)), id="colon-id"),
        pytest.param(line(detail("req-5f2a", 0.0015)), id="request-id"),
        pytest.param(line(detail("gen-" + "a" * 125, 0.0015)), id="long-id"),
        pytest.param(line(detail(7, 0.0015)), id="numeric-id"),
        pytest.param(
            '{"data":{"id":"gen-é","total_cost":0.1}}\n'.encode(), id="non-ascii-id"
        ),
        pytest.param(b"id,total_cost\ngen-a,0.0015\n", id="csv"),
        pytest.param(b"\xff\xfe\n", id="not-utf8"),
        pytest.param(VALID.replace(b"\n", b"\r\n"), id="carriage-return"),
        pytest.param(VALID + b"\n" + VALID, id="blank-row"),
        pytest.param(
            line(detail(GEN_TEAM, 0.0015, note="x" * MAX_LINE_BYTES)), id="long-row"
        ),
    ],
)
def test_unsupported_documents_refuse(packets, tmp_path, data):
    details = write_bytes(tmp_path / "details.jsonl", data)
    with pytest.raises(Malformed):
        generation_details.reconcile(packets, details)


def test_row_count_bound_refuses_even_idempotent_repeats(packets, tmp_path):
    details = write_bytes(tmp_path / "details.jsonl", VALID * MAX_ROWS)
    assert generation_details.reconcile(packets, details)["documents"] == {
        "rows": MAX_ROWS,
        "distinct_generations": 1,
        "repeated_rows": MAX_ROWS - 1,
    }
    write_bytes(tmp_path / "details.jsonl", VALID * (MAX_ROWS + 1))
    with pytest.raises(Malformed):
        generation_details.reconcile(packets, details)


def test_merge_reports_saved_details_as_provider_receipts(job, tmp_path, capsys):
    packet = export(capsys, job)
    packet_path = write_lines(tmp_path / "packet.json", [packet])
    owners = str(write_lines(tmp_path / "owners.jsonl", claims()))
    details = str(write_lines(tmp_path / "details.jsonl", documents()))
    code, out = outcome(
        capsys,
        "merge",
        packet_path,
        "--generation-details",
        details,
        "--owners",
        owners,
        "--json",
    )
    assert code == 0
    receipts = json.loads(out)["provider_receipts"]
    assert receipts == generation_details.reconcile([packet], details, owners)
    assert receipts["schema"] == SCHEMA
    assert not [text for text in (*PRIVATE, details, owners) if text in out]


def test_cli_refuses_mixed_settlement_sources_and_packet_export_with_details(
    job, tmp_path, capsys
):
    packet_path = write_lines(tmp_path / "packet.json", [export(capsys, job)])
    details = write_lines(tmp_path / "details.jsonl", [detail(GEN_TEAM, 0.0015)])
    ledger = write_lines(tmp_path / "events.jsonl", ledger_rows({GEN_TEAM: 0.0015}))
    mixed = ("--ledger", ledger, "--generation-details", details, "--json")
    assert outcome(capsys, "merge", packet_path, *mixed) == (2, "")
    exporting = ("--packet", "--generation-details", details)
    assert outcome(capsys, "native", job, *exporting) == (2, "")
