import hashlib
import importlib
import json
import os
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from stat import S_IMODE
from typing import Any

import pytest

from examples.seatlab import seat_agent
from examples.seatlab.tests.test_agent import _dispatch_view, _inventory_view

with pytest.MonkeyPatch.context() as patch:
    patch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    panel = importlib.import_module("run_panel")

dispatch_plan = panel.dispatch_plan

_MODEL = "example/model:free"
_PROVIDER = "example/fp16"
_DESIGN = [
    "--seeds",
    "17,29",
    "--conditions",
    "fresh,pooled",
    "--episodes",
    "12",
    "--jobs",
    "2",
    "--policy",
    "hosted",
]
_WORKLOAD = (
    "native_teams",
    "shipment_cases",
    "pooled_nop_seats",
    "hosted_inventory_calls_max",
    "hosted_dispatch_calls_max",
    "native_seat_slots",
    "hosted_seat_calls_max",
    "hosted_completion_tokens_max",
)
_ADAPTER = "seatlab/seat_agent.py"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical(value: object) -> bytes:
    text = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return text.encode("utf-8") + b"\n"


def _forbid_native(monkeypatch: pytest.MonkeyPatch) -> None:
    def boundary(name: str) -> Callable[..., None]:
        def reached(*args: object, **kwargs: object) -> None:
            pytest.fail(f"{name} must not be reached")

        return reached

    for name in ("_native_env", "_check_native", "_launch"):
        monkeypatch.setattr(panel, name, boundary(name))
    monkeypatch.setattr(panel.subprocess, "run", boundary("subprocess.run"))
    monkeypatch.setattr(
        panel.asyncio, "create_subprocess_exec", boundary("create_subprocess_exec")
    )


def _refused(argv: list[str]) -> int | str | None:
    with pytest.raises(SystemExit) as exc:
        panel.main(argv)
    return exc.value.code


def _view(role: str, condition: str, episodes: int) -> dict[str, Any]:
    base = _inventory_view() if role == "inventory" else _dispatch_view(condition)
    case = base["cases"][0]
    cases = [{**deepcopy(case), "id": episode} for episode in range(episodes)]
    return {"role": role, "condition": condition, "cases": cases}


@pytest.fixture
def labels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEATLAB_FREE_MODEL", _MODEL)
    monkeypatch.setenv("SEATLAB_FREE_PROVIDER", _PROVIDER)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("SEATLAB_OPENROUTER_OWNERS_DIR", raising=False)


@pytest.fixture
def harbor(tmp_path: Path) -> Path:
    path = tmp_path / "bin" / "harbor"
    path.parent.mkdir()
    path.write_text("never executed\n", encoding="utf-8")
    return path


