import json
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
from unittest import TestCase
from unittest.mock import patch

from examples.seatlab.visibility import calendar_packet as packet
from examples.seatlab.visibility import calendar_protocol as native
from examples.seatlab.visibility import calendar_tools_protocol as tools

_PICK = '{"tool":"local_starts"}'
_DECLINE = '{"tool":"none"}'
_EXACT = ([7, 8, 9, 26], [16, 26, 28, 29])  # Pinned world 0, p1 and p2.


class CalendarToolProtocolTest(TestCase):
    def test_registered_policy_order_and_action_names(self) -> None:
        first: Counter[str] = Counter()
        names = set()
        for block in native.blocks():
            run = f"rx-w{block.world:02d}-d{block.direction}-r{block.repetition}"
            self.assertEqual(tools.native_id(block), run)
            order = tools.policies(block)
            parity = (block.world // 2 + block.direction + block.repetition) % 2
            expected = ("off", "on") if parity == 0 else ("on", "off")
            self.assertEqual(order, expected)
            first[order[0]] += 1
            for policy in order:
                turns = {
                    f"{run}:cal_initial_p1": "seed1",
                    f"{run}:cal_initial_p2": "seed2",
                    f"{run}-blind:cal_recheck": "blind-s",
                    f"{run}-blind:cal_decide": "blind-r",
                    f"{run}-peer:cal_recheck": "peer-s",
                    f"{run}-peer:cal_decide": "peer-r",
                }
                for identifier, suffix in turns.items():
                    name = tools.action_id(block, policy, identifier)
                    self.assertEqual(name, f"tl-{run[3:]}-{policy}-{suffix}")
                    names.add(name)
        self.assertEqual(first, {"off": 24, "on": 24})
        self.assertEqual(len(names), 576)
        with self.assertRaises(ValueError):
            tools.action_id(native.Block(0, 0, 0), "on", "rx-w00-d1-r0:cal_initial_p1")
        with self.assertRaises(ValueError):
            tools.Session(native.Block(0, -1, 0))

    def test_each_native_action_uses_only_its_seat_table_in_both_directions(
        self,
    ) -> None:
        tables = packet.load(packet.PACKET, packet.PACKET_SHA256)[0].tables
        seeds = ((7,), (16,))
        for direction, rooms in ((0, ("blind", "peer")), (1, ("peer", "blind"))):
            with self.subTest(direction=direction):
                block = native.Block(0, direction, 0)
                run = f"rx-w00-d{direction}-r0"
                session = tools.Session(block)
                for index, table in enumerate(tables):
                    self._check_turn(
                        session,
                        run,
                        f"cal_initial_p{index + 1}",
                        index,
                        table,
                        native.initial_prompt(table),
                        native.feedback_wire(seeds[index]),
                    )
                sender, receiver = direction, 1 - direction
                for room, revised in zip(rooms, ((9, 26), ()), strict=True):
                    arm = f"{run}-{room}"
                    self._check_turn(
                        session,
                        arm,
                        "cal_recheck",
                        sender,
                        tables[sender],
                        native.recheck_prompt(
                            tables[sender],
                            seeds[sender],
                            seeds[receiver] if room == "peer" else None,
                        ),
                        native.feedback_wire(revised),
                    )
                    self._check_turn(
                        session,
                        arm,
                        "cal_decide",
                        receiver,
                        tables[receiver],
                        native.decision_prompt(
                            tables[receiver], seeds[receiver], revised
                        ),
                        '{"slot":26}',
                    )

    def _check_turn(
        self,
        session: tools.Session,
        run: str,
        role: str,
        seat: int,
        table: packet.Table,
        base: str,
        reply: str,
    ) -> None:
        identifier = f"{run}:{role}"
        view = session.view(identifier, base)
        self.assertEqual(
            asdict(view),
            {"seat": f"p{seat + 1}", "role": role, "base": base, "table": table},
        )
        selection = tools.select_prompt(view)
        result = tools.tool_result(view, "on", _PICK)
        final = tools.final_prompt(view, result)
        self.assertEqual(result, {"status": "returned", "starts": _EXACT[seat]})
        self.assertIn(base, selection)
        self.assertEqual(final.split("\n\n", 1)[1], base)
        for prompt in (selection, final):
            for private_label in ("rx-w", "tl-w", "blind", "peer", role):
                self.assertNotIn(private_label, prompt)
        session.accept(identifier, reply)

    def test_only_an_enabled_exact_request_reads_starts(self) -> None:
        identifier = "rx-w00-d0-r0:cal_initial_p1"
        block = native.Block(0, 0, 0)
        off_view = tools.Session(block).view(identifier)
        on_view = tools.Session(block).view(identifier)
        self.assertEqual(tools.select_prompt(off_view), tools.select_prompt(on_view))
        with patch.object(packet, "starts", wraps=packet.starts) as starts:
            self.assertEqual(
                tools.tool_result(off_view, "off", _PICK), {"status": "unavailable"}
            )
            off = tools.tool_result(off_view, "off", _DECLINE)
            on = tools.tool_result(on_view, "on", _DECLINE)
            self.assertEqual(off, {"status": "not_requested"})
            self.assertEqual(
                tools.final_prompt(off_view, off), tools.final_prompt(on_view, on)
            )
            for text in (
                '{"tool":"local_starts","seat":"p2"}',
                '{"tool":"local_starts","args":{}}',
                '{"tool":"local_starts","tool":"local_starts"}',
                '{"tool":"none","starts":[26]}',
                '{"tool":"other_seat"}',
                '[{"tool":"local_starts"}]',
                'planner text {"tool":"local_starts"}',
            ):
                with self.subTest(text=text):
                    for policy in ("off", "on"):
                        result = tools.tool_result(on_view, policy, text)
                        self.assertEqual(result, {"status": "invalid"})
                        final = tools.final_prompt(on_view, result)
                        envelope, base = final.split("\n\n", 1)
                        self.assertEqual(json.loads(envelope.split("\n", 1)[1]), result)
                        self.assertEqual(base, on_view.base)
            starts.assert_not_called()
            returned = tools.tool_result(on_view, "on", ' { "tool": "local_starts" }\n')
            self.assertEqual(returned, {"status": "returned", "starts": _EXACT[0]})
            starts.assert_called_once_with(on_view.table)

    def test_prompt_mismatch_and_out_of_order_requests_are_rejected(self) -> None:
        session = tools.Session(native.Block(0, 0, 0))
        p1, p2 = "rx-w00-d0-r0:cal_initial_p1", "rx-w00-d0-r0:cal_initial_p2"
        table2 = packet.load(packet.PACKET, packet.PACKET_SHA256)[0].tables[1]
        with self.assertRaises(ValueError):
            session.view(p2)
        with self.assertRaises(ValueError):
            session.accept(p2, '{"starts":[]}')
        with self.assertRaises(ValueError):
            session.view(p1, native.initial_prompt(table2))
        view = session.view(p1)
        with self.assertRaises(ValueError):
            session.view(p1, view.base + "\n")
        session.accept(p1, '{"starts":[]}')
        with self.assertRaises(ValueError):
            session.view(p1)
        with self.assertRaises(ValueError):
            session.accept(p1, '{"starts":[]}')
        session.accept(p2, '{"starts":[]}')
        with self.assertRaises(ValueError):
            session.view("rx-w00-d0-r0-peer:cal_recheck")
        self.assertIn(
            '{"ready":true}', session.view("rx-w00-d0-r0-blind:cal_recheck").base
        )

    def test_invalid_seed_and_recheck_skip_only_unrequested_turns(self) -> None:
        block = native.Block(0, 0, 0)
        run = "rx-w00-d0-r0"
        p1, p2 = f"{run}:cal_initial_p1", f"{run}:cal_initial_p2"
        blind_s, blind_r = f"{run}-blind:cal_recheck", f"{run}-blind:cal_decide"
        for replies in (
            ("bad", '{"starts":[]}'),
            ('{"starts":[]}', '{"starts":[7,3]}'),
        ):
            with self.subTest(replies=replies):
                session = tools.Session(block)
                session.accept(p1, replies[0])
                self.assertEqual(session.view(p2).seat, "p2")
                session.accept(p2, replies[1])
                with self.assertRaises(ValueError):
                    session.view(blind_s)

        session = tools.Session(block)
        session.accept(p1, '{"starts":[]}')
        session.accept(p2, '{"starts":[16]}')
        session.accept(blind_s, '{"starts":[7,7]}')
        with self.assertRaises(ValueError):
            session.view(blind_r)
        peer_s, peer_r = f"{run}-peer:cal_recheck", f"{run}-peer:cal_decide"
        self.assertIn('{"starts":[16]}', session.view(peer_s).base)
        session.accept(peer_s, '{"starts":[]}')
        self.assertIn(
            'Sender\'s revised candidate list: {"starts":[]}',
            session.view(peer_r).base,
        )
        session.accept(peer_r, "malformed decision")
        with self.assertRaises(ValueError):
            session.view(peer_r)

    def test_result_digest_binds_canonical_json_and_text_digest_binds_raw_reply(
        self,
    ) -> None:
        result: tools.ToolResult = {"status": "returned", "starts": [7, 8]}
        canonical = b'{"starts":[7,8],"status":"returned"}'
        self.assertEqual(tools.result_digest(result), sha256(canonical).hexdigest())
        reply = "é\n"
        self.assertEqual(tools.digest(reply), sha256(reply.encode("utf-8")).hexdigest())
        self.assertNotEqual(tools.digest(reply), tools.digest(reply.strip()))
