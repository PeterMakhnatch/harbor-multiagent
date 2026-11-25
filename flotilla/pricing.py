"""Token prices for a wave.

DeepSeek direct (published rates, checked 2026-11-20 on api-docs.deepseek.com/quick_start/pricing):
$0.003 / $0.15 / $0.60 per million cached-input / other-input / output tokens, off-peak. Peak is double,
01:00-04:00 and 06:00-10:00 UTC, Monday to Friday. Chinese public holidays, which are off-peak, are not
modelled: the peak factor is an upper bound. The runner and the analysis scripts charge DeepSeek this way.

OpenRouter: Harbor's recorded cost_usd (LiteLLM response_cost summed per seat) is the charge. It matched
OpenRouter's generation API; LiteLLM's completion_cost did not. A catalog frozen at plan start
(https://openrouter.ai/api/v1/models, USD per token) is the cross-check. A missing cache-read price charges
cached tokens at the input price. If the recorded cost is missing, or 0 while tokens are above 0, the charge
is that token estimate.

Forecast (run_wave.py --dry-run): use the same model's mean charge for the same probe kind with at least
5 seat samples. Otherwise, price the kind's median tokens per model seat (DeepSeek direct at the peak rate).
If that estimate is unavailable, use the same model's mean charge across any kind, or report unknown.
Exclude trials with exceptions and seats with no tokens. Modal adds MODAL_PER_TRIAL_USD per trial.

Data policy: the account has zero data retention (ZDR) on. zdr_lines reads OpenRouter's public ZDR endpoint list
(https://openrouter.ai/api/v1/endpoints/zdr, cached 24 h like the catalog, offline-safe) and warns for each
OpenRouter model without a ZDR endpoint, which such an account gets a 404 for.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from http.client import HTTPException
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

OFF_PEAK = {"hit": 0.003, "miss": 0.15, "out": 0.60}  # USD per million tokens
PEAK_HOURS_UTC = ((1, 4), (6, 10))
PEAK_FACTOR_MAX = 2.0
MODAL_PER_TRIAL_USD = 0.0021  # Modal sandbox per trial, measured 2026-11-24 (docs/NEXT-WAVE.md)
CATALOG_CACHE = "openrouter-models.json"
CATALOG_MAX_AGE_S = 24 * 3600
ZDR_CACHE = "openrouter-zdr.json"
ZDR_WARNING = "no zero-data-retention endpoint: an account with ZDR on gets 404"
HISTORY_MIN_SEATS = 5
ORACLE = "oracle (no model)"


def peak_factor(started: datetime) -> float:
    """1 off-peak, 2 at peak. `started` is already in the zone the caller priced (UTC)."""
    hour = started.hour + started.minute / 60
    return 2.0 if started.weekday() < 5 and any(a <= hour < b for a, b in PEAK_HOURS_UTC) else 1.0


def tokens_of(result: dict) -> dict[str, int]:
    """Cached input, other input, output. Harbor's input count includes the cached tokens."""
    tokens = {"hit": 0, "miss": 0, "out": 0}
    for seat in result.get("seat_results") or []:
        usage = seat.get("agent_result") or {}
        cached, total = usage.get("n_cache_tokens") or 0, usage.get("n_input_tokens") or 0
        tokens["hit"] += cached
        tokens["miss"] += max(0, total - cached)
        tokens["out"] += usage.get("n_output_tokens") or 0
    return tokens


def deepseek_cost(tokens: dict[str, int], started: datetime) -> float:
    """USD for DeepSeek direct tokens. Same expression commons_wave used before this module."""
    return peak_factor(started) * sum(tokens[k] * OFF_PEAK[k] for k in tokens) / 1e6


def is_deepseek_direct(model: str) -> bool:
    return model.startswith("deepseek/")


def known_model(model: str) -> bool:
    return isinstance(model, str) and bool(model) and (
        model.startswith("deepseek/") or model.startswith("openrouter/"))


def usage_tokens(usage: dict) -> dict[str, int]:
    cached = int(usage.get("n_cache_tokens") or 0)
    total = int(usage.get("n_input_tokens") or 0)
    return {"hit": cached, "miss": max(0, total - cached), "out": int(usage.get("n_output_tokens") or 0)}


