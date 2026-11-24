"""Per-trial timeline: one self-contained HTML page per trial, a swimlane per seat on one clock.

``flotilla-timeline TRIAL_DIR -o out.html`` renders one trial; ``flotilla-timeline --index JOBS_DIR GLOB
-o OUTDIR`` renders every trial of the job folders that match GLOB, plus ``index.html``.

World events carry ``t``, seconds since the world started; trajectory steps carry wall times. The page
puts both on the world clock: world zero is ``play.world_start_bound`` (the latest lower bound of the
world's start), so a world event is never shown later than it happened, and a step at wall time w is
shown at w − bound. Every text from a trial (messages, keystrokes, terminal output, model text) is
escaped, and long texts are cut with their full length stated.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from flotilla_analysis import __version__
from flotilla_analysis.load import (
    Evidence,
    Step,
    Trajectory,
    Trial,
    load,
    load_evidence,
    load_trajectories,
    parse_time,
    read_json,
)
from flotilla_analysis.play import world_start_bound

GAP_S = 120.0
OBSERVATION_CHARS = 1500
KEYSTROKE_CHARS = 600
MODEL_TEXT_CHARS = 1500
MESSAGE_CHARS = 4000
DATA_CHARS = 300
EXCERPT_CHARS = 160
DOCUMENT_CHARS = 20000

KEY_KINDS = frozenset(
    {
        "send",
        "deliver",
        "gated",
        "request",
        "pass",
        "claim",
        "harvest",
        "propose",
        "accept",
        "decline",
        "submit",
        "late",
        "early",
        "repeat",
        "denied",
        "probe",
        "invalid",
        "bad_token",
        "invalid_http",
        "freeze",
    }
)
POLL_KINDS = frozenset({"look", "wait", "state", "whoami", "health"})
FLAG_KINDS = ("gated", "denied", "probe", "late", "invalid", "bad_token")
PHASES = (
    ("environment setup", "environment_setup"),
    ("agent setup", "agent_setup"),
    ("agent execution", "agent_execution"),
    ("verifier", "verifier"),
)


@dataclass
class Row:
    at: float | None  # seconds on the world clock; None when the row has no time
    lane: str  # "world" or a seat
    kind: str
    group: str  # key, poll, phase, message or step: what the page's filters hide
    summary: str
    details: list[tuple[str, str]] = field(default_factory=list)
    gap: float | None = None  # seconds since the seat's previous step, when above the threshold
    rank: int = 0
    seq: int = 0

    def sort_key(self) -> tuple[Any, ...]:
        return (self.at is None, self.at or 0.0, self.rank, self.seq)


@dataclass
class Timeline:
    trial: Trial
    evidence: Evidence
    trajectories: dict[str, Trajectory]
    bound: datetime | None  # wall time of world zero; None when nothing bounds it
    zero: datetime | None  # wall time of the page's zero (the bound, else the first wall time known)
    lanes: list[str]
    rows: list[Row]
    gap_s: float
    stalls: dict[str, float]  # longest step gap above the threshold, per seat
    phases: dict[str, float | None]
    total_s: float | None
    kinds: Counter[str]
    oracle_logs: dict[str, str]

    @property
    def aligned(self) -> bool:
        return self.bound is not None

    @property
    def cost(self) -> tuple[float | None, list[str]]:
        """Summed seat cost, and the seats under test whose cost is missing."""
        known = [s.cost_usd for s in self.trial.seats if s.cost_usd is not None]
        missing = [s.seat for s in self.trial.seats if s.cost_usd is None and s.under_test]
        return (sum(known) if known else None), missing

    @property
    def flags(self) -> list[str]:
        found = [f"stall {seat} {gap:.0f}s" for seat, gap in sorted(self.stalls.items())]
        found += [f"error {e}" for e in self.trial.exceptions]
        if self.trial.unscored:
            found.append(f"unscored: {self.trial.unscored}")
        found += [f"{kind} {self.kinds[kind]}" for kind in FLAG_KINDS if self.kinds[kind]]
        if not self.aligned:
            found.append("clock not aligned")
        if self.evidence.problems:
            found.append(f"problems {len(self.evidence.problems)}")
        return found


def clip(text: str, limit: int) -> str:
    """The text, cut to ``limit`` characters with the cut stated."""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n… [cut: {len(text) - limit} more characters, {len(text)} in total]"


def one_line(text: str, limit: int = EXCERPT_CHARS) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _compact(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True, separators=(",", ":"))


def _data_text(data: dict[str, Any]) -> str:
    return one_line(" ".join(f"{k}={_compact(v)}" for k, v in sorted(data.items())), DATA_CHARS)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _span(block: Any) -> float | None:
    if not isinstance(block, dict):
        return None
    start, end = parse_time(block.get("started_at")), parse_time(block.get("finished_at"))
    return (end - start).total_seconds() if start and end else None


def _trajectory_file(trial: Trial, seat: str) -> Path | None:
    path = trial.directory / "seats" / seat / "agent" / "trajectory.json"
    if path.is_file():
        return path
    single = trial.directory / "agent" / "trajectory.json"
    return single if len(trial.seats) == 1 and single.is_file() else None


def _raw_steps(trial: Trial, seat: str) -> dict[int, dict[str, Any]]:
    path = _trajectory_file(trial, seat)
    data = read_json(path) if path else None
    steps = data.get("steps") if isinstance(data, dict) else None
    return {
        s["step_id"]: s
        for s in steps or []
        if isinstance(s, dict) and isinstance(s.get("step_id"), int) and s.get("source") == "agent"
    }


def _request_times(result: Any) -> dict[str, list[float]]:
    """Harbor's per-seat model request durations (seconds), one per agent episode."""
    found = {}
    for row in (result or {}).get("seat_results") or []:
        if not isinstance(row, dict):
            continue
        metadata = (
            ((row.get("agent_result") or {}).get("metadata") or {})
            if isinstance(row.get("agent_result"), dict)
            else {}
        )
        times = metadata.get("api_request_times_msec") if isinstance(metadata, dict) else None
        if isinstance(times, list) and all(_number(t) is not None for t in times):
            found[str(row.get("seat"))] = [float(t) / 1000 for t in times]
    return found


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_message_text(p.get("text") if isinstance(p, dict) else p) for p in content)
    return ""


