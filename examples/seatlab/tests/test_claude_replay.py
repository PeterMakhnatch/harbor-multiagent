import json
import os
import stat
import subprocess
import sys
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from runpy import run_path
from typing import Any
from uuid import UUID

import pytest

from examples.seatlab import claude_replay as replay
from examples.seatlab import seat_agent
from examples.seatlab.capsule import encode_view, extract_view
from examples.seatlab.evidence import read_trial
from examples.seatlab.export_capsules import export_trial

_FIXTURE = run_path(str(Path(__file__).with_name("test_evidence.py")))
_PRIVATE = "/private/repo/operator/never-include-this"
_ORDER = [
    (1001, "raw"),
    (1001, "highest_revision"),
    (1003, "highest_revision"),
    (1003, "raw"),
]


def _put(path: Path, value: dict[str, Any]) -> None:
    path.write_bytes(encode_view(value))


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_bytes())


@pytest.fixture
def panel(tmp_path: Path) -> Path:
    root = tmp_path / "panel-a"
    root.mkdir()
    planned, measured = [], []
    for seed in (1001, 1003):
        cell = {
            "cell_id": f"seed-{seed}--reordered",
            "seed": seed,
            "condition": "reordered",
        }
        world = _FIXTURE["_dossier"]("reordered")
        world["private_not_observed"] = _PRIVATE
        template = world["cases"]
        world["cases"] = [dict(deepcopy(template[i % 2]), id=i) for i in range(12)]
        world["settings"].update(seed=seed, episodes=12)
        native = _FIXTURE["_native"]("reordered")
        native["id"] = str(UUID(int=seed))
        for participant in native["config"]["participants"]:
            participant.update(
                name=None,
                import_path="seat_agent:SeatAgent",
                kwargs={"policy": "arrival"},
            )
        for seat in native["seat_results"]:
            seat["agent_info"].update(name="seatlab-dispatch-handoff", model_info=None)
        trial = root / cell["cell_id"]
        _FIXTURE["_write_trial"](trial, native, world)
        planned.append(cell)
        measured.append(
            {
                **cell,
                "status": "measured",
                "trial_path": trial.name,
                "evidence": read_trial(trial),
            }
        )
    _put(
        root / "plan.json",
        {
            "schema": "seatlab.panel-plan/v1",
            "seeds": [1001, 1003],
            "conditions": ["reordered"],
            "episodes": 12,
            "policy": "arrival",
            "cells": planned,
        },
    )
    _put(root / "runs.json", {"schema": "seatlab.panel-runs/v1", "cells": measured})
    return root


def _prepare(
    panel: Path, tmp_path: Path
) -> tuple[Path, dict[str, Any], list[replay._Cell], str]:
    out = tmp_path / "private-replay"
    system = replay._system_prompt()
    study, cells = replay._study(panel, system)
    out.mkdir(mode=0o700)
    replay._create(out / "plan.json", study)
    return out, study, cells, system


