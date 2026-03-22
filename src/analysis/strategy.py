"""Seasonal optimization strategy: recommendations based on usage and rates."""

from __future__ import annotations

from collections import defaultdict
from src.rates.tou import classify_tou_period
from src.rates.engine import lookup_rates


def seasonal_strategy(interval_data: list, rate_config: dict,
                      system_config: dict = None) -> dict:
    """
    Generate seasonal optimization recommendations.

    Analyzes usage patterns and rate structure to recommend:
    - Battery dispatch strategy by season
    - EV charging timing
    - Load shifting opportunities
    - When solar covers vs when grid-dependent

    Args:
        interval_data: Hourly records from parse_green_button
        rate_config: Output from lookup_rates()
        system_config: Optional system info (batteries, arrays)

    Returns:
        Dict with seasonal recommendations, EV charging advice,
        and load-shifting opportunities.
    """
    effective_rates = rate_config["effective_rates"]
    schedule_config = {
        "tou_windows": rate_config["tou_windows"],
        "summer_months": rate_config["summer_months"],
    }

    # Analyze by season and period
    season_period_import = defaultdict(lambda: defaultdict(float))
    season_period_export = defaultdict(lambda: defaultdict(float))
    season_period_hours = defaultdict(lambda: defaultdict(int))
    monthly_import = defaultdict(float)
    monthly_export = defaultdict(float)
    monthly_days = defaultdict(set)

    # Overnight charging analysis (midnight-6am)
    overnight_by_month = defaultdict(float)

    for iv in interval_data:
        period, season = classify_tou_period(
            iv["hour"], iv["month"], iv["day_of_week"],
            schedule_config=schedule_config)

        season_period_import[season][period] += iv["import_kwh"]
        season_period_export[season][period] += iv["export_kwh"]
        season_period_hours[season][period] += 1
        monthly_import[iv["month"]] += iv["import_kwh"]
        monthly_export[iv["month"]] += iv["export_kwh"]
        monthly_days[iv["month"]].add(iv["date"])

        if 0 <= iv["hour"] <= 5:
            overnight_by_month[iv["month"]] += iv["import_kwh"]

    # Rate spreads
    rate_spreads = {}
    for season in ["summer", "winter"]:
        rates = effective_rates.get(season, {})
        peak = rates.get("peak", 0)
        off = rates.get("off_peak", 0)
        partial = rates.get("partial_peak", off)
        rate_spreads[season] = {
            "peak_rate": round(peak, 5),
            "off_peak_rate": round(off, 5),
            "partial_peak_rate": round(partial, 5),
            "peak_offpeak_spread": round(peak - off, 5),
            "arbitrage_value_per_kwh": round((peak - off) * 0.90 - off * 0.10, 5),
        }

    # Seasonal analysis
    seasons = {}
    for season in ["summer", "winter"]:
        imports = season_period_import[season]
        exports = season_period_export[season]
        total_imp = sum(imports.values())
        total_exp = sum(exports.values())

        peak_imp = imports.get("peak", 0)
        off_imp = imports.get("off_peak", 0)

        seasons[season] = {
            "total_import_kwh": round(total_imp, 1),
            "total_export_kwh": round(total_exp, 1),
            "peak_import_kwh": round(peak_imp, 1),
            "off_peak_import_kwh": round(off_imp, 1),
            "peak_pct_of_import": round(peak_imp / total_imp * 100, 1) if total_imp else 0,
            "daily_avg_import_kwh": round(total_imp / max(1, sum(
                season_period_hours[season].values()) / 24), 1),
        }

    # Monthly trends
    monthly_trends = []
    for m in sorted(monthly_import.keys()):
        days = len(monthly_days[m])
        monthly_trends.append({
            "month": m,
            "daily_avg_import": round(monthly_import[m] / days, 1) if days else 0,
            "daily_avg_export": round(monthly_export[m] / days, 1) if days else 0,
            "overnight_avg_kwh": round(overnight_by_month[m] / days, 1) if days else 0,
            "net_monthly": round(monthly_import[m] - monthly_export[m], 1),
        })

    # Generate recommendations
    recommendations = _generate_recommendations(
        seasons, rate_spreads, monthly_trends, system_config)

    return {
        "seasons": seasons,
        "rate_spreads": rate_spreads,
        "monthly_trends": monthly_trends,
        "recommendations": recommendations,
    }