def _recipients(message: dict[str, Any], seats: list[str]) -> str:
    to = str(message.get("to"))
    if to != "all":
        return to
    others = [s for s in seats if s != message.get("from")]
    return f"all ({', '.join(others)})" if others else "all"


def _message_row(message: dict[str, Any], at: float | None, seats: list[str], lane: str, seq: int) -> Row:
    text = str(message.get("text", ""))
    return Row(
        at=at,
        lane=lane,
        kind="message",
        group="message",
        summary=f"{message.get('from')} → {_recipients(message, seats)}: {one_line(text)}",
        details=[(f"message {message.get('id')} ({len(text)} characters)", clip(text, MESSAGE_CHARS))],
        seq=seq,
    )


def _event_row(
    event: dict[str, Any], lanes: list[str], messages: dict[str, dict[str, Any]], seats: list[str]
) -> Row:
    kind = str(event.get("kind"))
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    seat = event.get("seat")
    lane = seat if seat in lanes else "world"
    at = _number(event.get("t"))
    seq = event.get("seq") if isinstance(event.get("seq"), int) else 0
    if kind == "send" and data.get("id") in messages:
        row = _message_row(messages[data["id"]], at, seats, lane, seq)
        row.kind = "send"
        return row
    group = "key" if kind in KEY_KINDS else "poll" if kind in POLL_KINDS else "phase"
    summary = _data_text(data)
    if kind == "deliver":
        ids = [str(i) for i in data.get("ids") or []]
        if not ids:
            group, summary = "poll", "nothing new"
        else:
            names = ", ".join(f"{i} from {messages.get(i, {}).get('from', '?')}" for i in ids)
            via = f" via {data['via']}" if data.get("via") else ""
            summary = f"{len(ids)}{via}: {names}"
    return Row(at=at, lane=lane, kind=kind, group=group, summary=one_line(summary, DATA_CHARS), seq=seq)


