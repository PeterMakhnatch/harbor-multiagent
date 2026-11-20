import json
from itertools import permutations

import pytest

from examples.seatlab.committee import contract, tally_analysis
from examples.seatlab.tests import test_committee_analysis as base
from examples.seatlab.tests.test_committee_contract import _dossier

_OPTIONS = ("Option_A", "Option_B", "Option_C")
_WIN = ("Option_A", "Option_A", "Option_A", "Option_B")
_LOSS = ("Option_A", "Option_B", "Option_B", "Option_A")


def _edit_votes(trial, index, edit):
    result_path = trial / "result.json"
    result = base._read(result_path)
    native = result["seat_results"][index]["agent_result"]["metadata"]["turns"]["vote"]
    receipt_path = base._receipt_path(trial, index)
    receipt = base._read(receipt_path)
    recorded = receipt["turns"]["vote"]
    edit(native, recorded["action"])
    receipt["turns"]["vote"] = native | {"action": recorded["action"]}
    base._write(result_path, result)
    base._write(receipt_path, receipt)


def _trial(root, index, rep, arm, *, totals=None, field_order=None, **kwargs):
    old_trial = base._fixture(root, index, rep, arm, **kwargs)
    # Reuse validated stock evidence, with voting-study names in each binding.
    cell_id = f"tv-m{index}-r{rep}-{arm}"
    trial_name = f"committee-{cell_id}"
    cell = old_trial.parents[1].with_name(cell_id)
    old_trial.parents[1].rename(cell)
    trial = cell / "trials" / trial_name
    trial.with_name(old_trial.name).rename(trial)
    attempt = base._read(cell / "attempt.json")
    base._write(
        cell / "attempt.json", attempt | {"cell_id": cell_id, "trial_name": trial_name}
    )
    config = base._read(cell / "config.json")
    config.update(trial_name=trial_name, trials_dir=str(trial.parent))
    for participant in config["participants"]:
        participant["kwargs"]["run_id"] = cell_id
    base._write(cell / "config.json", config)

    events = [
        json.loads(line) for line in (trial / base._WORLD).read_text().splitlines()
    ]
    messages = [
        {**item["payload"], "seq": item["seq"]}
        for item in events
        if item["event_type"] == "chat_message"
    ]
    chat = {"messages": messages, "unique_speakers": list(base._ROLES)}
    option_labels = dict(zip(_OPTIONS, list(permutations("XYZ"))[index], strict=True))
    result = base._read(trial / "result.json")
    result.update(trial_name=trial_name, config=config)
    for i, seat in enumerate(result["seat_results"]):
        dossier = _dossier(i + 1)
        metadata = seat["agent_result"]["metadata"]
        metadata["run_id"] = cell_id
        receipt_path = base._receipt_path(trial, i)
        receipt = base._read(receipt_path)
        native_action = receipt["turns"]["vote"]["action"]
        response = {
            "choice": option_labels[native_action["choice"]],
            "cited": [base._label(index, fid) for fid in native_action["cited_facts"]],
        }
        if arm != "plain":
            response["totals"] = (
                totals if totals is not None else {"X": 1, "Y": 7, "Z": 7}
            )
        if field_order is None and arm == "first":
            order = ("totals", "choice", "cited")
        else:
            order = field_order
        if order is not None:
            response = {key: response[key] for key in order}
        text = "  " + json.dumps(response) + "\n"
        metadata["turns"]["vote"].update(
            prompt_sha256=base._digest(
                contract.tally_prompt(dossier, chat, index, arm)
            ),
            response_sha256=base._digest(text),
            readout={
                "view_sha256": base._digest(contract.vote_prompt(dossier, chat, index)),
                "totals": response.get("totals"),
                "field_order": list(response),
                "response_text": text,
            },
        )
        base._write(
            receipt_path,
            metadata
            | {
                "turns": {
                    phase: turn | {"action": receipt["turns"][phase]["action"]}
                    for phase, turn in metadata["turns"].items()
                }
            },
        )
    base._write(trial / "result.json", result)
    return trial


def _cell(report, cell_id):
    return next(row for row in report["cells"] if row["cell_id"] == cell_id)


