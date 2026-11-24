import json
import re
from copy import deepcopy
from hashlib import sha256
from typing import Any
from unittest import TestCase

from examples.seatlab.capsule import encode_view
from examples.seatlab.dispatch_order_pair import build_pair
from examples.seatlab.dispatch_order_score import score_pairs

_SOURCE = sha256(b"dispatch order fixture").hexdigest()
_DOMAIN = "seatlab.dispatch-order-request/v1"
_BLOCK = 17


def _cases() -> list[dict[str, Any]]:
    return [
        {
            "id": 0,
            "utilities": {"A": 4, "B": 2, "C": 3, "D": 1},
            "before": ["A", "B"],
            "after": ["A", "B"],
            "reports": [
                {"revision": 1, "available": ["A", "B"]},
                {"revision": 2, "available": ["B", "C"]},
            ],
        },
        {
            "id": 1,
            "utilities": {"A": 4, "B": 1, "C": 3, "D": 2},
            "before": ["A", "B"],
            "after": ["B", "C"],
            "reports": [
                {"revision": 1, "available": ["A", "B"]},
                {"revision": 2, "available": ["B", "C"]},
            ],
        },
        {
            "id": 2,
            "utilities": {"A": 3, "B": 4, "C": 1, "D": 2},
            "before": ["A", "D"],
            "after": ["A", "D"],
            "reports": [
                {"revision": 1, "available": ["A", "D"]},
                {"revision": 2, "available": ["A", "D"]},
            ],
        },
    ]


_CHRONOLOGICAL = ("C", "C", "A")
_REVERSED = ("C", "A", "D")

# The reported recommendations are A/C, A/C, and A/A.
# The true optima are A, C, and A. C is infeasible in case 0.
# Only case 1 changes to the older recommendation. D is feasible in case 2.
_COUNTS = {
    "shipments": 3,
    "different_recommendations": 2,
    "changed_choice": 2,
    "changed_on_different_recommendations": 1,
    "chronological_latest": 3,
    "reversed_latest": 1,
    "chronological_optimal": 2,
    "reversed_optimal": 0,
    "chronological_feasible": 2,
    "reversed_feasible": 1,
}


def _observed(text: str) -> tuple[str, str]:
    return text, sha256(text.encode("utf-8")).hexdigest()


def _reply(choices: tuple[str, ...], ids: tuple[int, ...]) -> tuple[str, str]:
    return _observed(json.dumps({"rows": [{"id": i, "bay": choices[i]} for i in ids]}))


def _world(
    block: int, chronological: str, reversed_: str, counts: dict[str, int] | None
) -> dict[str, Any]:
    return {
        "block": block,
        "chronological": chronological,
        "reversed": reversed_,
        "counts": counts,
    }


def _case_document(text: str) -> tuple[list[Any], int, int]:
    decoder = json.JSONDecoder()
    for marker in re.finditer(r"[\[{]", text):
        start = marker.start()
        try:
            document, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            continue
        if isinstance(document, dict) and "cases" in document:
            if set(document) != {"cases"}:
                raise AssertionError("actor case document contains additional fields")
            return document["cases"], start, end
        if (
            isinstance(document, list)
            and document
            and isinstance(document[0], dict)
            and "utilities" in document[0]
        ):
            return document, start, end
    raise AssertionError("no actor-visible case document")


