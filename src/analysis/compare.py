"""Rate plan comparison: compare annual cost across multiple plan configurations.

Supports time-aware rate lookups — applies correct historical rates based on
each interval's date (e.g., pre-March 2026 delivery rates, pre-Feb 2026 PCE).
"""

from __future__ import annotations

from collections import defaultdict
from src.rates.engine import lookup_rates
from src.rates.tou import classify_tou_period
from src.rates.nem import calculate_export_credit


def compare(interval_data: list[dict], plans: list[dict],
            nem_version: str = "NEM2",
            time_aware: bool = True) -> dict:
    """
    Compare annual electricity cost across multiple rate plan configurations.

    Args:
        interval_data: Hourly records from parse_green_button
        plans: List of plan configs, each with:
               {schedule, provider, vintage_year, income_tier}
        nem_version: "NEM2" or "NEM3"
        time_aware: If True, apply historical rate overrides by interval date.
                    If False, use current rates for all intervals.

    Returns:
        Dict with per-plan annual cost, savings vs first plan, TOU breakdown.
    """
    results = []

    for plan in plans:
        cost_result = _calculate_annual_cost(interval_data, plan, nem_version,
                                             time_aware)
        cost_result["plan"] = plan
        results.append(cost_result)

    baseline_cost = results[0]["annual_total"]
    for r in results:
        r["savings_vs_baseline"] = round(baseline_cost - r["annual_total"], 2)

    cheapest = min(results, key=lambda r: r["annual_total"])

    return {
        "plans": results,
        "cheapest_plan": cheapest["plan"],
        "max_savings": round(baseline_cost - cheapest["annual_total"], 2),
        "nem_version": nem_version,
        "time_aware": time_aware,
    }


def _calculate_annual_cost(interval_data: list[dict], plan: dict,
                           nem_version: str, time_aware: bool) -> dict:
    """Calculate annual cost for a single rate plan against interval data."""
    schedule = plan["schedule"]
    provider = plan.get("provider", "PGE_BUNDLED")
    vintage_year = plan.get("vintage_year", 2016)
    income_tier = plan.get("income_tier", 3)

    # Get schedule config for TOU classification (doesn't change with date)
    base_rate_info = lookup_rates(schedule, provider, vintage_year, income_tier)
    schedule_config = {
        "tou_windows": base_rate_info["tou_windows"],
        "summer_months": base_rate_info["summer_months"],
    }

    # Cache rate lookups by date to avoid redundant calls
    rate_cache = {}

    import_cost_by_period = defaultdict(float)
    export_credit_by_period = defaultdict(float)
    import_kwh_by_period = defaultdict(float)
    export_kwh_by_period = defaultdict(float)
    season_import_cost = defaultdict(float)
    season_export_credit = defaultdict(float)
    days = set()
    bsc_by_date = {}

    for iv in interval_data:
        hour = iv["hour"]
        month = iv["month"]
        dow = iv["day_of_week"]
        imp = iv["import_kwh"]
        exp = iv["export_kwh"]
        dt = iv["date"]
        days.add(dt)

        period, season = classify_tou_period(hour, month, dow,
                                             schedule_config=schedule_config)

        # Get rate for this date
        if time_aware:
            rate_info = _get_cached_rates(rate_cache, schedule, provider,
                                          vintage_year, income_tier, dt)
        else:
            rate_info = base_rate_info

        rate = rate_info["effective_rates"].get(season, {}).get(period, 0.0)
        bsc_by_date[dt] = rate_info["base_services_charge_daily"]

        # Import cost
        imp_cost = imp * rate
        import_cost_by_period[f"{season}_{period}"] += imp_cost
        import_kwh_by_period[f"{season}_{period}"] += imp
        season_import_cost[season] += imp_cost

        # Export credit
        if exp > 0:
            credit = calculate_export_credit(exp, rate, nem_version)
            export_credit_by_period[f"{season}_{period}"] += credit
            export_kwh_by_period[f"{season}_{period}"] += exp
            season_export_credit[season] += credit

    # BSC: sum per-day BSC (may vary by date for time-aware mode)
    bsc_total = sum(bsc_by_date.values())

    total_import_cost = sum(import_cost_by_period.values())
    total_export_credit = sum(export_credit_by_period.values())
    net_energy_cost = total_import_cost - total_export_credit
    annual_total = net_energy_cost + bsc_total

    tou_breakdown = {}
    all_keys = set(import_cost_by_period.keys()) | set(export_credit_by_period.keys())
    for key in sorted(all_keys):
        tou_breakdown[key] = {
            "import_kwh": round(import_kwh_by_period[key], 1),
            "import_cost": round(import_cost_by_period[key], 2),
            "export_kwh": round(export_kwh_by_period[key], 1),
            "export_credit": round(export_credit_by_period[key], 2),
            "net_cost": round(import_cost_by_period[key] - export_credit_by_period[key], 2),
        }

    # Show which rates were used
    rate_info_summary = {
        "effective_rates": base_rate_info["effective_rates"],
        "base_services_charge_daily": base_rate_info["base_services_charge_daily"],
    }
    if time_aware and rate_cache:
        rate_info_summary["note"] = "Time-aware: rates varied by date period"

    return {
        "annual_total": round(annual_total, 2),
        "net_energy_cost": round(net_energy_cost, 2),
        "total_import_cost": round(total_import_cost, 2),
        "total_export_credit": round(total_export_credit, 2),
        "base_services_charge": round(bsc_total, 2),
        "num_days": len(days),
        "tou_breakdown": tou_breakdown,
        "season_summary": {
            season: {
                "import_cost": round(season_import_cost[season], 2),
                "export_credit": round(season_export_credit[season], 2),
                "net": round(season_import_cost[season] - season_export_credit[season], 2),
            }
            for season in ["summer", "winter"]
        },
        "rate_info": rate_info_summary,
    }


def _get_cached_rates(cache: dict, schedule: str, provider: str,
                      vintage_year: int, income_tier: int,
                      date: str) -> dict:
    """Get rates for a date, caching by date to avoid redundant lookups."""
    if date not in cache:
        cache[date] = lookup_rates(schedule, provider, vintage_year,
                                   income_tier, date=date)
    return cache[date]
