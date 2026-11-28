import json
import os
import stat
from dataclasses import asdict
from pathlib import Path

import pytest

from examples.seatlab.receiver_memory import __main__ as cli
from examples.seatlab.receiver_memory.model import POLICIES, packet
from examples.seatlab.receiver_memory.revised import SELECTOR, cases

SEED = 7
PRIVATE = "private-input-canary-8492"


@pytest.fixture(scope="module")
def generated():
    return {sender: cases(SEED, sender) for sender in ("truthful", "repeat")}


@pytest.fixture(autouse=True)
def reuse_native_census(generated, monkeypatch):
    def native(seed, sender):
        assert seed == SEED
        return generated[sender]

    monkeypatch.setattr(cli, "cases", native)


def _args(command, sender="truthful"):
    return [command, "--seed", str(SEED), "--sender", sender]


def _export(path: Path, capsys):
    assert cli.main(_args("export") + ["--out", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    return result, json.loads(path.read_bytes())


def test_native_reports_expectations_and_counts(capsys):
    for sender, expected in (("truthful", "yes"), ("repeat", "no")):
        args = _args("native", sender) + ["--expect-retention", expected]
        assert cli.main(args) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["qualification"]["retention_pattern"] is (expected == "yes")
        assert result["native_grades"] == 12
        assert result["model_calls"] == result["trials"] == 0
        args[-1] = "no" if expected == "yes" else "yes"
        assert cli.main(args) == 1
        assert json.loads(capsys.readouterr().out) == result


def test_packet_is_exact_next_choice_bytes(generated, capsysbinary):
    for case in generated["truthful"]:
        for policy in POLICIES:
            assert (
                cli.main(_args("packet") + ["--world", case.name, "--policy", policy])
                == 0
            )
            output = capsysbinary.readouterr()
            assert output.out == packet(case, policy, SELECTOR) + b"\n"
            assert output.err == b""


def test_grade_reads_only_a_known_slot_and_returns_native_score(
    generated, tmp_path, capsys
):
    answer = tmp_path / "answer.json"
    for case in generated["truthful"]:
        args = _args("grade") + ["--world", case.name, "--answer", str(answer)]
        for slot, score in case.scores.items():
            answer.write_text(json.dumps({"slot": slot}))
            assert cli.main(args) == 0
            result = json.loads(capsys.readouterr().out)
            assert result["score"] == score
            assert result["model_calls"] == result["trials"] == 0
        for raw in (
            b"{}",
            json.dumps({"slot": PRIVATE}).encode(),
            b'{"slot":null}',
            b'{"slot":"A","slot":"B"}',
            b'{"slot":"A","extra":0}',
            b'{"slot":NaN}',
            b'{"slot":1e999}',
        ):
            answer.write_bytes(raw)
            assert cli.main(args) == 2
            output = capsys.readouterr()
            assert output.out == ""
            assert output.err.startswith("receiver-memory: ")
            assert PRIVATE not in output.err


def test_export_is_private_no_clobber_and_inspect_round_trips(
    generated, tmp_path, capsys
):
    path = tmp_path / "cases.json"
    report, record = _export(path, capsys)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert record == json.loads(
        json.dumps(
            {
                "schema": "seatlab.receiver-memory.cases/v1",
                "scale": 1,
                "selector": asdict(SELECTOR),
                "cases": [asdict(case) for case in generated["truthful"]],
            }
        )
    )
    original = path.read_bytes()
    link = tmp_path / "link.json"
    link.symlink_to(path)
    for target in (path, link):
        assert cli.main(_args("export") + ["--out", str(target)]) == 2
        assert capsys.readouterr().out == ""
        assert path.read_bytes() == original
    args = ["inspect", str(path), "--expect-retention", "yes"]
    assert cli.main(args) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["qualification"] == report["qualification"]
    assert (
        inspected["native_grades"]
        == inspected["model_calls"]
        == inspected["trials"]
        == 0
    )
    args[-1] = "no"
    assert cli.main(args) == 1
    capsys.readouterr()
    for row in record["cases"]:
        row["instruction"] += PRIVATE
    path.write_text(json.dumps(record))
    assert cli.main(["inspect", str(path)]) == 0
    output = capsys.readouterr()
    assert PRIVATE not in output.out
    assert output.err == ""


def test_inspect_rejects_schema_and_nested_json_errors(tmp_path, capsys):
    path = tmp_path / "cases.json"
    _, record = _export(path, capsys)
    record["cases"][0]["history"][0]["extra"] = PRIVATE
    bad_frame = json.dumps(record).encode()
    del record["cases"][0]["history"][0]["extra"]
    valid = json.dumps(record).encode()
    invalid = (
        (bad_frame, "invalid frame fields"),
        (
            valid.replace(b'"scale": 1', b'"scale": 1, "scale": 1'),
            "duplicate JSON member",
        ),
        (valid.replace(b'"scale": 1', b'"scale": Infinity'), "nonfinite JSON number"),
        (valid.replace(b'"scale": 1', b'"scale": 1e999'), "nonfinite JSON number"),
        (b'{"private":"' + PRIVATE.encode() + b'"}', "invalid cases schema"),
        (b"{\xff" + PRIVATE.encode(), "invalid JSON input"),
    )
    for raw, reason in invalid:
        path.write_bytes(raw)
        assert cli.main(["inspect", str(path)]) == 2
        output = capsys.readouterr()
        assert output.out == ""
        assert output.err == f"receiver-memory: {reason}\n"
        assert PRIVATE not in output.err


def test_read_requires_bounded_regular_file(tmp_path, capsys):
    path = tmp_path / "too-large.json"
    with path.open("wb") as handle:
        handle.truncate(cli.LIMIT + 1)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    for source in (path, link, fifo, tmp_path, "-"):
        assert cli.main(["inspect", str(source)]) == 2
        result = capsys.readouterr()
        assert result.out == ""
        assert result.err.startswith("receiver-memory: ")
        if source != "-":
            assert str(source) not in result.err