def _process(
    args: list[str], response: dict[str, Any], returncode: int = 0
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(
        args, returncode, encode_view(response), _PRIVATE.encode()
    )


def _success(args: list[str], bay: str) -> dict[str, Any]:
    return {
        "type": "result",
        "is_error": False,
        "subtype": "success",
        "num_turns": 1,
        "stop_reason": "end_turn",
        "session_id": args[args.index("--session-id") + 1],
        "result": json.dumps(
            {"rows": [{"id": i, "bay": bay} for i in reversed(range(12))]}
        ),
        "duration_ms": 144,
        "duration_api_ms": 103,
        "total_cost_usd": 0.01,
        "modelUsage": {
            replay._MODEL: {
                "inputTokens": 10,
                "outputTokens": 21,
                "cacheReadInputTokens": 4,
            }
        },
        "private_extra": _PRIVATE,
    }


def test_plan_and_complete_study(panel, tmp_path, monkeypatch, capsys):
    out = tmp_path / "private-replay"
    cli = ["claude_replay.py", "plan", "--panel", str(panel), "--out", str(out)]
    monkeypatch.setattr(sys, "argv", cli)
    invocations = []
    monkeypatch.setattr(replay, "_managed_configuration", lambda: False)

    def invoke(args, body, timeout):
        assert (out / "started.json").is_file()
        if args == ["auth", "status", "--json"]:
            assert body is None
            assert timeout == replay._AUTH_TIMEOUT
            return _process(args, {**replay._AUTH, "email": _PRIVATE})
        index = len(invocations)
        seed, arm = _ORDER[index]
        marker = out / f"attempt-{index + 1:02}.json"
        assert stat.S_IMODE(marker.stat().st_mode) == 0o400
        session = args[args.index("--session-id") + 1]
        assert _read(marker)["session_id"] == session
        invocations.append(session)
        assert timeout == replay._GENERATION_TIMEOUT
        plan_cell = _read(out / "plan.json")["cells"][index]
        assert sha256(body).hexdigest() == plan_cell["input_sha256"]
        assert len(body) == plan_cell["input_bytes"]
        assert plan_cell["observed_reports"] == (24 if arm == "raw" else 12)
        trial = panel / f"seed-{seed}--reordered"
        capsule = next(
            row["capsule"] for row in export_trial(trial) if row["role"] == "dispatch"
        )
        observed = extract_view(capsule)
        received = json.loads(body)
        assert received.keys() == {"role", "condition", "cases"}
        assert received["condition"] == "reordered"
        if arm == "raw":
            assert body == encode_view(observed)
        else:
            for actual, original in zip(
                received["cases"], observed["cases"], strict=True
            ):
                # The fixture's newest observed availability is intentionally false.
                assert actual == {
                    **original,
                    "messages": [{"revision": 2, "available": ["B", "D"]}],
                }
        expected_system = seat_agent._model_request(
            seat_agent._Route("unused", "unused", "unused"),
            "dispatch",
            received,
        )["messages"][0]["content"]
        assert args[args.index("--system-prompt") + 1] == expected_system
        for forbidden in (
            _PRIVATE,
            str(seed),
            arm,
            "before",
            "after",
            "chosen",
            str(panel),
        ):
            assert forbidden.encode() not in body
        return _process(args, _success(args, "B" if arm == "raw" else "A"))

    monkeypatch.setattr(replay, "_invoke", invoke)
    with pytest.raises(SystemExit) as stop:
        replay.main()
    assert stop.value.code == 0
    assert not invocations
    plan = json.loads(capsys.readouterr().out)
    assert plan == _read(out / "plan.json")
    assert stat.S_IMODE((out / "plan.json").stat().st_mode) == 0o400
    assert plan["stop_on_first_unmeasured"] is True
    assert [(row["seed"], row["arm"]) for row in plan["cells"]] == _ORDER

    cli[1] = "run"
    with pytest.raises(SystemExit) as stop:
        replay.main()
    assert stop.value.code == 0
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report == _read(out / "report.json")
    assert not output.err
    assert _PRIVATE not in output.out
    assert set(invocations).isdisjoint(output.out.split('"'))
    assert len(set(invocations)) == 4
    assert report["planned"] == report["attempted"] == report["complete"] == 4
    for cell in report["cells"]:
        assert cell["served_model"] == replay._MODEL
        measurement = cell["measurement"]
        assert [choice["ordinal"] for choice in measurement["decisions"]] == list(
            range(1, 13)
        )
        if cell["arm"] == "raw":
            assert measurement["optimal"] == 0
            assert measurement["feasible"] == 6
        else:
            assert measurement["optimal"] == measurement["feasible"] == 12
        assert cell["telemetry"]["cache_creation_input_tokens"] is None
        assert cell["telemetry"]["estimated_cost_usd"] == 0.01
    assert report["pairs"] == [
        {"seed": seed, "changed_ordinals": list(range(1, 13))} for seed in (1001, 1003)
    ]


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("rate_limit", "rate_limited"),
        ("refusal", "refused"),
        ("wrong_model", "model_unattested"),
        ("mixed_model", "model_unattested"),
        ("missing_model", "model_unattested"),
        ("short", "invalid_or_refused_decisions"),
        ("duplicate_key", "invalid_or_refused_decisions"),
        ("wrong_bay", "invalid_or_refused_decisions"),
        ("timeout", "generation_timeout"),
        ("unsupported", "cli_or_model_unsupported"),
        ("budget", "budget_estimate_exceeded"),
        ("wrong_session", "session_unattested"),
    ],
)
def test_unmeasured_cell_stops_and_cannot_resume(
    panel, tmp_path, monkeypatch, case, expected
):
    out, study, cells, system = _prepare(panel, tmp_path)
    monkeypatch.setattr(replay, "_managed_configuration", lambda: False)
    calls = 0

    def invoke(args, body, timeout):
        nonlocal calls
        if body is None:
            return _process(args, replay._AUTH)
        calls += 1
        response = _success(args, "A")
        if calls == 1:
            return _process(args, response)
        if case == "rate_limit":
            response.update(api_error_status=429, is_error=True, errors=[_PRIVATE])
        elif case == "refusal":
            response.update(stop_reason="refusal", result=_PRIVATE)
        elif case == "wrong_model":
            response["modelUsage"] = {"claude-sonnet-5": {}}
        elif case == "mixed_model":
            response["modelUsage"]["claude-sonnet-5"] = {}
        elif case == "missing_model":
            del response["modelUsage"]
        elif case == "short":
            response["result"] = '{"rows":[{"id":0,"bay":"A"}]}'
        elif case == "duplicate_key":
            response["result"] = '{"rows":[],' + response["result"][1:]
        elif case == "wrong_bay":
            response["result"] = response["result"].replace('"bay": "A"', '"bay": "Z"')
        elif case == "timeout":
            raise subprocess.TimeoutExpired(args, timeout, output=_PRIVATE)
        elif case == "unsupported":
            return subprocess.CompletedProcess(
                args, 1, b"", b"unknown option" + _PRIVATE.encode()
            )
        elif case == "budget":
            response["total_cost_usd"] = 0.31
        elif case == "wrong_session":
            response["session_id"] = _PRIVATE
        return _process(args, response)

    report = replay._run(out, study, cells, system, invoke)
    with pytest.raises(replay._Invalid, match="study_already_consumed"):
        replay._run(out, study, cells, system, invoke)
    assert calls == 2
    assert report["planned"] == 4
    assert report["attempted"] == 2
    assert report["complete"] == 1
    assert [row["stage"] for row in report["cells"]] == [
        "complete",
        expected,
        "not_attempted",
        "not_attempted",
    ]
    assert all(row["measurement"] is None for row in report["cells"][1:])
    assert all(row["changed_ordinals"] is None for row in report["pairs"])
    assert _PRIVATE not in json.dumps(report)
    assert sorted(path.name for path in out.glob("attempt-*.json")) == [
        "attempt-01.json",
        "attempt-02.json",
    ]


