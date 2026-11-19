"""Exercise the registered prefix comparison with synthetic episode receipts."""

import importlib
import json
from pathlib import Path
from uuid import UUID

import pytest

_ADAPTER = Path(__file__).resolve().parents[1] / "adapters" / "masbench-scheduling"
_CASES = [f"{s}/{n}" for s in (2, 3, 4, 5) for n in (1, 2, 3, 4, 5, 6)]
_PRIVATE = "/private/host/not-for-publication"
_MATCH = object()


@pytest.fixture
def run(monkeypatch):
    monkeypatch.syspath_prepend(str(_ADAPTER))
    return importlib.import_module("validate_prefix").main


def _episode(case, arm, actions, *, views=None, closed=True, trial=None):
    from seatlab.tape import digest

    views = views if views is not None else list(range(len(actions)))
    frames = []
    for turn, (sample, action) in enumerate(zip(views, actions, strict=True), 1):
        view = {"sample": sample, "note": _PRIVATE}
        frames.append(
            {
                "turn": turn,
                "basis": "bound",
                "view": view,
                "view_sha256": digest(view),
                "observed": {"action": action},
            }
        )
    row = {
        "schema": "seatlab.episode/v1",
        "id": f"calendar:{case.replace('/', '-')}:cursor:m1",
        "case_sha256": digest({"case_id": case}),
        "role": "m1",
        "native_trial_id": trial or str(UUID(int=1 + _CASES.index(case) + 100 * arm)),
        "closed": closed,
        "frames": frames,
    }
    return {**row, "episode_sha256": digest(row)}


def _forecast(episode, *, candidate=_MATCH, turn=1):
    n_frames = len(episode["frames"])
    full_match = candidate is _MATCH
    return {
        **{
            field: episode[field]
            for field in (
                "id",
                "case_sha256",
                "role",
                "native_trial_id",
                "episode_sha256",
                "closed",
            )
        },
        "frames": n_frames,
        "matched_prefix": n_frames if full_match else turn - 1,
        "boundary_turn": None if full_match else turn,
        "status": "matched" if full_match else "diverged",
        "source_action": None
        if full_match
        else episode["frames"][turn - 1]["observed"],
        "candidate_action": None if full_match else {"action": candidate},
        "error": None,
        "calls": n_frames if full_match else turn,
        "withheld_suffix": 0 if full_match else n_frames - turn,
    }


def _prefix_result(rows):
    counts = dict.fromkeys(
        (
            "episodes",
            "frames",
            "matched_prefix",
            "calls",
            "withheld_suffix",
            "matched",
            "observed_prefix",
            "diverged",
            "missing_observation",
            "unbound",
            "policy_failed",
            "matched_open",
            "matched_closed",
        ),
        0,
    )
    counts["episodes"] = len(rows)
    for row in rows:
        for key in ("frames", "matched_prefix", "calls", "withheld_suffix"):
            counts[key] += row[key]
        counts[row["status"]] += 1
        if row["boundary_turn"] is None:
            counts["matched_closed" if row["closed"] else "matched_open"] += 1
    return {
        "schema": "seatlab.prefix-result/v1",
        "policy": "external",
        "interpretation": "recorded_prefix_agreement",
        "history": True,
        "counts": counts,
        "episodes": list(reversed(rows)),
    }


def _dump(path, document):
    path.write_text(json.dumps(document), encoding="utf-8")


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _export(root, episodes, missing=None):
    root.mkdir()
    missing = missing or {}
    rows = []
    for case in reversed(_CASES):
        episode = episodes.get(case)
        if episode is None:
            rows.append(
                {"case_id": case, "status": "unattempted", **missing.get(case, {})}
            )
        else:
            rows.append(
                {
                    "case_id": case,
                    "status": "verified",
                    "native_trial_id": episode["native_trial_id"],
                    "episode_id": episode["id"],
                }
            )
    _dump(root / "index.json", rows)
    (root / "episodes.jsonl").write_text(
        "".join(json.dumps(value) + "\n" for value in episodes.values()),
        encoding="utf-8",
    )
    return root


