import hashlib
import json
import re
import shutil
import stat
from itertools import product
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.receiver_memory import revised
from examples.seatlab.receiver_memory.cohort import PRICE, balanced_seeds
from examples.seatlab.receiver_memory.provenance import AGES, COINS, offer, reread
from examples.seatlab.receiver_memory.study import manifest


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture(scope="module")
def sealed(tmp_path_factory):
    root = tmp_path_factory.mktemp("receiver-study") / "sealed"
    receipt = manifest.seal(root, 0, "truthful", "scripted_control")
    return root, receipt


@pytest.fixture
def copy(sealed, tmp_path):
    root, receipt = sealed
    other = tmp_path / "other"
    shutil.copytree(root, other)
    return other, receipt["sha256"]


def _repin(root: Path, plan: dict[str, Any]) -> str:
    data = json.dumps(plan).encode("utf-8") + b"\n"
    (root / "plan.json").write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def test_seal_pins_balanced_native_frames_and_private_files(sealed):
    root, receipt = sealed
    raw = (root / "plan.json").read_bytes()
    pin = hashlib.sha256(raw).hexdigest()
    assert receipt == {
        "schema": manifest.SCHEMA,
        "sha256": pin,
        "assignments": 96,
        "model_calls": 0,
        "trials": 0,
    }
    assert _mode(root) == _mode(root / "attempts") == 0o700
    assert _mode(root / "plan.json") == 0o600
    plan = manifest.load(root, pin)
    assert plan["source"] == "scripted_control"
    rows = plan["assignments"]
    seeds = balanced_seeds(0)
    assert {
        (r["seed"], r["world"], r["age"], r["arm"], r["coin"]) for r in rows
    } == set(
        product(
            seeds.values(), ("confirm", "replace"), AGES, ("version", "sham"), COINS
        )
    )
    assert len({row["ticket"] for row in rows}) == 96
    assert all(re.fullmatch(r"[0-9a-f]{32}", r["ticket"]) for r in rows)
    seed = next(iter(seeds.values()))
    episodes = {case.name: case for case in revised.cases(seed, "truthful")}
    for row in (r for r in rows if r["seed"] == seed):
        case = episodes[row["world"]]
        frame = offer(case, row["age"], row["arm"], row["coin"], PRICE)
        assert row["offer_sha256"] == hashlib.sha256(frame + b"\n").hexdigest()
        assert row["read_sha256"] == hashlib.sha256(reread(case) + b"\n").hexdigest()
        assert row["scores"] == case.scores
        assert manifest.pick(plan, row["ticket"]) == row
    with pytest.raises(ValueError):
        manifest.pick(plan, "../plan.json")
    with pytest.raises(ValueError):
        manifest.pick(plan, "0" * 32)


def test_redraw_has_disjoint_tickets_and_same_cells(sealed, tmp_path):
    first, receipt = sealed
    second = tmp_path / "redraw"
    new_receipt = manifest.seal(second, 0, "truthful", "external_unverified")
    old = manifest.load(first, receipt["sha256"])["assignments"]
    new = manifest.load(second, new_receipt["sha256"])["assignments"]
    assert {r["ticket"] for r in old}.isdisjoint(r["ticket"] for r in new)

    def cells(rows):
        return sorted(
            json.dumps({k: v for k, v in row.items() if k != "ticket"}, sort_keys=True)
            for row in rows
        )

    assert cells(old) == cells(new)
    assert [r["seed"] for r in old] != [r["seed"] for r in new]
    assert (
        manifest.load(second, new_receipt["sha256"])["source"] == "external_unverified"
    )
    with pytest.raises(ValueError, match="pin mismatch"):
        manifest.load(second, receipt["sha256"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("seed", 999_999),
        ("world", "unknown"),
        ("coin", "3"),
        ("arm", "erased"),
        ("scores", {"A": False, "B": 0, "C": 1}),
        ("offer_sha256", "f" * 63),
        ("read_sha256", "z" * 64),
        ("ticket", "../plan.json"),
    ],
)
def test_even_repinned_malformed_assignments_are_rejected(copy, field, value):
    root, pin = copy
    plan = manifest.load(root, pin)
    plan["assignments"][0][field] = value
    changed_pin = _repin(root, plan)
    with pytest.raises(ValueError, match="pin mismatch"):
        manifest.load(root, pin)
    with pytest.raises(ValueError):
        manifest.load(root, changed_pin)


@pytest.mark.parametrize(
    "damage", ["missing", "duplicate_cell", "duplicate_ticket", "read", "score"]
)
def test_even_repinned_unbalanced_roster_is_rejected(copy, damage):
    root, pin = copy
    plan = manifest.load(root, pin)
    rows = plan["assignments"]
    if damage == "missing":
        rows.pop()
    elif damage == "duplicate_cell":
        rows[-1] = {**rows[0], "ticket": rows[-1]["ticket"]}
    elif damage == "duplicate_ticket":
        rows[-1]["ticket"] = rows[0]["ticket"]
    elif damage == "read":
        rows[0]["read_sha256"] = "0" * 64
    else:
        scores = rows[0]["scores"]
        winner = next(slot for slot, score in scores.items() if score)
        scores[winner] = 0
        scores[next(slot for slot in scores if slot != winner)] = 1
    with pytest.raises(ValueError):
        manifest.load(root, _repin(root, plan))


@pytest.mark.parametrize("relative", [".", "attempts", "plan.json"])
def test_public_modes_rejected(copy, relative):
    root, pin = copy
    (root / relative).chmod(0o644 if relative == "plan.json" else 0o755)
    with pytest.raises(ValueError, match="mode"):
        manifest.load(root, pin)


@pytest.mark.parametrize("relative", [".", "attempts", "plan.json"])
def test_symlink_paths_rejected(copy, relative):
    root, pin = copy
    path = root / relative
    real = path.with_name(path.name + "-real")
    path.rename(real)
    path.symlink_to(real)
    with pytest.raises(ValueError):
        manifest.load(root, pin)


def test_exclusive_atomic_records_and_bad_readers(copy):
    root, pin = copy
    outcome = root / "attempts" / "record.json"
    manifest.publish_private(outcome, b'{"x":1}\n')
    assert _mode(outcome) == 0o600
    assert manifest.load_private_json(outcome) == {"x": 1}
    with pytest.raises(FileExistsError):
        manifest.publish_private(outcome, b'{"x":2}\n')
    assert manifest.load_private_json(outcome) == {"x": 1}
    assert list(outcome.parent.iterdir()) == [outcome]
    alias = outcome.with_name("alias.json")
    alias.symlink_to(outcome)
    with pytest.raises(ValueError):
        manifest.load_private_json(alias)
    with pytest.raises(FileExistsError):
        manifest.publish_private(alias, b"{}\n")
    assert alias.is_symlink()
    outcome.chmod(0o644)
    with pytest.raises(ValueError):
        manifest.load_private_json(outcome)
    outcome.chmod(0o600)
    for data in (
        b'{"x":1,"x":2}',
        b'{"x":NaN}',
        b'{"x":1.0}',
        b"\xff",
        b" " * (1 << 20),
    ):
        outcome.write_bytes(data)
        with pytest.raises(ValueError):
            manifest.load_private_json(outcome)
    with pytest.raises(ValueError):
        manifest.seal(root, 0, "truthful", "scripted_control")
    assert manifest.load(root, pin)["assignments"]
    assert alias.is_symlink()
