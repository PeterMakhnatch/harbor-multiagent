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
"""

from __future__ import annotations

from datetime import datetime

OFF_PEAK = {"hit": 0.003, "miss": 0.15, "out": 0.60}  # USD per million tokens
PEAK_HOURS_UTC = ((1, 4), (6, 10))


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


def parse_deepseek_balance(payload: dict) -> float:
    infos = payload.get("balance_infos") or []
    if not infos:
        raise ValueError("DeepSeek balance response has no balance_infos")
    return float(infos[0]["total_balance"])


def parse_openrouter_credits(payload: dict) -> float:
    data = payload["data"] if isinstance(payload.get("data"), dict) else payload
    return float(data["total_credits"]) - float(data["total_usage"])