def _step_row(step: Step, raw: dict[str, Any], at: float | None, model_s: float | None, lane: str) -> Row:
    metrics = raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {}
    parts = [f"#{step.step_id}", f"{len(step.keystrokes)} keystroke batch(es)"]
    if model_s is not None:
        parts.append(f"model {model_s:.1f}s")
    prompt, completion = _number(metrics.get("prompt_tokens")), _number(metrics.get("completion_tokens"))
    if prompt is not None or completion is not None:
        cached = _number(metrics.get("cached_tokens"))
        tokens = f"{prompt or 0:.0f} in / {completion or 0:.0f} out"
        parts.append(tokens + (f" ({cached:.0f} cached)" if cached is not None else ""))
    cost = _number(metrics.get("cost_usd"))
    if cost is not None:
        parts.append(f"${cost:.5f}")
    summary = " · ".join(parts)
    if step.keystrokes:
        summary += " · $ " + one_line(step.keystrokes[0], 120)
    details = []
    if step.keystrokes:
        details.append(("keystrokes", "\n".join(clip(k, KEYSTROKE_CHARS) for k in step.keystrokes)))
    if step.observation:
        details.append(
            (f"observation ({len(step.observation)} characters)", clip(step.observation, OBSERVATION_CHARS))
        )
    text = _message_text(raw.get("message"))
    if text:
        details.append((f"model text ({len(text)} characters)", clip(text, MODEL_TEXT_CHARS)))
    return Row(at=at, lane=lane, kind="step", group="step", summary=summary, details=details, rank=1)


def _seat_rows(
    timeline_zero: datetime | None,
    trial: Trial,
    trajectories: dict[str, Trajectory],
    request_times: dict[str, list[float]],
    gap_s: float,
) -> tuple[list[Row], dict[str, float]]:
    rows: list[Row] = []
    stalls: dict[str, float] = {}

    def at(moment: datetime | None) -> float | None:
        if moment is None or timeline_zero is None:
            return None
        return (moment - timeline_zero).total_seconds()

    for seat in trial.seats:
        trajectory = trajectories.get(seat.seat)
        steps = trajectory.steps if trajectory else []
        raw = _raw_steps(trial, seat.seat) if trajectory else {}
        times = request_times.get(seat.seat, [])
        model_times = times if len(times) == len(steps) else []
        previous = seat.started
        rows.append(
            Row(
                at=at(seat.started),
                lane=seat.seat,
                kind="agent-start",
                group="phase",
                summary=seat.label,
                rank=-1,
            )
        )
        for i, step in enumerate(steps):
            row = _step_row(
                step,
                raw.get(step.step_id, {}),
                at(step.timestamp),
                model_times[i] if model_times else None,
                seat.seat,
            )
            row.seq = i
            if previous is not None and step.timestamp is not None:
                gap = (step.timestamp - previous).total_seconds()
                if gap > gap_s:
                    row.gap = gap
                    stalls[seat.seat] = max(stalls.get(seat.seat, 0.0), gap)
            previous = step.timestamp or previous
            rows.append(row)
        end = Row(
            at=at(seat.finished),
            lane=seat.seat,
            kind="agent-end",
            group="phase",
            summary=seat.exception or "",
            rank=2,
        )
        if previous is not None and seat.finished is not None and steps:
            gap = (seat.finished - previous).total_seconds()
            if gap > gap_s:
                end.gap = gap
                stalls[seat.seat] = max(stalls.get(seat.seat, 0.0), gap)
        rows.append(end)
    return rows, stalls


