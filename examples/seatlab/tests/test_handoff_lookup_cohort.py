import json
from hashlib import sha256

import pytest

from examples.seatlab.handoff import lookup_cohort, packet
from examples.seatlab.handoff.episode import episode

_NOTE = '{"note":"Read the current record."}'
_READ_FIRST = '{"read":"F"}'
_READ_REVISED = '{"read":"R"}'
_READ_OTHER = '{"read":"T"}'
_OLD = '{"choices":{"F":"A","R":"A","T":"A"}}'
_NEW = '{"choices":{"F":"A","R":"B","T":"A"}}'


def _record(record_id, *, revised, sensitive):
    return {
        "id": record_id,
        "old_version": "1",
        "new_version": "2" if revised else "1",
        "before": "Before",
        "after": "After" if revised else "Before",
        "question": "Which choice?",
        "old_best": "A",
        "best": "B" if revised and sensitive else "A",
    }


def _packet(*, first_case_revision="R"):
    cases = [
        ("alpha", first_case_revision, True),
        ("alpha", "R", False),
        ("beta", "R", True),
    ]
    return json.dumps(
        {
            "schema": "seatlab.handoff-input/v1",
            "brief": "Answer from current records.",
            "choices": ["A", "B"],
            "cases": [
                {
                    "id": f"case-{i}",
                    "cluster": world,
                    "records": [
                        _record(name, revised=name == revised, sensitive=sensitive)
                        for name in ("F", "R", "T")
                    ],
                }
                for i, (world, revised, sensitive) in enumerate(cases)
            ],
        }
    ).encode()


def _row(block, replies=None):
    turns = []
    halted = False

    def ask(turn):
        nonlocal halted
        if halted or replies is None or turn.stage not in replies:
            halted = True
            return None
        reply = replies[turn.stage]
        halted = reply is None
        turns.append(
            {
                "run": f"{block.key}-{turn.stage}",
                "role": turn.role,
                "status": "callback_error" if halted else "received",
                "prompt_sha256": sha256(turn.prompt.encode()).hexdigest(),
                "response_sha256": sha256(reply.encode()).hexdigest()
                if reply is not None
                else None,
                "usage": None,
            }
        )
        return reply

    return {
        "block": block.key,
        "case": block.case.id,
        "cluster": block.case.cluster,
        "rep": block.rep,
        "fingerprint": block.fingerprint,
        "state": "unclaimed" if replies is None else "claimed",
        "outcome": episode(block, ask),
        "turns": turns,
    }


def _run(tmp_path, capsys, raw, rows, indices, *, digest=None):
    subject = tmp_path / "packet.json"
    report = tmp_path / "report.json"
    subject.write_bytes(raw)
    fingerprint = sha256(raw).hexdigest()
    report.write_text(
        json.dumps(
            {
                "schema": "seatlab.handoff-report/v1",
                "packet_sha256": fingerprint,
                "outcomes": rows,
            }
        ),
        encoding="utf-8",
    )
    code = lookup_cohort.main(
        [
            "--packet",
            str(subject),
            "--sha256",
            fingerprint if digest is None else digest,
            "--indices",
            *map(str, indices),
            "--report",
            str(report),
        ]
    )
    output = capsys.readouterr()
    if code == 0:
        assert not output.err
        return json.loads(output.out)
    assert code == 1 and not output.out and output.err
    return None


