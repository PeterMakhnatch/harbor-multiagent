import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from examples.seatlab.committee import ballot_packet, contract, inspect_report, packing
from examples.seatlab.visibility import deepseek, spend

pytest.importorskip("inspect_ai")

from inspect_ai.event import ModelEvent  # noqa: E402
from inspect_ai.log import (  # noqa: E402
    EvalConfig,
    EvalDataset,
    EvalLog,
    EvalSample,
    EvalSpec,
    write_eval_log,
)
from inspect_ai.model import (  # noqa: E402
    ChatCompletionChoice,
    ChatMessage,
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageUser,
    GenerateConfig,
    ModelOutput,
    ModelUsage,
)
from inspect_ai.scorer import Score  # noqa: E402


@pytest.fixture(scope="module")
def packet():
    return ballot_packet.read_packet(packing.PACKET, packing.PACKET_SHA256)


def _answer(
    packet, cell, *, exact=True, correct=True, order=("totals", "choice", "cited")
):
    dossier, chat = packing._evidence(packet, cell)
    view = contract._vote_view(dossier, chat, cell.map_index)
    facts = view.facts | dict(view.submissions)
    totals = {label: 0 for label in view.options.values()}
    for fact in facts.values():
        totals[view.options[fact.option]] += fact.points
    if not exact:
        totals[view.options["Option_A"]] += 1
    values = {
        "totals": totals,
        "choice": view.options["Option_A" if correct else "Option_B"],
        "cited": [view.aliases[next(iter(facts))]],
    }
    return json.dumps({field: values[field] for field in order})


def _sample(
    packet,
    cell,
    *,
    text=None,
    output_model="external-served",
    event_model="external/model",
):
    if text is None:
        text = _answer(packet, cell)
    inputs: list[ChatMessage] = [
        ChatMessageSystem(content="Answer only with JSON."),
        ChatMessageUser(content=packing.prompt(packet, cell)),
    ]
    assistant = ChatMessageAssistant(content=text, source="generate")
    output = ModelOutput(
        model=output_model,
        choices=[ChatCompletionChoice(message=assistant, stop_reason="stop")],
        usage=ModelUsage(
            input_tokens=100,
            input_tokens_cache_read=20,
            output_tokens=30,
            total_tokens=130,
        ),
    )
    result = packing.score(packet, cell, text)
    score = {True: "C", False: "I", None: "N"}[result["exact_totals"]]
    return EvalSample(
        id=cell.cell_id,
        epoch=1,
        input=inputs,
        target="",
        messages=[*inputs, assistant],
        metadata={
            "block": cell.block,
            "map_index": cell.map_index,
            "seat_index": cell.seat_index,
            "arm": cell.arm,
            "packet_sha256": packing.PACKET_SHA256,
        },
        output=output,
        scores={"seatlab_facts_score": Score(value=score, metadata=dict(result))},
        events=[
            ModelEvent(
                model=event_model,
                input=inputs,
                tools=[],
                tool_choice="none",
                config=GenerateConfig(),
                output=output.model_copy(deep=True),
            )
        ],
    )


def _log(samples, *, model="external/model", task="seatlab_facts"):
    return EvalLog(
        status="success",
        eval=EvalSpec(
            created="2026-01-01T00:00:00Z",
            task=task,
            model=model,
            dataset=EvalDataset(samples=96),
            config=EvalConfig(epochs=1),
        ),
        samples=samples,
    )


def _read(path, **kwargs):
    return inspect_report.analyze(
        packing.PACKET, path, expected_sha256=packing.PACKET_SHA256, **kwargs
    )


def _cells(report):
    return {cell["cell_id"]: cell for cell in report["cells"]}


def test_saved_eval_weights_maps_and_excludes_unpaired_groups(packet, tmp_path):
    samples = []
    for index in range(6):
        for arm in ("separate", "indexed"):
            for seat in range(4 if index == 0 else 1):
                cell = packing.Cell(0, index, seat, arm)
                text = _answer(
                    packet,
                    cell,
                    exact=index == 0 or arm == "indexed",
                    correct=seat < (3 if arm == "indexed" else 2),
                )
                samples.append(_sample(packet, cell, text=text))
    extra = packing.Cell(1, 0, 0, "separate")
    invalid = packing.Cell(1, 2, 0, "indexed")
    order = ("choice", "cited", "totals")
    samples.extend(
        [
            _sample(packet, extra, text=_answer(packet, extra, order=order)),
            _sample(packet, invalid, text="private free text"),
        ]
    )
    log_path = tmp_path / "private-location.eval"
    write_eval_log(_log(samples), log_path)
    report = _read(log_path)

    assert report["model_source"] == "inspect_external"
    assert report["spend"]["status"] == "not_requested"
    assert report["summary"]["cell_status"] == {
        "accepted": 19,
        "invalid_response": 1,
        "missing_sample": 76,
    }
    contrast = report["indexed_minus_separate"]
    assert contrast["measures"]["exact_totals"] == {
        "indexed_numerator": 9,
        "separate_numerator": 4,
        "paired_denominator": 9,
        "supported_maps": 6,
        "effect": pytest.approx(5 / 6),
    }
    assert contrast["measures"]["correct_choice"]["effect"] == pytest.approx(1 / 24)
    assert contrast["by_block"][1]["measures"]["exact_totals"]["effect"] is None
    assert contrast["measures"]["strict_majority"]["paired_denominator"] == 1
    assert contrast["measures"]["strict_majority"]["effect"] is None
    groups = {group["arm"]: group for group in report["groups"] if group["matched"]}
    assert set(groups) == {"separate", "indexed"}
    assert not groups["separate"]["strict_majority"]
    assert groups["indexed"]["strict_majority"]
    assert groups["indexed"]["correct_majority"]
    assert _cells(report)[extra.cell_id]["field_order"] == list(order)
    assert not _cells(report)[extra.cell_id]["result"]["order_followed"]
    assert _cells(report)[invalid.cell_id]["result"]["exact_totals"] is None
    serialized = json.dumps(report)
    assert "private-location" not in serialized
    assert "private free text" not in serialized
    assert "analyst_" not in serialized
    assert "external-served" not in serialized