def build_timeline(trial: Trial, gap_s: float = GAP_S) -> Timeline:
    """Every world event, message and agent step of the trial, ordered on the world clock."""
    evidence = load_evidence(trial)
    trajectories = load_trajectories(trial)
    bound = world_start_bound(trial, evidence, trajectories)
    walls = [s.started for s in trial.seats if s.started] + [
        step.timestamp for t in trajectories.values() for step in t.steps if step.timestamp
    ]
    zero = bound or (min(walls) if walls else None)
    seats = sorted({s.seat for s in trial.seats} | set(evidence.seats or []))
    lanes = ["world", *seats]
    messages = {str(m.get("id")): m for m in evidence.messages or []}
    events = [e for e in evidence.events or [] if isinstance(e, dict)]
    rows = [_event_row(e, lanes, messages, seats) for e in events]
    sent = {
        e.get("data", {}).get("id")
        for e in events
        if e.get("kind") == "send" and isinstance(e.get("data"), dict)
    }
    for i, message in enumerate(evidence.messages or []):
        if message.get("id") not in sent:
            sender = message.get("from")
            row = _message_row(
                message, _number(message.get("t")), seats, sender if sender in seats else "world", i
            )
            row.summary += " [no send event: sent after the event log was collected]"
            rows.append(row)
    result = read_json(trial.directory / "result.json")
    result = result if isinstance(result, dict) else {}
    seat_rows, stalls = _seat_rows(zero, trial, trajectories, _request_times(result), gap_s)
    rows += seat_rows
    verifier = (
        parse_time((result.get("verifier") or {}).get("started_at"))
        if isinstance(result.get("verifier"), dict)
        else None
    )
    if verifier is not None and zero is not None:
        rows.append(
            Row(
                at=(verifier - zero).total_seconds(),
                lane="world",
                kind="verifier",
                group="phase",
                summary="verifier starts (it freezes the world and scores)",
                rank=3,
            )
        )
    rows.sort(key=Row.sort_key)
    oracle_logs = {}
    for seat in seats:
        path = trial.directory / "seats" / seat / "agent" / "oracle.txt"
        if path.is_file() and path.stat().st_size:
            oracle_logs[seat] = path.read_text(encoding="utf-8", errors="replace")
    total = None
    start, end = parse_time(result.get("started_at")), parse_time(result.get("finished_at"))
    if start and end:
        total = (end - start).total_seconds()
    return Timeline(
        trial=trial,
        evidence=evidence,
        trajectories=trajectories,
        bound=bound,
        zero=zero,
        lanes=lanes,
        rows=rows,
        gap_s=gap_s,
        stalls=stalls,
        phases={name: _span(result.get(key)) for name, key in PHASES},
        total_s=total,
        kinds=Counter(str(e.get("kind")) for e in events),
        oracle_logs=oracle_logs,
    )


STYLE = """
body { font: 13px/1.4 -apple-system, "Segoe UI", sans-serif; margin: 16px; color: #1f2328; }
h1 { font-size: 18px; margin: 0 0 8px; }
table { border-collapse: collapse; }
th, td { border: 1px solid #d0d7de; padding: 3px 6px; text-align: left; vertical-align: top; }
th { background: #f6f8fa; }
.facts th { width: 11em; }
#timeline { width: 100%; table-layout: fixed; margin-top: 8px; }
#timeline thead th { position: sticky; top: 0; z-index: 1; }
#timeline .t { width: 6.5em; font-family: ui-monospace, monospace; white-space: nowrap; color: #57606a; }
#timeline td { overflow-wrap: anywhere; }
.kind { display: inline-block; font-family: ui-monospace, monospace; font-size: 11px; padding: 0 4px;
  border-radius: 3px; background: #eaeef2; margin-right: 4px; }
tr.g-message td.cell { background: #ddf4ff; }
tr.g-key td.cell { background: #fff8c5; }
tr.g-step td.cell { background: #f6f8fa; }
tr.g-phase td.cell { color: #57606a; }
tr.g-poll td.cell { color: #8c959f; }
td.k-gated, td.k-denied, td.k-probe, td.k-late, td.k-invalid, td.k-bad_token {
  background: #ffebe9 !important; }
tr.gap td.cell { outline: 2px solid #cf222e; }
.gapnote { color: #cf222e; font-weight: 600; }
details { margin-top: 2px; }
summary { cursor: pointer; color: #0969da; }
pre { white-space: pre-wrap; margin: 2px 0; max-height: 24em; overflow: auto; background: #fff;
  border: 1px solid #d0d7de; padding: 4px; font-size: 12px; }
.controls { margin: 10px 0; }
.controls label { margin-right: 12px; }
body.hide-poll tr.g-poll, body.hide-step tr.g-step, body.hide-phase tr.g-phase { display: none; }
.flag { color: #cf222e; }
"""

