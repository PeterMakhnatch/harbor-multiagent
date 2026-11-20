from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.visibility import calendar_replica_series as series

_Row = dict[str, Any]


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class _Archive:
    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = root
        self.root.mkdir(mode=0o700)
        self.reports: dict[Path, _Row] = {}
        self.replayed: list[Path] = []
        monkeypatch.setattr(series.calendar_replica_report, "analyze", self._replay)

    def _replay(self, block: Path) -> _Row:
        self.replayed.append(block)
        return self.reports[block]

    def put(
        self,
        world: int,
        direction: int,
        repetition: int,
        *,
        rewards: tuple[int | None, int | None] = (0, 1),
        equal_input: bool = True,
        invalid: str | None = None,
    ) -> _Row:
        base = f"rx-w{world:02d}-d{direction}-r{repetition}"
        path = self.root / base / "block"
        path.parent.mkdir(mode=0o700)
        path.mkdir(mode=0o700)
        roles = (f"{base}:cal_initial_p1", f"{base}:cal_initial_p2")
        report: _Row = {
            "schema": "seatlab.calendar-replica-report/v1",
            "world": world,
            "direction": direction,
            "repetition": repetition,
            "status": "partial" if None in rewards else "complete",
            "native_sha256": _digest(f"native:{world}"),
            "semantics_sha256": _digest(f"semantics:{world}"),
            "arms": {},
            "seeds": {seat: {"status": "accepted"} for seat in ("p1", "p2")},
            "equal_final_prompt": equal_input if None not in rewards else None,
            "equal_choice": rewards[0] == rewards[1]
            if None not in rewards and invalid is None
            else None,
            "turns": {
                role: {
                    "prompt_sha256": _digest(role),
                    "response_sha256": _digest(f"r:{role}"),
                }
                for role in roles
            },
        }
        for name, reward in zip(("blind", "peer"), rewards, strict=True):
            run = f"{base}-{name}"
            sender = _digest(f"{run}:sender")
            receiver = _digest(f"{base if equal_input else run}:receiver")
            arm = {
                "status": "not_started",
                "reward": reward,
                "slot": None,
                "sender_prompt_sha256": None,
                "receiver_prompt_sha256": None,
                "trial_id_sha256": None,
                "native_timeline_verified": False,
                "usage": {"p1": None, "p2": None},
            }
            if reward is not None:
                arm.update(
                    status="model_invalid_scored_zero"
                    if name == invalid
                    else "complete",
                    slot=None if name == invalid else reward,
                    sender_prompt_sha256=sender,
                    receiver_prompt_sha256=receiver,
                    trial_id_sha256=_digest(f"trial:{run}"),
                    native_timeline_verified=True,
                )
                for role, prompt in (("cal_recheck", sender), ("cal_decide", receiver)):
                    report["turns"][f"{run}:{role}"] = {
                        "prompt_sha256": prompt,
                        "response_sha256": _digest(f"r:{run}:{role}"),
                    }
            report["arms"][name] = arm
        report["counted_turns"] = len(report["turns"])
        self.reports[path] = report
        return report

    def analyze(self) -> _Row:
        return series.analyze(self.root)


@pytest.fixture
def archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Archive:
    return _Archive(tmp_path.resolve() / "study", monkeypatch)


def test_complete_panel_uses_registered_order_and_needs_no_meter(
    archive: _Archive,
) -> None:
    expected = [
        (world, direction, repetition)
        for repetition in (0, 1)
        for world in (0, 4, 8)
        for direction in (0, 1)
    ]
    for coords in reversed(expected):
        archive.put(*coords)

    result = archive.analyze()
    assert result["schema"] == "seatlab.calendar-replica-series/v1"
    assert result["status"] == "complete"
    assert [
        (row["world"], row["direction"], row["repetition"]) for row in result["rows"]
    ] == expected
    expected_ids = [f"rx-w{w:02d}-d{d}-r{r}" for w, d, r in expected]
    assert [path.parent.name for path in archive.replayed] == expected_ids
    assert [row["block"] for row in result["rows"]] == expected_ids
    summary = result["summary"]
    assert summary["planned_pairs"] == summary["complete_pairs"] == 12
    assert summary["all_pairs_complete"] is True
    assert summary["bound_relay_receipts"] == 72
    assert summary["equal_world_mean_peer_minus_blind"] == 1
    assert summary["arms"]["blind"]["native_game_sends_delivered"] == 24
    assert summary["arms"]["peer"]["native_game_sends_delivered"] == 24