class TestDispatchOrder(TestCase):
    def test_actor_view_is_private_and_the_only_change_is_report_order(self) -> None:
        cases = _cases()
        original = deepcopy(cases)
        pair = build_pair(_BLOCK, _SOURCE, cases)
        self.assertEqual(cases, original)
        self.assertEqual(pair.cases, original)

        messages = deepcopy(pair.chronological.messages)
        [user] = [message for message in messages if message["role"] == "user"]
        actor_cases, start, end = _case_document(user["content"])
        self.assertEqual(len(actor_cases), len(original))
        expected_reports = []
        for row, case in zip(actor_cases, original, strict=True):
            report_key = "reports" if "reports" in row else "messages"
            self.assertEqual(
                row,
                {
                    "id": case["id"],
                    "utilities": case["utilities"],
                    report_key: case["reports"],
                },
            )
            expected_reports.extend(case["reports"])

        spans = [
            match
            for match in re.finditer(r"\{[^{}]*\}", user["content"], re.DOTALL)
            if start <= match.start() < match.end() <= end
            and set(json.loads(match.group())) == {"revision", "available"}
        ]
        self.assertEqual([json.loads(span.group()) for span in spans], expected_reports)
        text = user["content"]
        for left, right in reversed(list(zip(spans[::2], spans[1::2], strict=True))):
            text = (
                text[: left.start()]
                + right.group()
                + text[left.end() : right.start()]
                + left.group()
                + text[right.end() :]
            )
        user["content"] = text
        self.assertEqual(messages, pair.reversed.messages)

        changed_stock = deepcopy(original)
        changed_stock[0]["before"] = changed_stock[0]["after"] = ["B", "D"]
        private_source = sha256(b"different dispatch order source").hexdigest()
        counterfactual = build_pair(_BLOCK + 1, private_source, changed_stock)
        for name in ("chronological", "reversed"):
            arm = getattr(pair, name)
            alternate = getattr(counterfactual, name)
            self.assertEqual(arm.messages, alternate.messages)
            for message in arm.messages:
                self.assertNotRegex(
                    message["content"].lower(), r"\b(chronological|reversed)\b"
                )
                self.assertNotRegex(
                    message["content"].lower(),
                    r'"(origin|source_sha256|block|grade|seed|uuid|answers?|before|after|decisions?|condition|arm)"\s*:',
                )
                self.assertNotIn(_SOURCE, message["content"])
                self.assertNotIn(arm.id, message["content"])

    def test_prompt_and_subject_commitments_bind_the_assigned_request(self) -> None:
        cases = _cases()
        other_source = sha256(b"second dispatch order source").hexdigest()
        cases_with_new_report = deepcopy(cases)
        cases_with_new_report[0]["reports"][1]["available"] = ["C", "D"]
        subjects = set()
        for block, source, rows in (
            (_BLOCK, _SOURCE, cases),
            (_BLOCK + 1, _SOURCE, cases),
            (_BLOCK, other_source, cases),
            (_BLOCK, _SOURCE, cases_with_new_report),
        ):
            pair = build_pair(block, source, rows)
            self.assertNotEqual(
                pair.chronological.prompt_sha256, pair.reversed.prompt_sha256
            )
            for name in ("chronological", "reversed"):
                arm = getattr(pair, name)
                digest = sha256(encode_view(arm.messages)).hexdigest()
                self.assertEqual(arm.prompt_sha256, digest)
                self.assertEqual(
                    arm.id,
                    sha256(
                        encode_view([_DOMAIN, source, block, name, digest])
                    ).hexdigest(),
                )
                self.assertNotIn(arm.id, subjects)
                subjects.add(arm.id)

    def test_score_counts_and_reply_order_invariance(self) -> None:
        cases = _cases()
        original = deepcopy(cases)
        pair = build_pair(_BLOCK, _SOURCE, cases)
        pairs = [pair]
        replies = {
            pair.chronological.id: _reply(_CHRONOLOGICAL, (0, 1, 2)),
            pair.reversed.id: _reply(_REVERSED, (0, 1, 2)),
        }
        snapshot = deepcopy((pairs, replies))
        expected = {
            "schema": "seatlab.dispatch-order-score/v1",
            "coverage": {
                "expected_replies": 2,
                "valid": 2,
                "invalid": 0,
                "missing": 0,
                "paired_worlds": 1,
                "expected_worlds": 1,
            },
            "worlds": [_world(_BLOCK, "valid", "valid", _COUNTS)],
            "paired_totals": _COUNTS,
        }
        self.assertEqual(score_pairs(pairs, replies), expected)
        self.assertEqual(
            score_pairs(pairs, dict(reversed(list(replies.items())))), expected
        )
        self.assertEqual(
            score_pairs(
                pairs,
                {
                    pair.chronological.id: _reply(_CHRONOLOGICAL, (2, 0, 1)),
                    pair.reversed.id: _reply(_REVERSED, (1, 2, 0)),
                },
            ),
            expected,
        )
        self.assertEqual((pairs, replies), snapshot)
        self.assertEqual(cases, original)

    def test_partial_worlds_do_not_contribute_to_paired_totals(self) -> None:
        complete, missing, malformed = [
            build_pair(block, _SOURCE, _cases()) for block in range(_BLOCK, _BLOCK + 3)
        ]
        chronological = _reply(_CHRONOLOGICAL, (0, 1, 2))
        reversed_ = _reply(_REVERSED, (0, 1, 2))
        replies = {
            complete.chronological.id: chronological,
            complete.reversed.id: reversed_,
            missing.chronological.id: chronological,
            malformed.chronological.id: _observed("UNTRUSTED_REPLY_TEXT"),
            malformed.reversed.id: reversed_,
        }
        self.assertEqual(
            score_pairs([complete, missing, malformed], replies),
            {
                "schema": "seatlab.dispatch-order-score/v1",
                "coverage": {
                    "expected_replies": 6,
                    "valid": 4,
                    "invalid": 1,
                    "missing": 1,
                    "paired_worlds": 1,
                    "expected_worlds": 3,
                },
                "worlds": [
                    _world(_BLOCK, "valid", "valid", _COUNTS),
                    _world(_BLOCK + 1, "valid", "missing", None),
                    _world(_BLOCK + 2, "invalid", "valid", None),
                ],
                "paired_totals": _COUNTS,
            },
        )

    def test_invalid_reply_cannot_score_with_one_valid_side(self) -> None:
        pair = build_pair(_BLOCK, _SOURCE, _cases())
        valid = _reply(_CHRONOLOGICAL, (0, 1, 2))
        rows = json.loads(valid[0])["rows"]
        malformed = {
            "duplicate top key": '{"rows":[],"rows":' + json.dumps(rows) + "}",
            "duplicate nested key": valid[0].replace('"id": 0', '"id": 5, "id": 0'),
            "nonfinite": valid[0].replace('"id": 0', '"id": NaN'),
            "missing ID": json.dumps({"rows": rows[:-1]}),
            "duplicate ID": json.dumps({"rows": [rows[0], rows[1], rows[1]]}),
            "unknown ID": valid[0].replace('"id": 0', '"id": 3'),
            "boolean ID": valid[0].replace('"id": 0', '"id": false'),
            "unknown bay": valid[0].replace('"bay": "A"', '"bay": "X"'),
            "extra row key": valid[0].replace('"id": 0', '"id": 0, "extra": "secret"'),
            "extra root key": json.dumps({"rows": rows, "extra": "secret"}),
        }
        expected = {
            "schema": "seatlab.dispatch-order-score/v1",
            "coverage": {
                "expected_replies": 2,
                "valid": 1,
                "invalid": 1,
                "missing": 0,
                "paired_worlds": 0,
                "expected_worlds": 1,
            },
            "worlds": [_world(_BLOCK, "valid", "invalid", None)],
            "paired_totals": None,
        }
        for label, text in malformed.items():
            with self.subTest(label=label):
                self.assertEqual(
                    score_pairs(
                        [pair],
                        {
                            pair.chronological.id: valid,
                            pair.reversed.id: _observed(text),
                        },
                    ),
                    expected,
                )