def estimate_seats(result: dict, models_by_seat: dict[str, str], started: datetime, prices: dict | None) -> float:
    total = 0.0
    for seat in result.get("seat_results") or []:
        total += cost(models_by_seat[seat["seat"]], usage_tokens(seat.get("agent_result") or {}), started, prices)
    return total

def model_id(result: dict) -> str:
    """LiteLLM id from a trial result. Missing model info stays on the DeepSeek direct id, which is what
    every priced wave before this module used."""
    info: dict = {}
    for seat in result.get("seat_results") or []:
        info = (seat.get("agent_info") or {}).get("model_info") or {}
        if info.get("name"):
            break
    if not info.get("name"):
        info = (result.get("agent_info") or {}).get("model_info") or {}
    name, provider = info.get("name") or "", (info.get("provider") or "").lower()
    if not name:
        for participant in (result.get("config") or {}).get("participants") or []:
            if participant.get("model_name"):
                return participant["model_name"]
        return "deepseek/deepseek-flash"
    if provider == "openrouter":
        return name if name.startswith("openrouter/") else f"openrouter/{name}"
    if provider == "deepseek":
        return name if name.startswith("deepseek/") else f"deepseek/{name}"
    if provider and not name.startswith(provider + "/"):
        return f"{provider}/{name}"
    return name


def openrouter_cost(model: str, tokens: dict[str, int], prices: dict) -> float:
    """USD from a frozen catalog. `prices` is keyed by the LiteLLM id or the OpenRouter id."""
    row = prices.get(model)
    if row is None and model.startswith("openrouter/"):
        row = prices.get(model.removeprefix("openrouter/"))
    if not row:
        raise KeyError(model)
    prompt, completion = float(row["prompt"]), float(row["completion"])
    cached = row.get("input_cache_read")
    hit = prompt if cached is None else float(cached)
    return tokens["hit"] * hit + tokens["miss"] * prompt + tokens["out"] * completion


def cost(model: str, tokens: dict[str, int], started: datetime, prices: dict | None = None) -> float:
    if is_deepseek_direct(model):
        return deepseek_cost(tokens, started)
    if model.startswith("openrouter/"):
        return openrouter_cost(model, tokens, prices or {})
    raise KeyError(model)


def recorded_cost(result: dict) -> float | None:
    """Sum of seat agent_result cost_usd. None when a seat has no recorded cost."""
    seats = result.get("seat_results") or []
    if not seats:
        return None
    total = 0.0
    for seat in seats:
        usage = seat.get("agent_result") or {}
        if usage.get("cost_usd") is None:
            return None
        total += float(usage["cost_usd"])
    return total


def openrouter_spend(result: dict, model: str, prices: dict | None) -> tuple[float, float | None, str]:
    """(charged, token estimate, source). source is recorded or token-estimate."""
    tokens = tokens_of(result)
    recorded = recorded_cost(result)
    estimate = None
    if prices is not None:
        try:
            estimate = openrouter_cost(model, tokens, prices)
        except KeyError:
            estimate = None
    count = tokens["hit"] + tokens["miss"] + tokens["out"]
    if recorded is None or (recorded == 0 and count > 0):
        if estimate is None:
            raise KeyError(model)
        return estimate, estimate, "token-estimate"
    return recorded, estimate, "recorded"


def parse_openrouter_models(payload: dict) -> dict[str, dict[str, str]]:
    """id -> {prompt, completion, input_cache_read?} per token. A missing cache-read key stays missing."""
    found = {}
    for row in payload.get("data") or []:
        pricing = row.get("pricing") or {}
        if "id" not in row or "prompt" not in pricing or "completion" not in pricing:
            continue
        item = {"prompt": str(pricing["prompt"]), "completion": str(pricing["completion"])}
        if "input_cache_read" in pricing and pricing["input_cache_read"] not in (None, ""):
            item["input_cache_read"] = str(pricing["input_cache_read"])
        found[row["id"]] = item
    return found


