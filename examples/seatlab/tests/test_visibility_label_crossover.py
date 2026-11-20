import json
from dataclasses import replace

import pytest

from examples.seatlab.tests import test_visibility_listener as original_tests
from examples.seatlab.visibility import deepseek, label_crossover, listener

archive = original_tests.archive


@pytest.fixture
def crossover_archive(archive, monkeypatch):
    root, records = archive
    source = root / "census-fixture.json"
    source.write_bytes(b'{"census": 1}\n')
    records[0] = replace(records[0], _sources=(source,))
    original = listener.build_schedule(root)
    monkeypatch.setattr(
        label_crossover,
        "ORIGINAL_SCHEDULE_SHA256",
        listener._sha(original.seal_bytes()),
    )
    return root, source, original


def test_only_the_published_original_schedule_is_accepted(archive, tmp_path):
    root, _ = archive
    assert label_crossover.ORIGINAL_SCHEDULE_SHA256 == (
        "b95aa3434271a357cd297d8a56b1715ff8edae1fd4a8566441b4a0995c1dedda"
    )
    study = tmp_path / "crossover"
    with pytest.raises(ValueError, match="does not match the pinned SHA-256"):
        label_crossover.seal(root, study)
    assert not study.exists()


def test_crossover_treatment_order_and_prompt_identity(crossover_archive):
    root, source, original = crossover_archive
    plan = label_crossover.build_schedule(root)
    assert plan.cases == original.cases
    assert len(plan.cells) == 72
    receipt = json.loads(plan.seal_bytes())
    assert receipt["schema"] == "seatlab-listener-label-crossover-v1"
    assert receipt["original_schedule_sha256"] == listener._sha(original.seal_bytes())
    assert receipt["source_file_sha256"] == {
        source.name: listener._sha(source.read_bytes())
    }
    assert receipt["cases"] == json.loads(original.seal_bytes())["cases"]
    assert receipt["requests_per_case_per_arm_per_map"] == 3
    assert [cell["run_id"] for cell in receipt["cells"]] == [
        f"mirror-{i:02d}" for i in range(1, 73)
    ]
    assert [cell["id"] for cell in receipt["cells"]] == list(range(1, 73))
    even = (
        ("original", "visible"),
        ("original", "none"),
        ("complementary", "none"),
        ("complementary", "visible"),
    )
    odd = (
        ("complementary", "visible"),
        ("complementary", "none"),
        ("original", "none"),
        ("original", "visible"),
    )
    assert [
        (row["case"], row["replicate"], row["map"], row["arm"])
        for row in receipt["cells"]
    ] == [
        (case.id, rep_idx + 1, mapping, arm)
        for case_idx, case in enumerate(original.cases)
        for rep_idx in range(3)
        for mapping, arm in (even, odd)[(case_idx + rep_idx) % 2]
    ]

    original_by_key = {
        (cell.case.id, cell.replicate, cell.arm): cell for cell in original.cells
    }
    for row, cell in zip(receipt["cells"], plan.cells, strict=True):
        prior = original_by_key[cell.case.id, cell.replicate, cell.arm]
        assert row["prompt_sha256"] == listener._sha(cell.prompt.encode())
        assert cell.case.data == prior.case.data
        if row["map"] == "original":
            assert cell.case == prior.case
            assert cell.prompt == prior.prompt
        else:
            assert cell.case.swap != prior.case.swap
            assert cell.case.best_label != prior.case.best_label
            if cell.arm == "none":
                assert cell.prompt == prior.prompt
            else:
                header, old_data = prior.prompt.split("Data:\n")
                new_header, new_data = cell.prompt.split("Data:\n")
                assert new_header == header
                for a, b, flipped_a, flipped_b in zip(
                    old_data.splitlines()[::2],
                    old_data.splitlines()[1::2],
                    new_data.splitlines()[::2],
                    new_data.splitlines()[1::2],
                    strict=True,
                ):
                    assert (flipped_a, flipped_b) == ("A" + b[1:], "B" + a[1:])


