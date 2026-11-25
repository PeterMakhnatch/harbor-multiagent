import base64
import json
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.decision_boundary import World
from examples.seatlab.decision_boundary.__main__ import main, native
from examples.seatlab.decision_boundary.core import analyze, conflict_witness
from examples.seatlab.decision_boundary.inputs import LIMIT, census, read_input


def _table(observations: tuple[bytes, ...] = (b"", b"")) -> dict[str, Any]:
    return {
        "schema": "seatlab.decision-worlds/v1",
        "scale": 1,
        "worlds": [
            {
                "name": f"secret world {i}",
                "mass": 1,
                "scores": {
                    "private action A": int(i % 2 == 0),
                    "private action B": int(i % 2 == 1),
                },
                "observation_b64": base64.b64encode(obs).decode("ascii"),
            }
            for i, obs in enumerate(observations)
        ],
    }


def test_real_files_preserve_groups_and_reject_a_different_decision(tmp_path, capsys):
    paths = [tmp_path / f"private-{i}.json" for i in range(2)]
    for path, observations in zip(
        paths, ((b"", b""), (b"yellow", b"black")), strict=True
    ):
        path.write_text(json.dumps(_table(observations)))
    args = ["compare", "--files", *(str(p) for p in paths)]
    assert (
        main([*args, "--expect-gain", "positive", "--expect-relation", "refinement"])
        == 0
    )
    good = capsys.readouterr()
    assert good.err == "" and "private" not in good.out and "secret" not in good.out
    row = json.loads(good.out)["comparison"]
    assert (
        row["before_observed"],
        row["after_observed"],
        row["gain"],
        row["same_bytes"],
    ) == ("1/2", "1", "1/2", 0)
    assert main([*args, "--expect-gain", "zero"]) == 1
    assert json.loads(capsys.readouterr().out)["expectation_met"] is False
    changed = _table()
    changed["worlds"][0]["mass"] = 2
    paths[1].write_text(json.dumps(changed))
    assert main(args) == 2
    bad = capsys.readouterr()
    assert bad.out == "" and "mass mismatch" in bad.err and "secret" not in bad.err
    assert main(["compare", "--files", "-", "-"]) == 2
    assert "at most one" in capsys.readouterr().err


def test_conflict_witness_can_require_three_worlds_and_discloses_no_labels():
    worlds = [
        World(
            str(i),
            1,
            b"same",
            {action: int(i != k) for k, action in enumerate(("A", "B", "C"))},
        )
        for i in range(3)
    ]
    assert conflict_witness(worlds, 1) == {
        "row_indices": [0, 1, 2],
        "remaining_optimal_rule_counts": [2, 1, 0],
    }
    assert conflict_witness(worlds[:2], 1) is None
    assert (
        conflict_witness(
            [World(w.name, w.mass, bytes([i]), w.scores) for i, w in enumerate(worlds)],
            1,
        )
        is None
    )


@pytest.mark.parametrize("seed", [0, 7, 2**63 - 1])
def test_native_census_agrees_with_physical_and_documentary_controls(seed):
    cases = native((), seed)
    assert len(cases) == 9
    for name, (scale, worlds) in cases.items():
        row = analyze(worlds, scale)
        if name.startswith("ship/"):
            expected = {
                "ship/original-delivered": ("1", 4),
                "ship/original-withheld": ("1/2", 3),
                "ship/ambiguous-delivered": ("3/4", 4),
            }[name]
            assert (row["observed"], row["actions"]) == expected
            assert (
                row["observations"] == 1
                and row["informed"] == "1"
                and row["observation_value"] == "0"
            )
            assert all(w.scores["stop"] == w.scores["close"] for w in worlds)
        else:
            separated = name in (
                "exhibit/faithful/shown",
                "exhibit/heavy-only/shown",
                "exhibit/heavy-only/masked",
            )
            assert row["unobserved"] == "1/4" and row["informed"] == "2/5"
            assert row["observed"] == ("2/5" if separated else "1/4")
            assert row["observations"] == (2 if separated else 1)
            assert row["observation_value"] == ("3/20" if separated else "0")
        assert Fraction(row["gap"]) == Fraction(row["informed"]) - Fraction(
            row["observed"]
        )
        assert (conflict_witness(worlds, scale) is not None) == (row["gap"] != "0")


def test_native_pairs_separate_information_encoding_and_action_availability(capsys):
    for sender, relation, gain, delta in (
        ("faithful", "refinement", "positive", "3/20"),
        ("heavy-only", "equivalent", "zero", "0"),
        ("empty", "equivalent", "zero", "0"),
    ):
        prefix = f"exhibit/{sender}"
        assert (
            main(
                [
                    "compare",
                    "--native",
                    f"{prefix}/masked",
                    f"{prefix}/shown",
                    "--expect-relation",
                    relation,
                    "--expect-gain",
                    gain,
                ]
            )
            == 0
        )
        answer = json.loads(capsys.readouterr().out)["comparison"]
        assert answer["gain"] == delta and answer["same_bytes"] == (
            2 if sender == "empty" else 0
        )
    assert (
        main(
            ["compare", "--native", "ship/original-withheld", "ship/original-delivered"]
        )
        == 2
    )
    failed = capsys.readouterr()
    assert "score matrix mismatch" in failed.err and failed.out == ""


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"[]",
        b"null",
        b"{",
        b"\xff",
        b'{"schema":"seatlab.decision-worlds/v1","scale":1,"worlds":[],"worlds":[]}',
        b'{"schema":"seatlab.decision-worlds/v1","scale":1,"worlds":[{"name":"x","mass":1,"observation_b64":"AB==","scores":{"x":1}}]}',
        b'{"schema":"seatlab.decision-worlds/v1","scale":1,"worlds":[{"name":"x","mass":1,"observation_b64":"","scores":{"x":0,"x":1}}]}',
    ],
)
def test_rejects_partial_noncanonical_or_duplicate_cases(data):
    with pytest.raises(ValueError):
        census(data)


def test_file_limit_and_invalid_scores_fail_without_echo(tmp_path: Path, capsys):
    path = tmp_path / "private.json"
    with path.open("wb") as out:
        out.truncate(LIMIT + 1)
    with pytest.raises(ValueError):
        read_input(str(path))
    payload = _table()
    payload["worlds"][1]["scores"]["private action A"] = True
    path.write_text(json.dumps(payload))
    assert main(["inspect", str(path), "--witness"]) == 2
    capture = capsys.readouterr()
    assert (
        capture.out == ""
        and "invalid score" in capture.err
        and "private" not in capture.err
    )
