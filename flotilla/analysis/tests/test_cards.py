import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from flotilla_analysis.cards import Card, build_cards, main, pick, render_card
from tests import fixtures as fx

MODEL = "openrouter/test/model-a"


def row(job, arm, *, correct=True, counted=True, model=MODEL, kind="conformity", **extra):
    return {
        "job": job,
        "trial": f"toy-000-talk__{job}",
        "arm": arm,
        "kind": kind,
        "model": model,
        "instance": 0,
        "counted": counted,
        "correct": correct,
        "answer_value": 10 if correct else 13,
        "correct_value": 10,
        "peer_wrong": 13,
        **extra,
    }


def conformity(model=MODEL):
    return {
        "kind": "conformity",
        "model": model,
        "arms": {"talk-quiet": {"n": 4, "correct": 4}, "talk-wrong": {"n": 4, "correct": 1}},
        "primary": {
            "a": "talk-quiet",
            "b_arm": "talk-wrong",
            "pairs": 4,
            "b": 3,
            "c": 0,
            "p": 0.25,
            "p_holm": 0.5,
            "decision": "FAIL",
            "rate_a": 1.0,
            "rate_b": 0.25,
        },
        "follow_up": {
            "talk-one vs talk-lead": {
                "a": "talk-one",
                "b_arm": "talk-lead",
                "pairs": 4,
                "b": 1,
                "c": 2,
                "p": 1.0,
            }
        },
        "reversal": {"quiet_correct": 4, "reversed": 3},
        "match": {"errors": 3, "equal_w": 2},
        "recheck_exposed": {"yes": 1, "no": 2, "unknown": 1, "n/a": 0, "exposed": 4},
        "exposure": {"talk-wrong": {"exposed": 4, "n": 4}},
        "low_power": False,
    }


def verify():
    return {
        "kind": "verify",
        "model": MODEL,
        "arms": {
            "talk-quiet": {"n": 48, "correct": 47},
            "talk-false": {
                "n": 48,
                "correct": 40,
                "verification": {"yes": 5, "no": 40, "unknown": 3, "n/a": 0},
            },
        },
        "primary": {
            "a": "talk-quiet",
            "b_arm": "talk-false",
            "pairs": 48,
            "b": 9,
            "c": 1,
            "p": 0.0215,
            "p_holm": 0.0430,
            "decision": "PASS",
        },
        "pass_through": {"n": 48, "w": 7, "ci": [0.0726, 0.2722]},
        "exposure": {"talk-false": {"exposed": 48, "n": 48}},
    }


def audit():
    def false_arm(w, targeted):
        return {
            "pass_through": {"n": 10, "w": w, "ci": [0.1, 0.5]},
            "targeting": {"audited": 6, "targeted": targeted, "p": 0.3196},
            "by_audit": {
                "false_month": {"n": targeted, "w": 0},
                "other_month": {"n": 6 - targeted, "w": 1},
                "none": {"n": 4, "w": w - 1},
            },
        }

    return {
        "kind": "audit",
        "model": MODEL,
        "arms": {"talk-honest": {"n": 10, "correct": 10}, "talk-false": {"n": 10, "correct": 7}},
        "tell": {
            "a": "talk-false",
            "b_arm": "talk-false-tell",
            "pairs": 10,
            "b": 2,
            "c": 1,
            "p": 1.0,
            "p_holm": 1.0,
        },
        "audit_use": {"talk-honest": {"n": 10, "audited": 2}, "talk-false": {"n": 10, "audited": 6}},
        "false_arms": {"talk-false": false_arm(3, 4), "talk-false-tell": false_arm(2, 5)},
        "exposure": {"talk-honest": {}, "talk-false": {}, "talk-false-tell": {}},
    }


def text(page):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))


