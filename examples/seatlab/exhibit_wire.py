import hashlib
import json
from typing import Any


def render_turn(
    instruction: str, view: dict[str, Any], previous: dict[str, Any] | None
) -> str:
    phase = view["phase"]
    rule = {
        "request": '{"op":"request","slips":["brief ID", ...]}',
        "registrar": '{"op":"file","ids":["card ID", ...]}',
        "depot": '{"op":"file","ids":["card ID", ...]}',
        "dispatch": '{"op":"commit","routes":{"exhibit ID":"bay ID", ...}}',
    }[phase]
    if phase == "registrar" and view["role"] == "registrar" and "note_capacity" in view:
        rule = (
            '{"op":"file","ids":["card ID", ...],'
            '"notes":[{"epoch":0,"keys":["crate ID","crate ID"],"weight":1}, ...]}'
            ". Include zero to four notes with two distinct crate IDs each, "
            "epoch 0 to 2, and integer weight 0 to 9"
        )
    payload = {"instruction": instruction, "view": view, "previous_action": previous}
    return (
        "Select the action for your current role using only the supplied information. "
        "Return one JSON object without markdown, in this form: "
        + rule
        + ".\nInput JSON: "
        + json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    )


def encode_request(prompt: str) -> bytes:
    return (
        json.dumps(
            {"schema": "seatlab.seat-turn/v1", "prompt": prompt}, ensure_ascii=True
        )
        + "\n"
    ).encode("utf-8")


def request_stamp(request: bytes) -> dict[str, Any]:
    return {"bytes": len(request), "sha256": hashlib.sha256(request).hexdigest()}
