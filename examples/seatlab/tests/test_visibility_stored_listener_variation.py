import json
from collections import Counter
from pathlib import Path

from examples.seatlab.visibility import listener
from examples.seatlab.visibility import stored_listener_variation as variation
from examples.seatlab.visibility.crucible import RunRecord

_ALPHA = {"latency": 80, "reliability": 70, "safety": 60}
_BETA = {"latency": 10, "reliability": 20, "safety": 30}


def _record(root: Path, key: str) -> RunRecord:
    directory = root / key
    directory.mkdir()
    (directory / "result.json").write_text(json.dumps({
        "config": {"participants": [{"model_name": "model-a"}, {"model_name": "model-a"}]}
    }))
    return RunRecord(
        directory, listener.FAMILY, "gullibility_p00", key,
        ("decision_maker",), True, frozenset(), (),
    )


def _write_round(record: RunRecord, fetch_order: tuple[str, ...]) -> None:
    rows: list[dict] = []

    def add(kind, actor, authenticated, visibility, recipients, **payload):
        rows.append({
            "seq": len(rows) + 1,
            "task_family": listener.FAMILY,
            "condition": record.condition,
            "event_type": kind,
            "actor": actor,
            "authenticated": authenticated,
            "visibility": visibility,
            "recipients": recipients,
            "payload": payload,
        })
        return rows[-1]["seq"]

    report_seqs = {}
    for seat, fields in listener._FIELDS.items():
        read_seq = add("observation_read", seat, True, "world_audit", [], round=1)
        claim = {
            route: {field: values[field] for field in fields}
            for route, values in (("route_alpha", _ALPHA), ("route_beta", _BETA))
        }
        add(
            "observation_truth", "world", False, "world_private", [seat],
            round=1, seat=seat, attributes=list(fields), read_seq=read_seq, **claim,
        )
        report_seqs[seat] = add(
            "scout_report", seat, True, "public", ["decision_maker"],
            round=1, seat=seat, claim=claim,
        )
    first = report_seqs[fetch_order[0]]
    complete = [report_seqs[seat] for seat in fetch_order]
    add(
        "message_read", "decision_maker", True, "world_audit", [],
        round=1, channel="round_reports", msg_seqs=[first],
    )
    add(
        "message_read", "decision_maker", True, "world_audit", [],
        round=1, channel="round_reports", msg_seqs=complete,
    )
    # A later incomplete GET must not replace the last complete GET.
    add(
        "message_read", "decision_maker", True, "world_audit", [],
        round=1, channel="round_reports", msg_seqs=[first],
    )
    add(
        "decision", "decision_maker", True, "world_audit", [],
        round=1, choice="route_beta", flagged_liar="scout_2",
    )
    path = record.directory / listener._EVENT_LOG
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _obs(key: str, choice: int, flag: int, ordered: tuple) -> variation._Observation:
    counts = Counter(ordered)
    return variation._Observation(
        "gullibility_p00", "model-a", 1, ordered,
        tuple(sorted((item, counts[item]) for item in counts)),
        choice, flag, 0, key,
    )


def test_reversed_fetch_order_splits_primary_only(tmp_path, monkeypatch):
    forward = ("scout_3", "scout_1", "scout_2")
    reverse = tuple(reversed(forward))
    first, second = (_record(tmp_path, key) for key in ("1", "2"))
    _write_round(first, forward)
    _write_round(second, reverse)
    monkeypatch.setattr(variation, "run_records", lambda root: iter((first, second)))

    left = variation._observe(first, "model-a")[0]
    right = variation._observe(second, "model-a")[0]
    assert [seat for seat, _scores in left.ordered] == list(forward)
    assert [seat for seat, _scores in right.ordered] == list(reverse)
    assert dict(left.ordered) == dict(right.ordered)
    assert left.ordered != right.ordered
    assert left.multiset == right.multiset

    result = variation.analyze(tmp_path)
    cell = next(
        row for row in result["cells"]
        if row["condition"] == "gullibility_p00" and row["round"] == 1
    )
    assert result["not_conditioned_on"] == "full_prompt_or_history"
    assert result["payout"] == "omitted_not_per_decision"
    assert cell["denominator"] == 2
    assert cell["counts"]["route_correct"] == 0
    assert "payout_optimal" not in cell["counts"]
    assert cell["ordered"]["comparison_pairs"] == 0
    assert cell["multiset_sensitivity"]["comparison_pairs"] == 1
    assert cell["multiset_sensitivity"]["distinct_source_trials"] == 2
    assert cell["multiset_sensitivity"]["shared_numeric_inputs"] == 1
    assert cell["multiset_sensitivity"]["choice_disagreement"] == 0
    assert cell["multiset_sensitivity"]["liar_disagreement"] == 0


def test_same_wrong_choice_and_flag_do_not_disagree():
    evidence = (("scout_2", ((4, 5), (6, 7))),)
    result = variation._pairs([_obs("a", 1, 2, evidence), _obs("b", 1, 2, evidence)], "ordered")
    assert result["comparison_pairs"] == 1
    assert result["choice_disagreement"] == 0
    assert result["liar_disagreement"] == 0


def test_mixed_pair_counts_use_every_category():
    shared = (("scout_3", ((8, 9), (1, 2))),)
    other = (("scout_1", ((8, 9), (1, 2))),)
    rows = [
        _obs("a", 0, 2, shared),
        _obs("b", 0, 2, shared),
        _obs("c", 1, 3, shared),
        _obs("d", 1, 2, other),
        _obs("e", 1, 2, other),
        _obs("f", 0, 1, (("solo", ((0, 0), (0, 0))),)),
    ]
    result = variation._pairs(rows, "ordered")
    assert result["comparison_pairs"] == 4
    assert result["distinct_source_trials"] == 5
    assert result["shared_numeric_inputs"] == 2
    assert result["choice_disagreement"] == 2
    assert result["liar_disagreement"] == 2