@pytest.mark.parametrize(
    ("key", "invalid"),
    [
        ("loggedIn", 1),
        ("authMethod", "oauth_token"),
        ("apiProvider", "bedrock"),
        ("subscriptionType", "pro"),
    ],
)
def test_wrong_auth_has_no_generation_or_score(
    panel, tmp_path, monkeypatch, key, invalid
):
    out, study, cells, system = _prepare(panel, tmp_path)
    monkeypatch.setattr(replay, "_managed_configuration", lambda: False)

    def invoke(args, body, timeout):
        assert args == ["auth", "status", "--json"]
        assert body is None
        return _process(args, {**replay._AUTH, key: invalid, "email": _PRIVATE})

    report = replay._run(out, study, cells, system, invoke)
    assert report["route_preflight"] == "unverified_max_auth"
    assert report["attempted"] == report["complete"] == 0
    assert all(row["measurement"] is None for row in report["cells"])
    assert not list(out.glob("attempt-*.json"))
    assert _PRIVATE not in json.dumps(report)


def test_changed_dossier_cannot_run_against_approved_plan(panel, tmp_path):
    out, _, _, system = _prepare(panel, tmp_path)
    trial = panel / "seed-1001--reordered"
    dossier = trial / "artifacts/logs/world/handoff.json"
    world = _read(dossier)
    world["cases"][0]["reports"][1]["available"] = ["A", "C"]
    world["cases"][0]["visible"][0]["available"] = ["A", "C"]
    _put(dossier, world)
    study, cells = replay._study(panel, system)
    with pytest.raises(replay._Invalid, match="plan_or_source_changed"):
        replay._run(
            out, study, cells, system, lambda *args: pytest.fail("invoked Claude")
        )
    assert not (out / "started.json").exists()