def test_cli_publishes_label_counts_and_matched_pairs(
    crossover_archive, tmp_path, monkeypatch, capsys
):
    root, _, original = crossover_archive
    study = tmp_path / "crossover"
    plan = label_crossover.build_schedule(root)
    seen = []

    def turn(prompt, *, run_id, role):
        cell = plan.cells[len(seen)]
        seen.append(run_id)
        assert run_id == f"mirror-{cell.id:02d}"
        assert role == "decision_maker"
        assert prompt == cell.prompt
        choice = cell.case.best_label if cell.arm == "visible" else "A"
        return deepseek.SeatTurn(
            json.dumps({"choice": choice}), deepseek.REVISION, 12, 2, 4
        )

    monkeypatch.setattr(deepseek, "turn", turn)
    assert label_crossover.main(["fingerprint", str(root)]) == 0
    fingerprint = json.loads(capsys.readouterr().out)
    assert label_crossover.main(["seal", str(root), str(study)]) == 0
    assert json.loads(capsys.readouterr().out) == fingerprint
    assert fingerprint["schedule_sha256"] == listener._sha(
        (study / "schedule.json").read_bytes()
    )
    assert label_crossover.main(["run-cell", str(root), str(study), "1"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["served_model"] == "DeepSeek-V4.1-Flash-1017"
    assert first["status"] == "valid"
    assert "response" not in first
    for cell_id in range(2, 73):
        assert label_crossover.run_cell(root, study, cell_id)["status"] == "valid"
    assert seen == [f"mirror-{i:02d}" for i in range(1, 73)]

    assert label_crossover.main(["report", str(root), str(study)]) == 0
    raw = capsys.readouterr().out
    summary = json.loads(raw)
    for private in (
        "private_best_label",
        "No scores provided",
        "census-fixture.json",
        "latency=",
    ):
        assert private not in raw
    for case in original.cases:
        assert case.uuid not in raw
    assert summary["planned_cells"] == 72
    assert summary["unique_worlds"] == summary["worlds_with_valid_responses"] == 6
    assert summary["distinct_prompts"] == 5
    assert summary["served_model_counts"] == {"DeepSeek-V4.1-Flash-1017": 72}
    by_treatment = summary["by_visibility_and_map"]
    for arm, mapping, a, b, correct, prompts in (
        ("visible", "original", 12, 6, 18, 4),
        ("visible", "complementary", 6, 12, 18, 4),
        ("none", "original", 18, 0, 12, 1),
        ("none", "complementary", 18, 0, 6, 1),
    ):
        row = by_treatment[arm][mapping]
        assert row["scheduled"] == row["valid"] == row["correct_denominator"] == 18
        assert (row["A"], row["B"], row["correct"], row["distinct_prompts"]) == (
            a,
            b,
            correct,
            prompts,
        )
        assert row["correct_fraction_of_valid"] == correct / 18
    assert summary["map_pairs_by_visibility"] == {
        "visible": {
            "scheduled": 18,
            "both_valid": 18,
            "letter_flip_preserves_route": 18,
            "same_letter": 0,
        },
        "none": {
            "scheduled": 18,
            "both_valid": 18,
            "letter_flip_preserves_route": 0,
            "same_letter": 18,
        },
    }
    assert summary["visible_vs_none_matched_crossover"] == {
        "scheduled": 18,
        "all_four_valid": 18,
        "both_flip": 0,
        "visible_only_flips": 18,
        "none_only_flips": 0,
        "neither_flips": 0,
        "visible_minus_none_flip_fraction": 1.0,
    }


def test_failed_or_interrupted_cells_are_consumed_without_scoring(
    crossover_archive, tmp_path, monkeypatch
):
    root, _, _ = crossover_archive
    study = tmp_path / "crossover"
    label_crossover.seal(root, study)
    responses = iter(
        (
            '{"choice":"A"}',
            '"B"',
            deepseek.SeatUnavailable("balance_floor"),
            KeyboardInterrupt(),
            RuntimeError("request failed"),
            '{"choice":"B"}',
            '{"choice":"B"}',
        )
    )
    seen = []

    def turn(prompt, *, run_id, role):
        seen.append(run_id)
        response = next(responses)
        if isinstance(response, BaseException):
            raise response
        return deepseek.SeatTurn(response, "deepseek-flash", 12, 2, 4)

    monkeypatch.setattr(deepseek, "turn", turn)
    with pytest.raises(ValueError, match="reserve earlier listener cell 1"):
        label_crossover.run_cell(root, study, 2)
    assert not seen
    assert [
        label_crossover.run_cell(root, study, i)["status"] for i in range(1, 4)
    ] == [
        "valid",
        "invalid",
        "seat_unavailable",
    ]
    with pytest.raises(KeyboardInterrupt):
        label_crossover.run_cell(root, study, 4)
    assert label_crossover.run_cell(root, study, 5)["status"] == "request_error"
    assert label_crossover.run_cell(root, study, 6)["status"] == "valid"
    assert label_crossover.run_cell(root, study, 7)["status"] == "valid"
    for cell_id in range(1, 8):
        with pytest.raises(ValueError, match="retries are not allowed"):
            label_crossover.run_cell(root, study, cell_id)
    assert seen == [f"mirror-{i:02d}" for i in range(1, 8)]

    summary = label_crossover.report(root, study)
    counts = summary["by_visibility_and_map"]
    assert counts["none"]["original"]["invalid"] == 1
    assert counts["none"]["original"]["correct_denominator"] == 1
    assert counts["none"]["complementary"]["seat_unavailable"] == 1
    assert counts["visible"]["complementary"]["reserved_without_result"] == 1
    assert counts["visible"]["complementary"]["request_error"] == 1
    assert counts["visible"]["complementary"]["not_run"] == 16
    assert summary["map_pairs_by_visibility"]["visible"]["both_valid"] == 0
    assert summary["map_pairs_by_visibility"]["none"]["both_valid"] == 1
    assert summary["map_pairs_by_visibility"]["none"]["same_letter"] == 1
    matched = summary["visible_vs_none_matched_crossover"]
    assert matched["all_four_valid"] == 0
    assert matched["visible_minus_none_flip_fraction"] is None
    assert summary["worlds_with_valid_responses"] == 1
    assert summary["served_model_counts"] == {"deepseek-flash": 4}

    outcome = study / "cell-001" / "outcome.json"
    row = json.loads(outcome.read_bytes())
    row["served_model"] = "DeepSeek-Flash-Other-Revision"
    outcome.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(ValueError, match="no exact served model"):
        label_crossover.report(root, study)


def test_seal_detects_census_change_not_covered_by_original_receipt(
    crossover_archive, tmp_path, monkeypatch
):
    root, source, original = crossover_archive
    study = tmp_path / "crossover"
    original_study = tmp_path / "original"
    monkeypatch.setattr(
        deepseek, "turn", lambda *args, **kwargs: pytest.fail("unexpected turn")
    )
    listener.seal(root, original_study)
    with pytest.raises(ValueError, match="no longer matches"):
        label_crossover.run_cell(root, original_study, 1)
    label_crossover.seal(root, study)
    source.write_bytes(b'{"census": 2}\n')
    assert listener.build_schedule(root).seal_bytes() == original.seal_bytes()
    with pytest.raises(ValueError, match="no longer matches"):
        label_crossover.run_cell(root, study, 1)
    with pytest.raises(ValueError, match="no longer matches"):
        label_crossover.report(root, study)
    assert not (study / "cell-001").exists()
    assert not (original_study / "cell-001").exists()