TIMELINE_SCRIPT = """
for (const box of document.querySelectorAll("input[data-group]")) {
  box.addEventListener("change", () => {
    document.body.classList.toggle("hide-" + box.dataset.group, !box.checked);
  });
}
document.getElementById("expand").addEventListener("click", () => {
  const all = document.querySelectorAll("#timeline details");
  const open = Array.from(all).some((d) => !d.open);
  for (const d of all) d.open = open;
});
"""

INDEX_SCRIPT = """
for (const th of document.querySelectorAll("#trials th")) {
  th.addEventListener("click", () => {
    const body = document.querySelector("#trials tbody");
    const column = th.cellIndex;
    const down = th.dataset.down !== "1";
    th.dataset.down = down ? "1" : "0";
    const key = (tr) => tr.cells[column].dataset.sort ?? tr.cells[column].textContent;
    const rows = Array.from(body.rows);
    rows.sort((a, b) => {
      const x = key(a), y = key(b), nx = parseFloat(x), ny = parseFloat(y);
      const order = !isNaN(nx) && !isNaN(ny) ? nx - ny : x.localeCompare(y);
      return down ? order : -order;
    });
    for (const tr of rows) body.appendChild(tr);
  });
}
"""


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _css(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "_", name.lower())


def _seconds(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f} s"


def _iso(moment: datetime | None) -> str:
    return "n/a" if moment is None else moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.4f}"


def _reward(trial: Trial) -> str:
    if trial.reward is None:
        return f"unscored: {trial.unscored}"
    return f"{trial.reward:.3f}"


def _details(title: str, text: str) -> str:
    return f"<details><summary>{esc(title)}</summary><pre>{esc(text)}</pre></details>"


def _page(title: str, body: str, script: str) -> str:
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{esc(title)}</title>\n<style>{STYLE}</style>\n</head>\n<body>\n{body}\n"
        f"<script>{script}</script>\n</body>\n</html>\n"
    )


def _clock_text(timeline: Timeline) -> str:
    trial = timeline.trial
    step_note = (
        " An agent step is placed at its ATIF timestamp, which terminus-2 records after the step's commands "
        "ran and their output was captured: the step's world requests come before its row."
    )
    if timeline.bound is None:
        return (
            "Not aligned: nothing bounds the world's start (no environment setup start, and no seat with "
            "both an agent start and a world event). World events are at their own t; agent rows are at "
            f"their wall time minus the first wall time recorded ({_iso(timeline.zero)})." + step_note
        )
    return (
        f"World zero is placed at {_iso(timeline.bound)}: the latest lower bound of the world's start "
        f"(environment setup start {_iso(trial.environment_started)}; for each seat, its agent start and "
        "its first trajectory timestamp, minus the world time of its first request). The world started at "
        "or after this bound, so a world event (bound + t) is shown at or before the moment it happened; "
        "agent rows are at their recorded wall time minus the bound. Times are seconds on this clock; "
        "hover a time for its UTC wall time." + step_note
    )


