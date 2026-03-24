"""NEM version comparison: project costs under NEM 2.0 vs NEM 3.0.

Answers the key question for solar customers:
- NEM 2 customers: "How much more will I pay when grandfathering expires?"
- NEM 3 customers: "How much am I losing vs NEM 2?"
- Prospective buyers: "What's the real economics of solar under NEM 3?"

The comparison runs the same usage data through both NEM versions and shows
where the cost difference comes from (export credit loss, TOU period impact).
"""

from __future__ import annotations

from collections import defaultdict
from src.rates.engine import lookup_rates
from src.rates.tou import classify_tou_period
from src.rates.nem import calculate_export_credit, get_acc_rate, get_acc_summary


def compare_nem_versions(interval_data: list[dict], plan: dict,
                         time_aware: bool = True) -> dict:
    """
    Compare annual cost under NEM 2.0 vs NEM 3.0 for the same plan and usage.

    Args:
        interval_data: Hourly records from parse_green_button
        plan: {schedule, provider, vintage_year, income_tier}
        time_aware: Apply historical rate overrides by date

    Returns:
        Dict with NEM2 and NEM3 costs, delta, export credit breakdown by
        TOU period, and insights about the transition impact.
    """
    schedule = plan["schedule"]
    provider = plan.get("provider", "PGE_BUNDLED")
    vintage_year = plan.get("vintage_year", 2016)
    income_tier = plan.get("income_tier", 3)

    base_rate_info = lookup_rates(schedule, provider, vintage_year, income_tier)
    schedule_config = {
        "tou_windows": base_rate_info["tou_windows"],
        "summer_months": base_rate_info["summer_months"],
    }

    rate_cache = {}

    # Track per-period export credits under each NEM version
    nem2_credit_by_period = defaultdict(float)
    nem3_credit_by_period = defaultdict(float)
    export_kwh_by_period = defaultdict(float)
    import_cost_by_period = defaultdict(float)
    import_kwh_by_period = defaultdict(float)

    # Monthly breakdown
    monthly = defaultdict(lambda: {
        "nem2_credit": 0.0, "nem3_credit": 0.0,
        "export_kwh": 0.0, "import_cost": 0.0,
    })

    total_import_cost = 0.0
    total_nem2_credit = 0.0
    total_nem3_credit = 0.0
    total_export_kwh = 0.0
    total_import_kwh = 0.0
    days = set()
    bsc_by_date = {}

    for iv in interval_data:
        hour = iv["hour"]
        month = iv["month"]
        dow = iv["day_of_week"]
        imp = iv["import_kwh"]
        exp = iv["export_kwh"]
        dt = iv["date"]
        ym = dt[:7]
        days.add(dt)

        period, season = classify_tou_period(hour, month, dow,
                                             schedule_config=schedule_config)
        key = f"{season}_{period}"

        if time_aware:
            if dt not in rate_cache:
                rate_cache[dt] = lookup_rates(schedule, provider,
                                              vintage_year, income_tier, date=dt)
            rate_info = rate_cache[dt]
        else:
            rate_info = base_rate_info

        rate = rate_info["effective_rates"].get(season, {}).get(period, 0.0)
        bsc_by_date[dt] = rate_info["base_services_charge_daily"]

        # Import cost is the same under both NEM versions
        imp_cost = imp * rate
        total_import_cost += imp_cost
        total_import_kwh += imp
        import_cost_by_period[key] += imp_cost
        import_kwh_by_period[key] += imp
        monthly[ym]["import_cost"] += imp_cost

        # Export credits differ
        if exp > 0:
            nem2_cred = calculate_export_credit(exp, rate, "NEM2")
            nem3_cred = calculate_export_credit(exp, rate, "NEM3",
                                                hour=hour, month=month)

            total_nem2_credit += nem2_cred
            total_nem3_credit += nem3_cred
            total_export_kwh += exp
            export_kwh_by_period[key] += exp
            nem2_credit_by_period[key] += nem2_cred
            nem3_credit_by_period[key] += nem3_cred
            monthly[ym]["nem2_credit"] += nem2_cred
            monthly[ym]["nem3_credit"] += nem3_cred
            monthly[ym]["export_kwh"] += exp

    bsc_total = sum(bsc_by_date.values())

    nem2_net = total_import_cost - total_nem2_credit
    nem3_net = total_import_cost - total_nem3_credit
    nem2_annual = nem2_net + bsc_total
    nem3_annual = nem3_net + bsc_total

    credit_loss = total_nem2_credit - total_nem3_credit
    annual_increase = nem3_annual - nem2_annual

    # Per-period breakdown
    period_breakdown = {}
    all_keys = sorted(set(export_kwh_by_period.keys()))
    for key in all_keys:
        exp_kwh = export_kwh_by_period[key]
        if exp_kwh == 0:
            continue
        nem2_c = nem2_credit_by_period[key]
        nem3_c = nem3_credit_by_period[key]
        period_breakdown[key] = {
            "export_kwh": round(exp_kwh, 1),
            "nem2_credit": round(nem2_c, 2),
            "nem3_credit": round(nem3_c, 2),
            "credit_loss": round(nem2_c - nem3_c, 2),
            "nem2_avg_rate": round(nem2_c / exp_kwh, 4) if exp_kwh else 0,
            "nem3_avg_rate": round(nem3_c / exp_kwh, 4) if exp_kwh else 0,
        }

    # Monthly breakdown
    monthly_breakdown = []
    for ym in sorted(monthly.keys()):
        m = monthly[ym]
        monthly_breakdown.append({
            "year_month": ym,
            "export_kwh": round(m["export_kwh"], 1),
            "nem2_credit": round(m["nem2_credit"], 2),
            "nem3_credit": round(m["nem3_credit"], 2),
            "credit_loss": round(m["nem2_credit"] - m["nem3_credit"], 2),
        })

    # Worst months (biggest credit loss)
    worst_months = sorted(monthly_breakdown,
                          key=lambda m: m["credit_loss"], reverse=True)[:3]

    insights = _generate_nem_insights(
        nem2_annual, nem3_annual, credit_loss, total_export_kwh,
        total_nem2_credit, total_nem3_credit, period_breakdown,
        monthly_breakdown, bsc_total)

    return {
        "nem2": {
            "annual_total": round(nem2_annual, 2),
            "net_energy_cost": round(nem2_net, 2),
            "total_export_credit": round(total_nem2_credit, 2),
            "avg_export_rate": round(total_nem2_credit / total_export_kwh, 4) if total_export_kwh else 0,
        },
        "nem3": {
            "annual_total": round(nem3_annual, 2),
            "net_energy_cost": round(nem3_net, 2),
            "total_export_credit": round(total_nem3_credit, 2),
            "avg_export_rate": round(total_nem3_credit / total_export_kwh, 4) if total_export_kwh else 0,
        },
        "transition_impact": {
            "annual_increase": round(annual_increase, 2),
            "monthly_increase": round(annual_increase / 12, 2),
            "credit_loss": round(credit_loss, 2),
            "credit_retention_pct": round(total_nem3_credit / total_nem2_credit * 100, 1) if total_nem2_credit else 0,
            "total_export_kwh": round(total_export_kwh, 1),
            "total_import_kwh": round(total_import_kwh, 1),
        },
        "common": {
            "total_import_cost": round(total_import_cost, 2),
            "base_services_charge": round(bsc_total, 2),
            "plan": plan,
        },
        "period_breakdown": period_breakdown,
        "monthly_breakdown": monthly_breakdown,
        "worst_months": worst_months,
        "acc_summary": get_acc_summary(),
        "insights": insights,
    }


