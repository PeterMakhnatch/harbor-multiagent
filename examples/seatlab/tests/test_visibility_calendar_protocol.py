import json
from collections import Counter
from pathlib import Path

import pytest

from examples.seatlab.visibility.calendar_packet import PACKET, PACKET_SHA256, load
from examples.seatlab.visibility.calendar_protocol import (
    Block,
    answer,
    blocks,
    decision_prompt,
    decode_list,
    decode_slot,
    feedback_wire,
    initial_prompt,
    main,
    recheck_prompt,
)

_TABLE = ((129, 0, 2, 4, 8), (16, 32, 64, 128, 1), (0, 3, 12, 48, 192))
_TABLE_JSON = json.dumps(_TABLE)


def test_registered_order_roles_and_counterbalance() -> None:
    plan = blocks()
    assert len(plan) == len({block.id for block in plan}) == 48
    assert [(block.world, block.direction) for block in plan[:24]] == [
        (world, direction) for world in range(12) for direction in (0, 1)
    ]
    for first, second in zip(plan[:24], plan[24:], strict=True):
        assert first.repetition == 0 and second.repetition == 1
        assert (first.world, first.direction) == (second.world, second.direction)
        assert first.arms == second.arms[::-1]
    assert [block.id for block in plan[23:26]] == [
        "cf-w11-d1-r0",
        "cf-w00-d0-r1",
        "cf-w00-d1-r1",
    ]
    assert (plan[0].sender, plan[0].receiver) == ("p1", "p2")
    assert (plan[1].sender, plan[1].receiver) == ("p2", "p1")
    assert plan[0].arms == ("blind", "peer")
    assert plan[1].arms == plan[2].arms == ("peer", "blind")
    assert plan[3].arms == ("blind", "peer")
    assert plan[-1] == Block(11, 1, 1)
    assert Counter(block.arms[0] for block in plan) == {"blind": 24, "peer": 24}


def test_all_roles_see_the_same_public_rules_and_only_the_local_calendar() -> None:
    views = (
        initial_prompt(_TABLE),
        recheck_prompt(_TABLE, (2, 19), None),
        recheck_prompt(_TABLE, (2, 19), (4, 34)),
        decision_prompt(_TABLE, (2, 19), (4, 34)),
    )
    parts = [view.split("\n\nTask:\n", 1) for view in views]
    assert len({part[0] for part in parts}) == 1
    public = parts[0][0]
    initial, blind, peer, final = [part[1] for part in parts]
    rules, calendar = public.split("\nYour calendar: ")
    grid = rules.split("Slot IDs by weekday and start time:\n")[1].splitlines()
    assert [row.split() for row in grid] == [
        ["Day", "09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00"],
        ["Mon", "0", "1", "2", "3", "4", "5", "6"],
        ["Tue", "7", "8", "9", "10", "11", "12", "13"],
        ["Wed", "14", "15", "16", "17", "18", "19", "20"],
        ["Thu", "21", "22", "23", "24", "25", "26", "27"],
        ["Fri", "28", "29", "30", "31", "32", "33", "34"],
    ]
    assert calendar.splitlines()[1:] == [
        "Mon: 09:00, 16:00",
        "Tue: -",
        "Wed: 10:00",
        "Thu: 11:00",
        "Fri: 12:00",
        "",
        "Mon: 13:00",
        "Tue: 14:00",
        "Wed: 15:00",
        "Thu: 16:00",
        "Fri: 09:00",
        "",
        "Mon: -",
        "Tue: 09:00, 10:00",
        "Wed: 11:00, 12:00",
        "Thu: 13:00, 14:00",
        "Fri: 15:00, 16:00",
    ]
    assert "Find every start that fits your own calendar." in initial
    assert "candidate list:" not in initial
    assert blind.splitlines()[1] == 'Receiver message: {"ready":true}'
    assert peer.splitlines()[1] == 'Receiver message: {"starts":[4,34]}'
    assert blind == peer.replace('{"starts":[4,34]}', '{"ready":true}')
    assert "every start that fits your seat, not just the overlap" in peer
    assert final.splitlines()[:2] == [
        'Your initial candidate list: {"starts":[2,19]}',
        'Sender\'s revised candidate list: {"starts":[4,34]}',
    ]
    assert "even if you are uncertain" in final
    for view in views:
        assert not any(
            word in view for word in ("cf-w", "blind", "peer", "checksum", "proof")
        )


def test_final_depends_on_delivered_values_not_message_format() -> None:
    own = (2, 19)
    compact = decode_list('{"starts":[4,34]}')
    spaced = decode_list(' { "starts" : [4, 34] }\n')
    assert decision_prompt(_TABLE, own, compact) == decision_prompt(_TABLE, own, spaced)
    assert decision_prompt(_TABLE, own, compact) != decision_prompt(_TABLE, own, ())


@pytest.mark.parametrize(
    "text",
    [
        '```json\n{"starts":[0]}\n```',
        '{"starts":[0]} {"starts":[1]}',
        '{"starts":[0],"starts":[1]}',
        '{"starts":[],"other":0}',
        "{}",
        "[]",
        '{"starts":null}',
        '{"starts":0}',
        '{"starts":[false]}',
        '{"starts":[0.0]}',
        '{"starts":[NaN]}',
        '{"starts":["0"]}',
        '{"starts":[-1]}',
        '{"starts":[35]}',
        '{"starts":[1,0]}',
        '{"starts":[1,1]}',
    ],
)
def test_decode_list_rejects_invalid_actions(text: str) -> None:
    with pytest.raises(ValueError):
        decode_list(text)