@pytest.fixture
def packet(
    labels: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> tuple[Path, str]:
    _forbid_native(monkeypatch)
    out = tmp_path / "registration"
    assert panel.main(["--plan-only", "--out", str(out), *_DESIGN]) == 0
    path = out / "registration.json"
    pin = _sha256(path.read_bytes())
    stdout = capsys.readouterr().out
    assert str(path.resolve()) in stdout
    assert pin in stdout
    return path, pin


def test_plan_only_writes_one_canonical_pinned_registration(
    packet: tuple[Path, str],
) -> None:
    path, pin = packet
    raw = path.read_bytes()
    registration = json.loads(raw)
    assert registration.keys() == {"schema", "jobs", "plan", "workload"}
    assert _canonical(registration) == raw
    assert dispatch_plan.encode(registration) == raw
    assert dispatch_plan.read_pinned(path, pin) == registration
    assert [entry.name for entry in path.parent.iterdir()] == ["registration.json"]
    assert S_IMODE(path.parent.stat().st_mode) == 0o700
    assert S_IMODE(path.stat().st_mode) == 0o400

    plan = registration["plan"]
    assert registration["jobs"] == 2
    assert plan["policy"] == "hosted"
    assert plan["hosted_route"] == {"model": _MODEL, "provider": _PROVIDER}
    assert (plan["seeds"], plan["conditions"], plan["episodes"]) == (
        [17, 29],
        ["fresh", "pooled"],
        12,
    )
    assert plan["native"] == {
        "expected_version": panel.NATIVE_VERSION,
        "olam_base": panel.OLAM_BASE,
    }
    sources = plan["source_sha256"]
    assert {"seatlab/run_panel.py", "seatlab/dispatch_plan.py", _ADAPTER} <= set(
        sources
    )
    assert sources == {
        name: _sha256((panel.EXAMPLES_DIR / name).read_bytes()) for name in sources
    }
    assert set(_WORKLOAD) <= registration["workload"].keys()


def test_workload_bounds_one_hosted_chat_per_active_seat_per_team(
    packet: tuple[Path, str], tmp_path: Path
) -> None:
    registration = dispatch_plan.read_pinned(*packet)
    plan, workload = registration["plan"], registration["workload"]
    episodes = plan["episodes"]
    route = seat_agent._Route(
        plan["hosted_route"]["model"], plan["hosted_route"]["provider"], "Example"
    )
    calls = tokens = slots = shipments = 0
    role_calls = {"inventory": 0, "dispatch": 0}
    pooled = 0
    for cell in plan["cells"]:
        condition = cell["condition"]
        participants = panel._participants(plan["policy"], condition, episodes)
        slots += len(participants)
        shipments += episodes
        for participant in participants:
            role = participant["seat"]
            if participant["name"] == "nop":
                assert (role, condition) == ("inventory", "pooled")
                pooled += 1
                continue
            assert participant["import_path"] == "seat_agent:SeatAgent"
            assert participant["kwargs"] == {
                "policy": "hosted",
                "expected_episodes": episodes,
            }
            seat_agent.SeatAgent(tmp_path, **participant["kwargs"])
            view = _view(role, condition, episodes)
            assert len(seat_agent._checked_view(view, role)) == episodes
            request = seat_agent._model_request(route, role, view)
            assert request["model"] == _MODEL
            assert request["provider"]["only"] == [_PROVIDER]
            assert request["provider"]["allow_fallbacks"] is False
            user = json.loads(request["messages"][-1]["content"])
            assert len(user["cases"]) == episodes
            role_calls[role] += 1
            calls += 1
            tokens += request["max_tokens"]
    assert {key: workload[key] for key in _WORKLOAD} == {
        "native_teams": len(plan["cells"]),
        "shipment_cases": shipments,
        "native_seat_slots": slots,
        "pooled_nop_seats": pooled,
        "hosted_inventory_calls_max": role_calls["inventory"],
        "hosted_dispatch_calls_max": role_calls["dispatch"],
        "hosted_seat_calls_max": calls,
        "hosted_completion_tokens_max": tokens,
    }
    assert (len(plan["cells"]), shipments, slots, calls) == (4, 48, 8, 6)
    for condition in plan["conditions"]:
        for participant in panel._participants("arrival", condition, episodes):
            assert participant["kwargs"] in ({}, {"policy": "arrival"})


def test_check_plan_needs_no_route_labels_and_rejects_launch_extras(
    packet: tuple[Path, str],
    harbor: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path, pin = packet
    monkeypatch.delenv("SEATLAB_FREE_MODEL")
    monkeypatch.delenv("SEATLAB_FREE_PROVIDER")
    check = ["--check-plan", str(path), "--sha256", pin]
    assert panel.main(check) == 0
    assert pin in capsys.readouterr().out
    extras = [
        ["--out", str(tmp_path / "team")],
        ["--harbor", str(harbor)],
        ["--jobs", "2"],
        ["--policy", "hosted"],
        ["--seeds", "17,29"],
        ["--episodes", "12"],
        ["--conditions", "fresh,pooled"],
        ["--from-plan", str(path)],
        ["--plan-only"],
        ["--collect"],
    ]
    for extra in extras:
        assert _refused([*check, *extra]) == 2
    assert _refused(["--check-plan", str(path)]) == 2
    assert _refused(["--check-plan", str(path), "--sha256", _sha256(b"other")]) == 1
    assert not (tmp_path / "team").exists()
    assert _sha256(path.read_bytes()) == pin


def _resealed(
    registration: dict[str, Any], edit: Callable[[dict[str, Any]], None]
) -> bytes:
    plan = deepcopy(registration["plan"])
    edit(plan)
    return _canonical(dispatch_plan.seal(plan, registration["jobs"]))


def _raw_edit(
    registration: dict[str, Any], edit: Callable[[dict[str, Any]], None]
) -> bytes:
    value = deepcopy(registration)
    edit(value)
    return _canonical(value)


def _variant(case: str, raw: bytes, pin: str) -> tuple[bytes, str]:
    registration = json.loads(raw)
    if case == "wrong-pin":
        return raw, _sha256(b"other")
    if case == "malformed-pin":
        return raw, pin.upper()
    if case == "edited-under-old-pin":
        edited = raw.replace(b'"episodes":12', b'"episodes":13')
        assert edited != raw
        return edited, pin
    if case == "changed-source-digest":
        content = _resealed(
            registration,
            lambda plan: plan["source_sha256"].__setitem__(_ADAPTER, "0" * 64),
        )
    elif case == "dropped-source":
        content = _resealed(
            registration, lambda plan: plan["source_sha256"].pop(_ADAPTER)
        )
    elif case == "understated-workload":
        content = _raw_edit(
            registration,
            lambda value: value["workload"].__setitem__("hosted_seat_calls_max", 2),
        )
    elif case == "dropped-cell":
        content = _raw_edit(registration, lambda value: value["plan"]["cells"].pop())
    elif case == "dotted-provider":
        content = _raw_edit(
            registration,
            lambda value: value["plan"]["hosted_route"].__setitem__(
                "provider", "example.fp16"
            ),
        )
    elif case == "pretty-bytes":
        content = json.dumps(registration, indent=2, sort_keys=True).encode("utf-8")
    else:
        raise AssertionError(case)
    return content, _sha256(content)


@pytest.mark.parametrize(
    "case",
    [
        "wrong-pin",
        "malformed-pin",
        "edited-under-old-pin",
        "changed-source-digest",
        "dropped-source",
        "understated-workload",
        "dropped-cell",
        "dotted-provider",
        "pretty-bytes",
    ],
)
def test_altered_registration_is_refused_before_native_boundaries(
    case: str, packet: tuple[Path, str], harbor: Path, tmp_path: Path
) -> None:
    path, pin = packet
    raw = path.read_bytes()
    content, variant_pin = _variant(case, raw, pin)
    variant = tmp_path / "variant" / "registration.json"
    variant.parent.mkdir()
    variant.write_bytes(content)
    team = tmp_path / "team"
    assert _refused(["--check-plan", str(variant), "--sha256", variant_pin]) == 1
    launch = ["--from-plan", str(variant), "--sha256", variant_pin]
    assert _refused([*launch, "--harbor", str(harbor), "--out", str(team)]) == 1
    assert not team.exists()
    assert variant.read_bytes() == content
    assert path.read_bytes() == raw


def test_from_plan_refuses_ambient_drift_flags_and_links_before_native(
    packet: tuple[Path, str],
    harbor: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, pin = packet
    raw = path.read_bytes()
    team = tmp_path / "team"
    launch = ["--from-plan", str(path), "--sha256", pin, "--harbor", str(harbor)]
    for flag, value in (
        ("--jobs", "1"),
        ("--jobs", "2"),
        ("--policy", "oracle"),
        ("--seeds", "17"),
        ("--episodes", "3"),
        ("--conditions", "fresh"),
    ):
        assert _refused([*launch, "--out", str(team), flag, value]) == 2
    assert (
        _refused(
            ["--from-plan", str(path), "--harbor", str(harbor), "--out", str(team)]
        )
        == 2
    )

    link = tmp_path / "linked.json"
    link.symlink_to(path)
    linked = ["--from-plan", str(link), "--sha256", pin, "--harbor", str(harbor)]
    assert _refused([*linked, "--out", str(team)]) == 1

    for name, value in (
        ("SEATLAB_FREE_PROVIDER", "example/bf16"),
        ("SEATLAB_FREE_MODEL", "example/other:free"),
    ):
        with monkeypatch.context() as drift:
            drift.setenv(name, value)
            assert _refused([*launch, "--out", str(team)]) == 1
    assert not team.exists()
    assert path.read_bytes() == raw


def test_existing_or_unwritable_outputs_never_change_the_packet(
    packet: tuple[Path, str],
    harbor: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, pin = packet
    raw, listing = path.read_bytes(), sorted(path.parent.iterdir())
    assert _refused(["--plan-only", "--out", str(path.parent), *_DESIGN]) == 1
    changed = [*_DESIGN[:-1], "arrival"]
    assert _refused(["--plan-only", "--out", str(path.parent), *changed]) == 1
    launch = ["--from-plan", str(path), "--sha256", pin, "--harbor", str(harbor)]
    assert _refused([*launch, "--out", str(path.parent)]) == 1

    elsewhere = tmp_path / "elsewhere"
    link = tmp_path / "link"
    link.symlink_to(elsewhere, target_is_directory=True)
    assert _refused(["--plan-only", "--out", str(link), *_DESIGN]) == 1
    assert not elsewhere.exists()

    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory\n", encoding="utf-8")
    assert _refused(["--plan-only", "--out", str(blocker / "child"), *_DESIGN]) == 1

    fresh = tmp_path / "fresh"
    monkeypatch.setenv("SEATLAB_FREE_PROVIDER", "example.fp16")
    assert _refused(["--plan-only", "--out", str(fresh), *_DESIGN]) == 1
    assert not fresh.exists()

    assert path.read_bytes() == raw
    assert sorted(path.parent.iterdir()) == listing
    assert S_IMODE(path.stat().st_mode) == 0o400


@pytest.mark.parametrize(
    ("provider", "accepted"),
    [
        ("example/fp16", True),
        ("example", True),
        ("deep-infra/bf16/turbo", True),
        ("example.fp16", False),
        ("example_fp16", False),
        ("example/fp_16", False),
        ("Example/fp16", False),
        ("example/", False),
    ],
)
def test_registered_provider_slug_matches_native_adapter(
    provider: str,
    accepted: bool,
    labels: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_native(monkeypatch)
    monkeypatch.setenv("SEATLAB_FREE_PROVIDER", provider)
    try:
        seat_agent.SeatAgent(tmp_path, policy="hosted", expected_episodes=12)
    except ValueError:
        adapter = False
    else:
        adapter = True
    out = tmp_path / "registration"
    argv = ["--plan-only", "--out", str(out), *_DESIGN]
    registered = panel.main(argv) == 0 if accepted else _refused(argv) == 0
    assert (registered, adapter) == (accepted, accepted)
    assert out.exists() is accepted


def test_from_plan_hands_only_the_held_packet_to_launch(
    packet: tuple[Path, str],
    harbor: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, pin = packet
    registration = dispatch_plan.read_pinned(path, pin)
    monkeypatch.delenv("SEATLAB_FREE_MODEL")
    monkeypatch.delenv("SEATLAB_FREE_PROVIDER")
    inert: dict[str, str] = {"PATH": os.defpath}
    seen: dict[str, Any] = {}

    def held_env(route: dict[str, str] | None) -> dict[str, str]:
        seen["route"] = route
        return inert

    def held_check(executable: Path, env: dict[str, str]) -> None:
        seen["check"] = (executable, env)

    async def held_launch(
        out: Path,
        plan: Any,
        sources: dict[str, bytes],
        executable: Path,
        env: dict[str, str],
        jobs: int,
    ) -> bool:
        seen["launch"] = (out, plan, sources, executable, env, jobs)
        return True

    monkeypatch.setattr(panel, "_native_env", held_env)
    monkeypatch.setattr(panel, "_check_native", held_check)
    monkeypatch.setattr(panel, "_launch", held_launch)
    team = tmp_path / "team"
    argv = ["--from-plan", str(path), "--sha256", pin, "--harbor", str(harbor)]
    assert panel.main([*argv, "--out", str(team)]) == 0

    plan = registration["plan"]
    assert seen["route"] == plan["hosted_route"]
    assert seen["route"] == {"model": _MODEL, "provider": _PROVIDER}
    assert seen["check"] == (harbor.resolve(), inert)
    out, launched, sources, executable, env, jobs = seen["launch"]
    assert (out, executable, env) == (team.resolve(), harbor.resolve(), inert)
    assert launched == plan
    assert jobs == registration["jobs"] == 2
    assert {name: _sha256(content) for name, content in sources.items()} == plan[
        "source_sha256"
    ]
    assert (team / "registration.json").read_bytes() == path.read_bytes()
    assert json.loads((team / "plan.json").read_text(encoding="utf-8")) == plan
    assert not (team / "cells").exists()
    assert not (team / "runs.json").exists()
