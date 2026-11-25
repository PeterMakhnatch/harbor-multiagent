import json
import os
import tempfile
from pathlib import Path

import pytest

from examples.seatlab.exhibit_author_prepare import ARMS, TURNS, prepare


def test_preflight_isolates_world_not_owners_or_physical_results():
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="expre-") as workspace:
        root = Path(workspace).resolve()
        paths = {turn: root / str(i) for i, turn in enumerate(TURNS)}
        out = root / "inputs"
        receipt = prepare(out, 7, "b", paths)
        assert receipt["native_admission"] == "accepted"
        assert receipt["executions"] == 0 and not receipt["provider_authenticated"]
        assert receipt["owner_turns"] == list(TURNS)
        assert receipt["fixed_prefix_eligible"] is False
        assert all(not path.exists() for path in paths.values())
        assert all(os.stat(entry).st_mode & 0o777 == 0o600 for entry in out.iterdir())
        configs = [
            json.loads((out / receipt["arms"][arm]["config"]).read_bytes())
            for arm in ARMS
        ]
        for config, arm in zip(configs, ARMS, strict=True):
            assert config["environment"]["env"]["EXHIBIT_MODE"] == arm
            owners = {
                p["seat"]: p["kwargs"]["owner_sockets"] for p in config["participants"]
            }
            assert owners == {
                "loader": [str(paths["loader_request"]), str(paths["loader_dispatch"])],
                "registrar": [str(paths["registrar"])],
                "depot": [str(paths["depot"])],
            }
            lock = json.loads((out / receipt["arms"][arm]["lock"]).read_bytes())
            assert len(lock["participants"]) == 3
        assert configs[0]["participants"] == configs[1]["participants"]
        with pytest.raises(ValueError):
            prepare(root / "bad", 7, "b", {**paths, "depot": paths["registrar"]})
        assert not (root / "bad").exists()
