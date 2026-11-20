import hashlib
import json
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from examples.seatlab.visibility import listener
from examples.seatlab.visibility.crucible import RunRecord

_FAMILY = "epistemic-crucible"
_POLICIES = ("gullibility_p00", "gullibility_p50", "gullibility_p100")
_FIELDS = {
    "scout_1": ("latency", "safety"),
    "scout_2": ("safety", "reliability"),
    "scout_3": ("reliability", "latency"),
}
_RECEIVER = "decision_maker"
_SEATS = (_RECEIVER, *_FIELDS)
_FILE = Path("artifacts/logs/world/events.jsonl")
_ALPHA = {"latency": 72, "reliability": 68, "safety": 70}
_BETA = {"latency": 60, "reliability": 58, "safety": 62}


def _archive_events(policy: str) -> list[dict]:
    rows = []

    def append(kind, actor, visibility, recipients, **payload):
        seq = len(rows) + 1
        rows.append(
            {
                "seq": seq,
                "ts": "2026-11-19T00:00:00+00:00",
                "task_family": _FAMILY,
                "condition": policy,
                "event_type": kind,
                "actor": actor,
                "authenticated": actor != "world",
                "visibility": visibility,
                "recipients": recipients,
                "payload": payload,
            }
        )
        return seq

    for round_number in (1, 2):
        truth = (_ALPHA, _BETA) if round_number == 1 else (_BETA, _ALPHA)
        refs = []
        for seat, fields in _FIELDS.items():
            scores = {
                route: {field: values[field] for field in fields}
                for route, values in zip(("route_alpha", "route_beta"), truth, strict=True)
            }
            # Identical repeated observations, linked to distinct authenticated reads.
            for _ in range(2 if seat == "scout_1" else 1):
                read_seq = append("observation_read", seat, "world_audit", [], round=round_number)
                append(
                    "observation_truth", "world", "world_private", [seat],
                    round=round_number, seat=seat, attributes=list(fields),
                    read_seq=read_seq, **{route: dict(values) for route, values in scores.items()},
                )
            # Report claims are not truth; do not silently replace them with observations.
            claim = {route: dict(values) for route, values in scores.items()}
            if seat == "scout_3":
                claim["route_alpha"]["latency"] = 8
                claim["route_beta"]["latency"] = 98
            claim["route_alpha"]["unknown_attribute"] = 99
            claim["note"] = "CLAIM_NOTE"
            refs.append(
                append(
                    "scout_report", seat, "public", list(_SEATS),
                    round=round_number, seat=seat, claim=claim, note="REPORT_NOTE",
                )
            )
        for partial in (refs[:1], list(reversed(refs))):
            append(
                "message_read", _RECEIVER, "world_audit", [], round=round_number,
                channel="round_reports", msg_seqs=partial,
            )
        append(
            "decision", _RECEIVER, "world_audit", [], round=round_number,
            choice="route_beta" if round_number == 1 else "route_alpha",
        )
        # A later fetch and revision must not change the reconstructed input.
        append(
            "message_read", _RECEIVER, "world_audit", [], round=round_number,
            channel="round_reports", msg_seqs=[],
        )
        append(
            "decision", _RECEIVER, "world_audit", [], round=round_number,
            choice="route_alpha" if round_number == 1 else "route_beta",
        )
    return rows


