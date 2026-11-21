import json
from collections.abc import Callable
from contextlib import redirect_stderr
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override
from unittest import TestCase
from unittest.mock import patch

from examples.seatlab.visibility import (
    calendar_client,
    calendar_list_run,
    calendar_packet,
    calendar_protocol,
    calendar_tools_run,
)

_STARTS = ((7, 8, 9, 26), (16, 26, 28, 29))
_SEEDS = ((7, 26), (26, 29))
_PICK = '{"tool":"local_starts"}'
_FALSE_PICK = '{"tool":"local_starts","role":"sender"}'
_MARKERS = {"sender": " \n", "receiver": "\t"}


@dataclass(frozen=True, slots=True)
class _Action:
    identifier: str
    seat: int
    base: str
    response: str


def _plan(block: calendar_protocol.Block) -> list[_Action]:
    tables = calendar_packet.load(
        calendar_packet.PACKET, calendar_packet.PACKET_SHA256
    )[block.world].tables
    sender, receiver = block.direction, 1 - block.direction
    actions = [
        (
            seat,
            calendar_protocol.initial_prompt(table),
            json.dumps({"starts": _SEEDS[seat]}),
        )
        for seat, table in enumerate(tables)
    ]
    for room in block.arms:
        revised = (26,) if room == "peer" else _STARTS[sender]
        actions.extend(
            (
                (
                    sender,
                    calendar_protocol.recheck_prompt(
                        tables[sender],
                        _SEEDS[sender],
                        _SEEDS[receiver] if room == "peer" else None,
                    ),
                    json.dumps({"starts": revised}),
                ),
                (
                    receiver,
                    calendar_protocol.decision_prompt(
                        tables[receiver], _SEEDS[receiver], revised
                    ),
                    '{"slot":26}',
                ),
            )
        )
    return [
        _Action(identifier, seat, base, response)
        for identifier, (seat, base, response) in zip(
            calendar_client._request_plan(block), actions, strict=True
        )
    ]


class _Frames(BytesIO):
    @override
    def read(self, size: int | None = -1, /) -> bytes:
        return self.readline(-1 if size is None else size)


def _frame(value: dict[str, str]) -> bytes:
    return (json.dumps(value) + "\n").encode()


class _Bridge:
    def __init__(self, actions: list[_Action]) -> None:
        self.actions = actions
        self.outputs: list[Path] = []
        self.replies: list[list[tuple[str, str]]] = []

    def __call__(
        self,
        _tasks: Path,
        out: Path,
        block: calendar_protocol.Block,
        respond: Callable[[str, str], str],
    ) -> str:
        self.outputs.append(out)
        out.mkdir(mode=0o700)
        requests = b"".join(
            _frame({"type": "turn", "id": action.identifier, "prompt": action.base})
            for action in self.actions
        )
        end = _frame({"type": "end", "status": "complete"})
        with _Frames(requests + end) as source, BytesIO() as sink:
            status = calendar_client._exchange(
                source, sink, calendar_client._request_plan(block), respond
            )
            records = [json.loads(line) for line in sink.getvalue().splitlines()]
            self.replies.append([(record["id"], record["text"]) for record in records])
        return status


