import json
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.visibility import (
    boxes_packet,
    intent_packet,
    intent_protocol,
    ownership_evidence,
    ownership_protocol,
    ownership_report,
    ownership_source,
)

_PRIVATE = "never publish this private fixture message"


def _block(
    instance: int = 1, numbering: int = 0, rotation: int = 0
) -> intent_protocol.Block:
    return intent_protocol.Block(instance, numbering, rotation, 0)


def _row(report: dict[str, Any], block: intent_protocol.Block) -> dict[str, Any]:
    return next(row for row in report["blocks"] if row["id"] == block.id)


@dataclass
class _Study:
    root: Path
    proposals: dict[str, intent_protocol.Choices]
    prepared: list[int] = field(default_factory=list)
    inspected: list[str] = field(default_factory=list)
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)

    def prepare(
        self, archive: Path, tasks: Path, instance: int
    ) -> ownership_source.Cut:
        assert archive == self.root / "archive"
        assert tasks == self.root / "tasks"
        self.prepared.append(instance)
        return ownership_source.Cut(
            instance=instance,
            task=tasks / _PRIVATE,
            actions=(ownership_source.Action("p1", "msg", {"text": _PRIVATE}, {}),),
            task_checksum="a" * 64,
            task_digest="sha256:" + "b" * 64,
            prefix_sha256="c" * 64,
            baseline_flags=2,
        )

    def read_arm(
        self,
        directory: Path,
        cut: ownership_source.Cut,
        block: intent_protocol.Block,
        arm: str,
        proposals: intent_protocol.Choices,
    ) -> dict[str, Any]:
        run = ownership_protocol.run_id(block, arm)
        assert directory == self.root / "study" / run
        assert cut.instance == block.instance
        assert proposals == self.proposals[block.id]
        self.inspected.append(run)
        return self.rows.get(run, {"status": "not_started"})

    def complete(
        self,
        block: intent_protocol.Block,
        arm: str,
        new_flags: int,
        choices: intent_protocol.Choices = (7, 8, 9),
        *,
        digest: str | None = None,
    ) -> None:
        run = ownership_protocol.run_id(block, arm)

        def case(box: int) -> boxes_packet.Case:
            return boxes_packet.Case(block.instance, ((box, 1, False),), (), 8, True)

        world = intent_packet.World(block.instance, (case(1), case(2), case(3)))
        self.rows[run] = {
            "status": "complete",
            "trial_id_sha256": digest or sha256(run.encode()).hexdigest(),
            "task_checksum": "a" * 64,
            "task_digest": "sha256:" + "b" * 64,
            "prefix_sha256": "c" * 64,
            "baseline_flags": 2,
            "new_flags": new_flags,
            "team_reward": (2 + new_flags) / 20,
            "choices": choices,
            "metrics": intent_protocol.native_metrics(world, choices),
            "model_invalid_seats": [],
        }

    def analyze(self) -> dict[str, Any]:
        return ownership_report.analyze(
            self.root / "archive", self.root / "tasks", self.root / "study"
        )