def parse_zdr_endpoints(payload: dict) -> dict[str, list[str]]:
    """model id -> the providers that serve it with zero data retention (GET /api/v1/endpoints/zdr, no key)."""
    found: dict[str, list[str]] = {}
    for row in payload.get("data") or []:
        model = row.get("model_id") if isinstance(row, dict) else None
        if isinstance(model, str) and model:
            found.setdefault(model, []).append(str(row.get("provider_name") or ""))
    return found


def parse_deepseek_balance(payload: dict) -> float:
    infos = payload.get("balance_infos") or []
    if not infos:
        raise ValueError("DeepSeek balance response has no balance_infos")
    return float(infos[0]["total_balance"])


def _number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def parse_openrouter_credits(payload: dict) -> float:
    """The account balance from GET /api/v1/credits: total_credits - total_usage."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    credits, usage = _number(data.get("total_credits")), _number(data.get("total_usage"))
    if credits is None or usage is None:
        raise ValueError("OpenRouter credits response has no numeric data.total_credits and data.total_usage")
    return credits - usage


def parse_openrouter_key(payload: dict) -> float | None:
    """What the API key may still spend, from GET /api/v1/key: data.limit_remaining; None when data.limit is null
    (the key has no limit of its own)."""
    data = payload.get("data")
    if not isinstance(data, dict) or "limit" not in data:
        raise ValueError("OpenRouter key response has no data.limit")
    if data["limit"] is None:
        return None
    left = _number(data.get("limit_remaining"))
    if left is None:
        raise ValueError("OpenRouter key response has a data.limit but no numeric data.limit_remaining")
    return left


def openrouter_balance(account: float, key_left: float | None) -> tuple[float, str]:
    """What a wave can spend through the key: the lower of the account balance and the key's limit left, and
    which of the two set it."""
    key_text = "no key limit" if key_left is None else f"key limit left ${key_left:.2f}"
    if key_left is not None and key_left < account:
        return key_left, f"key limit (account ${account:.2f}, {key_text})"
    return account, f"account balance (account ${account:.2f}, {key_text})"


class History(NamedTuple):
    """Usable finished trial records under a jobs dir. costs: USD per model seat by (kind, model). profiles: per
    trial, the mean (total input, cached input, output) tokens of its model seats, by kind."""
    records: int
    costs: dict[tuple[str, str], list[float]]
    profiles: dict[str, list[tuple[float, float, float]]]


class Estimate(NamedTuple):
    """USD per model seat in one trial (None when unknown) and where it came from."""
    usd: float | None
    source: str
    uncertainty: float = 1.0


def validate_profiles(profiles: object) -> None:
    """A flat list of selectors; level, arm and role (seat name) may be omitted as wildcards."""
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("token_profiles must be a nonempty list")
    required = {"kind", "miss", "hit", "out", "source", "uncertainty"}
    optional = {"level", "arm", "role", "n"}
    seen = set()
    for index, row in enumerate(profiles):
        where = f"token_profiles[{index}]"
        if not isinstance(row, dict) or required - row.keys() or row.keys() - required - optional:
            raise ValueError(f"{where}: missing or unknown fields")
        for key in ("kind", "source", "level", "arm", "role"):
            if key in row and (not isinstance(row[key], str) or not row[key].strip()):
                raise ValueError(f"{where}.{key}: expected nonempty text")
        for key in ("miss", "hit", "out", "uncertainty"):
            value = row[key]
            try:
                valid = _number(value) is not None and math.isfinite(value) and value >= (key == "uncertainty")
            except OverflowError:
                valid = False
            if not valid:
                bound = "factor >= 1" if key == "uncertainty" else "tokens >= 0"
                raise ValueError(f"{where}.{key}: expected finite {bound}")
        if not any(row[key] for key in ("miss", "hit", "out")):
            raise ValueError(f"{where}: all token counts are zero")
        if "n" in row and (type(row["n"]) is not int or row["n"] < 1):
            raise ValueError(f"{where}.n: expected positive integer")
        selector = tuple(row.get(key) for key in ("kind", "level", "arm", "role"))
        if selector in seen:
            raise ValueError(f"{where}: duplicate selector")
        seen.add(selector)


def declared_profile(profiles: list[dict], kind: str | None, level: str | None,
                     arm: str | None, role: str | None) -> dict | None:
    matches = [row for row in profiles if row["kind"] == kind and all(
        key not in row or row[key] == value for key, value in (("level", level), ("arm", arm), ("role", role)))]
    # More specific wins; ties prefer level, then arm, then role.
    return max(matches, key=lambda row: (sum(key in row for key in ("level", "arm", "role")),
                                        "level" in row, "arm" in row, "role" in row), default=None)


def kind_of(label: str) -> str:
    """Probe kind of a task label: the label up to its first '-' (probe.verify-inbox -> probe.verify)."""
    return label.split("-", 1)[0]


def _instant(text) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC)


def _trial_kind(result: dict) -> str | None:
    """Kind of a history trial: the task directory's parent is its label."""
    config = result.get("config") or {}
    path = (config.get("task") or {}).get("path") or (result.get("task_id") or {}).get("path")
    label = Path(path).parent.name if path else ""
    return kind_of(label) if label else None


