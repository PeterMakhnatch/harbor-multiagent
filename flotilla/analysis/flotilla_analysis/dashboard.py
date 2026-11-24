"""HTML dashboard of ``flotilla-watch TARGET --html DIR``.

Every pass replaces ``DIR/index.html`` atomically with the pass's status: wave progress, spend against the
cap, ETA, running trials with each seat's last activity and stalls, alerts with the time each was first
seen, errors of finished trials, the reward summary, and every finished trial with its reward, duration,
cost, flags and a link to its timeline page. The timeline page (``timeline.py``) of a finished trial is
written once, as ``DIR/trials/<job>__<trial>.html``, and again only when its ``result.json`` changes. A
trial whose page cannot be rendered is flagged and the watch goes on. Every text is HTML-escaped and the
page needs no other file; while the watch runs, it reloads itself at the pass interval. In blind mode
(``status["blind"]``, ``flotilla-watch --blind``) the page has no rewards, no unscored reasons and no
timeline pages: a finished trial shows whether it is valid, its duration, cost and error flags.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from flotilla_analysis.load import parse_time, parse_trial, read_json
from flotilla_analysis.names import Names
from flotilla_analysis.timeline import (
    GAP_S,
    INDEX_SCRIPT,
    STYLE,
    build_timeline,
    esc,
    page_name,
    render_trial,
)
from flotilla_analysis.watch import _stamp, _usd, duration, replace_text

TRIALS = "trials"
DASHBOARD_STYLE = """
h2 { font-size: 15px; margin: 18px 0 6px; }
.facts td, .facts th { white-space: nowrap; }
.muted { color: #57606a; }
.stall { color: #cf222e; font-weight: 600; }
.bar { display: inline-block; width: 18em; height: 10px; background: #eaeef2; border-radius: 3px;
  vertical-align: middle; margin-left: 8px; overflow: hidden; }
.bar span { display: block; height: 100%; background: #2da44e; }
.over span { background: #cf222e; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
#trials th { cursor: pointer; }
"""


@dataclass
class Page:
    """A finished trial's timeline page: its link and the timeline's flags, or why there is none."""

    href: str | None
    flags: list[str]
    problem: str | None


BLIND_PAGE = Page(None, [], None)


class Dashboard:
    """Writes ``DIR/index.html`` per pass and each finished trial's timeline page once."""

    def __init__(self, out: Path, *, names: Names, refresh_s: float | None, gap_s: float = GAP_S) -> None:
        if refresh_s is not None and refresh_s <= 0:
            raise ValueError("refresh_s must be positive")
        self.out = out
        self.names = names
        self.refresh_s = refresh_s
        self.gap_s = gap_s
        self.renders = 0
        self._pages: dict[str, tuple[tuple[int, int] | None, Page]] = {}
        self._files: dict[str, str] = {}

    def update(self, status: dict[str, Any]) -> Path:
        blind = bool(status.get("blind"))
        rows = [(trial, BLIND_PAGE if blind else self.page(trial)) for trial in status["finished"]]
        path = self.out / "index.html"
        replace_text(path, render_dashboard(status, rows, self.refresh_s))
        return path

    def page(self, trial: dict[str, Any]) -> Page:
        """The trial's page, rendered on first sight and again only when its ``result.json`` changed."""
        key = trial["directory"]
        stamp = _stamp(Path(key) / "result.json")
        hit = self._pages.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]
        page = self._render(Path(key), trial["path"])
        self._pages[key] = (stamp, page)
        return page

    def _render(self, directory: Path, relative: str) -> Page:
        data = read_json(directory / "result.json")
        if not isinstance(data, dict):
            return Page(None, [], "no timeline: result.json unreadable")
        try:
            trial = parse_trial(data, directory=directory, relative=relative, names=self.names)
        except (KeyError, TypeError, ValueError) as exc:
            return Page(None, [], f"no timeline: result.json unusable ({exc})")
        if trial is None:
            return Page(None, [], "no timeline: not a Flotilla trial or its task is unresolved")
        name = self._file(str(directory), page_name(trial))
        self.renders += 1
        try:
            timeline = build_timeline(trial, self.gap_s)
            replace_text(self.out / TRIALS / name, render_trial(timeline))
            flags = timeline.flags
        except Exception as exc:
            return Page(None, [], f"timeline failed: {type(exc).__name__}: {exc}")
        return Page(f"{TRIALS}/{quote(name)}", flags, None)

    def _file(self, key: str, name: str) -> str:
        if key not in self._files:
            taken = set(self._files.values())
            while name in taken:
                name = name[: -len(".html")] + "_.html"
            self._files[key] = name
        return self._files[key]


def _clock(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _when(value: Any) -> str:
    moment = parse_time(value)
    return "-" if moment is None else moment.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _cell(text: Any, sort: Any = None, css: str = "") -> str:
    attributes = f' class="{css}"' if css else ""
    if sort is not None:
        attributes += f' data-sort="{esc(sort)}"'
    return f"<td{attributes}>{esc(text)}</td>"


def _table(head: list[str], rows: list[str], table_id: str = "") -> str:
    ident = f' id="{table_id}"' if table_id else ""
    columns = "".join(f"<th>{esc(c)}</th>" for c in head)
    return (
        f"<table{ident}><thead><tr>{columns}</tr></thead>\n<tbody>\n" + "\n".join(rows) + "\n</tbody></table>"
    )


def _bar(part: float, whole: float | None) -> str:
    if not whole or whole <= 0:
        return ""
    share = part / whole
    over = " over" if share > 1 else ""
    return f'<span class="bar{over}"><span style="width: {min(100.0, 100 * share):.1f}%"></span></span>'


def _wave(status: dict[str, Any]) -> str:
    wave = status["wave"]
    progress = (
        f"{wave['units']} {wave['total']}: done {wave['done']} (valid {wave['valid']}, invalid "
        f"{wave['invalid']}), running {wave['running']}, pending {wave['pending']}"
        + (f", abandoned {wave['abandoned']}" if wave["abandoned"] else "")
    )
    cap, spent = wave["cap_usd"], wave["spent_usd"]
    share = f" ({100 * spent / cap:.0f}%)" if cap and spent is not None else ""
    spend = f"{_usd(spent)} of cap {_usd(cap) if cap is not None else 'n/a'}{share}"
    facts = [
        ("state", esc(wave["state"])),
        ("target", esc(f"{status['mode']} {status['target']}")),
        ("progress", esc(progress) + _bar(wave["done"], wave["total"])),
        ("spend", esc(spend) + _bar(spent or 0.0, cap)),
        ("in flight", esc(f"~{_usd(wave['in_flight_usd'])}")),
        ("mean trial", esc(duration(wave["mean_trial_s"]))),
        ("ETA", esc(duration(wave["eta_s"]))),
        ("updated", esc(status["generated_at"])),
    ]
    rows = "".join(f"<tr><th>{esc(k)}</th><td>{v}</td></tr>" for k, v in facts)
    return f'<table class="facts">{rows}</table>'


def _running(status: dict[str, Any]) -> str:
    running = status["running"]
    live = [r for r in running if r["state"] != "abandoned"]
    abandoned = len(running) - len(live)
    note = f" (+{abandoned} abandoned: no write for 3 h or job ended)" if abandoned else ""
    out = [f"<h2>Running trials: {len(live)}{esc(note)}</h2>"]
    if not live:
        return out[0] + '<p class="muted">none</p>'
    rows = []
    for trial in live:
        what = f"{trial['label']}/{trial['condition']}" if trial["label"] else "?"
        errors = "; ".join(
            f"{k} x{v['count']}: {v['line'][:140]}" for k, v in sorted(trial["errors"].items())
        )
        seats = trial["seats"] or [None]
        for index, seat in enumerate(seats):
            cells = ""
            if index == 0:
                span = f' rowspan="{len(seats)}"'
                values = [
                    trial["job"],
                    trial["trial"] or "(no trial folder yet)",
                    what,
                    trial["state"],
                    duration(trial["elapsed_s"]),
                    _usd(trial["cost_usd"]),
                ]
                cells = "".join(f"<td{span}>{esc(v)}</td>" for v in values)
                cells += f'<td{span} class="flag">{esc(errors)}</td>'
            rows.append(f"<tr>{cells}{_seat_cells(seat)}</tr>")
    head = ["job", "trial", "task", "state", "elapsed", "spend", "errors", "seat", "model", "steps"]
    head += ["seat state", "last activity", "stall"]
    return out[0] + _table(head, rows)


def _seat_cells(seat: dict[str, Any] | None) -> str:
    if seat is None:
        return '<td colspan="6" class="muted">no seats yet</td>'
    if seat["scripted"]:
        return _cell(seat["seat"]) + _cell(f"scripted ({seat['agent']})") + '<td colspan="4"></td>'
    state = "ended" if seat["ended"] else ("last step" if seat["first_step"] else "no step yet, started")
    stall = "STALL" if seat["stalled"] else ""
    return (
        _cell(seat["seat"])
        + _cell(seat["model"])
        + _cell(seat["steps"], css="num")
        + _cell(state)
        + _cell(f"{duration(seat['age_s'])} ago ({_clock(seat['last_activity'])})")
        + _cell(stall, css="stall")
    )


def _alerts(status: dict[str, Any]) -> str:
    alerts = status.get("alerts") or []
    new = set(status.get("new_alerts") or [])
    stalls = sum(1 for a in alerts if a["kind"] == "stall")
    title = f"<h2>Alerts: {stalls} stall, {len(alerts) - stalls} error ({len(new)} new)</h2>"
    if not alerts:
        return title + '<p class="muted">none</p>'
    ordered = sorted(alerts, key=lambda a: (-a.get("first_seen", 0.0), a["key"]))
    rows = [
        "<tr>"
        + _cell(_clock(a["first_seen"]) if "first_seen" in a else "-")
        + _cell(a["kind"], css="stall" if a["kind"] == "stall" else "flag")
        + _cell(a["title"])
        + _cell(a["message"])
        + _cell("new" if a["key"] in new else "")
        + "</tr>"
        for a in ordered
    ]
    return title + _table(["first seen", "kind", "title", "message", "new"], rows)


def _errors(status: dict[str, Any]) -> str:
    errors = status["errors"]
    if not errors:
        return ""
    rows = []
    for error in reversed(errors):
        provider = ", ".join(f"{k} x{v['count']}" for k, v in sorted(error["provider_errors"].items()))
        rows.append(
            "<tr>"
            + _cell(_when(error["finished_at"]))
            + _cell(error["path"])
            + _cell(", ".join(error["exceptions"]), css="flag")
            + _cell(provider)
            + _cell(error["unscored"] or "")
            + "</tr>"
        )
    head = ["finished", "trial", "exceptions", "provider errors", "unscored"]
    return f"<h2>Errors in finished trials: {len(errors)}</h2>" + _table(head, rows)


def _rewards(status: dict[str, Any]) -> str:
    if status.get("blind"):
        return '<h2>Rewards: hidden (blind)</h2><p class="muted">a pre-registered wave: no interim looks</p>'
    title = "<h2>Rewards (finished trials)</h2>"
    if not status["rewards"]:
        return title + '<p class="muted">none</p>'
    rows = []
    for row in status["rewards"]:
        mean = "n/a" if row["mean_reward"] is None else f"{row['mean_reward']:.3f}"
        rows.append(
            "<tr>"
            + _cell(row["label"])
            + _cell(row["condition"])
            + _cell(row["team"])
            + "".join(_cell(row[k], css="num") for k in ("trials", "scored", "valid"))
            + _cell(mean, css="num")
            + "</tr>"
        )
    return title + _table(["label", "condition", "team", "trials", "scored", "valid", "mean reward"], rows)


def trial_flags(trial: dict[str, Any], page: Page) -> list[str]:
    """The timeline's flags (stalls, errors, unscored, world counts), else the watcher's errors and
    unscored reason, then provider errors from ``trial.log`` and why the trial has no page."""
    if page.href is not None:
        flags = list(page.flags)
    else:
        flags = [f"error {e}" for e in trial["exceptions"]]
        flags += [f"unscored: {trial['unscored']}"] if trial["unscored"] else []
    flags += [f"{k} x{v['count']}" for k, v in sorted(trial["provider_errors"].items())]
    return flags + ([page.problem] if page.problem else [])


def _finished(rows: list[tuple[dict[str, Any], Page]], blind: bool) -> str:
    title = f"<h2>Finished trials: {len(rows)}</h2>"
    if not rows:
        return title + '<p class="muted">none</p>'
    lines = []
    for trial, page in rows:
        name = f"{trial['job']}/{trial['trial']}"
        link = f'<a href="{esc(page.href)}">{esc(name)}</a>' if page.href else esc(name)
        if blind:
            outcome = _cell("yes" if trial["valid"] else "no")
        else:
            reward = trial["reward"]
            shown = f"{reward:.3f}" if reward is not None else f"unscored: {trial['unscored']}"
            outcome = _cell(shown, sort="" if reward is None else reward, css="num")
        flags = "; ".join(trial_flags(trial, page))
        lines.append(
            "<tr>"
            + _cell(_when(trial["finished_at"]), sort=trial["finished_at"] or "")
            + f'<td data-sort="{esc(name)}">{link}</td>'
            + _cell(trial["label"])
            + _cell(trial["condition"])
            + _cell(trial["team"])
            + outcome
            + _cell(duration(trial["duration_s"]), sort=trial["duration_s"] or "", css="num")
            + _cell(
                _usd(trial["cost_usd"]),
                sort="" if trial["cost_usd"] is None else trial["cost_usd"],
                css="num",
            )
            + _cell(flags, css="flag")
            + "</tr>"
        )
    head = ["finished", "trial", "label", "condition", "team", "valid" if blind else "reward"]
    head += ["duration", "cost", "flags"]
    return title + '<p class="muted">Click a column head to sort.</p>' + _table(head, lines, "trials")


def render_dashboard(
    status: dict[str, Any], rows: list[tuple[dict[str, Any], Page]], refresh_s: float | None
) -> str:
    wave = status["wave"]
    name = wave["plan"] or Path(status["target"]).name
    blind = bool(status.get("blind"))
    title = f"flotilla-watch{' BLIND' if blind else ''}: {name} [{wave['state']}]"
    refresh = f'<meta http-equiv="refresh" content="{max(1, round(refresh_s))}">\n' if refresh_s else ""
    body = "\n".join(
        [
            f"<h1>{esc(title)}</h1>",
            _wave(status),
            _running(status),
            _alerts(status),
            _errors(status),
            _rewards(status),
            _finished(rows, blind),
        ]
    )
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        f"{refresh}<title>{esc(title)}</title>\n<style>{STYLE}{DASHBOARD_STYLE}</style>\n</head>\n"
        f"<body>\n{body}\n<script>{INDEX_SCRIPT}</script>\n</body>\n</html>\n"
    )
