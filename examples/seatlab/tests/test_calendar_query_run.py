import json
import stat
from collections.abc import Callable
from contextlib import redirect_stderr
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import override
from unittest import TestCase
from unittest.mock import patch

from examples.seatlab.visibility import (
    calendar_client,
    calendar_list_run,
    calendar_packet,
    calendar_protocol,
    calendar_query_run,
)

_TABLES = (((1, 0, 0, 0, 0),), ((0, 2, 0, 0, 0),))
_SEEDS = ((5, 8), (5, 9))


class _Frames(BytesIO):
    @override
    def read(self, size: int | None = -1, /) -> bytes:
        return self.readline(-1 if size is None else size)


def _digest(text: str) -> str:
    return sha256(text.encode()).hexdigest()


def _list(starts: tuple[int, ...]) -> str:
    return ' { "starts" : ' + json.dumps(starts) + " }\t\n"


def _exchange(
    block: calendar_protocol.Block,
    turn: Callable[[str, str], str],
    requests: list[tuple[str, str]],
    *,
    status: str = "complete",
) -> tuple[str, list[dict[str, str]]]:
    frames = [
        {"type": "turn", "id": identifier, "prompt": base}
        for identifier, base in requests
    ]
    frames.append({"type": "end", "status": status})
    data = "".join(json.dumps(frame) + "\n" for frame in frames).encode()
    with _Frames(data) as source, BytesIO() as sink:
        result = calendar_client._exchange(
            source, sink, calendar_client._request_plan(block), turn
        )
        return result, [json.loads(line) for line in sink.getvalue().splitlines()]


def _plan(
    run: str, direction: int, rooms: tuple[str, str]
) -> list[tuple[str, int, str, str]]:
    peer = 1 - direction
    turns = [
        (
            f"{run}:cal_initial_p{seat + 1}",
            seat,
            calendar_protocol.initial_prompt(table),
            _list(_SEEDS[seat]),
        )
        for seat, table in enumerate(_TABLES)
    ]
    for room in rooms:
        revised = (5,) if room == "peer" else (5, 10, 11)
        suggestions = _SEEDS[peer] if room == "peer" else None
        turns.append(
            (
                f"{run}-{room}:cal_recheck",
                direction,
                calendar_protocol.recheck_prompt(
                    _TABLES[direction], _SEEDS[direction], suggestions
                ),
                _list(revised),
            )
        )
        turns.append(
            (
                f"{run}-{room}:cal_decide",
                peer,
                calendar_protocol.decision_prompt(_TABLES[peer], _SEEDS[peer], revised),
                ' { "slot" : 5 }\n',
            )
        )
    return turns