def _history_seat_cost(model: str, usage: dict, tokens: dict[str, int], started: datetime | None) -> float | None:
    """Charge for a seat with tokens: price DeepSeek directly and use OpenRouter's nonzero recorded cost."""
    if is_deepseek_direct(model):
        return deepseek_cost(tokens, started) if started is not None and usage.get("n_input_tokens") is not None else None
    recorded = _number(usage.get("cost_usd"))
    if recorded is None or recorded == 0:
        return None
    return recorded


def read_history(jobs_dir: Path) -> History:
    """Read finished, exception-free trials with model tokens. Join model seats to participants by seat."""
    history = History(0, {}, {})
    paths = sorted(jobs_dir.glob("*/*/result.json")) if jobs_dir.is_dir() else []
    records = 0
    for path in paths:
        try:
            result = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(result, dict) or not result.get("finished_at"):
            continue
        seats = result.get("seat_results") or []
        if result.get("exception_info") is not None or any(
            part.get("exception_info") is not None
            for parts in (seats, result.get("step_results") or []) for part in parts
        ):
            continue
        kind = _trial_kind(result) or "unidentified"
        started = _instant(result.get("started_at") or result["finished_at"])
        models = {row.get("seat"): row.get("model_name") for row in (result.get("config") or {}).get("participants")
                  or [] if row.get("name") != "oracle" and row.get("model_name")}
        used = []
        has_tokens = False
        for seat in seats:
            model, usage = models.get(seat.get("seat")), seat.get("agent_result") or {}
            if not model:
                continue
            tokens = usage_tokens(usage)
            if sum(tokens.values()) == 0:
                continue
            has_tokens = True
            amount = _history_seat_cost(model, usage, tokens, started)
            if amount is not None:
                history.costs.setdefault((kind, model), []).append(amount)
            if usage.get("n_input_tokens") is not None:
                used.append((float(usage["n_input_tokens"]), float(usage.get("n_cache_tokens") or 0),
                             float(usage.get("n_output_tokens") or 0)))
        if has_tokens:
            records += 1
        if used and kind != "unidentified":
            mean = tuple(sum(row[i] for row in used) / len(used) for i in range(3))
            history.profiles.setdefault(kind, []).append(mean)
    return history._replace(records=records)


def token_profile(history: History, kind: str) -> tuple[dict[str, float], int] | None:
    """Median total input, cached input and output tokens per model seat across trials of a kind (all models); the
    uncached input is the median total less the median cached, so cached tokens count once. None without trials."""
    rows = history.profiles.get(kind)
    if not rows:
        return None
    total, cached, out = (statistics.median(row[i] for row in rows) for i in range(3))
    return {"hit": cached, "miss": max(0.0, total - cached), "out": out}, len(rows)


def _shown(moment: datetime) -> str:
    return moment.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M ET")


