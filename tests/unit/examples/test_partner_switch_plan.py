import argparse
import json
import os
import stat
from pathlib import Path

import pytest

from examples.seatlab.convention_crossplay import ballots, loader
from examples.seatlab.coupled_retention import strategy
from examples.seatlab.partner_switch import planio
from examples.seatlab.partner_switch.engine import OPTIONS, ROLES

NAMES = ("beta", "alpha")
PACKETS = {
    (name, role): {
        f'{{"stage":"report","role":"{role}","own":["A","{slot}"]}}'.encode()
        for slot in ("B", "C")
    }
    for name in NAMES
    for role in ROLES
}
PARENTS = {
    "report_plan_sha256": loader.digest(b"report plan"),
    "report_captures_sha256": loader.digest(b"report captures"),
}


def _study(root: Path) -> planio.Plan:
    receipt = planio.write(root, "report", 19, NAMES, {}, PACKETS)
    return planio.load(root, receipt["study_sha256"])


def _captures(root: Path, study: Path, plan: planio.Plan) -> None:
    root.mkdir()
    for (name, role), deck in plan.decks.items():
        requests = study / deck.folder / "requests.jsonl"
        replies = root.parent / f"{root.name}-{deck.folder}.jsonl"
        rows = [json.loads(line) for line in requests.read_bytes().splitlines()]
        replies.write_text(
            "".join(
                json.dumps(
                    {
                        "id": row["id"],
                        "prompt_sha256": row["prompt_sha256"],
                        "reply": json.dumps({"slot": "A"}),
                    }
                )
                + "\n"
                for row in rows
            )
        )
        code, receipt = ballots.freeze(
            argparse.Namespace(
                deck=study / deck.folder,
                deck_sha256=deck.pin,
                role=role,
                source=name,
                replies=[replies],
                out=root / deck.folder,
            )
        )
        assert code == 0 and receipt["complete"]


def _rewrite(path: Path, value: object) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode()
    path.write_bytes(raw)
    return loader.digest(raw)


def test_round_trip_raw_packets_choices_and_private_decks(tmp_path):
    root = tmp_path / "study"
    plan = _study(root)
    assert (plan.stage, plan.start, plan.names, plan.parents) == (
        "report",
        19,
        NAMES,
        {},
    )
    assert list(plan.decks) == list(PACKETS)
    assert plan.pin == loader.digest((root / "study.json").read_bytes())
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "study.json").stat().st_mode) == 0o600
    for index, (key, deck) in enumerate(plan.decks.items()):
        assert deck.folder == f"t{index // 2:02}-{key[1]}"
        assert deck.packets == PACKETS[key]
        assert stat.S_IMODE((root / deck.folder).stat().st_mode) == 0o700
        for leaf in ("deck.json", "requests.jsonl"):
            assert stat.S_IMODE((root / deck.folder / leaf).stat().st_mode) == 0o600
            assert all(
                name.encode() not in (root / deck.folder / leaf).read_bytes()
                for name in NAMES
            )
    captures = tmp_path / "frozen"
    _captures(captures, root, plan)
    rules, pin = planio.recover(plan, captures)
    assert rules == {
        key: {strategy.digest(p): "A" for p in packets}
        for key, packets in PACKETS.items()
    }
    evidence = [
        [
            name,
            role,
            loader.digest((captures / deck.folder / "evidence.json").read_bytes()),
        ]
        for (name, role), deck in plan.decks.items()
    ]
    assert pin == loader.digest(
        planio.CAPTURE_DOMAIN + planio._encode([plan.pin, evidence])
    )
    assert planio.recover(plan, captures) == (rules, pin)
    receipt = captures / "t00-east" / "evidence.json"
    receipt.write_bytes(receipt.read_bytes() + b" ")
    same_rules, changed_pin = planio.recover(plan, captures)
    assert same_rules == rules and changed_pin != pin


@pytest.mark.parametrize(
    ("stage", "parents"),
    [
        ("report", PARENTS),
        ("vote", {}),
        ("vote", {"report_plan_sha256": PARENTS["report_plan_sha256"]}),
        ("vote", {**PARENTS, "other": PARENTS["report_plan_sha256"]}),
        ("vote", {**PARENTS, "report_captures_sha256": "0" * 64}),
        ("other", {}),
    ],
)
def test_wrong_stage_parents_rejected(tmp_path, stage, parents):
    out = tmp_path / "invalid"
    with pytest.raises(ValueError):
        planio.write(out, stage, 19, NAMES, parents, PACKETS)
    assert not out.exists()
    report = _study(tmp_path / "report")
    manifest = tmp_path / "report" / "study.json"
    data = json.loads(manifest.read_bytes())
    data.update(stage=stage, parents=parents)
    pin = _rewrite(manifest, data)
    assert pin != report.pin
    with pytest.raises(ValueError):
        planio.load(manifest.parent, pin)


def test_vote_preserves_both_parent_pins_and_does_not_overwrite(tmp_path):
    root = tmp_path / "vote"
    receipt = planio.write(root, "vote", 19, NAMES, PARENTS, PACKETS)
    plan = planio.load(root, receipt["study_sha256"])
    assert (plan.stage, plan.parents) == ("vote", PARENTS)
    with pytest.raises((ValueError, OSError)):
        planio.write(root, "vote", 20, NAMES, PARENTS, PACKETS)
    assert planio.load(root, plan.pin) == plan


