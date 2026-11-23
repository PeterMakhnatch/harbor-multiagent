"""Deterministic transport controls. These readers never open a generated stock task."""

from __future__ import annotations

import argparse
import json
import re
import sys


OWN = re.compile(r"Your own feasible start IDs: (\[[0-9,]+\])\.")
PEER = re.compile(
    r"The report delivered from the other seat lists these feasible start IDs: (\[[0-9,]*\])\."
)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("phase", choices=("report", "open", "book"))
    p.add_argument("control", choices=("correct", "wrong"))
    args = p.parse_args()
    try:
        message = json.loads(sys.stdin.buffer.readline(65537))
        if (
            set(message) != {"schema", "prompt"}
            or message["schema"] != "seatlab.seat-turn/v1"
        ):
            raise ValueError
        prompt = message["prompt"]
        own_matches = OWN.findall(prompt)
        if len(own_matches) != 1:
            raise ValueError
        own = json.loads(own_matches[0])
        if args.phase == "report":
            answer = {"starts": own}
        elif args.phase == "open":
            answer = {"open": args.control == "correct"}
        elif args.control == "correct":
            exact = PEER.findall(prompt)
            if len(exact) != 1:
                raise ValueError
            (slot,) = sorted(set(own).intersection(json.loads(exact[0])))
            answer = {"slot": slot}
        else:
            answer = {"slot": min(set(range(35)).difference(own))}
        print(
            json.dumps(
                {"text": json.dumps(answer, separators=(",", ":"))},
                separators=(",", ":"),
            )
        )
    except (KeyError, TypeError, ValueError):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
