import importlib
import json
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

with pytest.MonkeyPatch.context() as patch:
    root = Path(__file__).resolve().parents[3]
    patch.syspath_prepend(str(root / "examples" / "seatlab"))
    patch.syspath_prepend(str(root))
    attention = importlib.import_module("examples.seatlab.attention.__main__")
    briefing = importlib.import_module("examples.seatlab.briefing.__main__")
    native_panel = importlib.import_module("examples.seatlab.native_panel")


def _unexpected(*args: object, **kwargs: object) -> None:
    pytest.fail("the host executable or paid preflight must not start")


@pytest.mark.parametrize(
    ("entry", "cell", "stage", "role", "max_calls", "tag"),
    [
        (attention, "attn-s6401-r0-named", "select", "dispatch", 72, "attention"),
        (briefing, "brf-s6501-r0-brief", "push", "inventory", 96, "briefing"),
    ],
)
def test_paid_reservation_closes_only_its_cell(
    entry: ModuleType,
    cell: str,
    stage: str,
    role: str,
    max_calls: int,
    tag: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    study = tmp_path.resolve() / "study"
    ledger = tmp_path.resolve() / "spend"
    ledger.mkdir(mode=0o700)
    spend = native_panel.spend
    opening = Decimal("2")
    spend.initialize(ledger, opening, Decimal("1"))
    monkeypatch.setattr(spend, "_HOST_LEDGER", None)
    monkeypatch.setenv("SEATLAB_SPEND_DIR", str(ledger))
    monkeypatch.setattr(native_panel.native, "_preflight", _unexpected)
    monkeypatch.setattr(native_panel.native.deepseek, "preflight", _unexpected)
    study_args = ["--study", str(study)]
    assert (
        entry.main(
            ["seal", *study_args, "--transport", "direct", "--spend-dir", str(ledger)]
        )
        == 0
    )
    plan = json.loads(capsys.readouterr().out)
    assert plan["schema"] == f"seatlab.native-{tag}-plan/v1"
    assert len(plan["cells"]) == 24
    assert plan["max_direct_calls"] == max_calls
    assert set(plan["seat_sources"]) == (
        {"agent.py", "protocol.py", "channel.py"}
        if tag == "briefing"
        else {"agent.py", "protocol.py"}
    )
    spend.reserve(f"{cell}-{stage}", role, balance_usd=opening)

    assert entry.main(["pending", *study_args]) == 0
    assert json.loads(capsys.readouterr().out)["cells"] == [
        row["cell_id"] for row in plan["cells"] if row["cell_id"] != cell
    ]
    cell_args = [*study_args, "--cell", cell]
    assert entry.main(["collect", *cell_args]) == 1
    row = json.loads(capsys.readouterr().out)
    assert row["state"] == "reserved_without_trial"
    assert row["outcome"] is None
    assert entry.main(["run", *cell_args, "--harbor", "/unused-harbor"]) == 1
    assert not (study / "cells" / cell).exists()
    capsys.readouterr()

    assert entry.main(["report", *study_args]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["meter"]["ledger_without_turn"] == 1
    assert report["rows"][0] == row
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    assert entry.main(["verify", str(path)]) == 0
    assert json.loads(capsys.readouterr().out) == report["summary"]


@pytest.mark.parametrize(("entry", "channel"), [(attention, False), (briefing, True)])
def test_command_trial_claim_survives_failed_launch(
    entry: ModuleType,
    channel: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    study = tmp_path.resolve() / "study"
    command_config = tmp_path.resolve() / "command.json"
    command = {"argv": [sys.executable], "env": []}
    command_config.write_text(
        json.dumps({"reviewer": command, "decider": command}), encoding="utf-8"
    )
    study_args = ["--study", str(study)]
    assert (
        entry.main(
            [
                "seal",
                *study_args,
                "--transport",
                "commands",
                "--config",
                str(command_config),
            ]
        )
        == 0
    )
    cell = json.loads(capsys.readouterr().out)["cells"][0]["cell_id"]
    cell_args = [*study_args, "--cell", cell]
    run_args = ["run", *cell_args, "--harbor", "/unused-harbor"]
    monkeypatch.setattr(native_panel.native, "_preflight", lambda p: (p, {}))
    launches = 0

    def fail_launch(directory: Path, harbor: Path, env: dict[str, str]) -> int:
        nonlocal launches
        launches += 1
        assert launches == 1
        config = json.loads((directory / "config.json").read_bytes())
        expected = {
            "cell_id": cell,
            "transport": "commands",
            "command_config": str(study / "commands.json"),
        }
        if channel:
            expected["channel_id"] = config["trial_name"]
        assert [row["kwargs"] for row in config["participants"]] == [expected, expected]
        assert entry.main(run_args) == 1
        raise TimeoutError

    monkeypatch.setattr(native_panel, "_commands_trial", fail_launch)
    assert entry.main(run_args) == 1
    assert launches == 1
    capsys.readouterr()
    monkeypatch.setattr(native_panel.native, "_preflight", _unexpected)
    assert entry.main(["collect", *cell_args]) == 1
    row = json.loads(capsys.readouterr().out)
    assert row["state"] == "native_failed"
    assert row["outcome"] is None
    assert entry.main(run_args) == 1
    assert entry.main(["pending", *study_args]) == 0
    assert cell not in json.loads(capsys.readouterr().out)["cells"]

    scenario = study / "cells" / cell / "dispatch-handoff" / "environment/scenario.json"
    scenario.write_bytes(b"{}\n")
    assert entry.main(["collect", *cell_args]) == 1
    assert capsys.readouterr().out == ""