def test_contrasts_separate_matched_inputs_from_changed_submissions(tmp_path):
    first = _trial(
        tmp_path,
        0,
        0,
        "first",
        choices=_WIN,
        totals={"X": -99, "Y": 7, "Z": 7},
        field_order=("choice", "cited", "totals"),
    )
    _trial(tmp_path, 0, 0, "plain", choices=_LOSS, order=(1, 3, 0, 2))
    _trial(
        tmp_path,
        0,
        0,
        "last",
        choices=_LOSS,
        n_private=1,
        totals={"X": 19, "Y": 21, "Z": 10},
    )
    report = tally_analysis.analyze(tmp_path)

    assert report["summary"]["scheduled_cells"] == 36
    assert report["summary"]["scored_cells"] == 3
    assert report["summary"]["excluded_by_reason"] == {"not_attempted": 33}
    plain = report["contrasts"]["first_minus_plain"]
    last = report["contrasts"]["first_minus_last"]
    for contrast in (plain, last):
        pair = contrast["pairs"][0]
        assert pair["delta"] == {"native_vote_reward": 1, "correct_individual_votes": 1}
        assert contrast["all_complete"]["complete_pairs"] == 1
        assert len(contrast["all_complete"]["maps"]) == 6
        assert contrast["all_complete"]["maps"][0]["effects"] == pair["delta"]
        assert contrast["all_complete"]["effects"]["native_vote_reward"] is None
        assert contrast["pairs"][1]["delta"] is None
    assert plain["pairs"][0]["all_vote_inputs_match"] is True
    assert plain["pairs"][0]["all_submitted_facts_match_by_seat"] is True
    assert plain["input_matched"]["complete_pairs"] == 1
    assert last["pairs"][0]["all_vote_inputs_match"] is False
    assert last["pairs"][0]["all_submitted_facts_match_by_seat"] is False
    assert not last["pairs"][0]["seats"][0]["submitted_fact_matches"]
    assert all(row["submitted_fact_matches"] for row in last["pairs"][0]["seats"][1:])
    assert last["input_matched"]["complete_pairs"] == 0

    seat = _cell(report, "tv-m0-r0-first")["seats"][0]["vote"]
    assert seat["choice"] == "X"
    assert seat["correct"] is True
    assert seat["totals"] == {"X": -99, "Y": 7, "Z": 7}
    assert seat["visible_truth_totals"] == {"X": 10, "Y": 21, "Z": 10}
    assert seat["totals_match_visible"] is False
    assert seat["own_max_labels"] == ["Y", "Z"]
    assert seat["choice_agrees_with_own_max"] is False
    assert seat["order_adhered"] is False
    assert seat["field_order"] == ["choice", "cited", "totals"]
    native = base._read(first / "result.json")
    readout = native["seat_results"][0]["agent_result"]["metadata"]["turns"]["vote"][
        "readout"
    ]
    assert seat["response_sha256"] == base._digest(readout["response_text"])
    assert _cell(report, "tv-m0-r0-plain")["seats"][0]["vote"]["totals"] is None
    assert _cell(report, "tv-m0-r0-last")["seats"][1]["vote"][
        "visible_truth_totals"
    ] == {
        "X": 19,
        "Y": 21,
        "Z": 10,
    }
    arms = report["summary"]["by_arm"]
    assert arms["first"]["votes"]["own_tied_max_votes"] == 4
    assert arms["first"]["votes"]["choices_at_own_tied_max"] == 1
    assert arms["last"]["votes"]["visible_totals_exact_matches"] == 2
    assert arms["last"]["votes"]["assigned_order_matches"] == 4
    assert arms["plain"]["votes"]["structured_votes"] == 0
    public = json.dumps(report)
    for secret in (
        "response_text",
        "F_PRIV_",
        "Option_A",
        "agent_result",
        base._PRIVATE_PATH,
        native["id"],
    ):
        assert secret not in public