class Catalog:
    """OpenRouter list prices, read once on first use: a cache younger than CATALOG_MAX_AGE_S, else fetch_catalog()
    (then cached with its fetch time), else an older cache. An ordinary fetch error leaves no prices; SystemExit
    propagates."""

    CACHE = CATALOG_CACHE
    parse = staticmethod(parse_openrouter_models)

    def __init__(self, cache_dir: Path, fetch_catalog: Callable[[], dict], now: datetime):
        self.path = cache_dir / self.CACHE
        self.fetch_catalog = fetch_catalog
        self.now = now
        self.status: str | None = None
        self._prices: dict | None = None

    def prices(self) -> dict | None:
        if self.status is None:
            self._prices, self.status = self._load()
        return self._prices

    def _cached(self) -> tuple[datetime, dict] | None:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("payload"), dict):
            return None
        fetched = _instant(data.get("fetched_at"))
        try:
            priced = self.parse(data["payload"])
        except (AttributeError, TypeError):
            return None
        if fetched is None or not priced:
            return None
        return fetched, data["payload"]

    def _store(self, payload: dict) -> str:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=self.path.parent) as directory:
                temporary = Path(directory) / self.path.name
                temporary.write_text(json.dumps({"fetched_at": self.now.astimezone(UTC).isoformat(), "payload": payload}))
                os.replace(temporary, self.path)
        except OSError as exc:
            return f"not cached ({type(exc).__name__})"
        return f"cached at {self.path}"

    def _load(self) -> tuple[dict | None, str]:
        cached = self._cached()
        if cached and (self.now - cached[0]).total_seconds() < CATALOG_MAX_AGE_S:
            return self.parse(cached[1]), f"cache fetched {_shown(cached[0])} ({self.path})"
        try:
            payload = self.fetch_catalog()
            prices = self.parse(payload)
            if not prices:
                raise ValueError("empty payload")
        except (HTTPException, OSError, AttributeError, RuntimeError, TypeError, ValueError) as exc:
            if cached:
                return (self.parse(cached[1]),
                        f"stale cache fetched {_shown(cached[0])} ({self.path}); refresh failed: {type(exc).__name__}")
            return None, f"unavailable ({type(exc).__name__}); no cache at {self.path}"
        return prices, f"fetched {_shown(self.now)}, {self._store(payload)}"


class ZdrList(Catalog):
    """OpenRouter's zero-data-retention endpoints (public list, no key), cached and offline-safe like the catalog."""

    CACHE = ZDR_CACHE
    parse = staticmethod(parse_zdr_endpoints)


def zdr_lines(models: list, cache_dir: Path, fetch_zdr: Callable[[], dict], now: datetime | None = None) -> list[str]:
    """One warning per OpenRouter model without a zero-data-retention endpoint: the account has ZDR on, so such a
    model answers 404 (Qwen3.8 Flash and the ':free' models on 2026-11-23). No OpenRouter model: no lines, no read."""
    wanted = [model for model in dict.fromkeys(models) if isinstance(model, str) and model.startswith("openrouter/")]
    if not wanted:
        return []
    feed = ZdrList(cache_dir, fetch_zdr, now or datetime.now(UTC))
    endpoints = feed.prices()
    if endpoints is None:
        return [f"  zero-data-retention list: {feed.status}; not checked"]
    return [f"  zero-data-retention list: {feed.status}"] + [
        f"  ZDR warning: {model}: {ZDR_WARNING}" for model in wanted
        if model.removeprefix("openrouter/") not in endpoints]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _price_tokens(model: str, tokens: dict, catalog: Catalog, source: str, uncertainty: float = 1.0) -> Estimate:
    if is_deepseek_direct(model):
        usd = PEAK_FACTOR_MAX * sum(tokens[key] * OFF_PEAK[key] for key in tokens) / 1e6
        return Estimate(usd, f"{source}, DeepSeek peak rate", uncertainty)
    prices = catalog.prices()
    if prices is None:
        return Estimate(None, "unknown (no catalog)")
    try:
        return Estimate(openrouter_cost(model, tokens, prices), source, uncertainty)
    except (KeyError, ValueError):
        return Estimate(None, "unknown (not in catalog)")


