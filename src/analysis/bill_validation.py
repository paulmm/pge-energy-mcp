"""Validate the rate engine against an actual PG&E bill.

Recomputes expected bill components from hourly interval data for one
billing period and compares them with amounts read off the bill. When the
engine reproduces the bill, every other tool's output inherits that trust;
when it doesn't, the per-component deltas show whether rates are stale, the
plan details are wrong, or the interval data is incomplete.

Component model (matches the bill structure documented in CLAUDE.md):
- pge_delivery: net kWh per TOU period x PG&E delivery rate
  (for bundled customers the generation component is listed separately)
- generation: net kWh x CCA generation rate (CCA) or PG&E generation (bundled)
- pcia: net kWh x vintage PCIA (CCA only; 0 for bundled)
- nbc_on_exports: exported kWh x NBC — the non-offsettable remainder after
  the bill's "NBC on gross imports minus net-usage adjustment" algebra
- base_services_charge: daily BSC x days in period
"""

from __future__ import annotations

from src.rates.engine import RateCache
from src.rates.tou import classify_tou_period


def validate_bill(interval_data: list[dict], plan: dict,
                  period_start: str, period_end: str,
                  actual_charges: dict, nem_version: str = "NEM2") -> dict:
    rows = [iv for iv in interval_data
            if period_start <= iv["date"] <= period_end]
    if not rows:
        return {
            "error": "no_data_in_period",
            "message": (f"No interval data between {period_start} and "
                        f"{period_end}. Check the billing period dates on the "
                        f"bill and the date range of the uploaded CSV."),
        }

    rc = RateCache.from_plan(plan)
    schedule_config = rc.schedule_config

    expected_delivery = 0.0
    expected_generation = 0.0
    net_total = 0.0
    export_total = 0.0
    import_total = 0.0
    days = set()

    for iv in rows:
        period, season = classify_tou_period(
            iv["hour"], iv["month"], iv["day_of_week"],
            schedule_config=schedule_config, date_str=iv["date"])
        info = rc.for_date(iv["date"])
        comp = info["components"]
        net = iv["import_kwh"] - iv["export_kwh"]

        delivery_rate = comp["delivery"].get(season, {}).get(period, 0.0)
        gen_rate = comp["generation"].get(season, {}).get(period, 0.0)

        expected_delivery += net * delivery_rate
        expected_generation += net * gen_rate
        net_total += net
        export_total += iv["export_kwh"]
        import_total += iv["import_kwh"]
        days.add(iv["date"])

    base = rc.base
    pcia = net_total * base["components"]["pcia_per_kwh"]
    nbc_on_exports = export_total * base.get("nbc_per_kwh", 0.0)
    bsc = base["base_services_charge_daily"] * len(days)

    expected = {
        "pge_delivery": round(expected_delivery, 2),
        "generation": round(expected_generation, 2),
        "pcia": round(pcia, 2),
        "nbc_on_exports": round(nbc_on_exports, 2),
        "base_services_charge": round(bsc, 2),
        "total": round(expected_delivery + expected_generation + pcia
                       + nbc_on_exports + bsc, 2),
    }

    deltas = {}
    for key, actual in (actual_charges or {}).items():
        if actual is None or key not in expected:
            continue
        delta = round(expected[key] - actual, 2)
        pct = round(abs(delta) / abs(actual) * 100, 1) if actual else None
        deltas[key] = {"expected": expected[key], "actual": actual,
                       "delta": delta, "pct": pct}

    worst_pct = max((d["pct"] for d in deltas.values()
                     if d["pct"] is not None), default=0.0)
    if not deltas:
        match_quality = "no_actuals_provided"
    elif worst_pct < 2.0:
        match_quality = "good"
    elif worst_pct < 5.0:
        match_quality = "fair"
    else:
        match_quality = "poor"

    notes = []
    if match_quality == "good":
        notes.append("The rate engine reproduces this bill — analysis built "
                     "on these rates can be trusted.")
    elif deltas:
        worst_key = max(deltas, key=lambda k: deltas[k]["pct"] or 0)
        d = deltas[worst_key]
        notes.append(
            f"Biggest mismatch is {worst_key}: expected ${d['expected']:,.2f} "
            f"vs actual ${d['actual']:,.2f} ({d['pct']}% off).")
        hints = {
            "pge_delivery": "Delivery rates may be stale for this period, or "
                            "the schedule on the bill differs from the plan.",
            "generation": "Check the provider — CCA generation rates change "
                          "independently of PG&E and several are estimates.",
            "pcia": "Check the PCIA vintage year printed on the bill.",
            "nbc_on_exports": "NBC per-kWh in config may need updating from "
                              "the current PUC sheets.",
            "base_services_charge": "Check the income tier (CARE/FERA/standard) "
                                    "and the number of days in the period.",
            "total": "Compare the per-component lines on the bill to narrow "
                     "down which piece drifts.",
        }
        if worst_key in hints:
            notes.append(hints[worst_key])

    return {
        "period": {"start": period_start, "end": period_end,
                   "days": len(days),
                   "net_kwh": round(net_total, 1),
                   "import_kwh": round(import_total, 1),
                   "export_kwh": round(export_total, 1)},
        "plan": plan,
        "expected": expected,
        "actual": actual_charges,
        "deltas": deltas,
        "match_quality": match_quality,
        "notes": notes,
    }