def _inputs(tmp_path, source, target, forecasts, *, missing=None):
    left = _export(tmp_path / "source", source, missing)
    right = _export(tmp_path / "target", target)
    forecast = tmp_path / "forecast.json"
    _dump(forecast, _prefix_result(forecasts))
    return left, right, forecast


def _invoke(run, inputs, out):
    return run([*map(str, inputs), "--out", str(out)])


def test_registered_denominators_confirm_only_native_evidence(run, tmp_path):
    source = {
        "2/1": _episode("2/1", 0, ["query:1", "query:2", "choose:3"]),
        "2/2": _episode("2/2", 0, ["choose:5"]),
        "2/3": _episode("2/3", 0, ["query:1"]),
        "2/4": _episode("2/4", 0, ["query:1"]),
        "2/5": _episode("2/5", 0, ["query:1"]),
        "3/1": _episode("3/1", 0, ["query:1"]),
        "4/1": _episode("4/1", 0, ["choose:4"]),
    }
    target = {
        "2/1": _episode("2/1", 1, ["query:1", None]),
        "2/2": _episode("2/2", 1, ["choose:5"]),
        "2/3": _episode("2/3", 1, [None], views=[100]),
        "2/4": _episode("2/4", 1, ["query:1"]),
        "2/5": _episode("2/5", 1, [1]),
        "3/2": _episode("3/2", 1, [None]),
        "4/1": _episode("4/1", 1, ["choose:4"], closed=False),
    }
    incomplete_trial = str(UUID(int=500))
    inputs = _inputs(
        tmp_path,
        source,
        target,
        [
            _forecast(source["2/1"], candidate=None, turn=2),
            _forecast(source["2/2"]),
            _forecast(source["2/3"], candidate=None),
            _forecast(source["2/4"], candidate=None),
            _forecast(source["2/5"], candidate="1"),
            _forecast(source["3/1"], candidate=None),
            _forecast(source["4/1"]),
        ],
        missing={
            "5/1": {
                "status": "invalid",
                "reason": _PRIVATE,
                "native_trial_id": incomplete_trial,
            }
        },
    )
    out = tmp_path / "out"
    assert _invoke(run, inputs, out) == 0
    result = _load(out / "validation.json")
    pairs = _load(out / "pairs.json")["pairs"]
    alignment = _load(out / "alignment.json")
    rows = {row["case_id"]: row for row in result["cases"]}
    assert list(rows) == _CASES
    assert [pair["id"] for pair in pairs] == [
        "2-1",
        "2-2",
        "2-3",
        "2-4",
        "2-5",
        "3-1",
        "3-2",
        "4-1",
    ]
    assert result["counts"] == {
        "development": {
            "scheduled": 4,
            "source_verified": 3,
            "target_verified": 2,
            "manifest_pairs": 3,
            "skipped_no_case_hash": 1,
            "confirmed_action_change": 1,
            "confirmed_match": 0,
            "unconfirmed": 1,
            "missing": 2,
        },
        "follow_up": {
            "scheduled": 20,
            "source_verified": 4,
            "target_verified": 5,
            "manifest_pairs": 5,
            "skipped_no_case_hash": 15,
            "confirmed_action_change": 0,
            "confirmed_match": 1,
            "unconfirmed": 3,
            "missing": 16,
        },
        "all": {
            "scheduled": 24,
            "source_verified": 7,
            "target_verified": 7,
            "manifest_pairs": 8,
            "skipped_no_case_hash": 16,
            "confirmed_action_change": 1,
            "confirmed_match": 1,
            "unconfirmed": 4,
            "missing": 18,
        },
    }
    assert rows["2/1"]["status"] == "confirmed_action_change"
    assert rows["2/1"]["source"]["receipt"] == pairs[0]["source"]
    assert rows["2/1"]["target"]["receipt"] == pairs[0]["target"]
    assert (
        rows["2/1"]["forecast"]["episode_sha256"]
        == pairs[0]["source"]["episode_sha256"]
    )
    assert alignment["pairs"][0]["boundary_turn"] == 2
    assert alignment["pairs"][0]["target_action"] == {"action": None}
    assert rows["2/2"]["status"] == "confirmed_match"
    assert rows["2/3"]["alignment_status"] == "input_changed"
    assert rows["2/4"]["alignment_status"] == "matched"
    assert rows["2/5"]["alignment_status"] == "action_changed"
    for case in ("2/3", "2/4", "2/5", "4/1"):
        assert rows[case]["status"] == "unconfirmed"
    assert rows["4/1"]["alignment_status"] == "open_prefix"
    assert (
        rows["3/1"]["status"]
        == rows["3/2"]["status"]
        == rows["5/1"]["status"]
        == "missing"
    )
    assert alignment["pairs"][5]["target_action"] is None
    assert rows["3/2"]["forecast"] is None
    assert rows["5/1"]["pair_id"] is rows["5/1"]["case_sha256"] is None
    assert rows["5/1"]["source"]["status"] == "invalid"
    assert rows["5/1"]["source"]["native_trial_id"] == incomplete_trial
    assert rows["5/1"]["source"]["receipt"] is None
    assert rows["5/1"]["target"]["status"] == "unattempted"
    second = tmp_path / "second"
    assert _invoke(run, inputs, second) == 0
    for path in out.iterdir():
        content = path.read_bytes()
        assert _PRIVATE.encode() not in content
        assert b'"view":' not in content
        assert b'"reward":' not in content
        assert (second / path.name).read_bytes() == content


