"""Family `ledger`: pool a sharded transaction log and compute every account's closing balance.

Hidden profile: one period's transaction log is split into disjoint shards, one per seat, and each
account's opening balance is known to exactly one seat. Every account has rows in every seat's
shard, so no seat can compute any closing balance alone. Two twists make careless pooling fail:

- a transfer is two rows with one transfer id (the paying leg and the receiving leg), and different
  seats hold the two legs; a seat that also books the other leg from the row's details counts the
  transfer twice;
- a `reversal` row names only the earlier row that it cancels, and another seat holds that row, so
  per-account subtotals cannot settle it: the team must pool rows, not only totals.

The generator rejects instances where either careless pooling gets every balance right, where a
seat alone gets any balance right, or where a closing balance appears as a number in any text a seat
reads. Standard library only; deterministic for a seed. See flotilla/CONTRACT.md 7.
"""

from __future__ import annotations

import json
import math
import random
import re
from typing import Any

FAMILY = "ledger"
TITLE = "Close the books"
ACCOUNTS = ["Alder", "Birch", "Cedar", "Dogwood", "Elm", "Fir", "Hazel", "Juniper", "Larch", "Maple", "Oak", "Rowan"]
SETTINGS = {
    "easy": {"accounts": 5, "rows_per_account": 4, "transfers": 3, "reversals": 2},
    "hard": {"accounts": 7, "rows_per_account": 5, "transfers": 5, "reversals": 4},
}
SIMPLE = ("deposit", "withdrawal", "fee")  # one-account rows; only these can be reversed
SIMPLE_WEIGHTS = (4, 4, 2)
SIGN = {"deposit": 1, "withdrawal": -1, "fee": -1, "transfer-in": 1, "transfer-out": -1}
AMOUNTS = {"opening": (100, 2500), "deposit": (20, 900), "withdrawal": (20, 600), "fee": (2, 45),
           "transfer": (50, 700)}
MAX_ROWS_PER_SEAT = 60
MAX_BRIEFING_CHARS = 3_500  # the talk oracle posts a briefing as one message; the world allows 4,000

Row = dict[str, Any]


def generate(seed: int, instance: int, n_seats: int, difficulty: str) -> dict[str, Any]:
    setting = SETTINGS[difficulty]
    if n_seats > setting["rows_per_account"]:
        raise ValueError(f"ledger/{difficulty} supports at most {setting['rows_per_account']} seats")
    rng = random.Random(f"{FAMILY}/{seed}/{instance}/{n_seats}/{difficulty}")
    seats = [f"p{i + 1}" for i in range(n_seats)]
    for _attempt in range(1_000):
        accounts = sorted(rng.sample(ACCOUNTS, setting["accounts"]))
        openings = {account: rng.randint(*AMOUNTS["opening"]) for account in accounts}
        dealt = rng.sample(accounts, len(accounts))  # each opening balance goes to exactly one seat
        holder = {account: seats[i % n_seats] for i, account in enumerate(dealt)}
        log = _draw_log(rng, setting, accounts, seats)
        balances = _closing(openings, log)
        rows = [row for row, _seat in log]
        parts = {
            seat: {
                "accounts": accounts,
                "rows_in_log": len(log),
                "openings": {a: openings[a] for a in accounts if holder[a] == seat},
                "rows": [row for row, holder_seat in log if holder_seat == seat],
            }
            for seat in seats
        }
        solo = {"accounts": accounts, "rows_in_log": len(log), "openings": openings, "rows": rows}
        if max(len(part["rows"]) for part in parts.values()) > MAX_ROWS_PER_SEAT:
            continue
        if max(len(json.dumps(part, sort_keys=True)) for part in parts.values()) > MAX_BRIEFING_CHARS:
            continue
        if n_seats > 1:
            if any(_hits(oracle_answer([part]), balances) for part in parts.values()):
                continue  # a seat alone must not get any balance right
            team = list(parts.values())
            if _hits(pool_subtotals(team), balances) == len(accounts):
                continue  # pooling per-account subtotals must miss the cross-seat reversals
            if _hits(pool_double_booked(team), balances) == len(accounts):
                continue  # booking both legs of every transfer must go wrong
        texts = {seat: _render(part) for seat, part in parts.items()}
        goal, answer_format = _goal(accounts, len(log)), _answer_format(accounts)
        shown = set().union(*(numbers_in(text) for text in (goal, answer_format, _render(solo), *texts.values())))
        if shown & set(balances.values()):
            continue  # no closing balance may appear as a number in any text a seat reads
        return {
            "goal": goal,
            "answer_format": answer_format,
            "private": parts,
            "private_text": texts,
            "solo_private": solo,
            "solo_text": _render(solo),
            "answer_key": {"balances": balances},
        }
    raise RuntimeError("no instance found; relax the settings")


