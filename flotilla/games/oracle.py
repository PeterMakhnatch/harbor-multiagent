"""The oracle seat of a game task: the game's oracle request in every round until the season ends (commons: the
fair share of the current stock; split: the pot divided by the seats).

Runs in a seat under Harbor's `oracle` agent (`solution/solve.sh`). The public rules come from
`SEAT_BRIEFING`; the game module is `family.py` next to this file. Expected reward with every seat
an oracle: 1 (docs/COMMONS.md section 10). Standard library only.
"""

from __future__ import annotations

import importlib.util
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def call(method: str, path: str, body: Any = None) -> tuple[int, Any]:
    request = urllib.request.Request(
        os.environ["WORLD_URL"] + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {os.environ['SEAT_TOKEN']}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


def main() -> None:
    spec = importlib.util.spec_from_file_location("game", HERE / "family.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("no game module next to the oracle")
    game = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(game)
    rules = json.loads(os.environ["SEAT_BRIEFING"])
    action = getattr(game, "ACTION_PATH", "/harvest")
    if hasattr(game, "oracle_request"):  # a game with its own oracle rule (split: the pot / the seats)
        def request(view: dict) -> int:
            return game.oracle_request(view, rules)
    else:  # commons: the seat's part of the sustainable catch
        growth, players = game.growth_of(rules), len(rules["players"])

        def request(view: dict) -> int:
            return game.sustainable_catch(view["stock"], growth) // players
    deadline = time.monotonic() + float(os.environ.get("ORACLE_MAX_SECONDS") or 3000)
    while time.monotonic() < deadline:
        _status, view = call("GET", "/state")
        if view["season_over"]:
            return
        if not view["season_started"]:
            time.sleep(1)
            continue
        if not view["you_requested"]:
            call("POST", action, {"amount": request(view)})
        call("GET", f"/wait?round={view['round']}")


if __name__ == "__main__":
    main()