def _score_text(score: dict[str, Any] | None) -> str:
    if score is None:
        return "no score.json"
    return "; ".join(f"{k}={one_line(_compact(v), 120)}" for k, v in sorted(score.items()))


def _header(timeline: Timeline) -> str:
    trial, evidence = timeline.trial, timeline.evidence
    cost, missing = timeline.cost
    phases = ", ".join(f"{name} {_seconds(v)}" for name, v in timeline.phases.items())
    facts = [
        ("task", f"{trial.label} (family {trial.family}, variant {trial.variant or '-'}); {trial.task_name}"),
        ("condition", trial.condition),
        ("instance", str(trial.instance)),
        ("trial", f"{trial.job}/{trial.trial_name}"),
        ("reward", _reward(trial)),
        ("score.json", _score_text(evidence.score)),
        ("durations", f"{phases}; trial {_seconds(timeline.total_s)}"),
        ("total cost", _money(cost) + (f" (missing for {', '.join(missing)})" if missing else "")),
        ("clock", _clock_text(timeline)),
        (
            "gaps",
            f"a seat's step that comes more than {timeline.gap_s:g} s after its previous step (or "
            "agent start), and an agent end that long after its last step, is outlined in red",
        ),
        ("flags", "; ".join(timeline.flags) or "none"),
        ("evidence", ", ".join(evidence.sources) or "none"),
    ]
    rows = "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in facts)
    for problem in evidence.problems:
        rows += f'<tr><th>problem</th><td class="flag">{esc(problem)}</td></tr>'
    out = [f"<h1>{esc(trial.label)} · {esc(trial.condition)} · instance {trial.instance}</h1>"]
    out.append(f'<table class="facts">{rows}</table>')
    if evidence.score is not None:
        out.append(
            _details("score.json", clip(json.dumps(evidence.score, indent=2, sort_keys=True), DOCUMENT_CHARS))
        )
    out.append("<h2>Seats</h2>")
    out.append(_seats_table(timeline))
    return "\n".join(out)