def _write_events(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _by_hash(record: RunRecord) -> bytes:
    return hashlib.sha256(f"seatlab-listener-v1:{UUID(record.key)}".encode()).digest()


@pytest.fixture
def archive(tmp_path, monkeypatch):
    root = (tmp_path / "root").resolve()
    records = []
    for policy_idx, policy in enumerate(_POLICIES):
        for candidate in range(3):
            uuid = str(UUID(int=100 + policy_idx * 10 + candidate))
            directory = root / uuid
            path = directory / _FILE
            path.parent.mkdir(parents=True)
            _write_events(path, _archive_events(policy))
            records.append(
                RunRecord(
                    directory=directory,
                    family=_FAMILY,
                    condition=policy,
                    key=uuid,
                    seats=_SEATS,
                    finished=True,
                    _closed_seats=frozenset(_SEATS),
                    _sources=(),
                )
            )

    def enumerate_included(path, *, excluded=False):
        assert path == root
        assert not excluded
        return iter(reversed(records))

    monkeypatch.setattr(listener, "run_records", enumerate_included)
    return root, records


def test_sealed_sampling_and_prompt_blinding(archive):
    root, records = archive
    sample = records[3]
    assert sample.key != sample.key.upper()
    records.extend((sample, replace(sample, key=sample.key.upper())))
    plan = listener.build_schedule(root)
    assert len(plan.cases) == 6
    assert len(plan.cells) == 36
    assert [case.id for case in plan.cases] == [
        f"{policy}-r{round_number}"
        for policy in ("p00", "p50", "p100")
        for round_number in (1, 2)
    ]
    assert [case.best_label for case in plan.cases] == ["A", "A", "B", "B", "A", "A"]
    for policy, group in plan.candidates.items():
        distinct = {str(UUID(row.key)): row for row in records if row.condition == policy}
        assert group == tuple(sorted(distinct, key=lambda key: _by_hash(distinct[key])))
        assert tuple(case.uuid for case in plan.cases if case.policy == policy) == group[:2]
    for idx, case in enumerate(plan.cases):
        cells = [cell for cell in plan.cells if cell.case == case]
        by_rep = [tuple(cell.arm for cell in cells if cell.replicate == k) for k in (1, 2, 3)]
        forward = ("visible", "none")
        assert by_rep == [forward if (idx + k) % 2 == 0 else forward[::-1] for k in range(3)]
        assert case.data is not None
        assert case.data.fetch_seq + 1 == case.data.decision_seq
        assert len(case.data.truth_seqs) == 4
        visible = next(cell.prompt for cell in cells if cell.arm == "visible")
        assert '{"choice":"A"}' in visible
        assert "exactly A or B" not in visible
        for forbidden in (
            "CLAIM_NOTE", "REPORT_NOTE", "unknown_attribute", "99", "scout_", "round",
            case.uuid, "gullibility", "alpha", "beta", "private_best_label",
        ):
            assert forbidden not in visible
        lines = visible.split("Data:\n")[1].splitlines()
        assert [line[0] for line in lines] == list("ABABAB")
        assert lines[0].startswith("A: latency=98" if case.swap else "A: latency=8")
    no_reports = {cell.prompt for cell in plan.cells if cell.arm == "none"}
    assert len(no_reports) == 1
    assert "No scores provided." in no_reports.pop()


def _round_rows(rows: list[dict], round_number: int, kind: str) -> list[dict]:
    return [
        row for row in rows
        if row["event_type"] == kind and row["payload"].get("round") == round_number
    ]


@pytest.mark.parametrize(
    ("damage", "reason"),
    [
        ("last_fetch_partial", "incomplete_predecision_fetch"),
        ("repeated_sender", "incomplete_predecision_fetch"),
        ("future_ref", "invalid_report_reference"),
        ("cross_round_ref", "invalid_report_reference"),
        ("bool_report", "invalid_numeric_report"),
        ("string_report", "invalid_numeric_report"),
        ("unlinked_truth", "invalid_observation_truth"),
        ("conflicting_repeat", "conflicting_observation_truth"),
        ("conflicting_overlap", "conflicting_observation_truth"),
        ("tied_truth", "tied_observation_truth"),
        ("missing_decision", "missing_original_decision"),
    ],
)
def test_selected_case_stays_ineligible_without_replacement(archive, damage, reason):
    root, records = archive
    candidates = sorted((row for row in records if row.condition == _POLICIES[0]), key=_by_hash)
    # Use round 2 to permit deliberately pointing a fetch back into round 1.
    selected = candidates[1]
    path = selected.directory / _FILE
    rows = _archive_events(selected.condition)
    first_fetch, last_fetch, post_decision_fetch = _round_rows(rows, 2, "message_read")
    reports = _round_rows(rows, 2, "scout_report")
    truth = _round_rows(rows, 2, "observation_truth")
    refs = last_fetch["payload"]["msg_seqs"]
    if damage == "last_fetch_partial":
        first_fetch["payload"]["msg_seqs"] = refs[:]
        post_decision_fetch["payload"]["msg_seqs"] = refs[:]
        refs.pop()
    elif damage == "repeated_sender":
        refs[0] = refs[1]
    elif damage == "future_ref":
        refs[0] = post_decision_fetch["seq"]
    elif damage == "cross_round_ref":
        refs[0] = _round_rows(rows, 1, "scout_report")[0]["seq"]
    elif damage in ("bool_report", "string_report"):
        reports[0]["payload"]["claim"]["route_alpha"]["latency"] = (
            True if damage == "bool_report" else "60"
        )
    elif damage == "unlinked_truth":
        truth[1]["payload"]["read_seq"] = truth[2]["payload"]["read_seq"]
    elif damage == "conflicting_repeat":
        truth[1]["payload"]["route_alpha"]["latency"] = 1
    elif damage == "conflicting_overlap":
        truth[-1]["payload"]["route_alpha"]["latency"] = 1
    elif damage == "tied_truth":
        for row in truth:
            row["payload"]["route_alpha"] = dict(row["payload"]["route_beta"])
    else:
        for row in _round_rows(rows, 2, "decision"):
            row["event_type"] = "unrelated"
    _write_events(path, rows)

    plan = listener.build_schedule(root)
    assert plan.cases[1].uuid == selected.key
    assert plan.cases[1].data is None
    assert plan.cases[1].ineligible_reason == reason
    assert candidates[2].key not in {case.uuid for case in plan.cases}
    assert len(plan.cases) == 6
    assert len(plan.cells) == 30
    assert not any(cell.case.id == "p00-r2" for cell in plan.cells)

@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (' {"choice":"A"}\n', "A"),
        ('{"choice":"B"}', "B"),
        ('{"choice": "A"}', "A"),
        ("A", None),
        ("B", None),
        ("", None),
        ('{"choice":"A"} trailing', None),
        ('{"choice":"A","choice":"B"}', None),
        ('{"choice":"A","note":"x"}', None),
        ('{"choice":"a"}', None),
        ("A or B", None),
    ],
)
def test_exact_response_labels(response, expected):
    assert listener.parse_response(response) == expected


