"""NEM true-up projection: accumulate monthly NEM balances and project the annual bill.

Under NEM 2.0, energy charges accumulate monthly and settle at the annual
true-up date. Monthly charges are just the Base Services Charge. The NEM
balance carries forward — surplus months offset deficit months. At true-up,
any remaining balance is the bill (no cash back for net surplus).

CLAUDE.md reference: ~$2,000-2,100 in Dec-Jan cycle. Monthly charges $8-118.
"""

from __future__ import annotations

from collections import defaultdict
from src.rates.engine import lookup_rates
from src.rates.tou import classify_tou_period
from src.rates.nem import calculate_export_credit


def project_trueup(interval_data: list, plan: dict,
                   nem_version: str = "NEM2",
                   true_up_month: int = 1,
                   time_aware: bool = True) -> dict:
    """
    Project NEM true-up bill from interval data.

    Accumulates monthly NEM balances (import cost - export credit) and
    shows how they build toward the annual true-up settlement.

    Args:
        interval_data: Hourly records from parse_green_button
        plan: {schedule, provider, vintage_year, income_tier}
        nem_version: "NEM2" or "NEM3"
        true_up_month: Month of annual true-up (1=January)
        time_aware: Apply historical rate overrides by date

    Returns:
        Dict with monthly_balances, cumulative NEM balance, projected true-up,
        and BSC charges.
    """
    schedule = plan["schedule"]
    provider = plan.get("provider", "PGE_BUNDLED")
    vintage_year = plan.get("vintage_year", 2016)
    income_tier = plan.get("income_tier", 3)

    # Get schedule config for TOU classification
    base_rates = lookup_rates(schedule, provider, vintage_year, income_tier)
    schedule_config = {
        "tou_windows": base_rates["tou_windows"],
        "summer_months": base_rates["summer_months"],
    }

    # Rate cache for time-aware lookups
    rate_cache = {}

    # Group intervals by calendar month (YYYY-MM)
    monthly_data = defaultdict(lambda: {
        "import_cost": 0.0, "export_credit": 0.0,
        "import_kwh": 0.0, "export_kwh": 0.0,
        "days": set(), "bsc_by_day": {},
    })

    for iv in interval_data:
        year_month = iv["date"][:7]  # YYYY-MM
        dt = iv["date"]
        m = monthly_data[year_month]
        m["days"].add(dt)

        period, season = classify_tou_period(
            iv["hour"], iv["month"], iv["day_of_week"],
            schedule_config=schedule_config)

        if time_aware:
            if dt not in rate_cache:
                rate_cache[dt] = lookup_rates(schedule, provider,
                                              vintage_year, income_tier, date=dt)
            rate_info = rate_cache[dt]
        else:
            rate_info = base_rates

        rate = rate_info["effective_rates"].get(season, {}).get(period, 0.0)
        m["bsc_by_day"][dt] = rate_info["base_services_charge_daily"]

        m["import_kwh"] += iv["import_kwh"]
        m["export_kwh"] += iv["export_kwh"]
        m["import_cost"] += iv["import_kwh"] * rate
        m["export_credit"] += calculate_export_credit(
            iv["export_kwh"], rate, nem_version,
            hour=iv["hour"], month=iv["month"])

    # Build monthly balances in chronological order
    sorted_months = sorted(monthly_data.keys())
    monthly_balances = []
    cumulative_nem = 0.0
    total_bsc = 0.0
    total_monthly_charges = 0.0

    for ym in sorted_months:
        m = monthly_data[ym]
        num_days = len(m["days"])
        bsc = sum(m["bsc_by_day"].values())

        nem_balance = m["import_cost"] - m["export_credit"]
        cumulative_nem += nem_balance
        total_bsc += bsc

        # Monthly bill = BSC only (NEM charges accumulate to true-up)
        # But if NEM balance is positive (owe money), minimum bill applies
        monthly_charge = bsc  # NEM 2.0: monthly = BSC only
        total_monthly_charges += monthly_charge

        year = int(ym[:4])
        month = int(ym[5:7])

        monthly_balances.append({
            "year_month": ym,
            "month": month,
            "year": year,
            "days": num_days,
            "import_kwh": round(m["import_kwh"], 1),
            "export_kwh": round(m["export_kwh"], 1),
            "nem_balance": round(nem_balance, 2),
            "cumulative_nem": round(cumulative_nem, 2),
            "bsc": round(bsc, 2),
            "monthly_charge": round(monthly_charge, 2),
            "is_credit_month": nem_balance < 0,
        })

    # True-up projection
    # Under NEM 2.0: true-up = max(0, cumulative NEM balance) — no cash back
    trueup_balance = max(0, cumulative_nem)

    # Find true-up month in data
    trueup_months = [b for b in monthly_balances if b["month"] == true_up_month]

    # Annual total = true-up + sum of monthly BSC charges
    annual_total = trueup_balance + total_bsc

    # Credit vs debit months
    credit_months = [b for b in monthly_balances if b["is_credit_month"]]
    debit_months = [b for b in monthly_balances if not b["is_credit_month"]]

    # Worst months (highest NEM debit)
    worst = sorted(monthly_balances, key=lambda b: b["nem_balance"], reverse=True)[:3]
    best = sorted(monthly_balances, key=lambda b: b["nem_balance"])[:3]

    return {
        "true_up_month": true_up_month,
        "monthly_balances": monthly_balances,
        "summary": {
            "annual_total": round(annual_total, 2),
            "true_up_balance": round(trueup_balance, 2),
            "total_bsc": round(total_bsc, 2),
            "total_monthly_charges": round(total_monthly_charges, 2),
            "cumulative_nem_at_end": round(cumulative_nem, 2),
            "credit_months": len(credit_months),
            "debit_months": len(debit_months),
        },
        "worst_months": [
            {"month": w["year_month"], "nem_balance": w["nem_balance"]}
            for w in worst
        ],
        "best_months": [
            {"month": b["year_month"], "nem_balance": b["nem_balance"]}
            for b in best
        ],
        "insights": _generate_insights(monthly_balances, trueup_balance,
                                       total_bsc, true_up_month),
    }


