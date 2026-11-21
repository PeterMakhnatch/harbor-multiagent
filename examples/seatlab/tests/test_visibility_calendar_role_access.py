from collections import Counter
from unittest import TestCase

from examples.seatlab.visibility import calendar_protocol as native
from examples.seatlab.visibility import calendar_tools_protocol as tools

_PICK = '{"tool":"local_starts"}'
_EXACT = {"p1": [7, 8, 9, 26], "p2": [16, 26, 28, 29]}


class CalendarRoleAccessTest(TestCase):
    def test_policy_order_matches_registration_in_each_block(self) -> None:
        roles = ("sender", "receiver")
        availability = ("off", "on")
        self.assertEqual(tools.design_arms("role-access"), roles)
        self.assertEqual(tools.design_arms("availability"), availability)
        first: Counter[str] = Counter()
        for block in native.blocks():
            with self.subTest(block=block):
                swap = (block.world // 2 + block.direction + block.repetition) % 2
                order = tools.policies(block, design="role-access")
                self.assertEqual(order, (roles[swap], roles[1 - swap]))
                self.assertEqual(
                    tools.policies(block), (availability[swap], availability[1 - swap])
                )
                first[order[0]] += 1
        self.assertEqual(first, {"sender": 24, "receiver": 24})

    def test_action_names_keep_first_attempts_separate_between_designs(self) -> None:
        self.assertEqual(tools.spend_prefix("role-access"), "ra-")
        self.assertEqual(tools.spend_prefix("availability"), "tl-")
        seen: set[str] = set()
        for block in native.blocks():
            key = f"w{block.world:02d}-d{block.direction}-r{block.repetition}"
            run = f"rx-{key}"
            turns = (
                (f"{run}:cal_initial_p1", "seed1"),
                (f"{run}:cal_initial_p2", "seed2"),
                (f"{run}-blind:cal_recheck", "blind-s"),
                (f"{run}-blind:cal_decide", "blind-r"),
                (f"{run}-peer:cal_recheck", "peer-s"),
                (f"{run}-peer:cal_decide", "peer-r"),
            )
            for policy, prefix in (
                ("sender", "ra-"),
                ("receiver", "ra-"),
                ("off", "tl-"),
                ("on", "tl-"),
            ):
                for identifier, suffix in turns:
                    name = tools.action_id(block, policy, identifier)
                    self.assertEqual(name, f"{prefix}{key}-{policy}-{suffix}")
                    self.assertNotIn(name, seen)
                    seen.add(name)

    def test_only_the_assigned_role_can_read_its_own_calendar_on_every_turn(
        self,
    ) -> None:
        for direction, sender, receiver, rooms in (
            (0, "p1", "p2", ("blind", "peer")),
            (1, "p2", "p1", ("peer", "blind")),
        ):
            block = native.Block(0, direction, 0)
            run = f"rx-w00-d{direction}-r0"
            seed = '{"starts":[26]}'
            turns = [
                (f"{run}:cal_initial_p1", "p1", seed),
                (f"{run}:cal_initial_p2", "p2", seed),
            ]
            for room in rooms:
                turns.append((f"{run}-{room}:cal_recheck", sender, seed))
                turns.append((f"{run}-{room}:cal_decide", receiver, '{"slot":26}'))

            for policy, allowed in (("sender", sender), ("receiver", receiver)):
                session = tools.Session(block)
                for identifier, seat, reply in turns:
                    with self.subTest(policy=policy, turn=identifier):
                        view = session.view(identifier)
                        self.assertEqual(view.seat, seat)
                        level = tools.access_for(block, policy, view.seat)
                        result = tools.tool_result(view, level, _PICK)
                        if seat == allowed:
                            self.assertEqual(
                                result, {"status": "returned", "starts": _EXACT[seat]}
                            )
                        else:
                            self.assertEqual(result, {"status": "unavailable"})
                        session.accept(identifier, reply)

    def test_invalid_inputs_cannot_get_authority_or_claim_an_action(self) -> None:
        block = native.Block(0, 0, 0)
        turn = "rx-w00-d0-r0:cal_initial_p1"
        for design in ("role_access", "sender", ""):
            with self.subTest(design=design):
                with self.assertRaises(ValueError):
                    tools.design_arms(design)
                with self.assertRaises(ValueError):
                    tools.spend_prefix(design)
                with self.assertRaises(ValueError):
                    tools.policies(block, design=design)
        for policy in ("role-access", "p1", "ON"):
            with self.subTest(policy=policy):
                with self.assertRaises(ValueError):
                    tools.access_for(block, policy, "p1")
                with self.assertRaises(ValueError):
                    tools.action_id(block, policy, turn)
        for seat in ("sender", "p3"):
            with self.subTest(seat=seat), self.assertRaises(ValueError):
                tools.access_for(block, "off", seat)
        for invalid in (
            native.Block(12, 0, 0),
            native.Block(0, -1, 0),
            native.Block(0, True, 0),
            native.Block(0, 0, 2),
        ):
            with self.subTest(block=invalid):
                with self.assertRaises(ValueError):
                    tools.access_for(invalid, "sender", "p1")
                with self.assertRaises(ValueError):
                    tools.policies(invalid, design="role-access")
                with self.assertRaises(ValueError):
                    tools.action_id(invalid, "receiver", turn)
        for wrong_turn in ("rx-w00-d1-r0:cal_initial_p1", f"{turn}:extra"):
            with self.subTest(turn=wrong_turn), self.assertRaises(ValueError):
                tools.action_id(block, "sender", wrong_turn)