def _seats_table(timeline: Timeline) -> str:
    head = (
        "<tr><th>seat</th><th>role</th><th>agent:model</th><th>steps</th><th>tokens in / cached / out</th>"
        "<th>cost</th><th>agent time</th><th>exception</th><th>logs</th></tr>"
    )
    rows = []
    for seat in timeline.trial.seats:
        trajectory = timeline.trajectories.get(seat.seat)
        tokens = " / ".join(
            "n/a" if n is None else str(n) for n in (seat.n_input, seat.n_cache, seat.n_output)
        )
        agent_s = (seat.finished - seat.started).total_seconds() if seat.started and seat.finished else None
        logs = []
        if trajectory and trajectory.preamble:
            logs.append(
                _details(
                    f"instruction ({len(trajectory.preamble)} characters)",
                    clip(trajectory.preamble, MESSAGE_CHARS),
                )
            )
        if seat.seat in timeline.oracle_logs:
            text = timeline.oracle_logs[seat.seat]
            logs.append(_details(f"oracle.txt ({len(text)} characters)", clip(text, MESSAGE_CHARS)))
        cells = [
            seat.seat,
            "under test" if seat.under_test else "scripted",
            seat.label,
            str(len(trajectory.steps)) if trajectory else "no trajectory",
            tokens,
            _money(seat.cost_usd),
            _seconds(agent_s),
            seat.exception or "",
        ]
        rows.append("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in cells) + f"<td>{''.join(logs)}</td></tr>")
    return f"<table>{head}{''.join(rows)}</table>"


def _row_html(row: Row, timeline: Timeline) -> str:
    classes = f"g-{row.group}" + (" gap" if row.gap is not None else "")
    when = "n/a" if row.at is None else f"{row.at:+.1f}"
    wall = _iso(timeline.zero + timedelta(seconds=row.at)) if row.at is not None and timeline.zero else "n/a"
    card = f'<span class="kind">{esc(row.kind)}</span>{esc(row.summary)}'
    if row.gap is not None:
        card += f'<div class="gapnote">gap {row.gap:.0f} s since this seat\'s previous step</div>'
    card += "".join(_details(title, text) for title, text in row.details)
    cells = "".join(
        f'<td class="cell k-{_css(row.kind)}">{card}</td>' if lane == row.lane else "<td></td>"
        for lane in timeline.lanes
    )
    return f'<tr class="{classes}"><td class="t" title="{esc(wall)}">{esc(when)}</td>{cells}</tr>'


def render_trial(timeline: Timeline) -> str:
    """The trial's self-contained HTML page."""
    models = {s.seat: s.label for s in timeline.trial.seats}
    head = '<th class="t">t (s)</th>' + "".join(
        f"<th>{esc(lane)}" + (f"<br><small>{esc(models[lane])}</small>" if lane in models else "") + "</th>"
        for lane in timeline.lanes
    )
    controls = (
        '<div class="controls">'
        '<label><input type="checkbox" data-group="poll" checked> polls (look, wait, state, whoami, '
        "empty deliver)</label>"
        '<label><input type="checkbox" data-group="step" checked> agent steps</label>'
        '<label><input type="checkbox" data-group="phase" checked> phases</label>'
        '<button id="expand" type="button">expand / collapse all</button></div>'
    )
    rows = "\n".join(_row_html(row, timeline) for row in timeline.rows)
    body = (
        f"{_header(timeline)}\n<h2>Timeline ({len(timeline.rows)} rows)</h2>\n{controls}\n"
        f'<table id="timeline"><thead><tr>{head}</tr></thead>\n<tbody>\n{rows}\n</tbody></table>'
    )
    return _page(
        f"{timeline.trial.label} {timeline.trial.condition} {timeline.trial.trial_name}",
        body,
        TIMELINE_SCRIPT,
    )


def index_row(timeline: Timeline, href: str) -> dict[str, Any]:
    """One trial's line in ``index.html``."""
    trial = timeline.trial
    cost, missing = timeline.cost
    return {
        "trial": f"{trial.job}/{trial.trial_name}",
        "href": href,
        "label": trial.label,
        "condition": trial.condition,
        "instance": trial.instance,
        "models": ", ".join(f"{s.seat}={s.label}" for s in trial.seats),
        "reward": trial.reward,
        "duration_s": timeline.phases.get("agent execution"),
        "cost_usd": None if missing else cost,
        "messages": len(timeline.evidence.messages or []),
        "steps": sum(len(t.steps) for t in timeline.trajectories.values()),
        "flags": timeline.flags,
    }


def render_index(rows: list[dict[str, Any]], problems: list[str], title: str) -> str:
    columns = (
        "trial",
        "label",
        "condition",
        "instance",
        "models",
        "reward",
        "agent time",
        "cost",
        "messages",
        "steps",
        "flags",
    )
    head = "".join(f"<th>{esc(c)}</th>" for c in columns)
    body_rows = []
    for row in rows:
        reward = "unscored" if row["reward"] is None else f"{row['reward']:.3f}"
        cells = [
            (f'<a href="{esc(row["href"])}">{esc(row["trial"])}</a>', row["trial"]),
            (esc(row["label"]), row["label"]),
            (esc(row["condition"]), row["condition"]),
            (esc(row["instance"]), row["instance"]),
            (esc(row["models"]), row["models"]),
            (esc(reward), "" if row["reward"] is None else row["reward"]),
            (esc(_seconds(row["duration_s"])), "" if row["duration_s"] is None else row["duration_s"]),
            (esc(_money(row["cost_usd"])), "" if row["cost_usd"] is None else row["cost_usd"]),
            (esc(row["messages"]), row["messages"]),
            (esc(row["steps"]), row["steps"]),
            (f'<span class="flag">{esc("; ".join(row["flags"]))}</span>', "; ".join(row["flags"])),
        ]
        body_rows.append(
            "<tr>" + "".join(f'<td data-sort="{esc(sort)}">{cell}</td>' for cell, sort in cells) + "</tr>"
        )
    listed = "".join(f"<li>{esc(p)}</li>" for p in problems)
    body = (
        f"<h1>{esc(title)}</h1>\n<p>{len(rows)} trials. Click a column head to sort.</p>\n"
        f'<table id="trials"><thead><tr>{head}</tr></thead>\n<tbody>\n'
        + "\n".join(body_rows)
        + "\n</tbody></table>\n"
        + (f"<h2>Problems</h2><ul>{listed}</ul>" if problems else "")
    )
    return _page(title, body, INDEX_SCRIPT)


def page_name(trial: Trial) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", f"{trial.job}__{trial.trial_name}") + ".html"


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="flotilla-timeline",
        description=(
            "Render one Flotilla trial as a self-contained HTML timeline: a lane per seat on one clock with "
            "world events, messages and agent steps. With --index, PATH is a jobs folder and GLOB selects "
            "job folders in it; every trial gets a page and OUTDIR gets an index.html."
        ),
    )
    p.add_argument("path", metavar="PATH", type=Path, help="a trial folder (or a job folder with one trial)")
    p.add_argument("pattern", nargs="?", metavar="GLOB", help="with --index: job folder names (default *)")
    p.add_argument("--index", action="store_true", help="render every trial of PATH/GLOB plus index.html")
    p.add_argument("-o", "--output", type=Path, required=True, help="the HTML file, or with --index a folder")
    p.add_argument(
        "--gap",
        type=float,
        default=GAP_S,
        help=f"outline steps after a gap longer than this (s, default {GAP_S:g})",
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


def _problems(problems: list[Any]) -> list[str]:
    return [f"{p.path}: {p.reason}" for p in problems]


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.gap <= 0:
        print("flotilla-timeline: --gap must be positive", file=sys.stderr)
        return 2
    if args.index:
        return _main_index(args)
    if args.pattern is not None:
        print("flotilla-timeline: GLOB needs --index", file=sys.stderr)
        return 2
    trials, _, problems = load([args.path], args.manifest, task_roots=args.tasks)
    for problem in _problems(problems):
        print(f"flotilla-timeline: {problem}", file=sys.stderr)
    if len(trials) != 1:
        print(
            f"flotilla-timeline: {args.path} holds {len(trials)} Flotilla trials; give one trial folder "
            "or use --index",
            file=sys.stderr,
        )
        return 1
    timeline = build_timeline(trials[0], args.gap)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_trial(timeline), encoding="utf-8")
    print(f"{args.output}: {len(timeline.rows)} rows, flags: {'; '.join(timeline.flags) or 'none'}")
    return 0


def _main_index(args: argparse.Namespace) -> int:
    pattern = args.pattern or "*"
    jobs = sorted(p for p in args.path.glob(pattern) if p.is_dir())
    if not jobs:
        print(f"flotilla-timeline: no job folder matches {args.path}/{pattern}", file=sys.stderr)
        return 1
    trials, _, problems = load(jobs, args.manifest, task_roots=args.tasks)
    trials.sort(key=lambda t: (t.job, t.trial_name, t.path))
    args.output.mkdir(parents=True, exist_ok=True)
    rows, used = [], set()
    for trial in trials:
        name = page_name(trial)
        while name in used:
            name = name[: -len(".html")] + "_.html"
        used.add(name)
        timeline = build_timeline(trial, args.gap)
        (args.output / name).write_text(render_trial(timeline), encoding="utf-8")
        rows.append(index_row(timeline, quote(name)))
    listed = _problems(problems)
    title = f"Flotilla trials: {args.path.name}/{pattern}"
    (args.output / "index.html").write_text(render_index(rows, listed, title), encoding="utf-8")
    print(f"{args.output / 'index.html'}: {len(rows)} trials, {len(listed)} problems")
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
