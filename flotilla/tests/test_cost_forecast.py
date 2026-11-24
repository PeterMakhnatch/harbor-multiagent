"""The dry-run cost forecast in pricing.py, on local fixture history, manifests and catalogs (no network)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import FLOTILLA, load

PRICING = load(FLOTILLA / "pricing.py", "flotilla_test_cost_forecast_pricing")
NOW = datetime(2026, 11, 24, 15, 0, tzinfo=UTC)
ONE, TWO, NEW, OTHER = "openrouter/a/one", "openrouter/b/two", "openrouter/c/new", "openrouter/d/other"
DIRECT = "deepseek/deepseek-flash"


def usage(total: int, cached: int, out: int, cost: float | None = None) -> dict:
    return {"n_input_tokens": total, "n_cache_tokens": cached, "n_output_tokens": out, "cost_usd": cost}


def trial(jobs: Path, name: str, label: str, seats: list[tuple[str, str | None, dict | None]], *,
          finished: bool = True, started: str = "2026-11-24T12:00:00+00:00", failed: str | None = None) -> None:
    """One history trial; a seat with model None is a scripted oracle. seat_results come in reverse seat order so
    only a join by seat ID gets the models right."""
    participants = [{"seat": seat, "name": "oracle" if model is None else "terminus-2", "model_name": model}
                    for seat, model, _usage in seats]
    empty = {"n_input_tokens": None, "n_cache_tokens": None, "n_output_tokens": None, "cost_usd": None}
    results = [{"seat": seat, "agent_result": spent or empty} for seat, _model, spent in reversed(seats)]
    path = f"/lab/.flotilla-local/tasks/set/{label}/t-{name}"
    record = {"config": {"task": {"path": path}, "participants": participants}, "task_id": {"path": path},
              "seat_results": results, "started_at": started,
              "finished_at": "2026-11-24T12:05:00+00:00" if finished else None}
    if failed is not None:
        exception = {"exception_type": "AgentTimeoutError"}
        if failed == "trial":
            record["exception_info"] = exception
        elif failed == "step":
            record["step_results"] = [{"exception_info": exception}]
        else:
            next(seat for seat in results if seat["seat"] == failed)["exception_info"] = exception
    target = jobs / f"job-{name}" / f"t-{name}__abc" / "result.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(record))


def solo(jobs: Path, name: str, label: str, model: str, spent: dict, **kwargs) -> None:
    trial(jobs, name, label, [("p1", model, spent), ("p2", None, None), ("p3", None, None)], **kwargs)


def manifest(root: Path, set_name: str, label: str, instances: list[int], arms: list[str],
             seats: list[str] | None = None, scripted: list[str] | None = None) -> None:
    seats = seats or ["p1", "p2", "p3"]
    scripted = ["p2", "p3"] if scripted is None else scripted
    tasks = {f"flotilla/t-{i}{arm}": {"path": f"{label}/t-{i}{arm}", "instance": i, "condition": arm, "label": label,
                                      "seats": seats, "build": {"scripted_seats": scripted}}
             for i in instances for arm in arms}
    target = root / ".flotilla-local" / "tasks" / set_name / "manifest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"format": 1, "version": 1, "tasks": tasks}))


def make_plan(models: list[str | None] | None = None, *, sets: tuple = (("s1", [1, 2], ["a"]),),
              env: str = "modal", cap: float = 1.0, assignments: list[list[str]] | None = None) -> dict:
    plan = {"name": "fc", "task_sets": [{"tasks_dir": f".flotilla-local/tasks/{name}",
                                         "manifest": f".flotilla-local/tasks/{name}/manifest.json",
                                         "instances": instances, "arms": arms} for name, instances, arms in sets],
            "make_jobs": {"env": env}, "budget": {"cap_usd": cap}}
    if assignments is not None:
        plan["seat_assignments"] = assignments
    else:
        plan["models"] = models
    return plan


def order_of(plan: dict) -> list[dict]:
    cells = []
    for index, task_set in enumerate(plan["task_sets"]):
        for instance in task_set["instances"]:
            for arm in task_set["arms"]:
                for choice in plan.get("seat_assignments") or plan["models"]:
                    cell = {"task_set": index, "instance": instance, "arm": arm}
                    if isinstance(choice, list):
                        cell.update(model=choice[0], assignment=list(choice))
                    else:
                        cell["model"] = choice
                    cells.append(cell)
    return cells


class Fetch:
    """A fetch_catalog fake that counts calls and returns a payload or raises."""

    def __init__(self, payload: dict | None = None, error: BaseException | None = None):
        self.payload, self.error, self.calls = payload, error, 0

    def __call__(self) -> dict:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.payload


def catalog(*rows: tuple[str, str, str, str | None]) -> dict:
    data = []
    for model, prompt, completion, cached in rows:
        pricing = {"prompt": prompt, "completion": completion}
        if cached is not None:
            pricing["input_cache_read"] = cached
        data.append({"id": model.removeprefix("openrouter/"), "pricing": pricing})
    return {"data": data}


def forecast(plan: dict, repo: Path, jobs: Path, fetch: Fetch, now: datetime = NOW) -> list[str]:
    return PRICING.forecast_lines(plan, order_of(plan), repo, jobs, fetch_catalog=fetch, now=now)


def row(lines: list[str], model: str) -> str:
    found = [line for line in lines if line.startswith(f"  {model}: ")]
    assert len(found) == 1, lines
    return found[0]


def line(lines: list[str], prefix: str) -> str:
    found = [item for item in lines if item.startswith(f"  {prefix}")]
    assert len(found) == 1, lines
    return found[0]


@pytest.fixture
def where(tmp_path: Path) -> tuple[Path, Path]:
    repo, jobs = tmp_path / "repo", tmp_path / "lab" / ".flotilla-local" / "jobs"
    jobs.mkdir(parents=True)
    repo.mkdir()
    return repo, jobs


def test_kind_is_the_label_prefix_before_the_first_dash():
    assert PRICING.kind_of("probe.verify-inbox") == "probe.verify"
    assert PRICING.kind_of("probe.audit-b0") == "probe.audit"
    assert PRICING.kind_of("probe.verify-readfirst") == "probe.verify"
    assert PRICING.kind_of("probe.audit") == "probe.audit"
    assert PRICING.kind_of("xprobe.verify-inbox") == "xprobe.verify"
    assert PRICING.MODAL_PER_TRIAL_USD == 0.0021


def test_five_same_kind_seats_outrank_list_price_and_other_kinds(where):
    repo, jobs = where
    for index, cost in enumerate((0.002, 0.004, 0.003, 0.003, 0.003)):
        solo(jobs, f"v{index}", "probe.verify-readfirst", ONE, usage(10, 0, 1, cost))
    solo(jobs, "unfinished", "probe.verify", ONE, usage(10, 0, 1, 9.0), finished=False)
    solo(jobs, "a1", "probe.audit", ONE, usage(10, 0, 1, 0.5))
    solo(jobs, "a2", "probe.audit-b0", TWO, usage(10, 0, 1, 0.01))
    solo(jobs, "c1", "probe.conformity", TWO, usage(10, 0, 1, 0.03))
    manifest(repo, "s1", "probe.verify-inbox", [1, 2], ["a"])
    fetch = Fetch(catalog((ONE, "0.000001", "0.000002", None)))
    lines = forecast(make_plan([ONE], env="docker"), repo, jobs, fetch)
    assert lines[0] == f"cost forecast fc: history {jobs} (8 usable finished trial records)"
    assert row(lines, ONE).endswith("2 trials, 2 model seats, $0.003000/trial, total $0.0060 "
                                    "[history probe.verify n=5]")
    assert fetch.calls == 0
    assert line(lines, "catalog:") == "  catalog: not read (no model needed list prices)"
    assert not (repo / ".flotilla-local" / "cache").exists()

    lines = forecast(make_plan([ONE, TWO], env="docker"), repo, jobs, fetch)
    assert row(lines, TWO).endswith("2 trials, 2 model seats, $0.020000/trial, total $0.0400 "
                                    "[history any kind (probe.audit, probe.conformity) n=2]")
    assert fetch.calls == 1
    assert line(lines, "docker:") == "  docker: 4 trials, $0 sandbox cost"
    assert line(lines, "expected:") == "  expected: $0.0460 = OpenRouter $0.0460 + Modal $0.0000"
    assert line(lines, "maximum:") == "  maximum: $1.0000 = model cap $1.0000 + Modal $0.0000"


def test_same_kind_list_price_outranks_sparse_and_other_kind_history(where):
    repo, jobs = where
    for index in range(4):
        solo(jobs, f"v{index}", "probe.verify", ONE, usage(1000, 400, 100, 0.004))
    solo(jobs, "a1", "probe.audit", ONE, usage(30_000, 0, 2000, 0.45))
    solo(jobs, "a2", "probe.audit", TWO, usage(30_000, 0, 2000, 0.45))
    manifest(repo, "s1", "probe.verify", [1, 2], ["a"])
    fetch = Fetch(catalog(*((model, "0.000001", "0.000002", "0.0000001") for model in (ONE, TWO))))
    lines = forecast(make_plan([ONE, TWO]), repo, jobs, fetch)
    assert line(lines, "token profile") == ("  token profile probe.verify (median per model seat, 4 trials): "
                                            "1000 input (400 cached, 600 uncached), 100 output")
    for model in (ONE, TWO):
        assert row(lines, model).endswith("$0.000840/trial, total $0.0017 [price-estimate probe.verify]")


def test_history_excludes_trial_exceptions_and_zero_token_seats(where):
    repo, jobs = where
    label = "probe.verify"
    for failed in ("trial", "step", "p2"):
        solo(jobs, f"failed-{failed}", label, ONE, usage(9000, 0, 9000, 0.5), failed=failed)
    solo(jobs, "empty", label, ONE, usage(0, 0, 0, 0.0))
    trial(jobs, "mixed", label, [("p1", ONE, usage(1000, 400, 100, 0.004)),
                                 ("p2", ONE, usage(0, 0, 0, 0.9))])
    manifest(repo, "s1", label, [1, 2], ["a"])
    fetch = Fetch(catalog((NEW, "0.000001", "0.000002", "0.0000001")))
    lines = forecast(make_plan([ONE, NEW]), repo, jobs, fetch)
    assert lines[0] == f"cost forecast fc: history {jobs} (1 usable finished trial records)"
    assert row(lines, ONE).endswith("$0.004000/trial, total $0.0080 [history any kind (probe.verify) n=1]")
    assert row(lines, NEW).endswith("$0.000840/trial, total $0.0017 [price-estimate probe.verify]")
    assert line(lines, "token profile") == ("  token profile probe.verify (median per model seat, 1 trials): "
                                            "1000 input (400 cached, 600 uncached), 100 output")


def test_price_estimate_uses_median_tokens_of_the_same_kind_only(where):
    repo, jobs = where
    solo(jobs, "v1", "probe.verify", ONE, usage(1000, 400, 100, 0.001))
    solo(jobs, "v2", "probe.verify-inbox", TWO, usage(3000, 1000, 300, 0.001))
    solo(jobs, "v3", "probe.verify", ONE, usage(2000, 800, 900, 0.001))
    trial(jobs, "v4", "probe.verify", [("p1", TWO, usage(4000, 0, 50, 0.001)),
                                       ("p2", ONE, usage(6000, 2000, 150, 0.001)), ("p3", None, None)])
    solo(jobs, "a1", "probe.audit", ONE, usage(1_000_000, 0, 1_000_000, 0.001))
    manifest(repo, "s1", "probe.verify-readfirst", [1, 2], ["a"])
    manifest(repo, "s2", "probe.conformity", [1], ["a"])
    plan = make_plan([NEW], sets=(("s1", [1, 2], ["a"]), ("s2", [1], ["a"])))
    fetch = Fetch(catalog((NEW, "0.000001", "0.000002", "0.0000001")))
    lines = forecast(plan, repo, jobs, fetch)
    assert line(lines, "token profile") == ("  token profile probe.verify (median per model seat, 4 trials): "
                                            "2500 input (900 cached, 1600 uncached), 200 output")
    assert row(lines, NEW).endswith("3 trials, 3 model seats, $0.002090/trial, total $0.0042, 1 trials UNKNOWN "
                                    "[price-estimate probe.verify x2; unknown (no history, no probe.conformity token "
                                    "profile) x1]")
    assert fetch.calls == 1
    assert line(lines, "Modal:") == "  Modal: 3 trials x $0.0021 (measured 2026-11-24, docs/NEXT-WAVE.md) = $0.0063"
    assert line(lines, "expected:") == ("  expected: at least $0.0105 = OpenRouter $0.0042 + Modal $0.0063; UNKNOWN "
                                        f"cost for 1 model trials: {NEW} (1 trials)")
    assert line(lines, "maximum:") == "  maximum: $1.0063 = model cap $1.0000 + Modal $0.0063"


def test_a_model_missing_from_the_catalog_does_not_hide_the_others(where):
    repo, jobs = where
    solo(jobs, "v1", "probe.verify", ONE, usage(1000, 0, 100, 0.001))
    manifest(repo, "s1", "probe.verify", [1, 2], ["a"])
    fetch = Fetch(catalog((NEW, "0.000001", "0.000002", None)))
    lines = forecast(make_plan([NEW, OTHER, ONE]), repo, jobs, fetch)
    assert row(lines, NEW).endswith("$0.001200/trial, total $0.0024 [price-estimate probe.verify]")
    assert row(lines, OTHER).endswith("2 trials, 2 model seats, cost UNKNOWN [unknown (not in catalog)]")
    assert row(lines, ONE).endswith("$0.001000/trial, total $0.0020 [history any kind (probe.verify) n=1]")
    assert line(lines, "expected:") == ("  expected: at least $0.0170 = OpenRouter $0.0044 + Modal $0.0126; UNKNOWN "
                                        f"cost for 2 model trials: {OTHER} (2 trials)")


def test_catalog_cache_is_fresh_then_stale_then_absent_and_offline_safe(where, tmp_path):
    repo, jobs = where
    solo(jobs, "v1", "probe.verify", ONE, usage(1000, 0, 100, 0.001))
    manifest(repo, "s1", "probe.verify", [1], ["a"])
    plan = make_plan([NEW], sets=(("s1", [1], ["a"]),))
    cache = repo / ".flotilla-local" / "cache" / PRICING.CATALOG_CACHE
    fetched = Fetch(catalog((NEW, "0.000001", "0.000002", None)))
    lines = forecast(plan, repo, jobs, fetched)
    assert fetched.calls == 1
    assert line(lines, "catalog:") == f"  catalog: fetched 2026-11-24 10:00 ET, cached at {cache}"
    stored = json.loads(cache.read_text())
    assert stored["fetched_at"] == NOW.isoformat() and stored["payload"] == fetched.payload

    offline = Fetch(error=RuntimeError("offline"))
    lines = forecast(plan, repo, jobs, offline, NOW + timedelta(hours=1))
    assert offline.calls == 0
    assert line(lines, "catalog:") == f"  catalog: cache fetched 2026-11-24 10:00 ET ({cache})"
    assert row(lines, NEW).endswith("$0.001200/trial, total $0.0012 [price-estimate probe.verify]")

    lines = forecast(plan, repo, jobs, offline, NOW + timedelta(days=2))
    assert offline.calls == 1
    assert line(lines, "catalog:") == (f"  catalog: stale cache fetched 2026-11-24 10:00 ET ({cache}); refresh failed: "
                                       "RuntimeError")
    assert row(lines, NEW).endswith("$0.001200/trial, total $0.0012 [price-estimate probe.verify]")
    assert json.loads(cache.read_text()) == stored

    other = tmp_path / "other"
    manifest(other, "s1", "probe.verify", [1], ["a"])
    lines = forecast(plan, other, jobs, Fetch(error=RuntimeError("offline")))
    missing = other / ".flotilla-local" / "cache" / PRICING.CATALOG_CACHE
    assert line(lines, "catalog:") == f"  catalog: unavailable (RuntimeError); no cache at {missing}"
    assert row(lines, NEW).endswith("cost UNKNOWN [unknown (no catalog)]")
    assert not missing.parent.exists()

    missing.parent.mkdir(parents=True)
    missing.write_text("{not json")
    lines = forecast(plan, other, jobs, Fetch(error=RuntimeError("offline")))
    assert line(lines, "catalog:") == f"  catalog: unavailable (RuntimeError); no cache at {missing}"

    with pytest.raises(SystemExit):
        forecast(plan, other, jobs, Fetch(error=SystemExit("tripwire")))


def test_mixed_assignments_count_every_planned_model_seat(where):
    repo, jobs = where
    solo(jobs, "v1", "probe.verify", ONE, usage(10, 0, 1, 0.003))
    solo(jobs, "v2", "probe.verify", TWO, usage(10, 0, 1, 0.001))
    manifest(repo, "s1", "probe.verify", [1, 2], ["a"], seats=["p1", "p2", "p3", "p4"], scripted=["p3", "p4"])
    fetch = Fetch(error=RuntimeError("offline"))
    lines = forecast(make_plan(assignments=[[ONE, ONE], [ONE, TWO]]), repo, jobs, fetch)
    assert row(lines, ONE).endswith("4 trials, 6 model seats, $0.004500/trial, total $0.0180 "
                                    "[history any kind (probe.verify) n=1]")
    assert row(lines, TWO).endswith("2 trials, 2 model seats, $0.001000/trial, total $0.0020 "
                                    "[history any kind (probe.verify) n=1]")
    assert line(lines, "Modal:").endswith("= $0.0084")

    lines = forecast(make_plan([ONE]), repo, jobs, fetch)
    assert row(lines, ONE).endswith("2 trials, 4 model seats, $0.006000/trial, total $0.0120 "
                                    "[history any kind (probe.verify) n=1]")


def test_without_any_manifest_a_trial_has_one_model_seat_and_no_kind(where):
    repo, jobs = where
    solo(jobs, "v1", "probe.verify", ONE, usage(10, 0, 1, 0.003))
    solo(jobs, "a1", "probe.audit", ONE, usage(10, 0, 1, 0.001))
    solo(jobs, "a2", "probe.audit", TWO, usage(1000, 0, 100, 0.001))
    fetch = Fetch(catalog((NEW, "0.000001", "0.000002", None)))
    lines = forecast(make_plan([ONE, NEW]), repo, jobs, fetch)
    assert row(lines, ONE).endswith("2 trials, 2 model seats, $0.002000/trial, total $0.0040 "
                                    "[history any kind (probe.audit, probe.verify) n=2]")
    assert row(lines, NEW).endswith("cost UNKNOWN [unknown (no task kind, no history)]")
    assert fetch.calls == 0


def test_the_manifest_beside_the_history_jobs_sets_the_kind(where):
    repo, jobs = where
    for index in range(5):
        solo(jobs, f"a{index}", "probe.audit-b0", ONE, usage(10, 0, 1, 0.004))
    solo(jobs, "v1", "probe.verify", ONE, usage(10, 0, 1, 0.1))
    manifest(jobs.parent.parent, "s1", "probe.audit-b1", [1, 2], ["a"])
    lines = forecast(make_plan([ONE]), repo, jobs, Fetch())
    assert row(lines, ONE).endswith("$0.004000/trial, total $0.0080 [history probe.audit n=5]")


def test_deepseek_direct_history_is_charged_from_tokens_and_named(where):
    repo, jobs = where
    for index in range(3):
        seat = usage(1_000_000, 400_000, 100_000, 99.0)
        solo(jobs, f"d{index}-off", "probe.verify", DIRECT, seat)
        solo(jobs, f"d{index}-peak", "probe.verify", DIRECT, seat, started="2026-11-24T07:00:00Z")
    solo(jobs, "v1", "probe.verify", ONE, usage(10, 0, 1, 0.001))
    manifest(repo, "s1", "probe.verify", [1, 2], ["a"])
    lines = forecast(make_plan([DIRECT, ONE], cap=0.1), repo, jobs, Fetch())
    assert row(lines, DIRECT).endswith("$0.226800/trial, total $0.4536 [history probe.verify n=6]")
    assert line(lines, "expected:") == ("  expected: $0.4640 = OpenRouter $0.0020 + DeepSeek direct $0.4536 + Modal "
                                        "$0.0084")
    assert line(lines, "maximum:") == ("  maximum: $0.1084 = model cap $0.1000 + Modal $0.0084; expected model spend "
                                       "is above the cap")


def test_smoke_prices_the_oracle_and_free_models_at_zero_without_a_catalog(where):
    repo, jobs = where
    solo(jobs, "v1", "probe.verify", "openrouter/e/free:free", usage(10, 0, 1, 0.5))
    plan = make_plan([None, "openrouter/e/free:free", ONE], env="docker", cap=0.0)
    plan["smoke"] = 1
    tripwire = Fetch(error=SystemExit("smoke must not read the catalog"))
    lines = forecast(plan, repo, jobs, tripwire)
    assert tripwire.calls == 0
    assert lines[0] == "cost forecast fc: smoke, free subjects only; history and catalog not read"
    assert row(lines, PRICING.ORACLE).endswith("2 trials, 0 model seats, $0.000000/trial, total $0.0000 [free]")
    assert row(lines, "openrouter/e/free:free").endswith("2 trials, 2 model seats, $0.000000/trial, total $0.0000 "
                                                         "[free]")
    assert row(lines, ONE).endswith("cost UNKNOWN [unknown (smoke: not free)]")
    assert not any(item.startswith("  catalog:") for item in lines)
    assert line(lines, "maximum:") == "  maximum: $0.0000 = model cap $0.0000 + Modal $0.0000"
