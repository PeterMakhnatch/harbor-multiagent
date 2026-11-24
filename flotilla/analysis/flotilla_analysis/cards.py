"""Behavior cards: one static HTML page of the probe pilots, a card per (probe kind, or build when
probe_analysis split the run's kind by build; wave; model).

``flotilla-cards OUTDIR --analysis a.json [--analysis b.json ...] --jobs DIR [--examples N]`` reads the
JSON that ``flotilla/docs/results/scripts/probe_analysis.py ... --json PATH`` writes, and writes
``OUTDIR/index.html`` plus ``OUTDIR/trials/<job>.html``: the ``flotilla-timeline`` page of every example
trial a card links.

Every number on a card is copied from the JSON (its ``summaries`` and ``rows``) or read from a linked trial's
files; a value the JSON lacks is shown as "-". Example trials are the counted rows of the card's kind and
model, per category in a fixed order (job name, then trial name), at most N per category. The tool reads only
the trial folders that the given JSON names.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from flotilla_analysis import __version__
from flotilla_analysis.load import load, read_json, read_jsonl
from flotilla_analysis.timeline import build_timeline, esc, render_trial

SUBJECT = "p1"
EXAMPLES = 3

STYLE = """
body { font: 13px/1.45 -apple-system, "Segoe UI", sans-serif; margin: 16px; color: #1f2328;
  max-width: 1500px; }
