"""Build the draft, joint, repair, and finish prompts for Cramped Room."""

import json
from typing import Any

_RULES = """\
Both chefs see the same game state. Return actions only for your chef.

Map: The only floor tiles are x=1..3, y=1..2. The pot is at (2,0). The west onion source is at (0,1) and the east onion source is at (4,1). Dishes are at (1,3) and serving is at (3,3). All tiles outside the floor are blocked for movement.

Actions: N=[0,-1], S=[0,1], E=[1,0], W=[-1,0]. A direction changes facing even if movement is blocked. WAIT stays in place and keeps facing. USE interacts once with the tile directly in front before movement. The chefs act on the same transition. A swap or an attempt to end on the same tile blocks both moves. You can enter a tile that the other chef vacates if you do not swap.

Cooking: Each chef holds at most one item. USE with an empty hand takes an onion or dish from its source. USE facing the pot adds a held onion. The opening goal is three onions, but the engine can cook with two. Starting the pot needs a separate USE with an empty hand. The pot needs 20 cooking ticks, including the transition that starts cooking. USE with a dish facing a ready pot plates the soup. USE with plated soup facing serving delivers it.

Shared goals: chef0 adds two onions from the west, starts the pot from (2,1), and clears that square for chef1. chef1 adds one onion from the east and finishes the first phase at (2,1), facing the pot with a dish. In the final phase chef1 plates and serves; chef0 keeps the delivery route clear and does not serve. The only delivery must be by chef1 on the transition ending at state tick 38.

Timing: An action chosen at state tick t produces state tick t+1. The first 16 actions end at state tick 16. Both chefs then WAIT for 18 transitions without another planning turn. The final 4 actions start at state tick 34 and end at state tick 38."""


def phase_prompt(
    role: str,
    stage: str,
    tick: int,
    observation: dict[str, Any],
    *,
    own_plan: list[str] | None = None,
    peer_plan: tuple[str, ...] | list[str] | None = None,
) -> str:
    """Return one text-only planning prompt for the given chef and stage."""
    if role not in ("chef0", "chef1"):
        raise ValueError(f"Unknown kitchen role: {role!r}")

    if stage == "draft":
        if (
            role != "chef1"
            or tick != 0
            or own_plan is not None
            or peer_plan is not None
        ):
            raise ValueError("Draft requires chef1, state tick 0, and no plans")
        count = 16
        phase = (
            "Draft the first phase from the observed state and prepare for the idle transitions."
            " Your draft is committed as your opening plan and will not be revised."
        )
    elif stage == "joint":
        if role != "chef0" or tick != 0 or own_plan is not None or peer_plan is None:
            raise ValueError(
                "Joint requires chef0, state tick 0, and only the peer plan"
            )
        count = 16
        phase = (
            "Plan your first phase from the observed state around peer_plan, chef1's fixed "
            "opening plan. Check the actions tick by tick for movement conflicts and competing "
            "pot access. Return only your own actions and prepare for the idle transitions."
        )
    elif stage == "repair":
        count = 16 - tick
        if (
            role != "chef0"
            or tick not in (12, 13, 14)
            or not isinstance(own_plan, list)
            or len(own_plan) != count
            or not isinstance(peer_plan, list)
            or len(peer_plan) != count
        ):
            raise ValueError(
                "Repair requires chef0 and two plans of 4, 3, or 2 actions "
                "at state ticks 12, 13, or 14, respectively"
            )
        phase = (
            "Recover the opening from the observed state. "
            f"Return your next {count} actions. "
            "You may keep or change the old actions in own_plan. "
            "Chef1 will execute peer_plan unchanged. Check both plans for movement "
            "conflicts and competing pot access."
        )
    elif stage == "finish":
        if tick != 34 or own_plan is not None or peer_plan is not None:
            raise ValueError("Finish requires state tick 34 and no plans")
        count = 4
        phase = (
            "Plan the finish from the observed poses, held items, and pot. "
            "Do not assume the opening succeeded."
        )
    else:
        raise ValueError(f"Unknown kitchen stage: {stage!r}")

    sections = [
        f"You are {role} in Cramped Room.",
        _RULES,
        f"Current state tick: {tick}. {phase}\n"
        f"Observation (players[0] is chef0, players[1] is chef1):\n"
        f"{json.dumps(observation, separators=(',', ':'), sort_keys=True)}",
    ]
    if stage == "joint":
        plans = {"peer_plan": peer_plan}
        sections.append(f"Opening plans:\n{json.dumps(plans, separators=(',', ':'))}")
    elif stage == "repair":
        plans_json = json.dumps(
            {"own_plan": own_plan, "peer_plan": peer_plan}, separators=(",", ":")
        )
        sections.append(f"Opening actions for ticks {tick} through 15:\n{plans_json}")
    sections.extend(
        (
            "If a goal is no longer reachable in this phase, make the best legal plan "
            "within the remaining actions. Use WAIT when no useful action remains.",
            'Do not use tools. Return one JSON object of the form {"actions":[...]}. '
            f"The actions array must contain exactly {count} strings in execution order. "
            "Use only N, S, E, W, WAIT, or USE. No other keys, reasoning, Markdown, or text.",
        )
    )
    return "\n\n".join(sections)