@pytest.mark.parametrize(
    "filename", ["study.json", "t00-east/deck.json", "t00-east/requests.jsonl"]
)
def test_changed_manifest_or_deck_bytes_rejected(tmp_path, filename):
    root = tmp_path / "study"
    plan = _study(root)
    leaf = root / filename
    leaf.write_bytes(leaf.read_bytes() + b" ")
    with pytest.raises(ValueError):
        planio.load(root, plan.pin)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("role", "west"),
        ("actions", list(OPTIONS[:-1])),
        ("response_field", "action"),
    ],
)
def test_deck_settings_even_with_updated_pins(tmp_path, key, value):
    root = tmp_path / "study"
    _study(root)
    manifest = root / "t00-east" / "deck.json"
    data = json.loads(manifest.read_bytes())
    data[key] = value
    deck_pin = _rewrite(manifest, data)
    manifest = root / "study.json"
    data = json.loads(manifest.read_bytes())
    data["decks"][0]["deck_sha256"] = deck_pin
    pin = _rewrite(manifest, data)
    with pytest.raises(ValueError):
        planio.load(root, pin)


def test_manifest_count_must_match_exact_packet_count(tmp_path):
    root = tmp_path / "study"
    _study(root)
    path = root / "study.json"
    data = json.loads(path.read_bytes())
    data["decks"][0]["count"] += 1
    pin = _rewrite(path, data)
    with pytest.raises(ValueError):
        planio.load(root, pin)


def test_duplicate_ticket_across_decks_rejected(tmp_path):
    root = tmp_path / "study"
    _study(root)
    first = root / "t00-east" / "requests.jsonl"
    second = root / "t00-west" / "requests.jsonl"
    rows = [json.loads(row) for row in second.read_bytes().splitlines()]
    rows[0]["id"] = json.loads(first.read_bytes().splitlines()[0])["id"]
    raw = b"".join(json.dumps(row).encode() + b"\n" for row in rows)
    second.write_bytes(raw)
    manifest = second.with_name("deck.json")
    data = json.loads(manifest.read_bytes())
    data["requests_sha256"] = loader.digest(raw)
    deck_pin = _rewrite(manifest, data)
    study = root / "study.json"
    data = json.loads(study.read_bytes())
    data["decks"][1]["deck_sha256"] = deck_pin
    pin = _rewrite(study, data)
    with pytest.raises(ValueError):
        planio.load(root, pin)


@pytest.mark.parametrize("folder", ["t00-west", "t01-east"])
def test_capture_source_or_role_swap_rejected(tmp_path, folder):
    root = tmp_path / "study"
    plan = _study(root)
    captures = tmp_path / "frozen"
    _captures(captures, root, plan)
    path = captures / "t00-east"
    other = captures / folder
    temporary = captures / "temporary"
    path.rename(temporary)
    other.rename(path)
    temporary.rename(other)
    with pytest.raises(ValueError):
        planio.recover(plan, captures)


def test_same_shape_foreign_study_not_enrolled(tmp_path):
    root = tmp_path / "study"
    plan = _study(root)
    other = tmp_path / "other"
    foreign = _study(other)
    assert {k: d.packets for k, d in foreign.decks.items()} == PACKETS
    assert foreign.pin != plan.pin
    with pytest.raises(ValueError):
        planio.load(other, plan.pin)
    captures = tmp_path / "foreign-frozen"
    _captures(captures, other, foreign)
    with pytest.raises(ValueError):
        planio.recover(plan, captures)


@pytest.mark.parametrize("kind", ["wrong_packet", "wrong_slot"])
def test_capture_table_must_exactly_cover_raw_prompt_digests(tmp_path, kind):
    root = tmp_path / "study"
    plan = _study(root)
    captures = tmp_path / "frozen"
    _captures(captures, root, plan)
    folder = captures / "t00-east"
    path = folder / "strategy.json"
    table = json.loads(path.read_bytes())
    key = next(iter(table["rules"]))
    if kind == "wrong_packet":
        packet = next(p for p in PACKETS[NAMES[0], "east"] if strategy.digest(p) == key)
        table["rules"][strategy.digest(packet + b"\n")] = table["rules"].pop(key)
    else:
        table["rules"][key] = "G"
    table_pin = _rewrite(path, table)
    path = folder / "evidence.json"
    receipt = json.loads(path.read_bytes())
    receipt["table_sha256"] = table_pin
    _rewrite(path, receipt)
    with pytest.raises(ValueError):
        planio.recover(plan, captures)


@pytest.mark.parametrize(
    "relative",
    [".", "study.json", "t00-east", "t00-east/deck.json", "t00-east/requests.jsonl"],
)
def test_study_final_directory_or_leaf_symlink_rejected(tmp_path, relative):
    root = tmp_path / "study"
    plan = _study(root)
    path = root / relative
    saved = path.with_name(path.name + "-saved")
    path.rename(saved)
    path.symlink_to(saved, target_is_directory=saved.is_dir())
    with pytest.raises(ValueError):
        planio.load(root, plan.pin)


@pytest.mark.parametrize(
    "relative", [".", "t00-east", "t00-east/evidence.json", "t00-east/strategy.json"]
)
def test_capture_final_directory_or_leaf_symlink_rejected(tmp_path, relative):
    root = tmp_path / "study"
    plan = _study(root)
    captures = tmp_path / "frozen"
    _captures(captures, root, plan)
    path = captures / relative
    saved = path.with_name(path.name + "-saved")
    path.rename(saved)
    path.symlink_to(saved, target_is_directory=saved.is_dir())
    with pytest.raises(ValueError):
        planio.recover(plan, captures)


def test_nonregular_requests_rejected_without_opening_blocking_fifo(tmp_path):
    root = tmp_path / "study"
    plan = _study(root)
    path = root / "t00-east" / "requests.jsonl"
    path.unlink()
    os.mkfifo(path)
    with pytest.raises(ValueError):
        planio.load(root, plan.pin)