def _draw_log(rng: random.Random, setting: dict[str, int], accounts: list[str],
              seats: list[str]) -> list[tuple[Row, str]]:
    """The period's log in order: (row, seat that holds it). Row ids are T01, T02, ..."""
    entries: list[tuple[Row, str]] = []
    for account in accounts:
        # every seat holds at least one row of every account
        holders = rng.sample(seats, len(seats))
        holders += [rng.choice(seats) for _ in range(setting["rows_per_account"] - len(seats))]
        for seat in holders:
            kind = rng.choices(SIMPLE, weights=SIMPLE_WEIGHTS)[0]
            entries.append(({"type": kind, "account": account, "amount": rng.randint(*AMOUNTS[kind])}, seat))
    for t in range(setting["transfers"]):
        payer, payee = rng.sample(accounts, 2)
        amount = rng.randint(*AMOUNTS["transfer"])
        out_seat, in_seat = rng.sample(seats, 2) if len(seats) > 1 else (seats[0], seats[0])
        transfer = f"X{t + 1}"
        entries.append(({"type": "transfer-out", "account": payer, "amount": amount, "transfer": transfer,
                         "counterparty": payee}, out_seat))
        entries.append(({"type": "transfer-in", "account": payee, "amount": amount, "transfer": transfer,
                         "counterparty": payer}, in_seat))
    rng.shuffle(entries)
    log = list(entries)
    simple = [entry for entry in entries if entry[0]["type"] in SIMPLE]
    for target, target_seat in rng.sample(simple, setting["reversals"]):
        others = [seat for seat in seats if seat != target_seat] or seats
        position = next(i for i, (row, _seat) in enumerate(log) if row is target)
        log.insert(rng.randint(position + 1, len(log)), ({"type": "reversal", "target": target}, rng.choice(others)))
    for k, (row, _seat) in enumerate(log):
        row["id"] = f"T{k + 1:02d}"
    for row, _seat in log:
        if row["type"] == "reversal":
            row["reverses"] = row.pop("target")["id"]
    return log


def _closing(openings: dict[str, int], log: list[tuple[Row, str]]) -> dict[str, int]:
    """Closing balances, computed from the generator's own records."""
    by_id = {row["id"]: row for row, _seat in log}
    balances = dict(openings)
    for row, _seat in log:
        if row["type"] == "reversal":
            target = by_id[row["reverses"]]
            balances[target["account"]] -= SIGN[target["type"]] * target["amount"]
        else:
            balances[row["account"]] += SIGN[row["type"]] * row["amount"]
    return balances


def _effect(row: Row, rows: dict[str, Row]) -> tuple[str, int] | None:
    """(account, change) of one row, or None for a reversal whose target is unknown."""
    if row["type"] == "reversal":
        target = rows.get(row["reverses"])
        if target is None or target["type"] not in SIMPLE:
            return None
        return target["account"], -SIGN[target["type"]] * target["amount"]
    return row["account"], SIGN[row["type"]] * row["amount"]


def oracle_answer(private: list[dict[str, Any]]) -> dict[str, Any]:
    """Book every known row on its own account; an unknown opening balance counts as 0."""
    accounts: set[str] = set()
    openings: dict[str, int] = {}
    rows: dict[str, Row] = {}
    for part in private:
        accounts.update(part.get("accounts", []))
        openings.update(part.get("openings", {}))
        rows.update((row["id"], row) for row in part.get("rows", []))
    balances = {account: openings.get(account, 0) for account in sorted(accounts)}
    for row_id in sorted(rows):
        effect = _effect(rows[row_id], rows)
        if effect is not None:
            balances[effect[0]] = balances.get(effect[0], 0) + effect[1]
    return {"balances": balances}


def pool_subtotals(private: list[dict[str, Any]]) -> dict[str, Any]:
    """Careless pooling 1: each seat reports per-account subtotals (its opening balances plus its own
    rows); the team adds them up. Reversals of rows that another seat holds are lost."""
    totals: dict[str, int] = {}
    for part in private:
        for account, value in oracle_answer([part])["balances"].items():
            totals[account] = totals.get(account, 0) + value
    return {"balances": totals}


def pool_double_booked(private: list[dict[str, Any]]) -> dict[str, Any]:
    """Careless pooling 2: all rows are pooled, but each transfer row is also booked on its
    counterparty, so every transfer counts twice."""
    balances = oracle_answer(private)["balances"]
    for part in private:
        for row in part.get("rows", []):
            if row["type"] in ("transfer-in", "transfer-out"):
                balances[row["counterparty"]] -= SIGN[row["type"]] * row["amount"]
    return {"balances": balances}


def _hits(answer: dict[str, Any], balances: dict[str, int]) -> int:
    return sum(1 for account, value in balances.items() if answer["balances"].get(account) == value)