@pytest.mark.parametrize(
    ("tamper", "reason"),
    [
        ("response_hash", "vote_response_hash_mismatch"),
        ("prompt_hash", "vote_prompt_hash_mismatch"),
        ("plain_view_hash", "vote_view_hash_mismatch"),
        ("reported_type", "vote_readout_mismatch"),
        ("actual_vote", "vote_action_mismatch"),
    ],
)
def test_joined_receipt_is_not_proof_of_input_or_response(tmp_path, tamper, reason):
    trial = _trial(tmp_path, 0, 0, "first", choices=_WIN)
    _trial(tmp_path, 0, 0, "plain", choices=_LOSS)

    def edit(vote, action):
        if tamper in ("response_hash", "prompt_hash"):
            vote[tamper.removesuffix("_hash") + "_sha256"] = "0" * 64
        elif tamper == "plain_view_hash":
            vote["readout"]["view_sha256"] = "0" * 64
        elif tamper == "reported_type":
            vote["readout"]["totals"]["X"] = (
                True  # JSON true and integer 1 are distinct.
            )
        else:
            response = json.loads(vote["readout"]["response_text"])
            response["choice"] = "Z"
            text = json.dumps(response)
            vote["readout"]["response_text"] = text
            vote["response_sha256"] = base._digest(text)
            assert action["choice"] == "Option_A"

    _edit_votes(trial, 0, edit)
    report = tally_analysis.analyze(tmp_path)
    assert [row["cell_id"] for row in report["cells"]] == ["tv-m0-r0-plain"]
    excluded = base._excluded(report, "tv-m0-r0-first")
    assert excluded["reason"] == reason
    assert excluded["status"] == "failed"
    assert report["contrasts"]["first_minus_plain"]["pairs"][0]["delta"] is None


def test_maps_receive_equal_weight_and_matched_subset_requires_all_maps(tmp_path):
    for index in range(6):
        _trial(tmp_path, index, 0, "plain", choices=_LOSS)
        _trial(
            tmp_path,
            index,
            0,
            "first",
            choices=_WIN if index == 0 else _LOSS,
            n_private=int(index == 5),
        )
    _trial(tmp_path, 0, 1, "plain", choices=_LOSS)
    _trial(tmp_path, 0, 1, "first", choices=_LOSS)
    contrast = tally_analysis.analyze(tmp_path)["contrasts"]["first_minus_plain"]
    complete = contrast["all_complete"]
    assert complete["complete_pairs"] == 7
    assert complete["maps"][0]["effects"]["native_vote_reward"] == 0.5
    assert complete["effects"]["native_vote_reward"] == pytest.approx(1 / 12)
    assert complete["effects"]["correct_individual_votes"] == pytest.approx(1 / 12)
    matched = contrast["input_matched"]
    assert matched["complete_pairs"] == 6
    assert matched["maps"][5]["complete_pairs"] == 0
    assert matched["effects"]["native_vote_reward"] is None


def test_cli_counts_only_scheduled_tally_tokens_even_for_unscored_cells(
    tmp_path, capsys, monkeypatch
):
    study = tmp_path / "private-study"
    rows = []
    for arm, output in (("plain", 10), ("first", 20), ("last", 30)):
        _trial(study, 0, 0, arm)
        for role in base._ROLES:
            for phase in ("share", "vote"):
                rows.append(
                    (
                        f"tv-m0-r0-{arm}-{phase}",
                        role,
                        base._FINISHED,
                        base._SERVED,
                        2,
                        3,
                        output if phase == "vote" else 1,
                        1,
                        1,
                    )
                )
    for run, output in (
        ("tv-m1-r0-last-share", 4),
        ("tv-m6-r0-last-vote", 999),
        ("hp-m0-r0-open-vote", 999),
    ):
        rows.append(
            (run, base._ROLES[0], base._FINISHED, base._SERVED, 2, 3, output, 1, 1)
        )
    rows.append(
        (
            "tv-m1-r0-plain-vote",
            base._ROLES[0],
            base._FINISHED,
            None,
            None,
            None,
            None,
            None,
            None,
        )
    )
    ledger = base._ledger(tmp_path, rows)
    monkeypatch.setattr(
        "sys.argv", ["tally_analysis", str(study), "--spend-dir", str(ledger)]
    )
    tally_analysis.main()
    output = capsys.readouterr().out
    report = json.loads(output)
    assert str(study) not in output
    assert str(ledger) not in output
    assert report["spend_evidence"] == "available"
    assert report["summary"]["scored_cells"] == 3
    tokens = report["summary"]["tokens"]["all"]
    assert tokens["scheduled_calls"] == 288
    assert tokens["recorded_calls"] == 26
    assert tokens["measured_calls"] == 25
    assert tokens["held_calls"] == 1
    assert tokens["known_input_tokens"] == 125
    assert tokens["known_output_tokens"] == 256
    by_arm = report["summary"]["by_arm"]
    assert [
        by_arm[arm]["tokens"]["vote"]["known_output_tokens"]
        for arm in ("plain", "first", "last")
    ] == [40, 80, 120]
    assert by_arm["last"]["tokens"]["share"]["known_output_tokens"] == 8
