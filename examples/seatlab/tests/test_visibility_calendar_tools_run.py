import json
import stat
from collections.abc import Callable
from contextlib import redirect_stderr
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
    deepseek,
)

_BLOCK = calendar_protocol.Block(0, 0, 0)
_RUN = "rx-w00-d0-r0"
_SEED = "tl-w00-d0-r0-off-seed1"
_PICK = '{"tool":"local_starts"}'
_LIST = ' { "starts" : [] }\n'
_SLOT = ' { "slot" : 0 }\n'


class _Frames(BytesIO):
    @override
    def read(self, size: int | None = -1, /) -> bytes:
        return self.readline(-1 if size is None else size)


def _frame(value: dict[str, str]) -> bytes:
    return (json.dumps(value) + "\n").encode()


def _requests(block: calendar_protocol.Block) -> list[dict[str, str]]:
    tables = calendar_packet.load(
        calendar_packet.PACKET, calendar_packet.PACKET_SHA256
    )[block.world].tables
    identifiers = iter(calendar_client._request_plan(block))
    prompts = [calendar_protocol.initial_prompt(table) for table in tables]
    sender, receiver = tables[block.direction], tables[1 - block.direction]
    for arm in block.arms:
        prompts.extend(
            (
                calendar_protocol.recheck_prompt(
                    sender, (), () if arm == "peer" else None
                ),
                calendar_protocol.decision_prompt(receiver, (), ()),
            )
        )
    return [
        {"type": "turn", "id": identifier, "prompt": prompt}
        for identifier, prompt in zip(identifiers, prompts, strict=True)
    ]


class _Bridge:
    def __init__(self, *, duplicate_first: bool = False) -> None:
        self.duplicate_first = duplicate_first
        self.outputs: list[Path] = []
        self.replies: list[list[str]] = []

    def __call__(
        self,
        _tasks: Path,
        out: Path,
        block: calendar_protocol.Block,
        respond: Callable[[str, str], str],
    ) -> str:
        self.outputs.append(out)
        out.mkdir(mode=0o700)
        requests = _requests(block)
        if self.duplicate_first:
            requests = requests[:1] * 2
        frames = b"".join(_frame(request) for request in requests)
        end = _frame({"type": "end", "status": "complete"})
        with _Frames(frames + end) as source, BytesIO() as sink:
            try:
                return calendar_client._exchange(
                    source, sink, calendar_client._request_plan(block), respond
                )
            finally:
                self.replies.append(
                    [json.loads(line)["text"] for line in sink.getvalue().splitlines()]
                )