def numbers_in(text: str) -> set[int]:
    """Every whole number written in a text (row ids such as T07 or X2 are not numbers)."""
    return {int(m) for m in re.findall(r"(?<![\w.])-?\d+(?![\w.])", text, flags=re.ASCII)}


def _goal(accounts: list[str], n_rows: int) -> str:
    names = ", ".join(accounts[:-1]) + f" and {accounts[-1]}"
    return "\n".join([
        (f"Close the books for one period. There are {len(accounts)} accounts: {names}. Find each account's "
        "closing balance: its opening balance plus the effect of every row in the period's log. "
        f"The log has {n_rows} rows, T01 to T{n_rows:02d}; each row id appears exactly once."),
        "",
        "What each type of row does:",
        "",
        "- `deposit` adds its amount to its account.",
        "- `withdrawal` and `fee` subtract their amount from their account.",
        ("- A transfer between two accounts is two rows with the same transfer id (X1, X2, ...): "
        "`transfer-out` subtracts the amount from the paying account, and `transfer-in` adds it to the "
        "receiving account."),
        ("- `reversal` cancels the earlier row that it names: it undoes that row's effect on that row's "
        "account. A reversed deposit is subtracted again; a reversed withdrawal or fee is added back. "
        "A reversal never cancels a transfer row or another reversal."),
        "",
        ("Every row changes exactly one account: the account in its `account` column (for a reversal, "
        "the account of the row that it cancels). Amounts are whole units. A balance can be negative."),
    ])


def _answer_format(accounts: list[str]) -> str:
    example = ", ".join(f'"{account}": <integer>' for account in accounts[:2])
    return (
        f'A JSON object with the closing balance of every account as a whole number: '
        f'`{{"balances": {{{example}, ...}}}}`, with all {len(accounts)} accounts.'
    )


def _render(part: dict[str, Any]) -> str:
    openings = part["openings"]
    lines = ["Opening balances you know (at the start of the period):", ""]
    lines += [f"- {account}: {openings[account]}" for account in sorted(openings)] or ["- none"]
    lines += [
        "",
        f"Log rows you hold ({len(part['rows'])} of the {part['rows_in_log']} rows in the log):",
        "",
        "| row | type | account | amount | details |",
        "|---|---|---|---|---|",
    ]
    for row in part["rows"]:
        if row["type"] == "reversal":
            lines.append(f"| {row['id']} | reversal | | | cancels row {row['reverses']} |")
            continue
        details = ""
        if row["type"] == "transfer-out":
            details = f"transfer {row['transfer']}, paid to {row['counterparty']}"
        elif row["type"] == "transfer-in":
            details = f"transfer {row['transfer']}, paid by {row['counterparty']}"
        lines.append(f"| {row['id']} | {row['type']} | {row['account']} | {row['amount']} | {details} |")
    return "\n".join(lines)


_INT = re.compile(r"[+-]?\d+", re.ASCII)
_GROUPED = re.compile(r"[+-]?\d{1,3}(?:,\d{3})+", re.ASCII)
_DECIMAL = re.compile(r"([+-]?\d+)\.0+", re.ASCII)


def parse_amount(value: Any) -> int | None:
    """A whole number from an int, an integral float or a numeric string; None for anything else.

    Accepts "1234", " -56 ", "+7", "1,234", "1234.00" and the minus sign U+2212. Never rounds."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value.is_integer() else None
    if not isinstance(value, str):
        return None
    text = value.strip().replace("−", "-")
    if len(text) > 32:
        return None
    if _INT.fullmatch(text):
        return int(text)
    if _GROUPED.fullmatch(text):
        return int(text.replace(",", ""))
    match = _DECIMAL.fullmatch(text)
    return int(match.group(1)) if match else None


def _given(answer: Any) -> dict[str, set[int | None]]:
    """Casefolded account name -> every value given for it (None for a value that does not parse)."""
    if not isinstance(answer, dict):
        return {}
    table = answer
    for key, value in answer.items():
        if isinstance(key, str) and key.strip().casefold() == "balances" and isinstance(value, dict):
            table = value
            break
    given: dict[str, set[int | None]] = {}
    for key, value in table.items():
        if isinstance(key, str):
            given.setdefault(key.strip().casefold(), set()).add(parse_amount(value))
    return given


def score(answer: dict[str, Any] | None, key: dict[str, Any]) -> dict[str, Any]:
    """Fraction of accounts whose closing balance is exactly right. Two conflicting values for one
    account (for example "Elm" and "elm") earn nothing for that account."""
    expected: dict[str, int] = key["balances"]
    given = _given(answer)
    correct = sum(1 for account, value in expected.items() if given.get(account.casefold()) == {value})
    return {"score": correct / len(expected), "fields_correct": correct, "fields_total": len(expected)}