def _profile_estimate(model: str, kind: str | None, history: History, catalog: Catalog) -> Estimate:
    if kind is None:
        return Estimate(None, "unknown (no task kind, no history)")
    profile = token_profile(history, kind)
    if profile is None:
        return Estimate(None, f"unknown (no history, no {kind} token profile)")
    return _price_tokens(model, profile[0], catalog, f"price-estimate {kind}")


def seat_estimate(model: str | None, kind: str | None, history: History, catalog: Catalog, smoke: bool,
                  declared: dict | None = None) -> Estimate:
    """Prefer same-kind history, then a declared profile, then pooled history."""
    if model is None:
        return Estimate(0.0, "free")
    if smoke:
        return Estimate(0.0, "free") if model.endswith(":free") else Estimate(None, "unknown (smoke: not free)")
    same = history.costs.get((kind, model)) if kind else None
    if same and len(same) >= HISTORY_MIN_SEATS:
        return Estimate(_mean(same), f"history {kind} n={len(same)}")
    priced = _profile_estimate(model, kind, history, catalog)
    if priced.usd is not None:
        return priced
    if declared is not None and not token_profile(history, kind):
        tokens = {key: declared[key] for key in ("hit", "miss", "out")}
        return _price_tokens(model, tokens, catalog, f"declared profile ({declared['source']})",
                             declared["uncertainty"])
    pooled = {seen: amounts for (seen, name), amounts in history.costs.items() if name == model}
    if pooled:
        amounts = [amount for values in pooled.values() for amount in values]
        return Estimate(_mean(amounts), f"history any kind ({', '.join(sorted(pooled))}) n={len(amounts)}")
    return priced


def _manifest_of(task_set: dict, repo: Path, jobs_dir: Path) -> dict | None:
    """The set's manifest in the plan repo, else the same set beside the history jobs dir
    (<jobs>/../tasks/<tasks_dir name>/<manifest name>)."""
    beside = jobs_dir.parent / "tasks" / Path(task_set["tasks_dir"]).name / Path(task_set["manifest"]).name
    for path in (repo / task_set["manifest"], beside):
        try:
            manifest = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(manifest, dict) and isinstance(manifest.get("tasks"), dict):
            return manifest
    return None


def cell_layouts(plan: dict, repo: Path, jobs_dir: Path) -> dict[tuple, tuple]:
    """(kind, seats, scripted seats, level) per (task set, instance, arm) from the manifests."""
    found = {}
    for index, task_set in enumerate(plan["task_sets"]):
        for entry in ((_manifest_of(task_set, repo, jobs_dir) or {}).get("tasks") or {}).values():
            label = Path(entry.get("path") or "").parent.name or entry.get("label") or ""
            seats = entry.get("seats") if isinstance(entry.get("seats"), list) else None
            scripted = (entry.get("build") or {}).get("scripted_seats")
            found[(index, entry.get("instance"), entry.get("condition"))] = (
                kind_of(label) if label else None, seats, scripted if isinstance(scripted, list) else None,
                (entry.get("build") or {}).get("difficulty"))
    return found


def cell_seat_models(cell: dict, seats: list | None, scripted: list | None) -> list[tuple[str | None, str]]:
    """Seat name and model for each tested seat. Unknown rosters keep the legacy one-seat estimate."""
    skipped = scripted if scripted is not None else cell.get("scripted") or []
    tested = [seat for seat in sorted(seats or []) if seat not in skipped]
    models = cell.get("assignment")
    if models:
        return list(zip(tested or [None] * len(models), models, strict=True))
    if cell.get("model") is None:
        return []
    return [(seat, cell["model"]) for seat in tested or [None]]