class TestCalendarToolsRun(TestCase):
    @override
    def setUp(self) -> None:
        study = self.enterContext(TemporaryDirectory())
        self.study = Path(study).resolve()
        self.tasks = self.study / "tasks"
        self.pair = self.study / _RUN
        self.direct_args = [
            "direct",
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

    def test_pair_claim_blocks_reentry_and_keeps_policies_separate(self) -> None:
        bridge = _Bridge()
        finals = [_LIST, _LIST, _LIST, _SLOT, _LIST, _SLOT]
        prompts: list[str] = []
        first_action = self.pair / "off" / "actions" / f"{_SEED}.json"

        def respond(prompt: str) -> str:
            prompts.append(prompt)
            stage = len(prompts) - 1
            if stage == 0:
                self.assertEqual(first_action.read_bytes(), b"")
                with self.assertRaisesRegex(
                    calendar_list_run.CaptureError, "attempt_already_exists"
                ):
                    calendar_tools_run.play_pair(
                        self.tasks, self.study, _BLOCK, respond
                    )
            return _PICK if stage % 2 == 0 else finals[(stage // 2) % len(finals)]

        with patch.object(calendar_client, "_drive", bridge):
            statuses = calendar_tools_run.play_pair(
                self.tasks, self.study, _BLOCK, respond
            )
        self.assertEqual(
            list(statuses.items()), [("off", "complete"), ("on", "complete")]
        )
        self.assertEqual(
            bridge.outputs, [self.pair / policy / "block" for policy in statuses]
        )
        self.assertEqual(bridge.replies, [finals, finals])
        self.assertEqual(len(prompts), 24)
        self.assertEqual(prompts[:12:2], prompts[12::2])
        for policy in statuses:
            directory = self.pair / policy
            private_paths = (
                self.pair,
                directory,
                directory / "actions",
                directory / "meter",
            )
            for private in private_paths:
                self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o700)
            self.assertEqual(len(list((directory / "actions").iterdir())), 6)
        self.assertEqual(stat.S_IMODE(first_action.stat().st_mode), 0o600)
        receipt = json.loads(first_action.read_text())
        self.assertEqual(receipt["select_text"], _PICK)
        self.assertEqual(
            receipt["final_response_sha256"], sha256(_LIST.encode()).hexdigest()
        )
        self.assertEqual(receipt["tool_status"], "unavailable")
        on_action = self.pair / "on" / "actions" / "tl-w00-d0-r0-on-seed1.json"
        self.assertEqual(json.loads(on_action.read_text())["tool_status"], "returned")

    def test_failed_first_action_leaves_stage_cost_evidence_and_consumes_pair(
        self,
    ) -> None:
        bridge = _Bridge()
        calls: list[str] = []

        def provider(_prompt: str, *, run_id: str, role: str) -> deepseek.SeatTurn:
            calls.append(f"{run_id}:{role}")
            if role == "cal_tool_reply":
                raise deepseek.SeatUnavailable("private provider diagnostic")
            return deepseek.SeatTurn(_PICK, "deepseek-flash", 20, 3, 5)

        errors = StringIO()
        with (
            patch.object(calendar_client, "_drive", bridge),
            patch.object(calendar_client, "_bind_direct") as bind,
            patch.object(deepseek, "preflight", return_value={"balance_ok": True}),
            patch.object(deepseek, "turn", provider),
            redirect_stderr(errors),
        ):
            self.assertEqual(calendar_tools_run.main(self.direct_args), 1)
            self.assertEqual(calendar_tools_run.main(self.direct_args), 1)
        bind.assert_called_once_with()
        self.assertEqual(calls, [f"{_SEED}:cal_tool_pick", f"{_SEED}:cal_tool_reply"])
        self.assertEqual(bridge.replies, [[]])
        self.assertFalse((self.pair / "on").exists())
        self.assertIn("model_callback_failed", errors.getvalue())
        self.assertIn("attempt_already_exists", errors.getvalue())
        self.assertNotIn("private provider diagnostic", errors.getvalue())
        self.assertNotIn(str(self.study), errors.getvalue())
        policy = self.pair / "off"
        self.assertEqual((policy / "actions" / f"{_SEED}.json").read_bytes(), b"")
        for role, status in (
            ("cal_tool_pick", "success"),
            ("cal_tool_reply", "failed"),
        ):
            meter = json.loads((policy / "meter" / f"{_SEED}_{role}.json").read_text())
            self.assertEqual(meter["status"], status)
            self.assertEqual(meter["attempt"], 1)

    def test_direct_meters_both_roles_and_rejects_duplicate_request(self) -> None:
        bridge = _Bridge(duplicate_first=True)
        calls: list[tuple[str, str, str]] = []

        def provider(prompt: str, *, run_id: str, role: str) -> deepseek.SeatTurn:
            calls.append((run_id, role, prompt))
            text = _PICK if role == "cal_tool_pick" else _LIST
            return deepseek.SeatTurn(text, "deepseek-flash", 20, 3, 5)

        errors = StringIO()
        with (
            patch.object(calendar_client, "_drive", bridge),
            patch.object(calendar_client, "_bind_direct"),
            patch.object(deepseek, "preflight", return_value={"balance_ok": True}),
            patch.object(deepseek, "turn", provider),
            redirect_stderr(errors),
        ):
            self.assertEqual(calendar_tools_run.main(self.direct_args), 1)
        self.assertIn("invalid_replica_turn", errors.getvalue())
        self.assertEqual(bridge.replies, [[_LIST]])
        self.assertEqual(
            [(run, role) for run, role, _ in calls],
            [(_SEED, "cal_tool_pick"), (_SEED, "cal_tool_reply")],
        )
        policy = self.pair / "off"
        action = json.loads((policy / "actions" / f"{_SEED}.json").read_text())
        for (run, role, prompt), stage in zip(calls, ("select", "final"), strict=True):
            path = policy / "meter" / f"{run}_{role}.json"
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            meter = json.loads(path.read_text())
            self.assertEqual(meter["role"], role)
            self.assertEqual(meter["run"], run)
            self.assertEqual(meter["attempt"], 1)
            self.assertEqual(meter["status"], "success")
            self.assertEqual(
                meter["prompt_sha256"], sha256(prompt.encode()).hexdigest()
            )
            self.assertEqual(meter["prompt_sha256"], action[f"{stage}_prompt_sha256"])
            self.assertEqual(
                meter["response_sha256"], action[f"{stage}_response_sha256"]
            )
