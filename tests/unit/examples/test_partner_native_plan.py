import hashlib
import json
import os
import stat
from copy import deepcopy
from pathlib import Path

import pytest

from examples.joint_reopen import study, support
from examples.native_evidence import Record
from examples.seatlab.partner_native import plan
from examples.seatlab.partner_switch import engine

START = 19


def _pin(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _raw(value: Record) -> bytes:
    return json.dumps(value, indent=2, ensure_ascii=True).encode("ascii") + b"\n"


def _repin(root: Path, manifest: Record, registration: Record) -> str:
    raw = _raw(registration)
    (root / "registration.json").write_bytes(raw)
    manifest["registration_sha256"] = _pin(raw)
    raw = _raw(manifest)
    (root / "manifest.json").write_bytes(raw)
    return _pin(raw)


@pytest.fixture(scope="module")
def native_plan(tmp_path_factory):
    folder = tmp_path_factory.mktemp("partner-native-rosters")
    teams = []
    for name in ("other", "first", "third"):
        path = folder / f"{name}.json"
        path.write_bytes(
            _raw(
                {
                    "participants": [
                        {
                            "seat": role,
                            "import_path": study.OWNER_IMPORT_PATH,
                            "model_name": f"local/{name}-{role}",
                            "skip_model_parsing": True,
                        }
                        for role in ("west", "east")
                    ]
                }
            )
        )
        teams.append((name, path))
    return plan.build(START, teams, None)


@pytest.fixture
def saved(tmp_path, native_plan):
    manifest, registration = deepcopy(native_plan)
    root = tmp_path / "plan"
    pin = plan.save(root, manifest, registration)
    return root, pin, manifest, registration


def test_native_crossplay_layout_and_pinned_private_files(saved):
    root, pin, manifest, registration = saved
    names = ["other", "first", "third"]
    assert manifest == {
        "schema": "seatlab.partner-native/v1",
        "mode": "native-rosters",
        "start": START,
        "names": names,
        "registration_sha256": _pin((root / "registration.json").read_bytes()),
    }
    assert pin == _pin((root / "manifest.json").read_bytes())
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert {path.name for path in root.iterdir()} == {
        "manifest.json",
        "registration.json",
    }
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in root.iterdir())
    assert plan.load(root, pin) == (manifest, registration)
    assert study.load_registration(root / "registration.json") == registration
    files = sorted(
        p
        for p in study.TASK_DIR.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )
    package = hashlib.sha256()
    historical = hashlib.sha256()
    for path in files:
        name = path.relative_to(study.TASK_DIR).as_posix()
        line = f"{name}\0{hashlib.sha256(path.read_bytes()).hexdigest()}\n".encode()
        package.update(line)
        if not name.startswith("instructions/"):
            historical.update(line)
    assert registration["task"] == {
        **support.TASK,
        "digest": "sha256:" + package.hexdigest(),
    }
    assert (
        registration["task"]["digest"]
        != support.TASK["digest"]
        == "sha256:" + historical.hexdigest()
    )
    seeds = {case.seed for case in engine.cases(START)}
    assert len(seeds) == 6
    expected = set()
    for i, east in enumerate(names):
        for j, west in enumerate(names):
            pair = f"p{i:02}-{j:02}"
            members = registration["teams"][pair]
            assert [member["seat"] for member in members] == ["east", "west"]
            assert [member["model_name"] for member in members] == [
                f"local/{east}-east",
                f"local/{west}-west",
            ]
            expected.update((pair, seed, "both", "deliver") for seed in seeds)
    cells = registration["cells"]
    actual = {(c["team"], c["seed"], c["fixture"], c["channel"]) for c in cells}
    assert actual == expected
    assert len({c["ticket"] for c in cells}) == len(cells) == 54
    with pytest.raises(ValueError):
        plan.save(root, manifest, registration)
    assert plan.load(root, pin) == (manifest, registration)