@pytest.fixture
def study(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Study:
    study = _Study(tmp_path, ownership_protocol.seeds())
    monkeypatch.setattr(ownership_protocol, "seeds", lambda: study.proposals)
    monkeypatch.setattr(ownership_source, "prepare", study.prepare)
    monkeypatch.setattr(ownership_evidence, "read_arm", study.read_arm)
    return study


def test_registration_and_arm_scores_include_incomplete_evidence(study: _Study) -> None:
    block, incomplete = _block(), _block(numbering=1)
    study.complete(block, "neutral", 1)
    study.rows[ownership_protocol.run_id(incomplete, "neutral")] = {
        "status": "model_invalid",
        "trial_id_sha256": "d" * 64,
        "model_invalid_seats": ["p2"],
        "team_reward": 0.1,
        "new_flags": None,
    }
    study.rows[ownership_protocol.run_id(incomplete, "owner")] = {
        "status": "invalid",
        "reason": "native_evidence_invalid",
    }

    report = study.analyze()
    assert study.prepared == [1, 2, 6]
    assert study.inspected == [
        ownership_protocol.run_id(block, arm)
        for block in intent_protocol.blocks()
        for arm in ownership_protocol.arms(block)
    ]
    assert report["coverage"] == {
        "scheduled_pairs": 36,
        "scheduled_arms": 72,
        "possible_turns": 216,
        "reused_trial_id_digests": [],
    }
    assert len(report["blocks"]) == 36
    assert _row(report, block)["proposals"] == list(study.proposals[block.id])
    assert _row(report, block)["arms"]["owner"]["status"] == "not_started"
    assert _row(report, incomplete)["arms"]["neutral"]["new_flags"] is None
    assert (
        _row(report, incomplete)["arms"]["owner"]["reason"] == "native_evidence_invalid"
    )
    primary = report["primary"]
    assert primary["possible_pairs"] == 36
    assert primary["complete_pairs"] == 0
    assert primary["owner_minus_neutral"] is None
    neutral = primary["arms"]["neutral"]
    assert neutral["status_counts"] == {
        "complete": 1,
        "model_invalid": 1,
        "not_started": 34,
    }
    assert neutral["native_reward_known_arms"] == 2
    assert neutral["native_reward_mean"] == pytest.approx(0.125)
    assert neutral["primary_known_arms"] == 1
    assert neutral["primary_known_seats"] == 3
    assert neutral["primary_known_score_totals"]["new_flags"] == 1
    assert _PRIVATE not in json.dumps(report)


def test_aliases_invalidate_every_occurrence_including_known_invalid_rows(
    study: _Study,
) -> None:
    original, invalid, same_pair = _block(), _block(2), _block(6)
    cross_world_id, within_pair_id = "d" * 64, "e" * 64
    study.complete(original, "neutral", 1)
    study.complete(original, "owner", 2, digest=cross_world_id)
    study.rows[ownership_protocol.run_id(invalid, "neutral")] = {
        "status": "invalid",
        "trial_id_sha256": cross_world_id,
    }
    study.complete(invalid, "owner", 2)
    study.complete(same_pair, "neutral", 1, digest=within_pair_id)
    study.complete(same_pair, "owner", 2, digest=within_pair_id)

    report = study.analyze()
    assert report["coverage"]["reused_trial_id_digests"] == [
        cross_world_id,
        within_pair_id,
    ]
    for block, arm, old_status in (
        (original, "owner", "complete"),
        (invalid, "neutral", "invalid"),
        (same_pair, "owner", "complete"),
        (same_pair, "neutral", "complete"),
    ):
        row = _row(report, block)
        assert row["owner_minus_neutral"] is None
        info = row["arms"][arm]
        assert info["status"] == "invalid"
        assert info["reason"] == "reused_native_trial_id"
        assert info["evidence_status"] == old_status
        assert "scores" not in info
    primary = report["primary"]
    assert primary["complete_pairs"] == 0
    assert primary["arms"]["neutral"]["primary_known_arms"] == 1
    assert primary["arms"]["owner"]["primary_known_arms"] == 1


def test_equal_world_mean_requires_pairs_in_all_worlds(study: _Study) -> None:
    for block, neutral, owner in (
        (_block(), 0, 2),
        (_block(numbering=1), 0, 2),
        (_block(rotation=2), 0, 0),
        (_block(2), 1, 0),
    ):
        study.complete(block, "neutral", neutral)
        study.complete(block, "owner", owner)
    study.complete(_block(6), "owner", 2)
    unsupported = study.analyze()["primary"]
    assert unsupported["required_worlds"] == 3
    assert unsupported["supported_worlds"] == 2
    assert not unsupported["full_world_support"]
    assert unsupported["owner_minus_neutral"] is None
    assert unsupported["per_world"][2]["owner_minus_neutral"] is None

    study.complete(_block(6), "neutral", 2)
    report = study.analyze()
    primary = report["primary"]
    assert primary["full_world_support"]
    assert primary["complete_pairs"] == 5
    assert primary["direction_counts"] == {"wins": 2, "losses": 1, "ties": 2}
    assert primary["new_flags_difference_counts"] == {"-1": 1, "0": 2, "2": 2}
    assert primary["owner_minus_neutral"]["new_flags"] == pytest.approx(1 / 9)
    assert [row["complete_pairs"] for row in primary["per_world"]] == [3, 1, 1]
    assert primary["per_world"][0]["owner_minus_neutral_numerator"]["new_flags"] == 4
    assert _row(report, _block())["owner_minus_neutral"]["team_reward"] == 0.1
    assert [row["complete_pairs"] for row in report["by_numbering"]] == [4, 1]
    assert [row["complete_pairs"] for row in report["by_rotation"]] == [4, 0, 1]
    assert [row["complete_pairs"] for row in report["by_repetition"]] == [5, 0]


def test_ownership_uses_earliest_label_and_checks_all_proposals(study: _Study) -> None:
    collision, clean = _block(rotation=1), _block()
    study.proposals[collision.id] = (9, 9, 10)
    study.proposals[clean.id] = (7, 8, 9)
    study.complete(collision, "neutral", 0, (8, 10, 10))
    study.complete(collision, "owner", 1, (9, 1, 10))
    study.complete(clean, "neutral", 0)
    study.complete(clean, "owner", 0)

    report = study.analyze()
    row = _row(report, collision)
    assert collision.seat_index(0) == 2
    assert row["proposal_owners"] == {"A": "A", "B": "A", "C": "C"}
    assert row["arms"]["neutral"]["label_behavior"] == {
        "A": {"changed_proposal": True, "follows_ownership": False},
        "B": {"changed_proposal": True, "follows_ownership": False},
        "C": {"changed_proposal": False, "follows_ownership": True},
    }
    assert row["arms"]["owner"]["label_behavior"]["B"] == {
        "changed_proposal": True,
        "follows_ownership": True,
    }
    assert row["owner_minus_neutral"] == {
        "new_flags": 1,
        "team_reward": 0.05,
        "distinct_new": 0,
        "excess_new_collisions": -1,
        "historical_repeats": 1,
        "changed_proposals": -1,
        "follows_ownership": 2,
    }
    split = report["by_initial_collision"]
    assert split["colliding"]["complete_pairs"] == split["clean"]["complete_pairs"] == 1
    assert split["colliding"]["possible_pairs"] + split["clean"]["possible_pairs"] == 36


def test_cli_rejects_unverified_source_without_private_text(
    study: _Study, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def reject(archive: Path, tasks: Path, instance: int) -> ownership_source.Cut:
        if instance == 2:
            raise ValueError(_PRIVATE)
        return study.prepare(archive, tasks, instance)

    monkeypatch.setattr(ownership_source, "prepare", reject)
    exit_code = ownership_report.main(
        [
            "--archive",
            str(study.root / "archive"),
            "--tasks",
            str(study.root / "tasks"),
            "--study",
            str(study.root / "study"),
        ]
    )
    capture = capsys.readouterr()
    assert exit_code == 1
    assert not capture.out
    assert json.loads(capture.err) == {"error": "cannot_verify_box_ownership_study"}
    assert not study.inspected
