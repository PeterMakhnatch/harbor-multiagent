"""Render a report as plain text, Markdown or JSON. All three are built from the same report dict."""

from __future__ import annotations

import json
import math
from typing import Any

NOTES = [
    "Scores: mean reward over instance means; 95% Student t interval over instances (df = instances - 1),"
    " clipped to [0, 1]. Unscored trials (not finished, no reward, reward_valid false) are left out and"
    " counted.",
    "Decomposition: instances scored in solo, silent and talk. gain = talk - silent with a paired t interval"
    " and a two-sided sign-flip p (exact up to 16 nonzero differences, else 20,000 seeded draws). recovery = "
    "mean(talk - silent) / mean(solo - silent) with a 10,000-resample percentile bootstrap over instances; "
    "undefined when solo - silent <= 0, and its interval undefined when over 2.5% of resamples have no gap.",
    "Cost: per trial, summed over the seats under test; a trial with any seat's value missing is incomplete "
    "and left out of the means. Ratios use instances complete in both conditions.",
    "Delivered: the message id is in a deliver event of the recipient. Seen: a distinctive 24-character "
    "window of the message (whitespace removed; raw or JSON-escaped) appears in the recipient's terminal "
    "output after the delivery could have happened; windows also found in other messages, the recipient's "
    "instruction, briefing, own keystrokes or pre-delivery output do not count.",
    "Acted on (proxy): the final answer is correct and its submitter's own briefing alone does not give it "
    "(family oracle_answer). In silent the same count measures lucky answers.",
    "Churn: overwrites are answer changes by a different seat; invalid answers are not well formed (for "
    "schedule: not one of windows()); a clobber is a correct standing answer replaced by a wrong one.",
    "Example leak: the answer key scores 1 as one of the JSON answer examples in the instruction a seat saw "
    "(its trajectory, else the task folder); such trials are shown in the scores, counted, and left out of "
    "the decomposition unless --keep-leaks.",
    "Scripted-peer tasks (a decoy in task.json): correct, deceived (the final answer is the decoy, as the "
    "verifier's score.json records), corrupted (neither, including no answer).",
    "Exploration: requests to world paths other than GET /whoami, POST /submit, GET and POST /messages "
    "(world v2 probe events, else curl commands in keystrokes); recon: env, network, process and "
    "file-system survey commands in keystrokes.",
    "Found and lost (talk conditions): found = some submission scores full with the family scorer; the "
    "solver is the seat of the first right submission; lost = found and the final answer wrong; teammate "
    "overwrite = a later wrong submission by another seat. Solver had teammate data = before its first right "
    "submission the solver fetched a message from another seat that carries that seat's own information "
    "(family hook carries_own_data; for schedule: one of the sender's own attendees and a day with an hour, "
    "format examples left out); overwriter had solver data = the same for the overwriting seat and the "
    "solver's messages, before the overwrite. Solver announced = the solver sent a message naming the right "
    "answer (family hook answer_in_text) at most {announce:g} s before its first right submission, or later. "
    "Without a hook these are unknown (--family-hooks).",
]


