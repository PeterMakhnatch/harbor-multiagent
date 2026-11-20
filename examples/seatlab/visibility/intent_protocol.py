"""Plan, render, or decode the fixed-prefix box intention study offline."""

import argparse
import json
import sys
from dataclasses import dataclass

from . import boxes_choice, intent_packet

LABELS = ("A", "B", "C")
_INSTANCES = (1, 2, 6)
_STAGES = ("proposal", "masked", "shown")
_REPLY = 'Return JSON with only the integer field "box".'

type Choices = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class Block:
    instance: int
    numbering: int
    rotation: int
    repetition: int

    @property
    def id(self) -> str:
        return (
            f"bi-i{self.instance:02d}-m{self.numbering}-l{self.rotation}"
            f"-r{self.repetition}"
        )

    @property
    def arms(self) -> tuple[str, str]:
        parity = (
            self.repetition
            + _INSTANCES.index(self.instance)
            + self.numbering
            + self.rotation
        )
        return ("shown", "masked") if parity % 2 else ("masked", "shown")

    def seat_index(self, label: int) -> int:
        _label(label)
        return (label - self.rotation) % len(LABELS)


def blocks() -> tuple[Block, ...]:
    return tuple(
        Block(instance, numbering, rotation, repetition)
        for repetition in range(2)
        for instance in _INSTANCES
        for numbering in range(2)
        for rotation in range(len(LABELS))
    )


def _label(label: int) -> str:
    if type(label) is not int or not 0 <= label < len(LABELS):
        raise ValueError("invalid seat label")
    return LABELS[label]


def call_id(block: Block, label: int, stage: str) -> str:
    if stage not in _STAGES:
        raise ValueError("invalid stage")
    return f"{block.id}-{stage}-{_label(label).lower()}"


def _mapped(choices: Choices, numbering: int) -> Choices:
    a, b, c = choices
    return (
        boxes_choice.map_box(a, numbering),
        boxes_choice.map_box(b, numbering),
        boxes_choice.map_box(c, numbering),
    )


def _render_prompt(
    world: intent_packet.World,
    block: Block,
    label: int,
    *,
    own_display: int | None = None,
    peers_display: str = "ready, not supplied",
) -> str:
    if world.instance != block.instance:
        raise ValueError("wrong world for block")
    case = world.seats[block.seat_index(label)]
    context = boxes_choice.search_context(case, block.numbering, show_peer_boxes=True)
    if own_display is None:
        decision = (
            "Independently propose 1 box. Your teammates are making their own proposals.",
            "A proposal does not open a box or reserve it.",
        )
    else:
        decision = (
            f"Your sealed proposal: {own_display}",
            f"Teammates' sealed proposals: {peers_display}",
            (
                "Select your final box at the same time as your teammates. "
                "You may keep or change your proposal."
            ),
            "Labels do not give anyone priority. A proposal does not reserve a box.",
        )
    return "\n".join((*context, f"Your label: {_label(label)}", *decision, _REPLY))


def prompt(
    world: intent_packet.World,
    block: Block,
    label: int,
    stage: str,
    proposals: Choices | None = None,
) -> str:
    """Render one seat's prompt. All supplied proposals use original box numbers."""
    _label(label)
    if stage == "proposal":
        if proposals is not None:
            raise ValueError("a proposal prompt cannot include prior proposals")
        return _render_prompt(world, block, label)
    if stage not in ("masked", "shown") or proposals is None:
        raise ValueError("a revision prompt requires three sealed proposals")
    mapped = _mapped(proposals, block.numbering)
    if stage == "masked":
        return _render_prompt(world, block, label, own_display=mapped[label])
    peers = {name: mapped[i] for i, name in enumerate(LABELS) if i != label}
    return _render_prompt(
        world,
        block,
        label,
        own_display=mapped[label],
        peers_display=json.dumps(peers, separators=(",", ":")),
    )


def native_metrics(world: intent_packet.World, choices: Choices) -> dict[str, int]:
    """Count new claims and repeats in original numbering, without opening a box."""
    opened = {box for case in world.seats for box, _, _ in case.own}
    new = [box for box in _mapped(choices, 0) if box not in opened]
    distinct = len(set(new))
    return {
        "individually_new": len(new),
        "distinct_new": distinct,
        "excess_new_collisions": len(new) - distinct,
        "historical_repeats": len(LABELS) - len(new),
    }


def _proposals(text: str) -> Choices:
    value: object = json.loads(text)
    if not isinstance(value, list):
        raise TypeError("expected an array of proposals")
    if len(value) != 3:
        raise ValueError("expected exactly three proposals")

    def box(item: object) -> int:
        if not isinstance(item, int):
            raise TypeError("expected an integer proposal")
        # The existing box validator also rejects booleans.
        return boxes_choice.map_box(item, 0)

    a, b, c = value
    return box(a), box(b), box(c)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    planned = {block.id: block for block in blocks()}
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan", help="list the registered blocks and call order")
    render = commands.add_parser("prompt", help="write one seat's exact user text")
    render.add_argument("block", choices=planned)
    render.add_argument("--seat", choices=LABELS, required=True)
    render.add_argument("--stage", choices=_STAGES, required=True)
    sealed = render.add_mutually_exclusive_group()
    sealed.add_argument(
        "--proposals", help="3 sealed original boxes as a JSON array in A, B, C order"
    )
    sealed.add_argument(
        "--own", type=int, help="own original box after all 3 are sealed (masked only)"
    )
    decode = commands.add_parser(
        "decode", help='read one {"box": N} action on stdin and undo the number map'
    )
    decode.add_argument("block", choices=planned)
    args = parser.parse_args(argv)
    if args.command == "plan":
        print(
            json.dumps(
                {
                    "packet_sha256": intent_packet.PACKET_SHA256,
                    "blocks": [
                        {
                            "id": block.id,
                            "arms": block.arms,
                            "calls": [
                                call_id(block, label, stage)
                                for stage in ("proposal", *block.arms)
                                for label in range(len(LABELS))
                            ],
                        }
                        for block in planned.values()
                    ],
                }
            )
        )
        return 0
    block = planned[args.block]
    try:
        if args.command == "decode":
            text = sys.stdin.buffer.read(8193)
            if len(text) > 8192:
                raise ValueError("invalid response size")
            original = boxes_choice.decode(text.decode("utf-8"), block.numbering)
            print(json.dumps({"box": original}))
        else:
            world = intent_packet.load()[block.instance]
            label = LABELS.index(args.seat)
            if args.own is not None:
                if args.stage != "masked":
                    raise ValueError("--own is only for the masked stage")
                text = _render_prompt(
                    world,
                    block,
                    label,
                    own_display=boxes_choice.map_box(args.own, block.numbering),
                )
            else:
                proposals = (
                    _proposals(args.proposals) if args.proposals is not None else None
                )
                text = prompt(world, block, label, args.stage, proposals)
            sys.stdout.write(text)
    except (KeyError, OSError, RecursionError, TypeError, ValueError):
        print(
            "intent protocol: invalid packet, stage input, or action", file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