@pytest.mark.parametrize("control", ["same", "different"])
def test_control_registers_two_labels_with_public_model_bindings(tmp_path, control):
    manifest, registration = plan.build(START, None, control)
    active = "".join(engine.cases(START)[0].active)
    assert manifest["names"] == ["alpha", "beta"]
    assert manifest["mode"] == f"scripted-{control}"
    assert len(registration["cells"]) == 24
    for name, members in registration["teams"].items():
        i, j = (int(index) for index in name[1:].split("-"))
        for index, member in zip((i, j), members, strict=True):
            direction = "reverse" if control == "different" and index else "forward"
            assert member["model_name"] == f"seatlab-switch/{direction}-{active}"
            assert member["import_path"] == study.OWNER_IMPORT_PATH
            assert member["skip_model_parsing"] is True
            assert set(member) == study.OWNER_FIELDS
    root = tmp_path / "control"
    pin = plan.save(root, manifest, registration)
    assert plan.load(root, pin) == (manifest, registration)
    manifest["mode"] = "scripted-different" if control == "same" else "scripted-same"
    with pytest.raises(ValueError):
        plan.load(root, _repin(root, manifest, registration))


@pytest.mark.parametrize(
    "mutation", ["role", "fixture", "channel", "gap", "ticket", "task", "pair"]
)
def test_rejects_same_count_wrong_layout_even_after_repinning(saved, mutation):
    root, _, manifest, registration = saved
    teams = registration["teams"]
    cells = registration["cells"]
    if mutation == "role":
        teams["p00-01"][0] = deepcopy(teams["p02-02"][0])
    elif mutation == "fixture":
        cells[0]["fixture"] = "east"
    elif mutation == "channel":
        cells[0]["channel"] = "withhold"
    elif mutation == "gap":
        replacement = next(c for c in cells[1:] if c["team"] == cells[0]["team"])
        cells[0]["seed"] = replacement["seed"]
    elif mutation == "ticket":
        cells[0]["ticket"] = cells[1]["ticket"]
    elif mutation == "task":
        registration["task"]["digest"] = "sha256:" + "f" * 64
    else:
        teams["renamed"] = teams.pop("p00-01")
        for cell in cells:
            if cell["team"] == "p00-01":
                cell["team"] = "renamed"
    with pytest.raises(ValueError):
        plan.load(root, _repin(root, manifest, registration))


def test_invalid_pins_and_changed_raw_bytes(saved):
    root, pin, manifest, registration = saved
    with pytest.raises(ValueError):
        plan.load(root, pin.removeprefix("sha256:"))
    with pytest.raises(ValueError):
        plan.load(root, "sha256:" + "0" * 64)
    (root / "registration.json").write_bytes(_raw(registration) + b" ")
    with pytest.raises(ValueError):
        plan.load(root, pin)
    (root / "registration.json").write_bytes(_raw(registration))
    duplicate = _raw(manifest).replace(b'"start": 19', b'"start": 19, "start": 19')
    (root / "manifest.json").write_bytes(duplicate)
    with pytest.raises(ValueError):
        plan.load(root, _pin(duplicate))


@pytest.mark.parametrize("relative", [".", "manifest.json", "registration.json"])
def test_rejects_symlink_paths(saved, relative):
    root, pin, _, _ = saved
    relative_path = root / relative
    moved = relative_path.with_name(relative_path.name + "-original")
    relative_path.rename(moved)
    relative_path.symlink_to(moved, target_is_directory=relative == ".")
    with pytest.raises(ValueError):
        plan.load(root, pin)


def test_rejects_nonregular_registration(saved):
    root, pin, _, _ = saved
    path = root / "registration.json"
    path.unlink()
    os.mkfifo(path)
    with pytest.raises(ValueError):
        plan.load(root, pin)


@pytest.mark.parametrize(
    ("start", "teams", "control"),
    [
        (START, None, None),
        (START, [], "same"),
        (START, [], None),
        (START, None, "unknown"),
        (True, None, "same"),
        (study.MAX_SEED + 1, None, "same"),
    ],
)
def test_invalid_build_modes_and_limits(start, teams, control):
    with pytest.raises(ValueError):
        plan.build(start, teams, control)


@pytest.mark.parametrize(
    "names", [["one"], ["one", "one"], ["a", "b", "c", "d", "e", "f"]]
)
def test_invalid_explicit_team_names_and_counts(tmp_path, native_plan, names):
    _, registration = native_plan
    path = tmp_path / "roster.json"
    path.write_bytes(_raw({"participants": registration["teams"]["p00-00"]}))
    with pytest.raises(ValueError):
        plan.build(START, [(name, path) for name in names], None)