def test_missing_world_withholds_kpi_and_one_sided_zero_remains_scored(
    archive: _Archive,
) -> None:
    first = archive.put(0, 0, 0, rewards=(0, None))
    archive.put(0, 1, 0, rewards=(None, 1))
    archive.put(4, 0, 0, rewards=(1, 0), invalid="peer")
    # An adapter receipt count or usage field is not a native delivery or a bill.
    first["counted_turns"] = 1000
    first["arms"]["blind"]["usage"]["p1"] = {"input_tokens": 1000}
    first["arms"]["blind"]["private_file"] = "/private/native/hidden"

    result = archive.analyze()
    assert result["status"] == "partial"
    assert len(result["rows"]) == 12
    summary = result["summary"]
    assert summary["planned_pairs"] == 12
    assert summary["complete_pairs"] == 1
    assert summary["all_pairs_complete"] is False
    assert summary["supported_worlds"] == [4]
    assert summary["equal_world_mean_peer_minus_blind"] is None
    assert summary["status_counts"] == {"not_started": 9, "partial": 2, "complete": 1}
    assert summary["bound_relay_receipts"] == 14
    assert summary["paired"]["blind_only_win"] == 1
    blind, peer = (summary["arms"][arm] for arm in ("blind", "peer"))
    assert (
        blind["wins"],
        blind["valid_choice_losses"],
        blind["model_invalid_scored_zero"],
    ) == (1, 1, 0)
    assert (
        peer["wins"],
        peer["valid_choice_losses"],
        peer["model_invalid_scored_zero"],
    ) == (1, 0, 1)
    assert blind["unknown"] == peer["unknown"] == 10
    assert (
        blind["native_game_sends_delivered"] == peer["native_game_sends_delivered"] == 4
    )
    assert blind["planned_native_game_sends"] == peer["planned_native_game_sends"] == 24
    world_zero = summary["by_world"]["0"]["arms"]
    assert world_zero["blind"]["valid_choice_losses"] == 1
    assert world_zero["blind"]["wins"] == 0
    assert world_zero["peer"]["wins"] == 1
    assert world_zero["peer"]["valid_choice_losses"] == 0
    public = json.dumps(result)
    for private in ("input_tokens", "private_file", str(archive.root)):
        assert private not in public


def test_validated_native_sends_do_not_require_both_model_turns(
    archive: _Archive,
) -> None:
    report = archive.put(0, 0, 0, rewards=(0, None), invalid="blind")
    blind = report["arms"]["blind"]
    blind["receiver_prompt_sha256"] = None
    del report["turns"]["rx-w00-d0-r0-blind:cal_decide"]
    failed = report["arms"]["peer"]
    failed.update(status="native_failure", trial_id_sha256=_digest("failed trial"))
    report["turns"]["rx-w00-d0-r0-peer:cal_recheck"] = {
        "prompt_sha256": _digest("failed prompt"),
        "response_sha256": _digest("failed response"),
    }

    summary = archive.analyze()["summary"]
    assert summary["bound_relay_receipts"] == 4
    assert summary["arms"]["blind"]["native_game_sends_delivered"] == 2
    assert summary["arms"]["peer"]["native_game_sends_delivered"] == 0
    assert summary["arms"]["blind"]["model_invalid_scored_zero"] == 1
    assert summary["arms"]["peer"]["scored"] == 0


def test_world_kpi_weights_worlds_equally_with_partial_support(
    archive: _Archive,
) -> None:
    archive.put(0, 0, 0)
    archive.put(0, 1, 1)
    archive.put(4, 1, 0, rewards=(1, 0))
    archive.put(8, 1, 1, rewards=(0, 0))

    summary = archive.analyze()["summary"]
    assert summary["supported_worlds"] == [0, 4, 8]
    assert summary["equal_world_mean_peer_minus_blind"] == 0
    assert summary["paired"] == {
        "peer_only_win": 2,
        "blind_only_win": 1,
        "both_win": 0,
        "both_zero": 1,
        "mean_peer_minus_blind": 0.25,
    }
    assert [group["complete_pairs"] for group in summary["by_world"].values()] == [
        2,
        1,
        1,
    ]
    assert [group["complete_pairs"] for group in summary["by_direction"].values()] == [
        1,
        3,
    ]
    assert [group["complete_pairs"] for group in summary["by_repetition"].values()] == [
        2,
        2,
    ]