def _tally(order: list[dict], layouts: dict, estimate) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for cell in order:
        kind, seats, scripted, level = layouts.get((cell["task_set"], cell["instance"], cell["arm"]),
                                                    (None, None, None, None))
        models = cell_seat_models(cell, seats, scripted)
        grouped: dict[str | None, list[Estimate]] = {}
        for seat, model in models or [(None, None)]:
            grouped.setdefault(model, []).append(estimate(model, kind, level, cell["arm"], seat))
        for model, estimates in grouped.items():
            row = rows.setdefault(model or ORACLE, {"trials": 0, "seats": 0, "priced": 0, "usd": 0.0,
                                                   "low": 0.0, "high": 0.0, "complete_usd": 0.0, "sources": {}})
            row["trials"] += 1
            row["seats"] += len(estimates) if model else 0
            for source in dict.fromkeys(found.source for found in estimates):
                row["sources"][source] = row["sources"].get(source, 0) + 1
            if all(found.usd is not None for found in estimates):
                row["priced"] += 1
                row["complete_usd"] += sum(found.usd or 0.0 for found in estimates)
            for found in estimates:
                if found.usd is not None:
                    row["usd"] += found.usd
                    row["low"] += found.usd / found.uncertainty
                    row["high"] += found.usd * found.uncertainty
    return dict(sorted(rows.items()))


def _row_line(model: str, row: dict) -> str:
    sources = row["sources"]
    shown = "; ".join(source if len(sources) == 1 else f"{source} x{count}" for source, count in sources.items())
    head = f"  {model}: {row['trials']} trials, {row['seats']} model seats"
    if not row["priced"]:
        return f"{head}, cost UNKNOWN [{shown}]"
    missing = row["trials"] - row["priced"]
    tail = f", {missing} trials UNKNOWN" if missing else ""
    return f"{head}, ${row['complete_usd'] / row['priced']:.6f}/trial, total ${row['usd']:.4f}{tail} [{shown}]"


def _profile_lines(history: History, kinds: list[str]) -> list[str]:
    lines = []
    for kind in kinds:
        found = token_profile(history, kind)
        if found:
            tokens, count = found
            lines.append(f"  token profile {kind} (median per model seat, {count} trials): "
                         f"{tokens['hit'] + tokens['miss']:.0f} input ({tokens['hit']:.0f} cached, "
                         f"{tokens['miss']:.0f} uncached), {tokens['out']:.0f} output")
    return lines


def _range_text(rows: dict[str, dict], modal: float, divisor: int = 1, precision: int = 4) -> str:
    if any(row["priced"] < row["trials"] for row in rows.values()):
        return "UNKNOWN (some seats lack a price)"
    totals = [(sum(row[key] for row in rows.values()) + modal) / divisor for key in ("low", "usd", "high")]
    return " / ".join(f"${value:.{precision}f}" for value in totals)


def _detail_lines(plan: dict, order: list[dict], layouts: dict, estimate,
                  stages: list[list[dict]] | None) -> list[str]:
    """Grouped experimental cells and cumulative stage totals, excluding retries."""
    modal_rate = MODAL_PER_TRIAL_USD if plan["make_jobs"]["env"] == "modal" else 0.0
    groups: dict[tuple, list[dict]] = {}
    for cell in order:
        choice = tuple(cell.get("assignment") or [cell["model"] or ORACLE])
        groups.setdefault((cell["task_set"], cell["arm"], choice), []).append(cell)
    lines = ["  by cell (set, arm, model roster; low / expected / high, includes sandbox):"]
    for (index, arm, choice), cells in sorted(groups.items()):
        count = len(cells)
        rows = _tally(cells, layouts, estimate)
        modal = modal_rate * count
        lines.append(f"    set {index} {arm} {', '.join(choice)}: {count} trials; "
                     f"mean/trial {_range_text(rows, modal, count, 6)}; cell total {_range_text(rows, modal)}")
    if stages is not None:
        lines.append("  by stage (low / expected / high, includes sandbox, excludes retries):")
        cumulative = []
        for index, cells in enumerate(stages, 1):
            cumulative.extend(cells)
            added = _range_text(_tally(cells, layouts, estimate), modal_rate * len(cells))
            total = _range_text(_tally(cumulative, layouts, estimate), modal_rate * len(cumulative))
            lines.append(f"    stage {index}: {len(cells)} trials, added {added}; "
                         f"cumulative {len(cumulative)} trials, {total}")
    return lines