def test_orphan_attempt_marker_blocks_replay(panel, tmp_path):
    out, study, cells, system = _prepare(panel, tmp_path)
    replay._create(out / "attempt-02.json", {"position": 2})
    with pytest.raises(replay._Invalid, match="study_already_consumed"):
        replay._run(
            out, study, cells, system, lambda *args: pytest.fail("invoked Claude")
        )


def test_repository_and_source_directories_are_not_output_paths(panel, tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").touch()
    for path in (checkout / "replay", panel / "replay"):
        with pytest.raises(
            replay._Invalid, match="output_must_be_private_and_external"
        ):
            replay._output(path, panel)


def test_equal_revisions_are_not_resolved_by_arrival_order():
    view = {"cases": [{"id": 0, "messages": [{"revision": 2}, {"revision": 2}]}]}
    with pytest.raises(replay._Invalid, match="ambiguous_observed_revision"):
        replay._highest_revision(view)


def test_subprocess_uses_private_empty_context_and_isolated_env(tmp_path, monkeypatch):
    executable = tmp_path / "claude"
    monkeypatch.setenv("PATH", f"rel{os.pathsep}{tmp_path}{os.pathsep}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", _PRIVATE)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", _PRIVATE)
    monkeypatch.setenv("HTTPS_PROXY", _PRIVATE)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", _PRIVATE)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", _PRIVATE)
    monkeypatch.setenv("CLAUDE_CODE_MAX_RETRIES", "12")

    def which(command, path):
        assert command == "claude"
        assert path == str(tmp_path)
        return str(executable)

    def run(args, **kwargs):
        cwd = Path(kwargs["cwd"])
        assert stat.S_IMODE(cwd.stat().st_mode) == 0o700
        assert not list(cwd.iterdir())
        assert kwargs["env"]["TMPDIR"] == str(cwd)
        assert kwargs["env"].keys() <= {
            "HOME",
            "USER",
            "LOGNAME",
            "PATH",
            "TMPDIR",
            *replay._CLI_CONTROLS,
        }
        assert kwargs["env"]["CLAUDE_CODE_MAX_RETRIES"] == "0"
        assert _PRIVATE not in kwargs["env"].values()
        assert args[0] == str(executable)
        assert kwargs["input"] == b"{}"
        assert kwargs["timeout"] == 90
        assert kwargs["capture_output"]
        flags = {
            "--safe-mode",
            "--restricted",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-chrome",
            "--no-session-persistence",
            "--print",
        }
        assert flags <= set(args)
        for flag, value in {
            "--tools": "",
            "--setting-sources": "",
            "--mcp-config": '{"mcpServers":{}}',
            "--max-turns": "1",
            "--output-format": "json",
            "--model": replay._MODEL,
            "--max-budget-usd": "0.30",
            "--system-prompt": "synthetic",
            "--session-id": "fresh",
        }.items():
            assert args[args.index(flag) + 1] == value
        assert "--fallback-model" not in args
        assert "--json-schema" not in args
        return _process(args, {})

    monkeypatch.setattr(replay, "which", which)
    monkeypatch.setattr(replay.subprocess, "run", run)
    replay._invoke(replay._args("synthetic", "fresh"), b"{}", 90)