def _generate_insights(balances: list, trueup: float, bsc: float,
                       true_up_month: int) -> list:
    """Generate human-readable insights about the true-up projection."""
    insights = []

    # True-up dominance
    total = trueup + bsc
    if total > 0:
        trueup_pct = trueup / total * 100
        insights.append(
            f"True-up is {trueup_pct:.0f}% of annual cost "
            f"(${trueup:,.0f} true-up + ${bsc:,.0f} monthly BSC = ${total:,.0f} total)"
        )

    # Winter concentration
    winter_months = [b for b in balances if b["month"] in [11, 12, 1, 2]]
    winter_nem = sum(b["nem_balance"] for b in winter_months)
    total_nem = sum(b["nem_balance"] for b in balances)
    if total_nem > 0 and winter_nem > 0:
        winter_pct = winter_nem / total_nem * 100
        insights.append(
            f"Nov-Feb accounts for {winter_pct:.0f}% of NEM charges "
            f"(${winter_nem:,.0f} of ${total_nem:,.0f})"
        )

    # Summer credit months
    summer = [b for b in balances if b["month"] in [5, 6, 7, 8, 9]]
    summer_credit = sum(b["nem_balance"] for b in summer if b["nem_balance"] < 0)
    if summer_credit < 0:
        insights.append(
            f"Summer months generate ${abs(summer_credit):,.0f} in NEM credits "
            f"that offset winter charges"
        )

    # Monthly charge range
    charges = [b["monthly_charge"] for b in balances]
    if charges:
        insights.append(
            f"Monthly bills range ${min(charges):,.0f}-${max(charges):,.0f} "
            f"(BSC only — NEM settles at true-up)"
        )

    return insights
