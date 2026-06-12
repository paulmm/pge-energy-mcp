"""Rate engine: look up effective electricity rates for any plan + provider combination.

Supports time-aware rate lookups: pre-March 2026 PG&E delivery rates and
pre-Feb 2026 CCA generation rates are applied automatically when a date
is provided.
"""

from __future__ import annotations

import json
from pathlib import Path

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
_cache = {}


def _load_json(name: str) -> dict:
    if name not in _cache:
        with open(_CONFIG_DIR / name) as f:
            _cache[name] = json.load(f)
    return _cache[name]


def lookup_rates(schedule: str, provider: str = "PGE_BUNDLED",
                 vintage_year: int = 2016, income_tier: int = 3,
                 date: str = None) -> dict:
    """
    Look up effective electricity rates for a schedule + provider combination.

    For CCA customers: effective_rate = pge_delivery + cca_generation + pcia_vintage
    For bundled PG&E: effective_rate = total_bundled_rate (no PCIA)

    Args:
        schedule: Rate schedule name
        provider: "PGE_BUNDLED" or CCA provider code
        vintage_year: PCIA vintage year (ignored for bundled)
        income_tier: 1 (CARE), 2 (FERA), or 3 (standard)
        date: Optional ISO date string (YYYY-MM-DD). If provided, applies
              historical rate overrides for that date.

    Returns:
        Dict with effective rates, components, BSC, TOU windows, summer months.
    """
    pge_rates = _load_json("pge_rates.json")
    cca_rates = _load_json("cca_rates.json")
    pcia_data = _load_json("pcia_vintages.json")
    nbc_per_kwh = pge_rates.get("nbc", {}).get("per_kwh", 0.0)

    if schedule not in pge_rates["schedules"]:
        raise ValueError(f"Unknown schedule: {schedule}")

    sched = pge_rates["schedules"][schedule]
    is_bundled = provider == "PGE_BUNDLED"

    # Base services charge
    bsc_map = dict(sched.get("base_services_charge_daily", {}))
    tier_key = {1: "tier_1_care", 2: "tier_2_fera", 3: "tier_3_standard"}[income_tier]

    # Get delivery and generation (will be overridden by history if needed)
    delivery = _deep_copy_rates(sched.get("delivery", {}))
    generation = _deep_copy_rates(sched.get("generation", {}))
    total_bundled = _deep_copy_rates(sched.get("total_bundled", {}))

    # CCA generation rates
    cca_gen = {}
    if not is_bundled:
        provider_data = cca_rates["providers"].get(provider)
        if not provider_data:
            raise ValueError(f"Unknown provider: {provider}")
        cca_sched = provider_data["schedules"].get(schedule)
        if not cca_sched:
            raise ValueError(f"Provider {provider} has no rates for schedule {schedule}")
        cca_gen = _deep_copy_rates(cca_sched)

    # Apply historical overrides if date provided
    if date:
        _apply_history(date, schedule, provider, delivery, cca_gen, bsc_map,
                       total_bundled, generation)

    bsc_daily = bsc_map.get(tier_key, bsc_map.get("tier_3_standard", 0.0))

    if is_bundled:
        # Recalculate bundled total from delivery + generation if overridden
        effective = {}
        for season in ["summer", "winter"]:
            if season in total_bundled:
                effective[season] = dict(total_bundled[season])
            elif season in delivery and season in generation:
                effective[season] = {}
                for period in delivery[season]:
                    d = delivery[season][period]
                    g = generation[season].get(period, 0.0)
                    effective[season][period] = round(d + g, 5)
        return {
            "schedule": schedule,
            "provider": provider,
            "vintage_year": None,
            "effective_rates": effective,
            "components": {
                "delivery": delivery,
                "generation": generation,
                "pcia_per_kwh": 0.0,
            },
            "nbc_per_kwh": nbc_per_kwh,
            "base_services_charge_daily": bsc_daily,
            "tou_windows": sched["tou_windows"],
            "summer_months": sched["summer_months"],
        }

    # CCA customer
    pcia_per_kwh = pcia_data["vintages"].get(str(vintage_year), 0.0)

    effective = {}
    for season in ["summer", "winter"]:
        if season not in delivery or season not in cca_gen:
            continue
        effective[season] = {}
        for period in delivery[season]:
            if period.startswith("_"):
                continue
            d = delivery[season][period]
            g = cca_gen[season].get(period, 0.0)
            if isinstance(g, str):
                continue
            effective[season][period] = round(d + g + pcia_per_kwh, 5)

    return {
        "schedule": schedule,
        "provider": provider,
        "vintage_year": vintage_year,
        "effective_rates": effective,
        "components": {
            "delivery": delivery,
            "generation": cca_gen,
            "pcia_per_kwh": pcia_per_kwh,
        },
        "nbc_per_kwh": nbc_per_kwh,
        "base_services_charge_daily": bsc_daily,
        "tou_windows": sched["tou_windows"],
        "summer_months": sched["summer_months"],
    }