h1 { font-size: 20px; margin: 0 0 6px; }
h2 { font-size: 16px; margin: 22px 0 8px; border-bottom: 1px solid #d0d7de; }
h3 { font-size: 14px; margin: 0 0 4px; }
h4 { font-size: 12px; margin: 10px 0 3px; color: #57606a; text-transform: uppercase; }
table { border-collapse: collapse; margin: 2px 0; }
th, td { border: 1px solid #d0d7de; padding: 2px 6px; text-align: left; vertical-align: top; }
th { background: #f6f8fa; font-weight: 600; }
.cards { display: flex; flex-wrap: wrap; gap: 12px; }
.card { border: 1px solid #d0d7de; border-radius: 6px; padding: 10px 12px; width: 460px; background: #fff; }
.meta { color: #57606a; font-size: 12px; }
.pass { color: #1a7f37; font-weight: 700; }
.fail { color: #cf222e; font-weight: 700; }
.note { color: #9a6700; }
ul { margin: 2px 0; padding-left: 18px; }
code { font-size: 12px; }
"""


@dataclass
class Example:
    row: dict[str, Any]
    href: str | None = None  # the trial page, relative to index.html
    problem: str | None = None  # why there is no page


@dataclass
class Card:
    wave: str
    source: str
    summary: dict[str, Any]
    examples: list[tuple[str, list[Example]]] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return str(self.summary.get("kind") or "-")

    @property
    def model(self) -> str:
        return str(self.summary.get("model") or "-")

    @property
    def build(self) -> str | None:
        """The build label when probe_analysis split the run's kind by build (summary ``build``)."""
        build = self.summary.get("build")
        return build if isinstance(build, str) and build else None

    @property
    def label(self) -> str:
        return self.build or f"probe.{self.kind}"


def dig(data: Any, *keys: Any) -> Any:
    """``data[k1][k2]...``, or None when a key or index is missing."""
    for key in keys:
        indexable = isinstance(data, list) and isinstance(key, int) and -len(data) <= key < len(data)
        if not (indexable or (isinstance(data, dict) and key in data)):
            return None
        data = data[key]
    return data


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def of(k: Any, n: Any) -> str:
    """``k/n``; "-" when either is missing or n is 0 (as probe_analysis.of)."""
    k, n = _count(k), _count(n)
    return "-" if k is None or not n else f"{k}/{n}"


def share(k: Any, n: Any) -> str:
    k, n = _count(k), _count(n)
    return "-" if k is None or not n else f"{k}/{n} = {k / n:.3f}"


def interval(ci: Any) -> str:
    low, high = dig(ci, 0), dig(ci, 1)
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        return "-"
    return f"[{low:.3f}, {high:.3f}]"


def known(counts: Any) -> str:
    """yes / (yes + no), with the unknown and n/a counts (as probe_analysis.known)."""
    yes, no = _count(dig(counts, "yes")), _count(dig(counts, "no"))
    base = share(yes, yes + no) if yes is not None and no is not None else "-"
    return f"{base} (unknown {fmt(dig(counts, 'unknown'))}, n/a {fmt(dig(counts, 'n/a'))})"


def _decision(value: Any) -> str:
    text = fmt(value)
    css = "pass" if text == "PASS" else "fail" if text.startswith("FAIL") else ""
    return f'<span class="{css}">{esc(text)}</span>' if css else esc(text)


def _table(head: list[str], rows: list[list[str]]) -> str:
    """A table of already escaped cells."""
    top = "".join(f"<th>{h}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><tr>{top}</tr>{body}</table>"


def _test_rows(name: str, test: Any, decision: bool) -> list[str]:
    cells = [
        esc(name),
        esc(f"{fmt(dig(test, 'a'))} vs {fmt(dig(test, 'b_arm'))}"),
        esc(fmt(dig(test, "pairs"))),
        esc(fmt(dig(test, "b"))),
        esc(fmt(dig(test, "c"))),
        esc(fmt(dig(test, "p"), 4)),
        esc(fmt(dig(test, "p_holm"), 4)),
    ]
    return cells + [_decision(dig(test, "decision")) if decision else "-"]


TEST_HEAD = ["test", "arms", "pairs", "b", "c", "p", "Holm p", "decision"]


def _arms(summary: dict[str, Any]) -> str:
    arms = dig(summary, "arms") or {}
    if not isinstance(arms, dict) or not arms:
        return "<p>accuracy per arm: -</p>"
    rows = [[esc(arm), esc(of(dig(a, "correct"), dig(a, "n")))] for arm, a in arms.items()]
    return _table(["arm", "correct (k/n)"], rows)


def _primary(summary: dict[str, Any]) -> str:
    if summary.get("kind") == "audit":
        rows = [_test_rows("primary", dig(summary, "tell"), decision=True)]
        return "<h4>Pre-registered primary</h4>" + _table(TEST_HEAD, rows)
    rows = [_test_rows("primary", dig(summary, "primary"), decision=True)]
    for test in (dig(summary, "follow_up") or {}).values():
        rows.append(_test_rows("follow-up", test, decision=False))
    out = "<h4>Pre-registered primary</h4>" + _table(TEST_HEAD, rows)
    if dig(summary, "low_power") is True:
        out += '<p class="note">talk-quiet accuracy is below 0.6: the primary has little power.</p>'
    return out


def _secondaries(summary: dict[str, Any]) -> list[tuple[str, str]]:
    kind = summary.get("kind")
    if kind == "conformity":
        return [
            ("reversal", of(dig(summary, "reversal", "reversed"), dig(summary, "reversal", "quiet_correct"))),
            ("talk-wrong errors = W", of(dig(summary, "match", "equal_w"), dig(summary, "match", "errors"))),
            (
                f"re-check when exposed ({fmt(dig(summary, 'recheck_exposed', 'exposed'))} exposed)",
                known(dig(summary, "recheck_exposed")),
            ),
        ]
    if kind == "verify":
        passed = dig(summary, "pass_through")
        return [
            (
                "pass-through (talk-false final = W), Wilson 95%",
                f"{of(dig(passed, 'w'), dig(passed, 'n'))} {interval(dig(passed, 'ci'))}",
            ),
            ("verified (read the false file)", known(dig(summary, "arms", "talk-false", "verification"))),
        ]
    if kind == "audit":
        found = []
        for arm, a in (dig(summary, "false_arms") or {}).items():
            passed, target = dig(a, "pass_through"), dig(a, "targeting")
            by = dig(a, "by_audit")
            found += [
                (
                    f"{arm} pass-through, Wilson 95%",
                    f"{of(dig(passed, 'w'), dig(passed, 'n'))} {interval(dig(passed, 'ci'))}",
                ),
                (
                    f"{arm} targeting (false month / audited), binomial vs 1/3",
                    f"{of(dig(target, 'targeted'), dig(target, 'audited'))}, p = {fmt(dig(target, 'p'), 4)}",
                ),
                (
                    f"{arm} pass-through by audit (false month, other month, none)",
                    ", ".join(
                        of(dig(by, k, "w"), dig(by, k, "n")) for k in ("false_month", "other_month", "none")
                    ),
                ),
            ]
        for arm, use in (dig(summary, "audit_use") or {}).items():
            found.append((f"{arm} audit use", of(dig(use, "audited"), dig(use, "n"))))
        return found
    return []


def _example_html(example: Example) -> str:
    row = example.row
    text = (
        f"{fmt(row.get('job'))} · {fmt(row.get('arm'))} · inst {fmt(row.get('instance'))} · final "
        f"{fmt(row.get('answer_value'))} (C {fmt(row.get('correct_value'))}, W {fmt(row.get('peer_wrong'))})"
    )
    if example.href:
        return f'<li><a href="{esc(example.href)}">{esc(text)}</a></li>'
    return f'<li>{esc(text)} <span class="note">({esc(example.problem or "no page")})</span></li>'


def render_card(card: Card) -> str:
    summary = card.summary
    lines = "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in _secondaries(summary))
    examples = ""
    for category, found in card.examples:
        items = "".join(_example_html(e) for e in found) or "<li>-</li>"
        examples += f"<div>{esc(category)}<ul>{items}</ul></div>"
    return (
        f'<div class="card"><h3>{esc(card.label)} · {esc(card.model)}</h3>'
        f'<div class="meta">wave {esc(card.wave)} · source {esc(card.source)}</div>'
        f"<h4>Accuracy per arm</h4>{_arms(summary)}{_primary(summary)}"
        f"<h4>Key secondaries</h4>{f'<table>{lines}</table>' if lines else '-'}"
        f"<h4>Examples</h4>{examples or '-'}</div>"
    )


def p1_posted(trial_dir: Path) -> bool | None:
    """Whether p1 sent a message: world-state.json (authoritative), else messages.jsonl; None without
    either."""
    state = read_json(trial_dir / "verifier" / "world-state.json")
    messages = state.get("messages") if isinstance(state, dict) else None
    if not isinstance(messages, list):
        messages = read_jsonl(trial_dir / "artifacts" / "logs" / "world" / "messages.jsonl", [])
    if messages is None:
        return None
    return any(isinstance(m, dict) and m.get("from") == SUBJECT for m in messages)


def categories(summary: dict[str, Any], jobs: Path) -> list[tuple[str, Any]]:
    """(name, test) per example category of the card's kind; the posting and the false arms come from the
    summary."""
    posting = set(dig(summary, "exposure") or {})
    found: list[tuple[str, Any]] = [
        ("wrong final answers", lambda r: r.get("correct") is False),
        (
            "posting-arm trials where p1 posted a message",
            lambda r: (
                r.get("arm") in posting and p1_posted(jobs / str(r.get("job")) / str(r.get("trial"))) is True
            ),
        ),
    ]
    if summary.get("kind") == "audit":
        false_arms = set(dig(summary, "false_arms") or {})
        found += [
            (
                "false-arm trials that audited the false month",
                lambda r: r.get("arm") in false_arms and r.get("audited_false") is True,
            ),
            (
                "false-arm trials that did not audit the false month",
                lambda r: r.get("arm") in false_arms and r.get("audited_false") is not True,
            ),
        ]
    return found


def pick(rows: list[dict[str, Any]], summary: dict[str, Any], jobs: Path, n: int) -> list[tuple[str, list]]:
    """Up to n counted rows of the card's kind, build (when the run is split by build) and model per category,
    by job name then trial name."""
    mine = sorted(
        (
            r
            for r in rows
            if isinstance(r, dict)
            and r.get("counted") is True
            and r.get("kind") == summary.get("kind")
            and r.get("model") == summary.get("model")
            and ("build" not in summary or r.get("build") == summary.get("build"))
        ),
        key=lambda r: (str(r.get("job")), str(r.get("trial"))),
    )
    found = []
    for name, test in categories(summary, jobs):
        chosen = []
        for row in mine:
            if len(chosen) >= n:
                break
            if test(row):
                chosen.append(row)
        found.append((name, chosen))
    return found


class Pages:
    """Renders each linked trial's timeline once, into OUTDIR/trials/<job>.html."""

    def __init__(self, out: Path, jobs: Path, manifests: list[Path], tasks: list[Path]):
        self.out, self.jobs, self.manifests, self.tasks = out, jobs, manifests, tasks
        self.done: dict[tuple[str, str], Example] = {}
        self.names: set[str] = set()

    def example(self, row: dict[str, Any]) -> Example:
        key = (str(row.get("job")), str(row.get("trial")))
        if key not in self.done:
            self.done[key] = self._render(row, *key)
        return Example(row, self.done[key].href, self.done[key].problem)

    def _render(self, row: dict[str, Any], job: str, trial: str) -> Example:
        directory = self.jobs / job / trial
        if not (directory / "result.json").is_file():
            return Example(row, problem="trial folder not found")
        trials, _, problems = load([directory], self.manifests, task_roots=self.tasks)
        if len(trials) != 1:
            reason = problems[0].reason if problems else "not a Flotilla trial"
            return Example(row, problem=reason)
        name = re.sub(r"[^A-Za-z0-9._-]", "_", job) + ".html"
        if name in self.names:
            name = re.sub(r"[^A-Za-z0-9._-]", "_", f"{job}__{trial}") + ".html"
        self.names.add(name)
        (self.out / "trials").mkdir(parents=True, exist_ok=True)
        (self.out / "trials" / name).write_text(render_trial(build_timeline(trials[0])), encoding="utf-8")
        return Example(row, href=f"trials/{name}")


def build_cards(
    analyses: list[tuple[str, dict[str, Any]]], jobs: Path, n: int, pages: Pages | None
) -> list[Card]:
    cards = []
    for source, data in analyses:
        wave = fmt(dig(data, "plan", "name")) if dig(data, "plan", "name") else Path(source).stem
        rows = [r for r in data.get("rows") or [] if isinstance(r, dict)]
        for summary in data.get("summaries") or []:
            if not isinstance(summary, dict):
                continue
            card = Card(wave, source, summary)
            for name, chosen in pick(rows, summary, jobs, n):
                card.examples.append((name, [pages.example(r) if pages else Example(r) for r in chosen]))
            cards.append(card)
    return cards


def _source_html(source: str, data: dict[str, Any]) -> str:
    counts = ", ".join(
        f"{key} {len(data[key]) if isinstance(data.get(key), list) else '-'}"
        for key in ("rows", "excluded", "duplicates", "missing", "unmapped")
    )
    models = dig(data, "plan", "models")
    name, alpha = fmt(dig(data, "plan", "name")), fmt(data.get("alpha"))
    listed = ", ".join(map(str, models)) if isinstance(models, list) else "-"
    builds = sorted(
        {str(s["build"]) for s in data.get("summaries") or [] if isinstance(s, dict) and s.get("build")}
    )
    split = f"; split by build: {', '.join(builds)}" if builds else ""
    return (
        f"<li><code>{esc(source)}</code>: wave {esc(name)}, alpha {esc(alpha)}, models {esc(listed)}; "
        f"{esc(counts)}{esc(split)}</li>"
    )


def render_index(analyses: list[tuple[str, dict[str, Any]]], cards: list[Card], n: int) -> str:
    sources = "".join(_source_html(source, data) for source, data in analyses)
    sections = []
    for source, _ in analyses:
        mine = [c for c in cards if c.source == source]
        if not mine:
            sections.append(f"<h2>{esc(source)}</h2><p>no summaries</p>")
            continue
        groups: dict[str, list[Card]] = {}
        for card in mine:
            groups.setdefault(card.label, []).append(card)
        for label, group in groups.items():
            sections.append(
                f"<h2>{esc(label)} · wave {esc(group[0].wave)}</h2>"
                f'<div class="cards">{"".join(render_card(c) for c in group)}</div>'
            )
    body = (
        "<h1>Flotilla behavior cards</h1>"
        '<p class="meta">Every number comes from the JSON that <code>flotilla/docs/results/scripts/'
        "probe_analysis.py --json</code> wrote (the tests pre-registered in docs/PROBE.md sections 7 to 11); "
        '"-" means the JSON has no value. Only whether p1 posted a message is '
        "read from the trial files (world-state.json). Examples: counted trials of the card's model, at most "
        f"{n} per category, by job name; a link opens the trial's flotilla-timeline page.</p>"
        f"<h2>Sources</h2><ul>{sources}</ul>{''.join(sections)}"
    )
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        f"<title>Flotilla behavior cards</title>\n<style>{STYLE}</style>\n</head>\n<body>\n{body}\n</body>\n"
        "</html>\n"
    )


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="flotilla-cards",
        description="Write a static behavior-cards page from probe_analysis.py JSON, with example trial "
        "timelines.",
    )
    p.add_argument("out", metavar="OUTDIR", type=Path)
    p.add_argument(
        "--analysis",
        type=Path,
        action="append",
        required=True,
        metavar="JSON",
        help="a probe_analysis.py --json output; may be repeated (cards keep this order)",
    )
    p.add_argument("--jobs", type=Path, required=True, metavar="DIR", help="the jobs folder of the trials")
    p.add_argument(
        "--examples",
        type=int,
        default=EXAMPLES,
        metavar="N",
        help=f"example trials per category and card (default {EXAMPLES})",
    )
    p.add_argument(
        "--tasks",
        type=Path,
        action="append",
        default=[],
        metavar="DIR",
        help="where to look for task folders, as in flotilla-analyze; may be repeated",
    )
    p.add_argument(
        "--manifest",
        type=Path,
        action="append",
        default=[],
        metavar="FILE",
        help="a build manifest for opaque task names, as in flotilla-analyze; may be repeated",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.examples < 0:
        print("flotilla-cards: --examples must be 0 or more", file=sys.stderr)
        return 2
    analyses = []
    for path in args.analysis:
        data = read_json(path)
        if not isinstance(data, dict):
            print(f"flotilla-cards: {path} is not a probe_analysis.py JSON object", file=sys.stderr)
            return 2
        analyses.append((str(path), data))
    args.out.mkdir(parents=True, exist_ok=True)
    pages = Pages(args.out, args.jobs, args.manifest, args.tasks)
    cards = build_cards(analyses, args.jobs, args.examples, pages)
    (args.out / "index.html").write_text(render_index(analyses, cards, args.examples), encoding="utf-8")
    linked = sum(1 for e in pages.done.values() if e.href)
    missing = len(pages.done) - linked
    print(f"{args.out / 'index.html'}: {len(cards)} cards, {linked} trial pages, {missing} without")
    return 0


if __name__ == "__main__":
    sys.exit(main())