def test_lookup_cohort_weights_worlds_and_keeps_known_sides(tmp_path, capsys):
    raw = _packet()
    frames = packet.grid(packet.parse(raw))
    different = {
        "note": _NOTE,
        "bound-select": _READ_REVISED,
        "anonymous-select": _READ_FIRST,
        "bound-decide": _NEW,
        "anonymous-decide": _OLD,
    }
    shared = {
        "note": _NOTE,
        "bound-select": _READ_REVISED,
        "anonymous-select": _READ_REVISED,
        "both-decide": _OLD,
    }
    rows = [
        _row(frames[0], different),
        _row(frames[1], different | {"anonymous-select": "invalid"}),
        _row(frames[2], shared),
        _row(frames[3], {"note": "invalid"}),
        _row(
            frames[4],
            shared | {"bound-select": _READ_OTHER, "anonymous-select": _READ_OTHER},
        ),
        _row(frames[5]),
    ]
    result = _run(tmp_path, capsys, raw, rows, [0, 1, 2, 3, 4])
    assert result["packet_sha256"] == sha256(raw).hexdigest()
    assert result["indices"] == [0, 1, 2, 3, 4]
    bound, anonymous = (result["by_arm"][p] for p in ("bound", "anonymous"))
    assert bound["valid_lookups"] == 4 and anonymous["valid_lookups"] == 3
    assert bound["changed_selections"] == 3 and anonymous["changed_selections"] == 1
    assert bound["first_record_selections"] == 0
    assert anonymous["first_record_selections"] == 1
    assert anonymous["statuses"]["invalid_select"] == 1
    assert bound["statuses"]["no_note"] == 1
    assert result["shared_final_complete"] == 2

    primary = result["primary"]
    assert (primary["complete_pairs"], primary["scheduled_pairs"]) == (3, 5)
    assert primary["paired_complete_delta"] == 0.25
    assert (primary["worst_delta"], primary["best_delta"]) == (0, 0.375)
    alpha, beta = (primary["by_world"][world] for world in ("alpha", "beta"))
    assert (alpha["complete_pairs"], alpha["scheduled_pairs"]) == (2, 4)
    assert (alpha["worst_delta"], alpha["best_delta"]) == (0, 0.75)
    assert (beta["complete_pairs"], beta["tie"]) == (1, 1)

    stock = result["secondary_stock_sensitive"]
    assert stock["by_arm"] == {
        "bound": {"enrolled": 3, "observed": 3, "correct": 2},
        "anonymous": {"enrolled": 3, "observed": 2, "correct": 0},
    }
    assert (stock["complete_pairs"], stock["scheduled_pairs"]) == (2, 3)
    assert stock["paired_complete_delta"] == 0.5
    assert (stock["worst_delta"], stock["best_delta"]) == (0.25, 0.5)

    rows[2]["turns"].pop()
    assert _run(tmp_path, capsys, raw, rows, [0, 1, 2, 3, 4]) is None


def test_valid_lookup_survives_missing_final_and_empty_world(tmp_path, capsys):
    raw = _packet()
    frames = packet.grid(packet.parse(raw))
    rows = [_row(frame) for frame in frames]
    rows[0] = _row(
        frames[0],
        {
            "note": _NOTE,
            "bound-select": _READ_REVISED,
            "anonymous-select": "invalid",
            "bound-decide": None,
        },
    )
    result = _run(tmp_path, capsys, raw, rows, [0, 4])
    assert result["by_arm"]["bound"]["valid_lookups"] == 1
    assert result["by_arm"]["bound"]["statuses"]["missing_decide"] == 1
    assert result["shared_final_complete"] == 0
    primary = result["primary"]
    assert primary["enrolled_worlds"] == 2 and primary["worlds_with_pairs"] == 0
    assert primary["paired_complete_delta"] is None
    assert (primary["worst_delta"], primary["best_delta"]) == (-0.5, 1)
    stock = result["secondary_stock_sensitive"]
    assert stock["by_arm"]["bound"] == {"enrolled": 2, "observed": 0, "correct": 0}
    assert (stock["worst_delta"], stock["best_delta"]) == (-1, 1)


def test_lookup_cohort_rejects_leaks_and_nonregistered_rows(tmp_path, capsys):
    raw = _packet()
    frames = packet.grid(packet.parse(raw))
    rows = [_row(frame) for frame in frames]
    for leak in (
        _row(frames[1], {"note": None}),
        _row(frames[1], {}),
        rows[1] | {"state": "unbound"},
    ):
        assert _run(tmp_path, capsys, raw, [rows[0], leak, *rows[2:]], [0]) is None
    for corrupted in (
        rows[:-1],
        [rows[1], rows[0], *rows[2:]],
        [*rows[:-1], rows[-1] | {"cluster": "other"}],
    ):
        assert _run(tmp_path, capsys, raw, corrupted, [0]) is None
    for indices in ([1, 0], [0, 0], [-1], [len(rows)]):
        assert _run(tmp_path, capsys, raw, rows, indices) is None
    assert _run(tmp_path, capsys, raw, rows, [0], digest="0" * 64) is None


@pytest.mark.parametrize("revision", [None, "F"])
def test_lookup_cohort_rejects_ineligible_selected_cases(tmp_path, capsys, revision):
    raw = _packet(first_case_revision=revision)
    rows = [_row(frame) for frame in packet.grid(packet.parse(raw))]
    assert _run(tmp_path, capsys, raw, rows, [0]) is None
    result = _run(tmp_path, capsys, raw, rows, [2])
    assert result["primary"]["scheduled_pairs"] == 1
    assert result["secondary_stock_sensitive"]["scheduled_pairs"] == 0