def test_same_target_action_at_a_different_turn_does_not_confirm(run, tmp_path):
    left = _episode("2/1", 0, ["query:1", "query:2"])
    right = _episode("2/1", 1, [None])
    inputs = _inputs(
        tmp_path,
        {"2/1": left},
        {"2/1": right},
        [_forecast(left, candidate=None, turn=2)],
    )
    out = tmp_path / "out"
    assert _invoke(run, inputs, out) == 0
    row = _load(out / "validation.json")["cases"][0]
    assert row["alignment_status"] == "action_changed"
    assert row["status"] == "unconfirmed"


def test_external_policy_failure_does_not_confirm_a_native_match(run, tmp_path):
    left = _episode("2/1", 0, ["query:1", "choose:2"])
    right = _episode("2/1", 1, ["query:1", "choose:2"])
    row = {
        **_forecast(left, candidate=None),
        "status": "policy_failed",
        "candidate_action": None,
        "error": "timeout",
    }
    inputs = _inputs(tmp_path, {"2/1": left}, {"2/1": right}, [row])
    out = tmp_path / "out"
    assert _invoke(run, inputs, out) == 0
    result = _load(out / "validation.json")["cases"][0]
    assert result["alignment_status"] == "matched"
    assert result["status"] == "unconfirmed"
    assert result["forecast"]["withheld_suffix"] == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "calendar:2-2:cursor:m1"),
        ("case_sha256", "0" * 64),
        ("role", "m2"),
        ("native_trial_id", str(UUID(int=101))),
        ("episode_sha256", "0" * 64),
        ("closed", 1),
        ("frames", True),
        ("matched_prefix", False),
        ("candidate_action", {"action": False}),
        ("calls", 2),
    ],
)
def test_rejects_forged_source_forecast_receipts(run, tmp_path, field, value):
    left = _episode("2/1", 0, ["query:1"])
    right = _episode("2/1", 1, [None])
    row = {**_forecast(left, candidate=None), field: value}
    inputs = _inputs(tmp_path, {"2/1": left}, {"2/1": right}, [row])
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as failure:
        _invoke(run, inputs, out)
    assert failure.value.code == 1
    assert not out.exists()


@pytest.mark.parametrize("tamper", ["omitted", "target_only", "duplicate", "counts"])
def test_requires_source_only_forecast_coverage(run, tmp_path, tamper):
    left = _episode("2/1", 0, [None])
    right = _episode("2/2", 1, [None])
    prediction = _forecast(left)
    forecasts = {
        "omitted": [],
        "target_only": [prediction, _forecast(right)],
        "duplicate": [prediction, prediction],
        "counts": [prediction],
    }[tamper]
    inputs = _inputs(tmp_path, {"2/1": left}, {"2/2": right}, forecasts)
    if tamper == "counts":
        data = _load(inputs[2])
        data["counts"]["matched_closed"] = 0
        _dump(inputs[2], data)
    out = tmp_path / "out"
    with pytest.raises(SystemExit, match="1"):
        _invoke(run, inputs, out)
    assert not out.exists()


