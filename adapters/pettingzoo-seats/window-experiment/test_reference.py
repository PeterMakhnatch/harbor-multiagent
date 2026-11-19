import hashlib
import json

import reference

DOCUMENTED = {
    "sort_keys": True,
    "separators": (",", ":"),
    "ensure_ascii": True,
    "allow_nan": False,
}


def documented(value: object) -> bytes:
    return json.dumps(value, **DOCUMENTED).encode("ascii")


def observations(result: dict, role: str) -> list[int]:
    return [step["decisions"][role]["view"]["observation"] for step in result["steps"]]


def test_encoder_and_depth_zero_window() -> None:
    current = {"observation": 3, "legal_actions": [0, 1, 2]}
    older = {"view": current, "action": 1}
    newer = {"view": {"observation": 0, "legal_actions": [0, 1, 2]}, "action": 2}
    empty = reference.actor_window(current, [older, newer], 0)
    last = reference.actor_window(current, [older, newer], 1)
    assert empty["history"] == []
    assert set(empty) == {"schema", "current", "history"}
    assert last["history"] == [newer]
    assert "schema" not in newer["view"]
    raw = reference.canon({"b": 1, "a": "x"})
    assert raw == documented({"b": 1, "a": "x"}) == b'{"a":"x","b":1}'
    assert b"\n" not in raw


def test_start_sentinel_and_index_phase() -> None:
    for first in (1, 2):
        result = reference.play(first, 0)
        seen = observations(result, "p0")
        assert seen[0] == 3
        assert result["p0_actions"][0] == 1
        assert result["p1_actions"] == [0, first, 0, 3 - first] * 3
        assert result["flags"]["terminated"] == {"p0": False, "p1": False}
        assert result["flags"]["truncated"] == {"p0": True, "p1": True}
        for obs, action in zip(seen, result["p0_actions"]):
            if obs in (1, 2, 3):
                assert action == 1
            if obs == 0:
                assert action == 2


def test_one_frame_changes_same_view_and_improves_payoff() -> None:
    for cycle in (1, 2):
        base, one = reference.play(cycle, 0), reference.play(cycle, 1)
        assert base["p1_actions"] == one["p1_actions"]
        assert observations(base, "p0") == observations(one, "p0")
        changed = [
            i
            for i, (left, right) in enumerate(
                zip(base["p0_actions"], one["p0_actions"])
            )
            if left != right
        ]
        assert changed
        assert all(observations(base, "p0")[i] == 0 for i in changed)
        assert one["payoffs"]["p0"] > base["payoffs"]["p0"]
        assert one["degrees"] == {"p0": 1, "p1": 2}


def test_four_frames_keep_actions_and_use_more_bytes() -> None:
    for cycle in (1, 2):
        one, wide = reference.play(cycle, 1), reference.play(cycle, 4)
        assert wide["p0_actions"] == one["p0_actions"]
        assert wide["p1_actions"] == one["p1_actions"]
        assert wide["payoffs"] == one["payoffs"]
        assert wide["window_bytes"]["p0"] > one["window_bytes"]["p0"]
        assert wide["window_bytes"]["p1"] == one["window_bytes"]["p1"]
        assert wide["degrees"]["p0"] == 4


def test_byte_sum_uses_private_canonical_windows() -> None:
    result = reference.play(2, 4)
    totals = {"p0": 0, "p1": 0}
    history = {"p0": [], "p1": []}
    for step in result["steps"]:
        for role, depth in result["degrees"].items():
            decision = step["decisions"][role]
            framed = reference.actor_window(decision["view"], history[role], depth)
            raw = documented(framed)
            assert set(framed) == {"schema", "current", "history"}
            assert all(set(item) == {"view", "action"} for item in framed["history"])
            assert len(raw) == decision["window_bytes"]
            assert hashlib.sha256(raw).hexdigest() == decision["window_sha256"]
            totals[role] += len(raw)
            history[role].append(
                {"view": decision["view"], "action": decision["action"]}
            )
    assert totals == result["window_bytes"]
    assert (
        result["steps"][1]["decisions"]["p0"]["view"]
        != result["steps"][1]["decisions"]["p1"]["view"]
    )


def test_cli_json_is_the_independent_result(capsys) -> None:
    assert reference.main(["--cycle", "2", "--depth", "1"]) == 0
    out = capsys.readouterr().out
    got = json.loads(out)
    assert got == reference.play(2, 1)
    for key in (
        "p0_actions",
        "p1_actions",
        "payoffs",
        "flags",
        "degrees",
        "window_bytes",
    ):
        assert key in got
    assert out.encode("ascii") == reference.canon(got) + b"\n"


def _write(tmp_path, report: dict) -> str:
    path = tmp_path / "world.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return str(path)


def test_matching_world_ignores_native_labels(tmp_path, capsys) -> None:
    result = reference.play(1, 0)
    report = reference.native_report(result)
    report["predicted_p0"] = [0] * 12
    report["label"] = "not-a-policy-input"
    code = reference.main(
        ["--cycle", "1", "--depth", "0", "--world", _write(tmp_path, report)]
    )
    out, err = capsys.readouterr()
    assert code == 0 and err == ""
    printed = json.loads(out)
    assert printed["p0_actions"] == result["p0_actions"]
    assert printed["p0_actions"] != report["predicted_p0"]


def test_native_discrepancies_stop_at_first_mismatch(tmp_path, capsys) -> None:
    result = reference.play(1, 4)
    missing = reference.native_report(result)
    del missing["steps"][3]
    missing["returns"]["p0"] = missing["returns"]["p0"] + 1
    bad_return = reference.native_report(result)
    bad_return["returns"]["p0"] = bad_return["returns"]["p0"] + 1
    bad_term = reference.native_report(result)
    bad_term["terminated"]["p0"] = True
    bad_cut = reference.native_report(result)
    bad_cut["truncated"]["p1"] = False
    bad_group = reference.native_report(result)
    del bad_group["steps"][4]["decisions"]["p1"]
    bad_view = reference.native_report(result)
    bad_view["steps"][0]["decisions"]["p0"]["view"] = {
        "observation": 0,
        "legal_actions": [0, 1, 2],
    }
    bad_action = reference.native_report(result)
    bad_action["steps"][2]["decisions"]["p1"]["action"] = 2
    cases = (
        (missing, "steps"),
        (bad_return, "returns.p0"),
        (bad_term, "terminated.p0"),
        (bad_cut, "truncated.p1"),
        (bad_group, "steps[4].group"),
        (bad_view, "steps[0].decisions.p0.view"),
        (bad_action, "steps[2].decisions.p1.action"),
    )
    for report, loc in cases:
        code = reference.main(
            ["--cycle", "1", "--depth", "4", "--world", _write(tmp_path, report)]
        )
        out, err = capsys.readouterr()
        assert code != 0 and out == ""
        assert err.strip() == f"mismatch: {loc}"