def get_effective_rate(schedule: str, provider: str, vintage_year: int,
                       income_tier: int, season: str, period: str,
                       date: str = None) -> float:
    """
    Get a single effective rate for a specific season/period.

    Convenience function for per-interval cost calculation.
    """
    rates = lookup_rates(schedule, provider, vintage_year, income_tier, date)
    return rates["effective_rates"].get(season, {}).get(period, 0.0)


def _apply_history(date: str, schedule: str, provider: str,
                   delivery: dict, cca_gen: dict, bsc_map: dict,
                   total_bundled: dict, generation: dict):
    """Apply historical rate overrides in-place based on date."""
    try:
        history = _load_json("rate_history.json")
    except FileNotFoundError:
        return

    # Apply periods in descending cutoff order so that when several periods
    # match a date, the one with the smallest applies_before (the oldest
    # rate era, closest to the date) is applied last and wins.
    periods = sorted(history.get("periods", []),
                     key=lambda p: p.get("applies_before", ""), reverse=True)
    for period in periods:
        cutoff = period.get("applies_before", "")
        if not cutoff or date >= cutoff:
            continue

        # PG&E delivery overrides
        overrides = period.get("pge_delivery_overrides", {}).get(schedule, {})
        for season in ["summer", "winter"]:
            if season in overrides:
                for p, rate in overrides[season].items():
                    if not p.startswith("_") and isinstance(rate, (int, float)):
                        delivery.setdefault(season, {})[p] = rate
                        # Also update total_bundled if we have generation
                        if season in generation and p in generation.get(season, {}):
                            gen = generation[season][p]
                            total_bundled.setdefault(season, {})[p] = round(rate + gen, 5)

        # BSC overrides
        bsc_override = period.get("bsc_overrides", {}).get(schedule, {})
        bsc_map.update(bsc_override)

        # CCA generation overrides
        cca_overrides = period.get("cca_generation_overrides", {})
        if provider in cca_overrides and schedule in cca_overrides[provider]:
            override = cca_overrides[provider][schedule]
            for season in ["summer", "winter"]:
                if season in override:
                    for p, rate in override[season].items():
                        if not p.startswith("_") and isinstance(rate, (int, float)):
                            cca_gen.setdefault(season, {})[p] = rate


def _deep_copy_rates(d: dict) -> dict:
    """Deep copy rate dict, filtering out _notes and non-rate entries."""
    result = {}
    for season in ["summer", "winter"]:
        if season in d:
            result[season] = {k: v for k, v in d[season].items()
                              if not k.startswith("_") and isinstance(v, (int, float))}
    return result


class RateCache:
    """Date-aware rate lookups for one plan, cached by date.

    Replaces the cache-by-date pattern previously duplicated across
    compare.py, trueup.py, and nem_compare.py.
    """

    def __init__(self, schedule: str, provider: str = "PGE_BUNDLED",
                 vintage_year: int = 2016, income_tier: int = 3,
                 time_aware: bool = True):
        self.schedule = schedule
        self.provider = provider
        self.vintage_year = vintage_year
        self.income_tier = income_tier
        self.time_aware = time_aware
        self.base = lookup_rates(schedule, provider, vintage_year, income_tier)
        self._by_date: dict[str, dict] = {}

    @classmethod
    def from_plan(cls, plan: dict, time_aware: bool = True) -> "RateCache":
        return cls(plan["schedule"], plan.get("provider", "PGE_BUNDLED"),
                   plan.get("vintage_year", 2016), plan.get("income_tier", 3),
                   time_aware=time_aware)

    @property
    def schedule_config(self) -> dict:
        return {"tou_windows": self.base["tou_windows"],
                "summer_months": self.base["summer_months"]}

    def for_date(self, date_str: str) -> dict:
        if not self.time_aware:
            return self.base
        if date_str not in self._by_date:
            self._by_date[date_str] = lookup_rates(
                self.schedule, self.provider, self.vintage_year,
                self.income_tier, date=date_str)
        return self._by_date[date_str]

    @property
    def used_history(self) -> bool:
        return self.time_aware and bool(self._by_date)
