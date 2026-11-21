"""Compose three RolePack stages from one real conversation between two seats."""

import argparse
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..handoff.external_audit import _report
from ..rolepack import protocol
from ..rolepack.__main__ import _emit, _load
from ..turn_fork.packet import _read_bytes

_RESULTS = Path(__file__).resolve().parents[1] / "results"
_FILES = {
    "source": "calendar-question-sources.json",
    "questions": "calendar-question-ask-live.json",
    "answers": "calendar-question-answer-live.json",
    "decisions": "calendar-question-final-live.json",
}
type _Doc = dict[str, Any]


def _sources(a: argparse.Namespace) -> _Doc:
    from . import source

    if (a.sources is None) != (a.sha256 is None):
        raise ValueError("a custom source needs both --sources and --sha256")
    path = a.sources or _RESULTS / _FILES["source"]
    digest = a.sha256 or (_RESULTS / "calendar-question-sources.sha256").read_text(encoding="ascii").strip()
    raw = _read_bytes(path, 1024 * 1024)
    if sha256(raw).hexdigest() != digest:
        raise ValueError("the calendar source digest differs")
    return source.validate(protocol.loads(raw))


def _stage(doc: _Doc | None) -> None:
    if doc is None:
        raise ValueError("no eligible first reply; do not start the next stage")
    _emit(doc)


def _native(root: Path, src: _Doc, question: _Doc, answer: _Doc | None, decision: _Doc | None) -> _Doc:
    from ..rolepack import calendar_source, evidence
    from . import source, stages

    if src != source.build(root, _load(None, None)):
        raise ValueError("a retained role differs from its original native observation")
    if decision is None:
        if answer is None:
            evidence.verify(stages.question(src), question)
        else:
            parent = stages.answer(src, question)
            if parent is None:
                raise ValueError("no questions for this answer report")
            evidence.verify(parent, answer)

    # Grade the actually chosen slot, not the peer's answer or the frozen role rubric.
    actions: list[tuple[str, int, bool]] = []
    if decision is not None:
        if answer is None:
            raise ValueError("cannot grade final decisions without their parent replies")
        pack = stages.final(src, question, answer)
        if pack is None:
            raise ValueError("final decisions have no eligible registered parents")
        for row in evidence.verify(pack, decision)["rows"]:
            if row["state"] == "complete":
                record_id, arm = row["record_id"].rsplit("--", 1)
                if arm not in ("visible", "withheld") or row["arm"] != "original":
                    raise ValueError("invalid final condition")
                actions.append((record_id, row["outcome"]["values"][0], row["outcome"]["correct"][0]))
    result = calendar_source._worker(root, [(r, slot) for r, slot, _ in actions])
    if result["scores"] != [correct for _, _, correct in actions]:
        raise ValueError("a final decision disagrees with the original calendar scorer")
    return {"schema":"seatlab.calendar-question-stock/v1", "measurement":"original_calendar_scorer_not_a_new_native_reward", "original_sources":len(src["records"]), "registered_worlds":len({r["world"] for r in src["records"]}), "actions_checked":len(actions), "optimal_actions":sum(result["scores"]), "final_report_sha256":protocol.digest(decision) if decision is not None else None}


def _run(a: argparse.Namespace) -> None:
    from . import stages

    if a.mode == "build":
        from . import source

        _emit(source.build(a.calendar_checkout, _load(None, None)))
        return
    src = _sources(a)
    if a.mode == "ask":
        _stage(stages.question(src))
        return
    question = _report(a.questions)
    if a.mode == "answer":
        _stage(stages.answer(src, question))
        return
    answer = _report(a.answers) if a.answers else None
    if a.mode == "decide":
        if answer is None:
            raise ValueError("decide needs the teammate answer report")
        _stage(stages.final(src, question, answer))
        return
    decision = _report(a.decisions) if a.decisions else None
    if a.mode == "stock":
        _emit(_native(a.calendar_checkout, src, question, answer, decision))
    else:
        from . import analysis

        _emit(analysis.summarize(src, question, answer, decision))


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = cli.add_subparsers(dest="mode", required=True)
    for name in ("build", "ask", "answer", "decide", "summary", "stock"):
        p = modes.add_parser(name, allow_abbrev=False)
        if name in ("build", "stock"):
            p.add_argument("--calendar-checkout", type=Path, required=True)
        if name == "build":
            continue
        p.add_argument("--sources", type=Path)
        p.add_argument("--sha256", help="Exact source bytes for --sources.")
        if name in ("answer", "decide", "summary", "stock"):
            p.add_argument("--questions", type=Path, required=name in ("answer", "decide"), default=_RESULTS / _FILES["questions"])
        if name in ("decide", "summary", "stock"):
            g = p.add_mutually_exclusive_group(required=name == "decide")
            g.add_argument("--answers", type=Path, default=_RESULTS / _FILES["answers"])
            if name != "decide":
                g.add_argument("--no-answers", action="store_const", dest="answers", const=None)
        if name in ("summary", "stock"):
            g = p.add_mutually_exclusive_group()
            g.add_argument("--decisions", type=Path, default=_RESULTS / _FILES["decisions"])
            g.add_argument("--no-decisions", action="store_const", dest="decisions", const=None)
    try:
        _run(cli.parse_args(argv))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, RecursionError):
        print("Invalid source, prior report, or inaccessible original calendar. See --help.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