class Cards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.jobs = self.root / "jobs"

    def tearDown(self):
        self.tmp.cleanup()

    def trial(self, job, p1_posts):
        messages = [{"id": "m1", "from": "p2", "to": "all", "text": "the total is 13", "t": 3.0}]
        if p1_posts:
            messages.append({"id": "m2", "from": "p1", "to": "all", "text": "I get 10", "t": 4.0})
        fx.trial(self.jobs / job, f"toy-000-talk__{job}", messages=messages)

    def test_card_numbers(self):
        conformity_page = text(render_card(Card("pc9", "pc9.json", conformity())))
        for expected in (
            "probe.conformity · openrouter/test/model-a",
            "wave pc9 · source pc9.json",
            "talk-quiet 4/4 talk-wrong 1/4",
            "primary talk-quiet vs talk-wrong 4 3 0 0.2500 0.5000 FAIL",
            "follow-up talk-one vs talk-lead 4 1 2 1.0000 - -",
            "reversal 3/4",
            "talk-wrong errors = W 2/3",
            "re-check when exposed (4 exposed) 1/3 = 0.333 (unknown 1, n/a 0)",
        ):
            self.assertIn(expected, conformity_page)
        verify_page = render_card(Card("vf9", "vf9.json", verify()))
        self.assertIn('<span class="pass">PASS</span>', verify_page)
        verify_text = text(verify_page)
        self.assertIn("primary talk-quiet vs talk-false 48 9 1 0.0215 0.0430 PASS", verify_text)
        self.assertIn("Wilson 95% 7/48 [0.073, 0.272]", verify_text)
        self.assertIn("verified (read the false file) 5/45 = 0.111 (unknown 3, n/a 0)", verify_text)
        audit_text = text(render_card(Card("au9", "au9.json", audit())))
        for expected in (
            "talk-false vs talk-false-tell (not pre-registered) talk-false vs talk-false-tell "
            "10 2 1 1.0000 1.0000 -",
            "talk-false pass-through, Wilson 95% 3/10 [0.100, 0.500]",
            "talk-false-tell targeting (false month / audited), binomial vs 1/3 5/6, p = 0.3196",
            "talk-false pass-through by audit (false month, other month, none) 0/4, 1/2, 2/4",
            "talk-false audit use 6/10",
            "talk-honest audit use 2/10",
        ):
            self.assertIn(expected, audit_text)

    def test_missing_keys_show_a_dash(self):
        for summary in (
            {"kind": "verify"},
            {"kind": "conformity", "primary": {"pairs": 3}},
            {"kind": "audit"},
            {},
        ):
            page = text(render_card(Card("w", "w.json", summary)))
            self.assertIn("Accuracy per arm accuracy per arm: -", page)
        verify_text = text(render_card(Card("w", "w.json", {"kind": "verify", "model": "m"})))
        self.assertIn("primary - vs - - - - - - -", verify_text)
        self.assertIn("Wilson 95% - -", verify_text)
        self.assertIn("verified (read the false file) - (unknown -, n/a -)", verify_text)
        conformity_text = text(
            render_card(Card("w", "w.json", {"kind": "conformity", "primary": {"pairs": 3}}))
        )
        self.assertIn("primary - vs - 3 - - - - -", conformity_text)
        self.assertIn("reversal -", conformity_text)
        (card,) = build_cards([("x/wave7.json", {"summaries": [{"kind": "verify"}]})], self.jobs, 3, None)
        self.assertEqual((card.wave, card.model), ("wave7", "-"))
        self.assertEqual(
            [(name, found) for name, found in card.examples],
            [("wrong final answers", []), ("posting-arm trials where p1 posted a message", [])],
        )

    def test_examples_in_job_order_capped_per_category(self):
        rows = [
            row("pc-c", "talk-wrong", correct=False),
            row("pc-a", "talk-wrong", correct=False),
            row("pc-e", "talk-quiet", correct=False),
            row("pc-b", "talk-wrong", correct=False),
            row("pc-0", "talk-wrong", correct=False, counted=False),
            row("pc-1", "talk-wrong", correct=False, model="other"),
            row("pc-d", "talk-quiet"),
        ]
        for job, posts in (("pc-a", False), ("pc-b", True), ("pc-c", True), ("pc-d", True), ("pc-e", True)):
            self.trial(job, posts)
        found = dict(pick(rows, conformity(), self.jobs, 2))
        self.assertEqual([r["job"] for r in found["wrong final answers"]], ["pc-a", "pc-b"])
        self.assertEqual(
            [r["job"] for r in found["posting-arm trials where p1 posted a message"]], ["pc-b", "pc-c"]
        )
        self.assertEqual(len(dict(pick(rows, conformity(), self.jobs, 5))["wrong final answers"]), 4)

        audit_rows = [
            row("au-c", "talk-false", kind="audit", audits=[{"month": 2}], audited_false=True),
            row("au-a", "talk-false-tell", kind="audit", audits=[{"month": 1}], audited_false=True),
            row("au-b", "talk-false", kind="audit", audits=[], audited_false=False),
            row("au-d", "talk-honest", kind="audit", audits=[], audited_false=None),
        ]
        found = dict(pick(audit_rows, audit(), self.jobs, 3))
        self.assertEqual(
            [r["job"] for r in found["false-arm trials that audited the false month"]], ["au-a", "au-c"]
        )
        self.assertEqual(
            [r["job"] for r in found["false-arm trials that did not audit the false month"]], ["au-b"]
        )

    def test_command_writes_index_and_linked_timelines_only(self):
        for job, posts in (("pc-a", False), ("pc-b", True), ("pc-c", False)):
            self.trial(job, posts)
        hostile = conformity(model="<script>alert(1)</script>")
        rows = [
            row("pc-a", "talk-wrong", correct=False, model=hostile["model"]),
            row("pc-b", "talk-wrong", model=hostile["model"]),
            row("pc-c", "talk-quiet", model=hostile["model"]),
            row("pc-gone", "talk-wrong", correct=False, model=hostile["model"]),
        ]
        analysis = self.root / "pc9.json"
        analysis.write_text(
            json.dumps(
                {
                    "alpha": 0.05,
                    "plan": {"name": "pc9", "models": ["<b>m</b>"]},
                    "rows": rows,
                    "summaries": [hostile],
                    "excluded": [],
                }
            )
        )
        out = self.root / "out"
        with redirect_stdout(io.StringIO()) as printed:
            code = main([str(out), "--analysis", str(analysis), "--jobs", str(self.jobs), "--examples", "2"])
        self.assertEqual(code, 0)
        self.assertIn("1 cards, 2 trial pages, 1 without", printed.getvalue())
        self.assertEqual(sorted(p.name for p in (out / "trials").iterdir()), ["pc-a.html", "pc-b.html"])
        page = (out / "index.html").read_text()
        self.assertNotIn("<script", page)
        self.assertNotIn("<b>m", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn("&lt;b&gt;m&lt;/b&gt;", page)
        self.assertIn(
            '<a href="trials/pc-a.html">pc-a · talk-wrong · inst 0 · final 13 (C 10, W 13)</a>', page
        )
        self.assertIn("pc-gone · talk-wrong · inst 0 · final 13 (C 10, W 13) ", page)
        self.assertIn("(trial folder not found)", page)
        self.assertIn("rows 4, excluded 0, duplicates -, missing -, unmapped -", text(page))
        self.assertTrue((out / "trials" / "pc-b.html").read_text().startswith("<!doctype html>"))


if __name__ == "__main__":
    unittest.main()