def test_equal_inputs_and_different_choices_use_measured_choices_only(
    archive: _Archive,
) -> None:
    same = archive.put(0, 0, 0)
    archive.put(0, 1, 0, equal_input=False)
    archive.put(0, 0, 1, rewards=(1, 1))
    archive.put(0, 1, 1, rewards=(0, 1), invalid="blind")

    result = archive.analyze()
    assert result["summary"]["final_input"] == {
        "comparable_input_pairs": 4,
        "equal_input_pairs": 3,
        "different_input_pairs": 1,
        "equal_input_comparable_choice_pairs": 2,
        "equal_input_different_choice_pairs": 1,
        "equal_input_same_choice_pairs": 1,
    }
    assert result["summary"]["paired"]["both_win"] == 1
    public_report = result["rows"][0]["report"]
    assert public_report["turns"] == same["turns"]
    assert public_report["equal_final_prompt"] is True
    assert public_report["equal_choice"] is False
    assert public_report["native_sha256"] == same["native_sha256"]
    assert (
        public_report["arms"]["blind"]["trial_id_sha256"]
        == same["arms"]["blind"]["trial_id_sha256"]
    )


def test_reversed_directions_do_not_publish_copied_block_evidence(
    archive: _Archive,
) -> None:
    left = archive.put(0, 0, 0)
    right = archive.put(0, 1, 0)
    left["direction"], right["direction"] = 1, 0

    result = archive.analyze()
    assert result["status"] == "invalid_evidence"
    assert (
        result["summary"]["complete_pairs"]
        == result["summary"]["bound_relay_receipts"]
        == 0
    )
    for row, planned_direction in zip(result["rows"][:2], (0, 1), strict=True):
        assert row["direction"] == planned_direction
        assert row["status"] == "invalid_evidence"
        assert row["reason"] == "coordinate_mismatch"
        assert row["report"] is None
    assert result["rows"][2]["status"] == "not_started"


@pytest.mark.parametrize("digest", ["native_sha256", "semantics_sha256"])
def test_rejects_both_nonidentical_source_bindings_within_world(
    archive: _Archive, digest: str
) -> None:
    archive.put(0, 0, 0)
    second = archive.put(0, 1, 0)
    second[digest] = _digest("different source")
    archive.put(4, 0, 0)

    result = archive.analyze()
    assert result["status"] == "invalid_evidence"
    assert result["summary"]["complete_pairs"] == 1
    assert result["summary"]["supported_worlds"] == [4]
    for row in result["rows"][:2]:
        assert row["reason"] == "mixed_world_source_identity"
        assert row["report"] is None
    assert result["rows"][2]["status"] == "complete"


def test_rejects_trial_reused_by_a_partial_block(archive: _Archive) -> None:
    first = archive.put(0, 0, 0)
    second = archive.put(4, 1, 0, rewards=(None, None))
    second["arms"]["peer"].update(
        status="missing_evidence",
        trial_id_sha256=first["arms"]["blind"]["trial_id_sha256"],
    )

    result = archive.analyze()
    assert result["status"] == "invalid_evidence"
    assert result["summary"]["complete_pairs"] == 0
    assert result["summary"]["arms"]["blind"]["scored"] == 0
    for index in (0, 3):
        assert result["rows"][index]["reason"] == "reused_trial_id"
        assert result["rows"][index]["report"] is None


@pytest.mark.parametrize(
    "kind", ["extra_slot", "slot_link", "native_link", "slot_file", "slot_extra"]
)
def test_rejects_malformed_or_nonphysical_layout(archive: _Archive, kind: str) -> None:
    slot = archive.root / "rx-w00-d0-r0"
    outside = archive.root.with_name("private-target")
    if kind == "extra_slot":
        (archive.root / "rx-w01-d0-r0").mkdir()
    elif kind == "slot_link":
        slot.symlink_to(outside, target_is_directory=True)  # Includes dangling targets.
    elif kind == "native_link":
        outside.mkdir()
        slot.mkdir()
        (slot / "block").symlink_to(outside, target_is_directory=True)
    elif kind == "slot_file":
        slot.touch()
    else:
        archive.put(0, 0, 0)
        (slot / "unregistered").touch()

    result = archive.analyze()
    assert result["status"] == "invalid_evidence"
    assert result["summary"]["complete_pairs"] == 0
    assert len(result["rows"]) == 12
    assert result["rows"][0]["report"] is None
    assert "private-target" not in json.dumps(result)
    assert not archive.replayed


def test_unbound_block_keeps_coordinates_only_in_planned_row(archive: _Archive) -> None:
    report = archive.put(0, 0, 0)
    report.update(
        world=None, direction=None, repetition=None, status="invalid_evidence"
    )

    row = archive.analyze()["rows"][0]
    assert (row["world"], row["direction"], row["repetition"]) == (0, 0, 0)
    assert row["reason"] == "unbound_block"
    assert row["report"] is None


def test_cli_prints_one_public_json_for_empty_archive(
    archive: _Archive, capsys: pytest.CaptureFixture[str]
) -> None:
    assert series.main([str(archive.root)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert result["status"] == "not_started"
    assert result["summary"]["complete_pairs"] == 0
    assert len(result["rows"]) == 12