class TestCalendarQueryRun(TestCase):
    @override
    def setUp(self) -> None:
        self.study = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.tasks = self.study / "tasks"
        self.enterContext(
            patch.object(
                calendar_packet,
                "load",
                return_value={1: SimpleNamespace(tables=_TABLES)},
            )
        )

    def test_schedule_has_only_the_registered_first_repetitions(self) -> None:
        self.assertEqual(
            [(b.world, b.direction, b.repetition) for b in calendar_query_run.blocks()],
            [(w, d, 0) for w in (1, 2, 3, 5, 6, 7, 9, 10, 11) for d in (0, 1)],
        )

    def test_native_pair_uses_disjoint_claims_and_exact_seat_prompts(self) -> None:
        suffixes: dict[str, set[str]] = {"initial": set(), "recheck": set()}
        for direction, rooms in ((0, ("peer", "blind")), (1, ("blind", "peer"))):
            with self.subTest(direction=direction):
                self._simulate_pair(direction, rooms, suffixes)
        self.assertEqual(
            {path.name for path in self.study.iterdir()},
            {"rx-w01-d0-r0", "rx-w01-d1-r0"},
        )
        self.assertEqual(len(suffixes["initial"]), 1)
        self.assertEqual(len(suffixes["recheck"]), 1)

    def _simulate_pair(
        self, direction: int, rooms: tuple[str, str], suffixes: dict[str, set[str]]
    ) -> None:
        block = calendar_protocol.Block(1, direction, 0)
        run = f"rx-w01-d{direction}-r0"
        actions = self.study / run / "actions"
        expected = _plan(run, direction, rooms)
        calls: list[tuple[Path, dict[str, str]]] = []

        def reply(role: str, prompt: str) -> str:
            identifier, seat, base, text = expected[len(calls)]
            self.assertEqual(role, "sender" if seat == direction else "receiver")
            native_role = identifier.split(":", 1)[1]
            self.assertEqual(prompt, calendar_query_run.model_prompt(native_role, base))
            if native_role == "cal_decide":
                self.assertEqual(prompt, base)
            else:
                self.assertTrue(prompt.startswith(base))
                suffix = prompt[len(base) :]
                self.assertTrue(suffix.strip())
                stage = "recheck" if native_role == "cal_recheck" else "initial"
                suffixes[stage].add(suffix)
            receipt = actions / (identifier.replace(":", "_") + ".json")
            self.assertEqual(receipt.read_bytes(), b"")
            calls.append(
                (
                    receipt,
                    {
                        "schema": "seatlab.calendar-query-turn/v1",
                        "id": identifier,
                        "seat": f"p{seat + 1}",
                        "role": role,
                        "base_sha256": _digest(base),
                        "prompt_sha256": _digest(prompt),
                        "response_sha256": _digest(text),
                    },
                )
            )
            return text

        def drive(
            tasks: Path,
            out: Path,
            native_block: calendar_protocol.Block,
            turn: Callable[[str, str], str],
        ) -> str:
            self.assertEqual(
                (tasks, out, native_block),
                (self.tasks, self.study / run / "block", block),
            )
            self.assertFalse(out.exists())
            out.mkdir(mode=0o700)
            requests = [(identifier, base) for identifier, _, base, _ in expected]
            status, replies = _exchange(block, turn, requests)
            self.assertEqual(
                replies,
                [
                    {"id": identifier, "text": text}
                    for identifier, _, _, text in expected
                ],
            )
            return status

        def play() -> str:
            return calendar_query_run.play(
                self.tasks,
                self.study,
                block,
                sender=lambda prompt: reply("sender", prompt),
                receiver=lambda prompt: reply("receiver", prompt),
            )

        with patch.object(calendar_client, "_drive", side_effect=drive) as native:
            self.assertEqual(play(), "complete")
            with self.assertRaisesRegex(
                calendar_list_run.CaptureError, "attempt_already_exists"
            ):
                play()
            self.assertEqual(native.call_count, 1)
        self.assertEqual(set(actions.iterdir()), {path for path, _ in calls})
        for path, receipt in calls:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(json.loads(path.read_text()), receipt)

    def test_invalid_seed_reaches_native_unchanged(self) -> None:
        block = calendar_protocol.Block(1, 0, 0)
        seeds = ("rx-w01-d0-r0:cal_initial_p1", "rx-w01-d0-r0:cal_initial_p2")
        requests = [
            (seed, calendar_protocol.initial_prompt(table))
            for seed, table in zip(seeds, _TABLES, strict=True)
        ]
        invalid = ' { "starts" : [9, 5] } \n'
        valid = _list(_SEEDS[1])

        def drive(
            _tasks: Path,
            _out: Path,
            native_block: calendar_protocol.Block,
            turn: Callable[[str, str], str],
        ) -> str:
            status, replies = _exchange(
                native_block, turn, requests, status="invalid_seed"
            )
            self.assertEqual(
                replies,
                [{"id": seeds[0], "text": invalid}, {"id": seeds[1], "text": valid}],
            )
            return status

        with patch.object(calendar_client, "_drive", side_effect=drive):
            status = calendar_query_run.play(
                self.tasks,
                self.study,
                block,
                sender=lambda _: invalid,
                receiver=lambda _: valid,
            )
        self.assertEqual(status, "invalid_seed")

    def test_cli_callback_failure_leaves_only_empty_claim(self) -> None:
        identifier = "rx-w01-d0-r0:cal_initial_p1"
        actions = self.study / "rx-w01-d0-r0" / "actions"
        receipt = actions / "rx-w01-d0-r0_cal_initial_p1.json"
        base = calendar_protocol.initial_prompt(_TABLES[0])
        calls: list[str] = []

        def sender(prompt: str) -> str:
            self.assertEqual(receipt.read_bytes(), b"")
            calls.append(prompt)
            raise TimeoutError("private provider detail")

        def receiver(_prompt: str) -> str:
            self.fail("receiver called after sender failure")

        def drive(
            _tasks: Path,
            _out: Path,
            block: calendar_protocol.Block,
            turn: Callable[[str, str], str],
        ) -> str:
            return _exchange(block, turn, [(identifier, base)])[0]

        config = self.study / "commands.json"
        argv = [
            "--tasks",
            str(self.tasks),
            "--study",
            str(self.study),
            "--commands",
            str(config),
            "--world",
            "1",
            "--direction",
            "0",
        ]
        with (
            patch.object(calendar_client, "_drive", side_effect=drive) as native,
            patch(
                "examples.seatlab.visibility.seat_command.load_commands",
                return_value=(sender, receiver),
            ) as load,
            redirect_stderr(StringIO()) as stderr,
        ):
            self.assertEqual(calendar_query_run.main(argv), 1)
            load.assert_called_once_with(config, roles=("sender", "receiver"))
            self.assertEqual(calendar_query_run.main(argv), 1)
            self.assertEqual(native.call_count, 1)
        self.assertIn("model_callback_failed", stderr.getvalue())
        self.assertIn("attempt_already_exists", stderr.getvalue())
        self.assertNotIn("private provider detail", stderr.getvalue())
        self.assertEqual(
            calls, [calendar_query_run.model_prompt("cal_initial_p1", base)]
        )
        self.assertEqual(list(actions.iterdir()), [receipt])
        self.assertEqual(receipt.read_bytes(), b"")