def _generate_recommendations(seasons: dict, rate_spreads: dict,
                              monthly_trends: list,
                              system_config: dict = None) -> list:
    """Generate actionable recommendations based on analysis."""
    recs = []

    # Battery strategy recommendation
    for season in ["summer", "winter"]:
        spread = rate_spreads[season]
        arb = spread["arbitrage_value_per_kwh"]
        if arb > 0.10:
            recs.append({
                "category": "battery_dispatch",
                "season": season,
                "priority": "high",
                "recommendation": f"TOU-optimized dispatch is highly valuable in {season}",
                "detail": (f"Peak/off-peak spread is ${spread['peak_offpeak_spread']:.3f}/kWh. "
                          f"After 90% round-trip efficiency, net arbitrage value is "
                          f"${arb:.3f}/kWh shifted. Charge from grid midnight-3PM, "
                          f"discharge 4-9PM."),
            })
        elif arb > 0.03:
            recs.append({
                "category": "battery_dispatch",
                "season": season,
                "priority": "medium",
                "recommendation": f"TOU dispatch marginally beneficial in {season}",
                "detail": (f"Arbitrage value ${arb:.3f}/kWh. Consider TOU mode if battery "
                          f"capacity exceeds daily solar excess."),
            })

    # EV charging timing
    for month_data in monthly_trends:
        m = month_data["month"]
        overnight = month_data["overnight_avg_kwh"]
        if overnight > 8:
            recs.append({
                "category": "ev_charging",
                "month": m,
                "priority": "info",
                "recommendation": f"Month {m}: heavy overnight usage ({overnight:.1f} kWh/night avg)",
                "detail": ("This is already during off-peak hours — good. "
                          "Ensure EV charging starts after midnight for lowest rates."),
            })

    # Winter grid dependency
    winter = seasons.get("winter", {})
    summer = seasons.get("summer", {})
    if winter.get("total_import_kwh", 0) > summer.get("total_import_kwh", 0) * 2:
        ratio = winter["total_import_kwh"] / max(1, summer["total_import_kwh"])
        recs.append({
            "category": "solar_expansion",
            "priority": "high",
            "recommendation": "Winter grid dependency is the biggest cost driver",
            "detail": (f"Winter imports are {ratio:.1f}x summer. "
                      f"Additional solar panels would have the highest ROI by reducing "
                      f"winter grid dependency. Even panels that clip in summer provide "
                      f"full value in winter's shorter days."),
        })

    # Export analysis
    summer_exp = summer.get("total_export_kwh", 0)
    winter_exp = winter.get("total_export_kwh", 0)
    if summer_exp > 500:
        recs.append({
            "category": "battery_sizing",
            "priority": "medium",
            "recommendation": f"Summer has {summer_exp:.0f} kWh of exports — battery capture opportunity",
            "detail": ("Under NEM 2.0 exports earn full retail credit, so capturing them "
                      "in a battery only helps if you can time-shift to higher-rate hours. "
                      "Under NEM 3.0 self-consumption would be worth 5-15x more than export."),
        })

    # Peak exposure
    for season in ["summer", "winter"]:
        peak_pct = seasons[season].get("peak_pct_of_import", 0)
        if peak_pct > 20:
            recs.append({
                "category": "load_shifting",
                "season": season,
                "priority": "high",
                "recommendation": f"{season.title()}: {peak_pct:.0f}% of imports during peak hours",
                "detail": ("Shift discretionary loads (laundry, dishwasher, pool pump) "
                          "to off-peak hours (midnight-3PM). Each kWh shifted saves "
                          f"${rate_spreads[season]['peak_offpeak_spread']:.3f}."),
            })

    return recs