def test_rejects_missing_duplicate_and_altered_sample_evidence(packet):
    cells = packing.grid()[:10]
    samples = [_sample(packet, cell) for cell in cells]
    samples[0].output = ModelOutput()
    samples[1].epoch = 2
    samples[2].input = [ChatMessageUser(content="wrong input")]
    samples[3].metadata["packet_sha256"] = "0" * 64
    samples[4].scores["seatlab_facts_score"].metadata["exact_totals"] = 1
    samples[5].events[0].output.completion = "altered model event"
    samples[6].events.append(samples[6].events[0].model_copy())
    samples[7].events[0].tool_choice = "auto"
    samples[8].messages = [
        *samples[8].messages,
        ChatMessageUser(content="unexpected turn"),
    ]
    samples.extend(
        [
            samples[9].model_copy(deep=True),
            samples[9].model_copy(update={"id": "outside-private-grid"}),
        ]
    )
    report = inspect_report._analyze_log(packet, _log(samples), None, None)
    rows = _cells(report)
    assert (
        report["log"]["duplicate_samples"] == report["log"]["unregistered_samples"] == 1
    )
    assert [rows[cell.cell_id]["reason"] for cell in cells] == [
        "missing_final_output",
        "unexpected_epoch",
        "sample_input_mismatch",
        "sample_metadata_mismatch",
        "scorer_mismatch",
        "model_output_mismatch",
        "multiple_generation_attempts",
        "tool_activity",
        "conversation_mismatch",
        "duplicate_sample_id",
    ]
    assert all(rows[cell.cell_id]["result"] is None for cell in cells)
    assert (
        report["indexed_minus_separate"]["measures"]["exact_totals"][
            "paired_denominator"
        ]
        == 0
    )
    assert rows[packing.grid()[-1].cell_id]["status"] == "missing_sample"
    assert "outside-private-grid" not in json.dumps(report)


def test_excludes_replays_but_not_identical_text_or_missing_optional_events(packet):
    a = packing.Cell(0, 0, 0, "separate")
    b = packing.Cell(1, 0, 0, "separate")
    c = packing.Cell(0, 0, 0, "indexed")
    d = packing.Cell(1, 0, 0, "indexed")
    samples = [_sample(packet, cell) for cell in (a, b, c, d)]
    samples[0].events[0].cache = "read"
    samples[1].events = []
    samples[2].events[0].uuid = samples[3].events[0].uuid = "common-event"
    report = inspect_report._analyze_log(packet, _log(samples), None, None)
    rows = _cells(report)
    assert rows[a.cell_id]["status"] == "replayed"
    assert rows[b.cell_id]["status"] == "accepted"
    assert rows[b.cell_id]["generation"] == "conversation_only"
    assert rows[c.cell_id]["status"] == rows[d.cell_id]["status"] == "replayed"
    assert "common-event" not in json.dumps(report)


def test_order_uses_times_not_file_enumeration(packet):
    first, second = [_sample(packet, cell) for cell in packing.grid()[:2]]

    def order():
        report = inspect_report._analyze_log(packet, _log([second, first]), None, None)
        return report["log"]["execution_order"]

    assert order()["start_order"] == "not_assessed"
    first.started_at = "2026-01-01T00:00:01Z"
    first.completed_at = "2026-01-01T00:00:02Z"
    second.started_at = "2026-01-01T00:00:03Z"
    second.completed_at = "2026-01-01T00:00:05Z"
    assert order() == {
        "basis": "sample_started_at",
        "start_order": "matches",
        "observed_samples": 2,
        "nonoverlap": True,
    }
    second.started_at = "2026-01-01T00:00:00Z"
    assert order()["start_order"] == "differs"
    assert order()["nonoverlap"] is False