def _generate_nem_insights(nem2_annual, nem3_annual, credit_loss,
                           total_export, nem2_credit, nem3_credit,
                           periods, monthly, bsc) -> list:
    """Generate human-readable insights about the NEM transition impact."""
    insights = []

    increase = nem3_annual - nem2_annual
    if increase > 0:
        pct = increase / nem2_annual * 100 if nem2_annual > 0 else 0
        insights.append(
            f"Moving from NEM 2 to NEM 3 increases annual cost by "
            f"${increase:,.0f} ({pct:.0f}% more) — "
            f"from ${nem2_annual:,.0f} to ${nem3_annual:,.0f}"
        )

    if nem2_credit > 0:
        retention = nem3_credit / nem2_credit * 100
        insights.append(
            f"NEM 3 retains only {retention:.0f}% of NEM 2 export credits "
            f"(${nem3_credit:,.0f} vs ${nem2_credit:,.0f} — "
            f"${credit_loss:,.0f} lost)"
        )

    # Find which periods lose the most
    if periods:
        worst_period = max(periods.items(), key=lambda x: x[1]["credit_loss"])
        key, data = worst_period
        insights.append(
            f"Biggest credit loss is {key.replace('_', ' ')}: "
            f"${data['credit_loss']:,.0f} lost "
            f"({data['export_kwh']:,.0f} kWh exported, "
            f"NEM2 ${data['nem2_avg_rate']:.3f}/kWh → NEM3 ${data['nem3_avg_rate']:.3f}/kWh)"
        )

    # Midday solar surplus impact
    summer_midday_loss = sum(
        p["credit_loss"] for k, p in periods.items()
        if "off_peak" in k and "summer" in k
    )
    if summer_midday_loss > 0 and credit_loss > 0:
        pct = summer_midday_loss / credit_loss * 100
        insights.append(
            f"Summer off-peak exports account for {pct:.0f}% of credit loss "
            f"(${summer_midday_loss:,.0f}) — midday solar surplus is worth "
            f"very little under NEM 3"
        )

    # Battery recommendation
    if credit_loss > 200:
        monthly_savings = credit_loss / 12
        insights.append(
            f"Battery storage could recover ~${credit_loss * 0.6:,.0f}/yr of the "
            f"${credit_loss:,.0f} credit loss by shifting exports to self-consumption"
        )

    # Worst month
    if monthly:
        worst = max(monthly, key=lambda m: m["credit_loss"])
        if worst["credit_loss"] > 0:
            insights.append(
                f"Worst month for NEM 3 transition: {worst['year_month']} "
                f"(${worst['credit_loss']:,.0f} credit loss, "
                f"{worst['export_kwh']:,.0f} kWh exported)"
            )

    return insights