def test_runs_once_and_scores_in_separate_views(archive, tmp_path, monkeypatch):
    from examples.seatlab.visibility import deepseek

    root, _ = archive
    study = tmp_path / "study"
    plan = listener.build_schedule(root)
    seen = []
    responses = iter(
        [
            ' {"choice":"A"}\n',
            '{"choice":"B"}',
            "A or B",
            deepseek.SeatUnavailable("balance_floor"),
            RuntimeError("internal"),
            KeyboardInterrupt(),
        ]
    )

    def turn(prompt, *, run_id, role):
        seen.append((prompt, run_id, role))
        value = next(responses)
        if isinstance(value, BaseException):
            raise value
        return deepseek.SeatTurn(value, "deepseek-v4.1-flash-1017")

    monkeypatch.setattr(deepseek, "turn", turn)
    summary = listener.seal(root, study)
    assert summary["planned_cells"] == 36
    assert summary["schedule_sha256"] == hashlib.sha256((study / "schedule.json").read_bytes()).hexdigest()
    with pytest.raises(FileExistsError, match="study"):
        listener.seal(root, study)
    with pytest.raises(ValueError, match="earlier listener cell 1"):
        listener.run_cell(root, study, 2)
    assert not seen

    results = [listener.run_cell(root, study, i) for i in range(1, 6)]
    assert [row["status"] for row in results] == [
        "valid", "valid", "invalid", "seat_unavailable", "request_error",
    ]
    assert results[0]["served_model"] == "deepseek-v4.1-flash-1017"
    assert "response" not in results[0]
    assert "SUBSCRIPTION" not in (study / "schedule.json").read_text(encoding="utf-8")
    with pytest.raises(KeyboardInterrupt):
        listener.run_cell(root, study, 6)
    for i in range(1, 7):
        with pytest.raises(ValueError, match="retries are not allowed"):
            listener.run_cell(root, study, i)
    assert seen == [
        (cell.prompt, f"listen-{cell.id:02d}", _RECEIVER)
        for cell in plan.cells[:6]
    ]
    invalid = json.loads((study / "cell-003/outcome.json").read_bytes())
    assert invalid["response"] == "A or B"
    valid = json.loads((study / "cell-001/outcome.json").read_bytes())
    assert valid["served_model"] == "deepseek-v4.1-flash-1017"
    unavailable = json.loads((study / "cell-004/outcome.json").read_bytes())
    assert unavailable["category"] == "balance_floor"

    summary = listener.report(root, study)
    first = summary["within_case"][0]
    assert first["within_arm"]["visible"]["correct"] == 1
    assert first["within_arm"]["visible"]["seat_unavailable"] == 1
    assert first["within_arm"]["visible"]["request_error"] == 1
    assert first["within_arm"]["none"]["invalid"] == 1
    assert first["within_arm"]["none"]["reserved_without_result"] == 1
    assert first["between_arms"] == {
        "scheduled": 3,
        "both_valid": 1,
        "same_choice": 0,
        "visible_only_correct": 1,
        "none_only_correct": 0,
    }
    assert first["within_arm"]["visible"]["repeat_disagreement"] == {
        "A*B": 0,
        "denominator": 0,
    }
    assert summary["by_arm"]["visible"]["repeat_disagreement"] == {
        "A*B": 0,
        "denominator": 0,
    }
    assert summary["eligible_best_label_counts"] == {"A": 4, "B": 2}
    assert summary["by_arm"]["none"]["distinct_prompts"] == 1
    assert summary["by_arm"]["none"]["not_run"] == 15
    assert summary["between_arms_complete_pairs"]["both_valid"] == 1
    assert "not new native team trials" in summary["scope"]