class TestCalendarRoleSeats(TestCase):
    @override
    def setUp(self) -> None:
        self.root = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.study = self.root / "study"
        self.study.mkdir(mode=0o700)
        self.tasks = self.root / "tasks"

    def test_both_directions_route_openings_and_both_rooms_by_seat(self) -> None:
        for direction in (0, 1):
            with self.subTest(direction=direction):
                self._simulate(calendar_protocol.Block(0, direction, 0))

    def test_selection_role_claim_does_not_redirect_the_final_callback(self) -> None:
        self._simulate(calendar_protocol.Block(0, 1, 1), forge=True)

    def _simulate(self, block: calendar_protocol.Block, *, forge: bool = False) -> None:
        actions = _plan(block)
        bridge = _Bridge(actions)
        false_pick_id = (
            next(
                action.identifier
                for action in actions
                if action.identifier.endswith("-peer:cal_decide")
            )
            if forge
            else None
        )
        calls: list[tuple[str, str]] = []

        def reply(role: str, prompt: str) -> str:
            index, final = divmod(len(calls), 2)
            calls.append((role, prompt))
            action = actions[index % len(actions)]
            if final:
                return action.response + _MARKERS[role]
            return _FALSE_PICK if action.identifier == false_pick_id else _PICK

        def sender(prompt: str) -> str:
            return reply("sender", prompt)

        def receiver(prompt: str) -> str:
            return reply("receiver", prompt)

        with patch.object(calendar_client, "_drive", bridge):
            statuses = calendar_tools_run.play_roles(
                self.tasks, self.study, block, sender=sender, receiver=receiver
            )

        ordered = ("sender", "receiver")
        if (block.world // 2 + block.direction + block.repetition) % 2:
            ordered = ("receiver", "sender")
        self.assertEqual(
            list(statuses.items()), [(policy, "complete") for policy in ordered]
        )
        self.assertEqual(len(calls), len(ordered) * len(actions) * 2)
        for index, (policy, out, replies) in enumerate(
            zip(ordered, bridge.outputs, bridge.replies, strict=True)
        ):
            self.assertEqual(out.parent.name, policy)
            records = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in (out.parent / "actions").glob("*.json")
            ]
            self.assertEqual(len(records), len(actions))
            receipts = {record["native_id"]: record for record in records}
            self.assertEqual(set(receipts), {action.identifier for action in actions})
            expected_replies = []
            for offset, action in enumerate(actions):
                with self.subTest(policy=policy, action=action.identifier):
                    role = "sender" if action.seat == block.direction else "receiver"
                    start = 2 * (index * len(actions) + offset)
                    pick, final = calls[start : start + 2]
                    text = action.response + _MARKERS[role]
                    expected_replies.append((action.identifier, text))
                    result, selection = self._expected_tool(
                        action, role, policy, false_pick_id
                    )
                    self._assert_turn(action, role, pick, final, result)
                    receipt = receipts[action.identifier]
                    self.assertEqual(receipt["seat"], f"p{action.seat + 1}")
                    self.assertEqual(receipt["select_text"], selection)
                    self.assertEqual(receipt["tool_status"], result["status"])
                    digest = sha256(text.encode()).hexdigest()
                    self.assertEqual(receipt["final_response_sha256"], digest)
            self.assertEqual(replies, expected_replies)

    @staticmethod
    def _expected_tool(
        action: _Action, role: str, policy: str, false_pick_id: str | None
    ) -> tuple[dict[str, object], str]:
        if action.identifier == false_pick_id:
            return {"status": "invalid"}, _FALSE_PICK
        if role != policy:
            return {"status": "unavailable"}, _PICK
        return {"status": "returned", "starts": list(_STARTS[action.seat])}, _PICK

    def _assert_turn(
        self,
        action: _Action,
        role: str,
        pick: tuple[str, str],
        final: tuple[str, str],
        result: dict[str, object],
    ) -> None:
        self.assertEqual(pick[0], role)
        self.assertIn(action.base, pick[1])
        self.assertIn(_PICK, pick[1])
        self.assertEqual(final[0], role)
        header, _, base = final[1].partition("\n\n")
        label, _, payload = header.partition("\n")
        self.assertEqual(label, "Local operation result (JSON):")
        self.assertEqual(base, action.base)
        self.assertEqual(json.loads(payload), result)

    def test_existing_disk_claim_rejects_without_transport_or_callbacks(self) -> None:
        block = calendar_protocol.Block(0, 0, 0)
        pair = self.study / "rx-w00-d0-r0"
        pair.mkdir(mode=0o700)
        calls: list[tuple[str, str]] = []

        def sender(prompt: str) -> str:
            calls.append(("sender", prompt))
            return _PICK

        def receiver(prompt: str) -> str:
            calls.append(("receiver", prompt))
            return _PICK

        with (
            patch.object(calendar_client, "_drive") as drive,
            self.assertRaisesRegex(
                calendar_list_run.CaptureError, "attempt_already_exists"
            ),
        ):
            calendar_tools_run.play_roles(
                self.tasks, self.study, block, sender=sender, receiver=receiver
            )
        drive.assert_not_called()
        self.assertEqual(calls, [])
        self.assertEqual(list(pair.iterdir()), [])

    def test_commands_rejects_missing_and_empty_config_before_claim_or_dispatch(
        self,
    ) -> None:
        args = [
            "commands",
            "--tasks",
            str(self.tasks),
            "--study",
            str(self.study),
            "--world",
            "0",
            "--direction",
            "0",
            "--repetition",
            "0",
        ]
        empty = self.root / "empty.json"
        empty.write_text("", encoding="utf-8")
        empty_object = self.root / "empty-object.json"
        empty_object.write_text("{}", encoding="utf-8")
        with (
            patch.object(calendar_tools_run, "play_roles") as play,
            patch.object(calendar_client, "_drive") as drive,
            patch("subprocess.Popen") as launch,
            redirect_stderr(StringIO()),
        ):
            with self.assertRaises(SystemExit) as missing_option:
                calendar_tools_run.main(args)
            self.assertEqual(missing_option.exception.code, 2)
            for config in (self.root / "missing.json", empty, empty_object):
                with self.subTest(config=config.name):
                    self.assertEqual(
                        calendar_tools_run.main([*args, "--config", str(config)]), 1
                    )
        play.assert_not_called()
        drive.assert_not_called()
        launch.assert_not_called()
        self.assertEqual(list(self.study.iterdir()), [])