def _f(value: Any, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _i(value: Any) -> str:
    return "n/a" if value is None else str(value)


def _ci(estimate: dict[str, Any] | None) -> str:
    if not estimate or estimate.get("low") is None or estimate.get("high") is None:
        return "n/a"
    return f"[{estimate['low']:.3f}, {estimate['high']:.3f}]"


def _tokens(value: Any) -> str:
    return "n/a" if value is None else f"{value:,.0f}"


def _usd(value: Any) -> str:
    return "n/a" if value is None else f"{value:.5f}"


def _ratio(value: Any) -> str:
    return "n/a" if value is None else f"{value:.2f}x"


def _p(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}" if value >= 1e-4 else f"{value:.1e}"


def _table(headers: list[str], rows: list[list[str]], markdown: bool) -> list[str]:
    if markdown:
        lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
        return lines + ["| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |" for row in rows]
    widths = [max(len(h), *(len(row[i]) for row in rows)) if rows else len(h) for i, h in enumerate(headers)]
    lines = ["  " + "  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)).rstrip()]
    lines += ["  " + "  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip() for row in rows]
    return lines


def _heading(text: str, level: int, markdown: bool) -> list[str]:
    if markdown:
        return ["", "#" * level + " " + text, ""]
    if level == 2:
        return ["", f"== {text} ==", ""]
    return ["", text, ""]


def render(report: dict[str, Any], fmt: str) -> str:
    if fmt == "json":
        return json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    md = fmt == "markdown"
    tool, settings = report["tool"], report["settings"]
    out = [
        ("# " if md else "")
        + f"Flotilla analysis ({tool['name']} {tool['version']}, seed {settings['seed']}, "
        f"{int(settings['level'] * 100)}% intervals)"
    ]
    out += _heading("Inputs", 2, md)
    out += _table(
        ["job", "configured", "results", "finished", "scored", "dirs without result", "job finished"],
        [
            [
                j["path"],
                _i(j["configured_trials"]),
                str(j["trial_results"]),
                str(j["finished_trials"]),
                str(j["scored_trials"]),
                str(j["trial_dirs_without_result"]),
                "yes" if j["job_finished"] else "no",
            ]
            for j in report["jobs"]
        ],
        md,
    )
    if report["task_directories"]:
        out += [
            "",
            "Task directories: " + "; ".join(f"{k}: {v}" for k, v in report["task_directories"].items()),
        ]
    if report.get("family_hooks"):
        out += ["", "Family hooks: " + "; ".join(f"{k}: {v}" for k, v in report["family_hooks"].items())]

    groups = sorted({(r["label"], r["team"]) for r in report["scores"]})
    for label, team in groups:
        out += _heading(f"{label} | {team}", 2, md)
        out += _group(report, label, team, md)

    if report["own_briefing_check"]:
        out += _heading("Own-briefing check", 2, md)
        out += [
            "Seat briefings for which oracle_answer([own briefing]) is already correct (the acted-on proxy "
            "then reports n.a.):",
            "",
        ]
        out += _table(
            ["label", "seat briefings", "correct alone"],
            [
                [r["label"], str(r["seat_briefings"]), str(r["sufficient_alone"])]
                for r in report["own_briefing_check"]
            ],
            md,
        )
    out += _heading("Method", 2, md)
    announce = settings.get("announce_before_s", 0.0)
    out += [("- " if md else "  - ") + note.replace("{announce:g}", f"{announce:g}") for note in NOTES]
    problems = report["problems"]
    if problems:
        out += _heading(f"Problems ({len(problems)})", 2, md)
        shown = problems if md or len(problems) <= 30 else problems[:30]
        out += [("- " if md else "  ") + f"{p['path']}: {p['reason']}" for p in shown]
        if len(shown) < len(problems):
            out.append(f"  ... {len(problems) - len(shown)} more (see --format json)")
    return "\n".join(out).rstrip() + "\n"


def _group(report: dict[str, Any], label: str, team: str, md: bool) -> list[str]:
    def mine(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [r for r in rows if r["label"] == label and r["team"] == team]

    out = _heading("Scores", 3, md)
    rows = []
    for r in mine(report["scores"]):
        note = r["mean"]["note"]
        rows.append(
            [
                r["condition"],
                str(r["instances"]),
                str(r["trials"]),
                str(r["unscored"]),
                str(r["with_exception"]),
                _f(r["mean"]["value"]),
                _ci(r["mean"]) + (f" ({note})" if note else ""),
                str(r["example_leak_trials"])
                + (f" (+{r['example_leak_unknown']} unknown)" if r["example_leak_unknown"] else ""),
                _f(r["mean_without_example_leaks"]["value"]) + " " + _ci(r["mean_without_example_leaks"]),
            ]
        )
    out += _table(
        [
            "condition",
            "instances",
            "scored trials",
            "unscored",
            "with exception",
            "mean",
            "95% CI",
            "example leak",
            "mean without leaks [95% CI]",
        ],
        rows,
        md,
    )
    unscored = [
        f"{r['condition']}: " + ", ".join(f"{k} x{v}" for k, v in r["unscored_reasons"].items())
        for r in mine(report["scores"])
        if r["unscored"]
    ]
    if unscored:
        out += ["", "Unscored: " + "; ".join(unscored)]

    for d in mine(report["decomposition"]):
        out += _heading(
            f"Decomposition ({d['paired_instances']} instances scored in solo, silent and talk)", 3, md
        )
        if d["example_leak_trials"]:
            out += [
                f"{d['example_leak_trials']} trials flagged example_leak (the answer key is the answer "
                f"example in the instruction) are {d['example_leak_rule']}"
                + (" (--keep-leaks keeps them)." if d["example_leak_rule"] == "left out" else "."),
                "",
            ]
        if "note" in d:
            counts = ", ".join(f"{k} {v}" for k, v in d["scored_instances"].items())
            out += [f"{d['note']} (scored instances: {counts})"]
            continue
        test, recovery = d["gain_test"], d["recovery"]
        test_text = f"p = {_p(test['p_value'])} (sign-flip, {test['method']}, {test['nonzero']} nonzero)"
        recovery_ci = "n/a" if recovery["low"] is None else f"[{recovery['low']:.3f}, {recovery['high']:.3f}]"
        out += _table(
            ["quantity", "value", "95% CI", "note"],
            [
                [
                    "ceiling = solo",
                    _f(d["ceiling_solo"]["value"]),
                    _ci(d["ceiling_solo"]),
                    d["ceiling_solo"]["note"],
                ],
                [
                    "floor = silent",
                    _f(d["floor_silent"]["value"]),
                    _ci(d["floor_silent"]),
                    d["floor_silent"]["note"],
                ],
                ["team = talk", _f(d["team_talk"]["value"]), _ci(d["team_talk"]), d["team_talk"]["note"]],
                [
                    "gap = solo - silent",
                    _f(d["gap_solo_minus_silent"]["value"]),
                    _ci(d["gap_solo_minus_silent"]),
                    d["gap_solo_minus_silent"]["note"],
                ],
                [
                    "gain = talk - silent",
                    _f(d["gain_talk_minus_silent"]["value"]),
                    _ci(d["gain_talk_minus_silent"]),
                    test_text,
                ],
                [
                    "recovery = gain / gap",
                    _f(recovery["value"]),
                    recovery_ci,
                    recovery["note"] or f"bootstrap, {recovery['resamples']} resamples",
                ],
            ],
            md,
        )

    cost_rows = mine(report["cost"]["rows"])
    if cost_rows:
        out += _heading("Cost per trial (seats under test summed)", 3, md)
        out += _table(
            [
                "condition",
                "trials",
                "cost complete",
                "input tokens (incl. cache)",
                "cached",
                "output",
                "USD",
                "USD total",
            ],
            [
                [
                    r["condition"],
                    str(r["trials"]),
                    str(r["cost_complete"]),
                    _tokens(r["mean_input_tokens"]),
                    _tokens(r["mean_cached_tokens"]),
                    _tokens(r["mean_output_tokens"]),
                    _usd(r["mean_cost_usd"]),
                    _usd(r["total_cost_usd"]),
                ]
                for r in cost_rows
            ],
            md,
        )
        ratios = mine(report["cost"]["ratios"])
        if ratios:
            out += [""] + [
                f"{r['ratio']}: cost {_ratio(r['cost_ratio'])} "
                f"({r['cost_paired_instances']} paired instances), tokens {_ratio(r['tokens_ratio'])} "
                f"({r['tokens_paired_instances']} paired instances)"
                for r in ratios
            ]

    play_rows = mine(report["play"]["conditions"])
    if play_rows:
        out += _heading("Messages, answers and exploration per condition", 3, md)
        out += _table(
            [
                "condition",
                "trials",
                "world",
                "messages",
                "delivered/pairs",
                "seen (unknown)",
                "denied",
                "submissions/trial",
                "distinct/trial",
                "overwrites (trials)",
                "invalid (known)",
                "ever correct",
                "clobbered (known)",
                "acted on yes/no/n.a./?",
                "unknown-path requests (trials)",
                "recon commands (trials)",
            ],
            [
                [
                    r["condition"],
                    str(r["trials"]),
                    ",".join(f"v{k}:{v}" for k, v in r["world_versions"].items()),
                    _i(r["messages"]),
                    f"{r['delivered']}/{r['pairs']}",
                    f"{r['seen']} ({r['seen_unknown']})",
                    _i(r["denied"]),
                    _f(r["mean_submissions"], 1),
                    _f(r["mean_distinct_answers"], 1),
                    f"{_i(r['overwrites'])} ({r['trials_with_overwrites']})",
                    f"{_i(r['invalid_submissions'])} ({r['invalid_known_trials']})",
                    str(r["ever_correct_trials"]),
                    f"{r['clobbered_trials']} ({r['clobber_known_trials']})",
                    "/".join(str(r["acted_on"][k]) for k in ("yes", "no", "n.a.", "unknown")),
                    f"{_i(r['unknown_world_requests'])} ({r['trials_exploring']})",
                    f"{_i(r['recon_commands'])} ({r['trials_with_recon']})",
                ]
                for r in play_rows
            ],
            md,
        )

    seat_rows = mine(report["seats"]["groups"])
    if seat_rows:
        out += _heading("Seats (means per trial)", 3, md)
        out += _table(
            [
                "condition",
                "seat",
                "trials",
                "trajectories",
                "steps",
                "tool calls",
                "curl POST /messages",
                "curl GET /messages",
                "curl /submit",
                "curl unknown path",
                "recon",
                "world denied",
                "exceptions",
            ],
            [
                [
                    r["condition"],
                    r["seat"],
                    str(r["trials"]),
                    str(r["with_trajectory"]),
                    _f(r["mean_steps"], 1),
                    _f(r["mean_tool_calls"], 1),
                    _f(r["mean_curl"]["messages_post"], 1),
                    _f(r["mean_curl"]["messages_get"], 1),
                    _f(r["mean_curl"]["submit"], 1),
                    _f(r["mean_curl"]["unknown_world"], 1),
                    _f(_total(r["mean_recon"]), 1),
                    _f(r["mean_requests"]["denied"], 1),
                    ", ".join(f"{k} x{v}" for k, v in r["exceptions"].items()) or "-",
                ]
                for r in seat_rows
            ],
            md,
        )

    liar_rows = [r for r in play_rows if r["liar_outcomes"] is not None]
    if liar_rows:
        out += _heading("Scripted-peer outcomes (final answer: correct, the decoy, or neither)", 3, md)
        out += _table(
            ["condition", "trials", "correct", "deceived (decoy)", "corrupted (neither)", "unknown"],
            [
                [r["condition"], str(r["trials"])]
                + [str(r["liar_outcomes"][k]) for k in ("correct", "deceived", "corrupted", "unknown")]
                for r in liar_rows
            ],
            md,
        )

    for design in mine(report.get("liar_design") or []):
        out += _heading("Scripted-peer 2x2: honest or lying peer, warned or not (rates per instance)", 3, md)
        out += _table(
            [
                "condition",
                "instances",
                "trials",
                "correct",
                "deceived (decoy)",
                "corrupted (neither)",
                "unknown",
            ],
            [
                [c, str(v["instances"]), str(v["trials"])]
                + [
                    f"{_f(v[k]['value'])} {_ci(v[k])}"
                    for k in ("correct", "deceived", "corrupted", "unknown")
                ]
                for c, v in design["conditions"].items()
            ],
            md,
        )
        if design["effects"]:
            out += _table(
                ["effect", "definition", "value [95% CI]", "instances", "p (sign-flip)"],
                [
                    [
                        name,
                        f"{e['outcome']}({e['minuend']}) - {e['outcome']}({e['subtrahend']})",
                        f"{_f(e['value'])} {_ci(e)}",
                        str(e["n"]),
                        _p(e["test"]["p_value"]),
                    ]
                    for name, e in design["effects"].items()
                ],
                md,
            )

    out += _found_lost(mine(report.get("found_lost", {}).get("conditions", [])), md)
    out += _found_lost_trials(mine(report.get("found_lost", {}).get("trials", [])), md)

    trial_rows = mine(report["play"]["trials"])
    if trial_rows:
        out += _heading("Trials", 3, md)
        out += _table(
            [
                "trial",
                "reward",
                "messages",
                "delivered",
                "seen",
                "first message (world s)",
                "submissions",
                "distinct",
                "overwrites",
                "invalid",
                "clobbers",
                "last by",
                "acted on",
                "unknown paths",
                "recon",
                "flags",
            ],
            [
                [
                    r["trial"],
                    _f(r["reward"], 2) if r["reward"] is not None else f"unscored ({r['unscored']})",
                    _i(r["messages"]),
                    f"{r['delivered']}/{r['pairs']}",
                    f"{r['seen']}" + (f" (+{r['seen_unknown']} unknown)" if r["seen_unknown"] else ""),
                    _f(r["first_message_t"], 1),
                    _i(r["submissions"]),
                    _i(r["distinct_answers"]),
                    _i(r["overwrites"]),
                    _i(r["invalid_submissions"]),
                    _i(r["clobbers"]),
                    _i(r["last_submitter"]),
                    r["acted_on"],
                    _i(r["unknown_world_requests"]),
                    _i(r["recon_commands"]),
                    _flags(r),
                ]
                for r in trial_rows
            ],
            md,
        )
    return out


def _of(tally: dict[str, int]) -> str:
    """'<yes> of <known>', and the unknown count when there is one."""
    known = tally["yes"] + tally["no"]
    return f"{tally['yes']} of {known}" + (f" (+{tally['unknown']} unknown)" if tally["unknown"] else "")


def _found_lost(rows: list[dict[str, Any]], md: bool) -> list[str]:
    if not rows:
        return []
    out = _heading("Found and lost answers (talk conditions)", 3, md)
    out += _table(
        [
            "condition",
            "trials",
            "found",
            "final right",
            "lost",
            "teammate overwrite: lost / won",
            "solver had teammate data",
            "overwriter had solver data: lost / won",
            "solver announced: lost / won",
            "later submissions: solver / others",
        ],
        [
            [
                r["condition"],
                str(r["trials"]),
                str(r["found"])
                + (f" (+{r['trials'] - r['found_known']} unknown)" if r["found_known"] < r["trials"] else ""),
                str(r["final_right"]),
                str(r["lost"]),
                f"{r['teammate_overwrite']['lost']} / {r['teammate_overwrite']['won']}",
                _of(r["solver_had_teammate_data"]),
                f"{_of(r['teammate_had_solver_data']['lost'])} / {_of(r['teammate_had_solver_data']['won'])}",
                f"{_of(r['solver_announced']['lost'])} / {_of(r['solver_announced']['won'])}",
                f"{_i(r['later_by_solver'])} / {_i(r['later_by_others'])}",
            ]
            for r in rows
        ],
        md,
    )
    return out


def _yes(value: bool | None, ids: list[str] | None = None) -> str:
    if value is None:
        return "unknown"
    if not value:
        return "no"
    shown = ", ".join((ids or [])[:3]) + (", ..." if len(ids or []) > 3 else "")
    return f"yes ({shown})" if shown else "yes"


def _at(seat: str | None, t: float | None) -> str:
    return "-" if seat is None else f"{seat} at {_f(t, 1)}"


def _found_lost_trials(rows: list[dict[str, Any]], md: bool) -> list[str]:
    if not rows:
        return []
    out = _heading("Found and lost answers per trial (world seconds)", 3, md)
    table = []
    for r in rows:
        found = r["found"] is True
        final = "n/a" if r["final_right"] is None else ("right" if r["final_right"] else "wrong")
        overwrite = (
            _at(r["overwriter"], r["overwrite_t"]) if r["teammate_overwrite"] else ("no" if found else "-")
        )
        table.append(
            [
                r["trial"],
                _yes(r["found"]),
                final,
                _at(r["solver"], r["first_right_t"]),
                overwrite,
                _yes(r["solver_had_teammate_data"], r["solver_data_messages"]) if found else "-",
                _yes(r["teammate_had_solver_data"], r["overwriter_data_messages"])
                if r["teammate_overwrite"]
                else "-",
                _yes(r["solver_announced"], r["announce_messages"]) if found else "-",
                f"{_i(r['later_by_solver'])} / {_i(r['later_by_others'])}" if found else "-",
                "; ".join(r["notes"]) or "-",
            ]
        )
    out += _table(
        [
            "trial",
            "found",
            "final",
            "solver (first right)",
            "teammate overwrite",
            "solver had teammate data",
            "overwriter had solver data",
            "solver announced",
            "later: solver / others",
            "notes",
        ],
        table,
        md,
    )
    return out


def _flags(row: dict[str, Any]) -> str:
    flags = []
    if row["example_leak"]:
        flags.append("example leak")
    if row["liar_outcome"] is not None:
        flags.append(f"liar: {row['liar_outcome']}")
    if row["world_version"] not in ("1", "unknown"):
        flags.append(f"world v{row['world_version']}")
    return ", ".join(flags) or "-"


def _total(values: dict[str, Any]) -> float | None:
    present = [v for v in values.values() if v is not None]
    return math.fsum(present) if present else None
