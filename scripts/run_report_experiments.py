"""Reproduce the controlled comparisons cited in reports/REPORT.md."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gameday_lab.cache import CachingClient, SemanticCache
from gameday_lab.client import ResilientClient
from gameday_lab.sim.config import LabConfig, load_config
from gameday_lab.sim.engine import run_scenario


def metrics(cfg: LabConfig, scenario: str, client: ResilientClient) -> dict:
    result = run_scenario(cfg, scenario, client, seed=25).metrics()
    keys = ("availability", "latency_p95_s", "amplification", "retry_ratio", "recovery_s",
            "give_up_reasons", "incompatible_routes", "faq_availability", "cache_hit_ratio",
            "wrong_answers", "wrong_answers_by_reason", "outdated_answers", "stale_served",
            "cost_per_good_turn", "pass")
    return {key: result[key] for key in keys if key in result}


class NoCapabilityFilter(ResilientClient):
    def ladder(self, task: str) -> list[str]:
        return list(self.order)


def main() -> None:
    base = load_config()
    output: dict[str, dict] = {"retry_budget": {}, "attempt_timeout": {},
                               "tool_capability": {}, "cache_threshold": {},
                               "cache_ttl": {}, "cache_stale": {}}

    # Keep the breaker deliberately ineffective to isolate the retry budget.
    for label, disable_budget in (("budget_on", False), ("budget_off", True)):
        settings = dict(base.client)
        settings["breaker"] = {**settings["breaker"], "min_calls": 1_000_000_000}
        settings["max_attempts_per_provider"] = 5
        if disable_budget:
            settings.update(retry_budget_ratio=100, retry_budget_max_tokens=1e9,
                            retry_budget_min_per_s=1e6)
        cfg = replace(base, client=settings)
        for scenario in ("G2_overload_storm", "G3_brownout"):
            output["retry_budget"][f"{label}:{scenario}"] = metrics(
                cfg, scenario, ResilientClient(cfg.provider_infos(), cfg.fallback_order, cfg.client, seed=25))

    for label, timeout in (("normal", base.client["attempt_timeout_s"]), ("30s", 30.0)):
        cfg = replace(base, client={**base.client, "attempt_timeout_s": timeout})
        for scenario in ("G4_slow_region", "G6_provider_outage"):
            output["attempt_timeout"][f"{label}:{scenario}"] = metrics(
                cfg, scenario, ResilientClient(cfg.provider_infos(), cfg.fallback_order, cfg.client, seed=25))

    for label, cls in (("filtered", ResilientClient), ("unfiltered", NoCapabilityFilter)):
        output["tool_capability"][label] = metrics(
            base, "G6_provider_outage", cls(base.provider_infos(), base.fallback_order, base.client, seed=25))

    cache_base = load_config(scenarios_path="configs/scenarios_cache.yaml")
    for label, overrides in (
        ("threshold_0.80", {"similarity_threshold": 0.80}),
        ("threshold_0.92", {"similarity_threshold": 0.92}),
        ("threshold_0.97", {"similarity_threshold": 0.97}),
        ("ttl_30", {"ttl_s": 30}),
        ("ttl_60", {"ttl_s": 60}),
        ("ttl_300", {"ttl_s": 300}),
        ("stale_0", {"max_stale_s": 0}),
    ):
        cfg = replace(cache_base, cache={**cache_base.cache, **overrides})
        client = CachingClient(ResilientClient(cfg.provider_infos(), cfg.fallback_order, cfg.client, seed=25),
                               SemanticCache(**cfg.cache))
        result = run_scenario(cfg, "C1_faq_outage", client, seed=25).metrics()
        fields = ("availability", "faq_availability", "cache_hit_ratio", "wrong_answers",
                  "wrong_answers_by_reason", "outdated_answers", "stale_served",
                  "cost_per_good_turn", "pass")
        part = ("cache_threshold" if label.startswith("threshold") else
                "cache_ttl" if label.startswith("ttl") else "cache_stale")
        output[part][label] = {key: result[key] for key in fields if key in result}

    path = Path("reports/experiments.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