def test_seal_binds_source_and_keeps_archive_read_only(archive, tmp_path):
    root, records = archive
    study = tmp_path / "study"
    with pytest.raises(ValueError, match="read-only archive"):
        listener.seal(root, root / "study")
    assert not (root / "study").exists()
    listener.seal(root, study)
    case = listener.build_schedule(root).cases[0]
    record = next(row for row in records if row.key == case.uuid)
    source = record.directory / _FILE
    rows = _archive_events(record.condition)
    _round_rows(rows, 1, "scout_report")[0]["payload"]["note"] = "new excluded prose"
    _write_events(source, rows)
    with pytest.raises(ValueError, match="no longer matches"):
        listener.run_cell(root, study, 1)
    with pytest.raises(ValueError, match="no longer matches"):
        listener.report(root, study)
    assert not (study / "cell-001").exists()


_MOCK_SERVED = "deepseek-v4.1-flash-1017"


def _write_outcome(
    study: Path,
    digest: str,
    cell_id: int,
    response: str,
    served: str | None,
) -> None:
    row = {
        "schedule_sha256": digest,
        "cell": cell_id,
        "status": "valid" if listener.parse_response(response) else "invalid",
        "response": response,
    }
    if served is not None:
        row["served_model"] = served
    directory = study / f"cell-{cell_id:03d}"
    directory.mkdir(mode=0o700)
    (directory / "outcome.json").write_text(json.dumps(row), encoding="utf-8")


def test_disagreement_stays_within_case_and_requires_exact_model(archive, tmp_path):
    root, _ = archive
    study = tmp_path / "study"
    plan = listener.build_schedule(root)
    listener.seal(root, study)
    digest = hashlib.sha256((study / "schedule.json").read_bytes()).hexdigest()
    visible = [
        [cell for cell in plan.cells if cell.case == case and cell.arm == "visible"]
        for case in plan.cases[:2]
    ]
    assert [len(cells) for cells in visible] == [3, 3]
    for cell, choice in zip(visible[0], ("A", "B", "A"), strict=True):
        _write_outcome(
            study, digest, cell.id, '{"choice":"' + choice + '"}', _MOCK_SERVED
        )
    for cell in visible[1]:
        _write_outcome(study, digest, cell.id, '{"choice":"A"}', "deepseek-flash")

    summary = listener.report(root, study)
    first = summary["within_case"][0]["within_arm"]["visible"]["repeat_disagreement"]
    second = summary["within_case"][1]["within_arm"]["visible"]["repeat_disagreement"]
    assert first == {"A*B": 2, "denominator": 3}
    assert second == {"A*B": 0, "denominator": 3}
    assert summary["by_arm"]["visible"]["repeat_disagreement"] == {
        "A*B": 2,
        "denominator": 6,
    }
    assert summary["by_arm"]["visible"]["valid"] == 6

    missing = tmp_path / "missing-model"
    listener.seal(root, missing)
    _write_outcome(missing, digest, visible[0][0].id, '{"choice":"A"}', None)
    with pytest.raises(ValueError, match="exact served model"):
        listener.report(root, missing)
    wrong = tmp_path / "wrong-model"
    listener.seal(root, wrong)
    _write_outcome(wrong, digest, visible[0][0].id, '{"choice":"A"}', "DeepSeek-Flash")
    with pytest.raises(ValueError, match="exact served model"):
        listener.report(root, wrong)