@pytest.mark.parametrize(
    "fields", [("episode_id", "native_trial_id"), ("native_trial_id",)]
)
def test_rejects_swapped_index_identities(run, tmp_path, fields):
    source = {case: _episode(case, 0, [None]) for case in ("2/1", "2/2")}
    inputs = _inputs(tmp_path, source, {}, [_forecast(row) for row in source.values()])
    index = inputs[0] / "index.json"
    rows = _load(index)
    a, b = (next(row for row in rows if row["case_id"] == case) for case in source)
    for field in fields:
        a[field], b[field] = b[field], a[field]
    _dump(index, rows)
    out = tmp_path / "out"
    with pytest.raises(SystemExit, match="1"):
        _invoke(run, inputs, out)
    assert not out.exists()


@pytest.mark.parametrize("arm", [0, 1])
@pytest.mark.parametrize("edit", ["omitted", "unexpected", "duplicate"])
def test_requires_exact_schedule_even_when_every_episode_is_missing(
    run, tmp_path, arm, edit
):
    inputs = _inputs(tmp_path, {}, {}, [])
    index = inputs[arm] / "index.json"
    rows = _load(index)
    if edit == "omitted":
        rows.pop()
    elif edit == "unexpected":
        rows[-1]["case_id"] = "2/7"
    else:
        rows[-1]["case_id"] = rows[0]["case_id"]
    _dump(index, rows)
    out = tmp_path / "out"
    with pytest.raises(SystemExit, match="1"):
        _invoke(run, inputs, out)
    assert not out.exists()


def test_empty_verified_exports_keep_all_scheduled_cases(run, tmp_path):
    inputs = _inputs(tmp_path, {}, {}, [])
    out = tmp_path / "out"
    assert _invoke(run, inputs, out) == 0
    assert _load(out / "pairs.json")["pairs"] == []
    assert _load(out / "alignment.json")["pairs"] == []
    result = _load(out / "validation.json")
    assert result["counts"]["all"]["skipped_no_case_hash"] == 24
    assert all(
        row["forecast"] is None and row["status"] == "missing"
        for row in result["cases"]
    )


@pytest.mark.parametrize(
    "tamper", ["wrong_case", "same_trial", "bad_episode_digest", "observed_policy"]
)
def test_rejects_tampered_native_exports_and_non_external_forecast(
    run, tmp_path, tamper
):
    from seatlab.tape import digest

    left = _episode("2/1", 0, [None])
    right = _episode("2/1", 1, [None])
    if tamper == "wrong_case":
        right["case_sha256"] = "0" * 64
    if tamper == "same_trial":
        right["native_trial_id"] = left["native_trial_id"]
    right["episode_sha256"] = digest(
        {k: v for k, v in right.items() if k != "episode_sha256"}
    )
    if tamper == "bad_episode_digest":
        right["episode_sha256"] = "0" * 64
    inputs = _inputs(tmp_path, {"2/1": left}, {"2/1": right}, [_forecast(left)])
    if tamper == "observed_policy":
        data = _load(inputs[2])
        data["policy"] = "observed"
        _dump(inputs[2], data)
    out = tmp_path / "out"
    with pytest.raises(SystemExit, match="1"):
        _invoke(run, inputs, out)
    assert not out.exists()


@pytest.mark.parametrize("existing", ["directory", "file", "dangling_symlink"])
def test_does_not_overwrite_existing_destination(run, tmp_path, existing):
    inputs = _inputs(tmp_path, {}, {}, [])
    out = tmp_path / "out"
    if existing == "directory":
        out.mkdir()
        protected = out / "validation.json"
        protected.write_bytes(b"keep")
    elif existing == "file":
        protected = out
        protected.write_bytes(b"keep")
    else:
        protected = tmp_path / "absent"
        out.symlink_to(protected, target_is_directory=True)
    with pytest.raises(SystemExit, match="1"):
        _invoke(run, inputs, out)
    if existing == "directory":
        assert list(out.iterdir()) == [protected]
    if existing == "dangling_symlink":
        assert out.is_symlink()
        assert not protected.exists()
    else:
        assert protected.read_bytes() == b"keep"