@pytest.mark.parametrize(
    "text",
    [
        "0",
        '{"slot":0} done',
        '{"slot":0,"slot":0}',
        '{"slot":0,"start":9}',
        '{"slot":true}',
        '{"slot":1.0}',
        '{"slot":"1"}',
        '{"slot":-1}',
        '{"slot":35}',
    ],
)
def test_decode_slot_rejects_invalid_actions(text: str) -> None:
    with pytest.raises(ValueError):
        decode_slot(text)


def test_feedback_wire_and_native_answer_boundaries() -> None:
    assert decode_list(' { "starts": [0, 12, 34] }\n') == (0, 12, 34)
    assert decode_list('{"starts":[]}') == ()
    assert feedback_wire(None) == '{"ready":true}'
    assert feedback_wire(()) == '{"starts":[]}'
    assert feedback_wire((0, 12, 34)) == '{"starts":[0,12,34]}'
    with pytest.raises(ValueError):
        feedback_wire((12, 0))
    assert answer(decode_slot('{"slot":0}')) == {"day": "Mon", "start": "09:00"}
    assert answer(6) == {"day": "Mon", "start": "15:00"}
    assert answer(7) == {"day": "Tue", "start": "09:00"}
    assert answer(decode_slot(' { "slot": 34 }\n')) == {"day": "Fri", "start": "15:00"}
    with pytest.raises(ValueError):
        answer(True)


def test_cli_reproduces_api_prompts_from_inline_and_saved_inputs(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    table = tmp_path / "table.json"
    table.write_text(_TABLE_JSON, encoding="utf-8")
    own = tmp_path / "own.json"
    own.write_text(' { "starts": [2, 19] }\n', encoding="utf-8")
    delivered = tmp_path / "delivery.json"
    delivered.write_text('{"starts":[4,34]}\n', encoding="utf-8")
    main(["prompt", "initial", "--table", f"@{table}"])
    assert capsys.readouterr().out == initial_prompt(_TABLE)
    recheck = [
        "prompt",
        "recheck",
        "--table",
        _TABLE_JSON,
        "--own",
        f"@{own}",
        "--peer",
    ]
    main([*recheck, '{"ready":true}'])
    assert capsys.readouterr().out == recheck_prompt(_TABLE, (2, 19), None)
    main([*recheck, f"@{delivered}"])
    assert capsys.readouterr().out == recheck_prompt(_TABLE, (2, 19), (4, 34))
    main(
        [
            "prompt",
            "final",
            "--table",
            f"@{table}",
            "--own",
            f"@{own}",
            "--sender",
            f"@{delivered}",
        ]
    )
    assert capsys.readouterr().out == decision_prompt(_TABLE, (2, 19), (4, 34))
    main(["decode", "list", f"@{own}"])
    assert capsys.readouterr().out == '{"starts":[2,19]}\n'
    main(["decode", "slot", '{"slot":34}'])
    assert capsys.readouterr().out == '{"day":"Fri","start":"15:00"}\n'


def test_cli_selects_only_the_requested_seat_from_the_pinned_packet(
    capsys: pytest.CaptureFixture[str],
) -> None:
    p1, p2 = load(PACKET, PACKET_SHA256)[0].tables
    for seat, table in (("p1", p1), ("p2", p2)):
        main(["prompt", "initial", "--world", "0", "--seat", seat])
        assert capsys.readouterr().out == initial_prompt(table)


def test_cli_plan(capsys: pytest.CaptureFixture[str]) -> None:
    main(["plan"])
    result = json.loads(capsys.readouterr().out)
    assert len(result) == 48
    assert result[0] == {
        "id": "cf-w00-d0-r0",
        "sender": "p1",
        "receiver": "p2",
        "arms": ["blind", "peer"],
    }
    assert result[-1]["id"] == "cf-w11-d1-r1"


@pytest.mark.parametrize(
    "args",
    [
        ["prompt", "initial", "--table", "[]"],
        ["prompt", "initial", "--table", _TABLE_JSON.replace("129", "true")],
        ["prompt", "initial", "--table", _TABLE_JSON.replace("129", "256")],
        ["prompt", "recheck", "--table", _TABLE_JSON, "--own", '{"starts":[]}'],
        [
            "prompt",
            "recheck",
            "--table",
            _TABLE_JSON,
            "--own",
            '{"starts":[]}',
            "--peer",
            '{"ready":1}',
        ],
        [
            "prompt",
            "recheck",
            "--table",
            _TABLE_JSON,
            "--own",
            '{"starts":[]}',
            "--peer",
            '{"ready":true,"ready":true}',
        ],
        [
            "prompt",
            "final",
            "--table",
            _TABLE_JSON,
            "--own",
            '{"starts":[]}',
            "--sender",
            '{"ready":true}',
        ],
        ["decode", "slot", '{"slot":true}'],
    ],
)
def test_cli_does_not_repair_or_infer_inputs(args: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
