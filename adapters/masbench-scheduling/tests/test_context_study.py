"""Focused boundary tests for the calendar context-field study runner."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from uuid import UUID

import pytest

ADAPTER = Path(__file__).resolve().parents[1]
STRATA = (2, 3, 4, 5)
FIT_CASES = [f"{stratum}/{num}" for stratum in STRATA for num in range(1, 5)]
HELDOUT_CASES = [f"{stratum}/{num}" for stratum in STRATA for num in (5, 6)]
ALL_CASES = [f"{stratum}/{num}" for stratum in STRATA for num in range(1, 7)]
EXPECTED_PROJECTIONS = [
    ("Full", None),
    ("Role only", []),
    ("Round", ["/round"]),
    ("Request bound", ["/bound"]),
    ("Participant replies", ["/replies"]),
    ("Exchange", ["/bound", "/replies", "/round"]),
]


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "masbench_scheduling_context_study", ADAPTER / "context_study.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def study_mod():
    pytest.importorskip("seatlab.sufficiency")
    return _load_module()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _uuid_for(index: int) -> str:
    return str(UUID(int=(0x4000 << 64) | (0x8000 << 48) | index, version=4))


def _make_frame(
    turn: int,
    view: dict[str, object],
    action: str | int | None,
    *,
    basis: str = "bound",
    observed: bool = True,
) -> dict[str, object]:
    return {
        "turn": turn,
        "basis": basis,
        "view": view,
        "view_sha256": _digest(view),
        "observed": {"action": action} if observed else None,
    }


def _make_episode(
    case_id: str,
    native_trial_id: str,
    *,
    case_digest: str | None = None,
    episode_id: str | None = None,
    role: str = "m1",
    closed: bool = True,
    frames: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    stratum, number = (int(part) for part in case_id.split("/"))
    if frames is None:
        # Keep frame 1 initialization distinct from round views without defaults.
        init_view: dict[str, object] = {
            "role": role,
            "mode": "cursor",
            "horizon": 720,
            "duration": 2,
            "slots_per_day": 144,
            "busy": [stratum * 10 + number],
        }
        round_one_view: dict[str, object] = {
            "round": 1,
            "bound": 0,
            "replies": {
                "m1": number,
                "m2": number + 1,
                "m3": 0,
                "m4": 0,
                "m5": 0,
            },
        }
        round_two_view: dict[str, object] = {
            "round": 2,
            "bound": number + 1,
            "replies": {
                "m1": number + 1,
                "m2": number + 1,
                "m3": number + 1,
                "m4": number + 1,
                "m5": number + 1,
            },
        }
        terminal: str | None = (
            None if (stratum == 5 and number in (4, 6)) else f"choose:{number + 1}"
        )
        frames = [
            _make_frame(1, init_view, "query:0"),
            _make_frame(2, round_one_view, f"query:{number + 1}"),
            _make_frame(3, round_two_view, terminal),
        ]
    stratum_text, number_text = case_id.split("/")
    body: dict[str, object] = {
        "schema": "seatlab.episode/v1",
        "id": episode_id or f"calendar:{stratum_text}-{number_text}:cursor:{role}",
        "case_sha256": case_digest or _digest({"synthetic_case": case_id}),
        "role": role,
        "native_trial_id": native_trial_id,
        "closed": closed,
        "frames": frames,
    }
    return {**body, "episode_sha256": _digest(body)}


def _build_export(
    root: Path,
    *,
    index_overrides: dict[str, dict[str, object]] | None = None,
    episode_overrides: dict[str, dict[str, object]] | None = None,
    drop_index_cases: set[str] | None = None,
    drop_episode_cases: set[str] | None = None,
    extra_episodes: list[dict[str, object]] | None = None,
    reverse_episode_order: bool = False,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    root.mkdir(parents=True, exist_ok=True)
    index_rows: list[dict[str, object]] = []
    episodes: list[dict[str, object]] = []
    for idx, case_id in enumerate(ALL_CASES, start=1):
        native_id = _uuid_for(idx)
        ep = _make_episode(case_id, native_id)
        if episode_overrides and case_id in episode_overrides:
            ep = episode_overrides[case_id]
        row: dict[str, object] = {
            "case_id": case_id,
            "status": "verified",
            "native_trial_id": native_id,
            "episode_id": ep["id"],
        }
        if index_overrides and case_id in index_overrides:
            row = index_overrides[case_id]
        if not drop_index_cases or case_id not in drop_index_cases:
            index_rows.append(row)
        if not drop_episode_cases or case_id not in drop_episode_cases:
            episodes.append(ep)
    if extra_episodes:
        episodes.extend(extra_episodes)
    if reverse_episode_order:
        episodes = list(reversed(episodes))

    (root / "index.json").write_bytes(_canonical(index_rows) + b"\n")
    (root / "episodes.jsonl").write_bytes(
        b"".join(_canonical(ep) + b"\n" for ep in episodes)
    )
    return index_rows, episodes


def test_valid_24_case_export_produces_canonical_schema_and_immutability(
    study_mod, tmp_path: Path
) -> None:
    export_dir = tmp_path / "source-export"
    _build_export(export_dir, reverse_episode_order=True)
    index_before = (export_dir / "index.json").read_bytes()
    episodes_before = (export_dir / "episodes.jsonl").read_bytes()
    out = tmp_path / "study-report.json"

    exit_code = study_mod.main([str(export_dir), "--out", str(out)])
    assert exit_code == 0

    # Source export remains untouched and no stray files are written into it.
    assert {p.name for p in export_dir.iterdir()} == {"index.json", "episodes.jsonl"}
    assert (export_dir / "index.json").read_bytes() == index_before
    assert (export_dir / "episodes.jsonl").read_bytes() == episodes_before

    raw_out = out.read_bytes()
    assert str(tmp_path).encode() not in raw_out
    assert b"source-export" not in raw_out
    assert b"study-report.json" not in raw_out

    report = json.loads(raw_out)
    assert report["schema"] == "seatlab.calendar-context-study/v1"
    assert (
        report["protocol"] == "adapters/masbench-scheduling/context-audit-protocol.md"
    )
    assert report["role"] == "m1"
    assert report["source"] == {
        "index_sha256": hashlib.sha256(index_before).hexdigest(),
        "episodes_sha256": hashlib.sha256(episodes_before).hexdigest(),
        "verified_cases": 24,
    }
    assert report["split"] == {
        "fit_cases": FIT_CASES,
        "heldout_cases": HELDOUT_CASES,
    }

    rows = report["representations"]
    assert [
        (row["representation"], row["fields"]) for row in rows
    ] == EXPECTED_PROJECTIONS
    for row in rows:
        fit = row["fit"]
        heldout = row["heldout"]
        assert fit["counts"]["episodes"] == 16
        assert fit["counts"]["cases"] == 16
        assert fit["counts"]["trials"] == 16
        assert fit["counts"]["frames"] == 48
        assert fit["counts"]["eligible"] == 48
        assert heldout["counts"]["episodes"] == 8
        assert heldout["counts"]["cases"] == 8
        assert heldout["counts"]["trials"] == 8
        assert heldout["counts"]["frames"] == 24
        assert heldout["counts"]["eligible"] == 24
        assert len(fit["witnesses"]) <= 1
        # Held-out outcomes sum to the eligible denominator.
        for key in ("full", "selected"):
            summary = heldout[key]
            assert (
                summary["matched"]
                + summary["disagreed"]
                + summary["tied"]
                + summary["unseen"]
                == heldout["counts"]["eligible"]
            )

    # Role only must have at least one conflict witness and its witness must stay redacted.
    role_only = rows[1]
    assert role_only["fit"]["selected"]["minimum_errors"] > 0
    assert len(role_only["fit"]["witnesses"]) == 1
    witness = role_only["fit"]["witnesses"][0]
    assert set(witness) == {"role", "projection_sha256", "count", "examples"}
    assert witness["role"] == "m1"
    assert b"query:" not in raw_out
    assert b"choose:" not in raw_out


def test_switched_native_uuid_between_index_and_episode_fails(
    study_mod, tmp_path: Path
) -> None:
    export_dir = tmp_path / "switched-uuid"
    uuid_2_1 = _uuid_for(1)
    uuid_2_2 = _uuid_for(2)
    # Swap native_trial_id in the episodes relative to index.json.
    overrides = {
        "2/1": _make_episode("2/1", uuid_2_2),
        "2/2": _make_episode("2/2", uuid_2_1),
    }
    _build_export(export_dir, episode_overrides=overrides)
    out = tmp_path / "out.json"

    with pytest.raises(ValueError, match="native_trial_id does not match source index"):
        study_mod.run_study(export_dir, out)
    assert not out.exists()


@pytest.mark.parametrize(
    ("bad_episode_id", "expected_error"),
    [
        ("calendar:2-1:full:m1", "indexed episode_id does not match"),
        ("calendar:2-2:cursor:m1", "indexed episode_id does not match"),
        ("calendar:2-1:cursor:m2", "indexed episode_id does not match"),
    ],
)
def test_wrong_indexed_episode_key_fails(
    study_mod, tmp_path: Path, bad_episode_id: str, expected_error: str
) -> None:
    export_dir = tmp_path / "bad-key"
    native_id = _uuid_for(1)
    _build_export(
        export_dir,
        index_overrides={
            "2/1": {
                "case_id": "2/1",
                "status": "verified",
                "native_trial_id": native_id,
                "episode_id": bad_episode_id,
            }
        },
        episode_overrides={
            "2/1": _make_episode("2/1", native_id, episode_id=bad_episode_id)
        },
    )
    out = tmp_path / "out.json"

    with pytest.raises(ValueError, match=expected_error):
        study_mod.run_study(export_dir, out)
    assert not out.exists()


def test_wrong_episode_id_in_jsonl_relative_to_valid_index_fails(
    study_mod, tmp_path: Path
) -> None:
    export_dir = tmp_path / "mismatched-jsonl-key"
    native_id = _uuid_for(1)
    _build_export(
        export_dir,
        index_overrides={
            "2/1": {
                "case_id": "2/1",
                "status": "verified",
                "native_trial_id": native_id,
                "episode_id": "calendar:2-1:cursor:m1",
            }
        },
        episode_overrides={
            "2/1": _make_episode("2/1", native_id, episode_id="calendar:2-99:cursor:m1")
        },
    )
    out = tmp_path / "out.json"

    with pytest.raises(ValueError, match="not registered in source index"):
        study_mod.run_study(export_dir, out)
    assert not out.exists()


@pytest.mark.parametrize(
    ("drop_where", "expected_error"),
    [
        ("index", "exactly the 24 registered cases"),
        ("episodes", "match all 24 registered cases one-to-one"),
    ],
)
def test_missing_native_denominator_case_fails(
    study_mod, tmp_path: Path, drop_where: str, expected_error: str
) -> None:
    export_dir = tmp_path / f"missing-{drop_where}"
    _build_export(
        export_dir,
        drop_index_cases={"5/6"} if drop_where == "index" else None,
        drop_episode_cases={"5/6"} if drop_where == "episodes" else None,
    )
    out = tmp_path / "out.json"

    with pytest.raises(ValueError, match=expected_error):
        study_mod.run_study(export_dir, out)
    assert not out.exists()


def test_unverified_index_row_fails(study_mod, tmp_path: Path) -> None:
    export_dir = tmp_path / "unverified"
    _build_export(
        export_dir,
        index_overrides={
            "5/6": {
                "case_id": "5/6",
                "status": "failed",
                "native_trial_id": _uuid_for(24),
                "episode_id": "calendar:5-6:cursor:m1",
            }
        },
    )
    out = tmp_path / "out.json"

    with pytest.raises(ValueError, match="must be verified"):
        study_mod.run_study(export_dir, out)
    assert not out.exists()


@pytest.mark.parametrize(
    ("first_case", "second_case", "first_idx", "second_idx"),
    [
        ("2/1", "2/2", 1, 2),  # duplicate case_sha256 within fit split
        ("2/5", "3/6", 5, 12),  # duplicate case_sha256 within heldout split
        ("2/1", "2/5", 1, 5),  # duplicate case_sha256 across fit and heldout
    ],
    ids=["within-fit", "within-heldout", "across-splits"],
)
def test_duplicate_case_digest_fails_even_within_same_split(
    study_mod,
    tmp_path: Path,
    first_case: str,
    second_case: str,
    first_idx: int,
    second_idx: int,
) -> None:
    export_dir = tmp_path / f"dup-case-{first_idx}-{second_idx}"
    shared_digest = _digest({"cloned_case": True})
    _build_export(
        export_dir,
        episode_overrides={
            first_case: _make_episode(
                first_case, _uuid_for(first_idx), case_digest=shared_digest
            ),
            second_case: _make_episode(
                second_case, _uuid_for(second_idx), case_digest=shared_digest
            ),
        },
    )
    out = tmp_path / "out.json"

    with pytest.raises(ValueError, match="duplicate case_sha256"):
        study_mod.run_study(export_dir, out)
    assert not out.exists()


def test_unclosed_episode_fails(study_mod, tmp_path: Path) -> None:
    export_dir = tmp_path / "unclosed"
    _build_export(
        export_dir,
        episode_overrides={"3/2": _make_episode("3/2", _uuid_for(8), closed=False)},
    )
    out = tmp_path / "out.json"

    with pytest.raises(ValueError, match="must be closed"):
        study_mod.run_study(export_dir, out)
    assert not out.exists()


def test_non_m1_role_episode_fails(study_mod, tmp_path: Path) -> None:
    export_dir = tmp_path / "wrong-role"
    native_id = _uuid_for(1)
    _build_export(
        export_dir,
        index_overrides={
            "2/1": {
                "case_id": "2/1",
                "status": "verified",
                "native_trial_id": native_id,
                "episode_id": "calendar:2-1:cursor:m1",
            }
        },
        episode_overrides={
            "2/1": _make_episode(
                "2/1",
                native_id,
                episode_id="calendar:2-1:cursor:m1",
                role="m2",
            )
        },
    )
    out = tmp_path / "out.json"

    with pytest.raises(ValueError, match="role must be m1"):
        study_mod.run_study(export_dir, out)
    assert not out.exists()


@pytest.mark.parametrize("broken_link", [False, True], ids=["file", "broken-link"])
def test_existing_output_path_is_never_overwritten(
    study_mod, tmp_path: Path, broken_link: bool
) -> None:
    export_dir = tmp_path / "source-export"
    _build_export(export_dir)
    out = tmp_path / "existing.json"
    target = tmp_path / "missing-target.json"
    if broken_link:
        out.symlink_to(target)
    else:
        out.write_bytes(b"keep\n")

    with pytest.raises(ValueError, match="must not already exist"):
        study_mod.run_study(export_dir, out)
    if broken_link:
        assert out.readlink() == target and not target.exists()
    else:
        assert out.read_bytes() == b"keep\n"


def test_cli_error_is_safe_without_machine_paths(
    study_mod, tmp_path: Path, capsys
) -> None:
    export_dir = tmp_path / "missing-export"
    out = tmp_path / "out.json"

    with pytest.raises(SystemExit) as exc:
        study_mod.main([str(export_dir), "--out", str(out)])
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "context_study:" in captured.err
    assert str(tmp_path) not in captured.err
    assert not out.exists()