def test_absent_unsupported_and_sampleless_logs_keep_schedule(packet, tmp_path):
    absent = _read(tmp_path / "absent.eval")
    unsupported = _read(tmp_path / "not-an-eval.json")
    broken = tmp_path / "broken.eval"
    broken.write_text("not a log")
    unreadable = _read(broken)
    sampleless = inspect_report._analyze_log(packet, _log(None), None, None)
    foreign = inspect_report._analyze_log(
        packet, _log([], task="unrelated"), None, None
    )
    assert [
        report["log"]["status"]
        for report in (absent, unsupported, unreadable, sampleless, foreign)
    ] == [
        "absent",
        "unsupported",
        "unsupported",
        "samples_unavailable",
        "unsupported",
    ]
    for report in (absent, unsupported, unreadable, sampleless, foreign):
        assert report["summary"]["scheduled_cells"] == 96
        assert report["summary"]["cell_status"] == {"missing_sample": 96}
        assert (
            report["indexed_minus_separate"]["measures"]["exact_totals"]["effect"]
            is None
        )
    with pytest.raises(ValueError, match="registered packet digest"):
        inspect_report.analyze(packing.PACKET, broken, expected_sha256="0" * 64)


def test_direct_usage_identity_and_holds(packet, tmp_path, monkeypatch):
    ledger = tmp_path / "private-ledger"
    ledger.mkdir(mode=0o700)
    balance = Decimal("2.00")
    spend.initialize(ledger, balance, Decimal("0.20"))
    monkeypatch.setattr(spend, "_HOST_LEDGER", ledger)
    good, token, model, hold, wrong_role, no_sample = packing.grid()[:6]
    now = datetime(2026, 1, 1, 5, tzinfo=UTC)
    for cell in (good, token, model, hold, wrong_role, no_sample):
        spend.reserve(
            cell.cell_id,
            hold.role if cell == wrong_role else cell.role,
            balance_usd=balance,
        )
        if cell not in (hold, wrong_role, no_sample):
            spend.settle(
                cell.cell_id,
                cell.role,
                cache_hit=20,
                cache_miss=80,
                output=30,
                served_model=deepseek.MODEL,
                started_at=now,
                finished_at=now,
            )
    direct = "seatlab-direct/deepseek-flash"
    samples = [
        _sample(
            packet,
            cell,
            output_model=deepseek.REVISION if cell == model else deepseek.MODEL,
            event_model=direct,
        )
        for cell in (good, token, model, wrong_role)
    ]
    samples[1].output.usage.total_tokens = 129
    samples[1].events[0].output.usage.total_tokens = 129
    samples.append(
        _sample(packet, hold, output_model=deepseek.MODEL, event_model=direct)
    )
    samples[-1].output = ModelOutput()
    samples[-1].events = []
    report = inspect_report._analyze_log(
        packet, _log(samples, model=direct), None, ledger
    )
    rows = _cells(report)
    assert report["model_source"] == "inspect_guarded_direct"
    assert report["spend"]["wrong_role_rows"] == 1
    assert report["spend"]["unsettled_eligible_rows"] == 2
    assert report["spend"]["observed_action_status"] == {
        "matched": 1,
        "mismatch": 2,
        "missing_ledger_row": 1,
    }
    assert rows[good.cell_id]["status"] == "accepted"
    assert rows[good.cell_id]["spend"]["status"] == "matched"
    assert (
        rows[token.cell_id]["status"]
        == rows[model.cell_id]["status"]
        == "unverified_direct"
    )
    assert rows[token.cell_id]["spend"]["mismatches"] == ["total_tokens"]
    assert rows[token.cell_id]["spend"]["inspect_usage"]["total_tokens"] == 129
    assert rows[model.cell_id]["spend"]["mismatches"] == ["model_identity"]
    assert (
        rows[model.cell_id]["spend"]["inspect_served_model"]
        == deepseek.REVISION.lower()
    )
    assert rows[wrong_role.cell_id]["spend"]["status"] == "missing_ledger_row"
    assert rows[hold.cell_id]["status"] == "missing_generation"
    assert rows[hold.cell_id]["spend"]["status"] == "unsettled_hold"
    assert rows[no_sample.cell_id]["status"] == "missing_sample"
    assert rows[no_sample.cell_id]["spend"]["unsettled_hold_usd"] == "0.010000000"
    assert "private-ledger" not in json.dumps(report)

    no_ledger = inspect_report._analyze_log(
        packet, _log(samples, model=direct), None, None
    )
    assert _cells(no_ledger)[good.cell_id]["spend"]["status"] == "ledger_required"
    assert _cells(no_ledger)[good.cell_id]["status"] == "unverified_direct"
    supplied = spend.report(ledger)
    supplied["calls"].extend(
        row.copy() for row in supplied["calls"][:] if row["run"] == good.cell_id
    )
    monkeypatch.setattr(spend, "report", lambda *args, **kwargs: supplied)
    duplicated = inspect_report._analyze_log(
        packet, _log(samples, model=direct), None, ledger
    )
    assert (
        _cells(duplicated)[good.cell_id]["spend"]["status"] == "duplicate_ledger_rows"
    )
    assert _cells(duplicated)[good.cell_id]["status"] == "unverified_direct"