def _total_lines(plan: dict, trials: int, rows: dict[str, dict]) -> list[str]:
    modal = MODAL_PER_TRIAL_USD * trials if plan["make_jobs"]["env"] == "modal" else 0.0
    openrouter = sum(row["usd"] for model, row in rows.items() if model.startswith("openrouter/"))
    deepseek = sum(row["usd"] for model, row in rows.items() if is_deepseek_direct(model))
    spend = f"OpenRouter ${openrouter:.4f}"
    if any(is_deepseek_direct(model) for model in rows):
        spend += f" + DeepSeek direct ${deepseek:.4f}"
    if modal:
        lines = [
            (f"  Modal: {trials} trials x ${MODAL_PER_TRIAL_USD} (measured 2026-11-24, docs/NEXT-WAVE.md) = "
             f"${modal:.4f}")
        ]
    else:
        lines = [f"  {plan['make_jobs']['env']}: {trials} trials, $0 sandbox cost"]
    expected = openrouter + deepseek + modal
    unknown = {model: row["trials"] - row["priced"] for model, row in rows.items() if row["priced"] < row["trials"]}
    if unknown:
        names = ", ".join(f"{model} ({count} trials)" for model, count in unknown.items())
        lines.append(f"  expected: at least ${expected:.4f} = {spend} + Modal ${modal:.4f}; UNKNOWN cost for "
                     f"{sum(unknown.values())} model trials: {names}")
    else:
        lines.append(f"  expected: ${expected:.4f} = {spend} + Modal ${modal:.4f}")
    if plan.get("token_profiles") and not plan.get("smoke"):
        lines.append(f"  low / expected / high total: {_range_text(rows, modal)}")
    cap = float(plan["budget"]["cap_usd"])
    note = "; expected model spend is above the cap" if openrouter + deepseek > cap else ""
    if plan.get("token_profiles") and not plan.get("smoke") and sum(row["high"] for row in rows.values()) > cap:
        note += "; high model estimate is above the cap"
    lines.append(f"  maximum: ${cap + modal:.4f} = model cap ${cap:.4f} + Modal ${modal:.4f}{note}")
    return lines


def forecast_lines(plan: dict, order: list[dict], repo: Path, history_jobs: Path | None, *,
                   fetch_catalog: Callable[[], dict], now: datetime | None = None,
                   stages: list[list[dict]] | None = None) -> list[str]:
    """The dry-run cost block, title first. history_jobs None means <repo>/.flotilla-local/jobs. The catalog cache is
    <repo>/.flotilla-local/cache. A smoke plan (plan["smoke"]) reads neither history nor catalog: the oracle agent and
    ':free' models cost $0, anything else is unknown."""
    repo = Path(repo)
    smoke = bool(plan.get("smoke"))
    jobs_dir = Path(history_jobs) if history_jobs is not None else repo / ".flotilla-local" / "jobs"
    history = History(0, {}, {}) if smoke else read_history(jobs_dir)
    catalog = Catalog(repo / ".flotilla-local" / "cache", fetch_catalog, now or datetime.now(UTC))
    layouts = {} if smoke else cell_layouts(plan, repo, jobs_dir)
    cache: dict[tuple, Estimate] = {}

    def estimate(model: str | None, kind: str | None, level: str | None, arm: str, seat: str | None) -> Estimate:
        key = (model, kind, level, arm, seat)
        if key not in cache:
            declared = declared_profile(plan.get("token_profiles", []), kind, level, arm, seat)
            cache[key] = seat_estimate(model, kind, history, catalog, smoke, declared)
        return cache[key]

    rows = _tally(order, layouts, estimate)
    if smoke:
        lines = [f"cost forecast {plan['name']}: smoke, free subjects only; history and catalog not read"]
    else:
        lines = [f"cost forecast {plan['name']}: history {jobs_dir} ({history.records} usable finished trial records)"]
    lines += [_row_line(model, row) for model, row in rows.items()]
    kinds = sorted({key[1] for key, found in cache.items() if key[1] and found.source.startswith("price-")})
    lines += _profile_lines(history, kinds)
    if not smoke:
        lines.append(f"  catalog: {catalog.status or 'not read (no model needed list prices)'}")
    lines += _total_lines(plan, len(order), rows)
    if plan.get("token_profiles") and not smoke:
        lines += _detail_lines(plan, order, layouts, estimate, stages)
    return lines
