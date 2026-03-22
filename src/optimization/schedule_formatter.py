"""Convert Pyomo solution to readable output with schedules and summaries.

Formats the raw variable arrays from the optimizer into per-hour schedules,
daily summaries, TOU period breakdowns, and savings vs a no-optimization baseline.
"""

from __future__ import annotations

from collections import defaultdict


def format_schedule(
    solution: dict,
    interval_data: list[dict],
    import_rate: list[float],
    export_rate: list[float],
    battery_capacity_kwh: float,
) -> dict:
    """
    Convert optimizer solution into a readable schedule and summary.

    Args:
        solution: Output from extract_solution() — per-hour variable arrays.
        interval_data: The hourly interval data (with date, hour, month, etc.).
        import_rate: Grid import cost per hour ($/kWh).
        export_rate: Grid export credit per hour ($/kWh).
        battery_capacity_kwh: Total battery capacity for SOC percentage calc.

    Returns:
        Dict with hourly_schedule, daily_summary, tou_breakdown, totals.
    """
    hours = len(solution["charge"])

    hourly_schedule = []
    daily_stats = defaultdict(lambda: {
        "cost": 0.0, "solar_used_kwh": 0.0, "battery_cycles": 0.0,
        "import_kwh": 0.0, "export_kwh": 0.0,
    })
    tou_stats = defaultdict(lambda: {
        "import_kwh": 0.0, "export_kwh": 0.0,
        "import_cost": 0.0, "export_credit": 0.0,
        "charge_kwh": 0.0, "discharge_kwh": 0.0,
    })

    total_import_cost = 0.0
    total_export_credit = 0.0
    total_charge = 0.0
    total_discharge = 0.0

    for t in range(hours):
        charge_kwh = solution["charge"][t]
        discharge_kwh = solution["discharge"][t]
        grid_imp = solution["grid_import"][t]
        grid_exp = solution["grid_export"][t]
        soc = solution["soc"][t]
        soc_pct = round(soc / battery_capacity_kwh * 100, 1) if battery_capacity_kwh > 0 else 0

        imp_cost = grid_imp * import_rate[t]
        exp_credit = grid_exp * export_rate[t]
        total_import_cost += imp_cost
        total_export_credit += exp_credit
        total_charge += charge_kwh
        total_discharge += discharge_kwh

        # Determine action
        if charge_kwh > 0.01:
            action = "charge"
            kw = round(charge_kwh, 2)
        elif discharge_kwh > 0.01:
            action = "discharge"
            kw = round(discharge_kwh, 2)
        else:
            action = "idle"
            kw = 0.0

        iv = interval_data[t] if t < len(interval_data) else {}
        tou_period = iv.get("tou_period", "unknown")
        date_str = iv.get("date", "")
        hour = iv.get("hour", t % 24)

        hourly_schedule.append({
            "hour": hour,
            "date": date_str,
            "action": action,
            "kw": kw,
            "soc_pct": soc_pct,
            "grid_import_kwh": round(grid_imp, 3),
            "grid_export_kwh": round(grid_exp, 3),
            "import_cost": round(imp_cost, 4),
            "export_credit": round(exp_credit, 4),
            "tou_period": tou_period,
        })

        # Daily aggregation
        daily_stats[date_str]["cost"] += imp_cost - exp_credit
        daily_stats[date_str]["import_kwh"] += grid_imp
        daily_stats[date_str]["export_kwh"] += grid_exp
        daily_stats[date_str]["battery_cycles"] += discharge_kwh

        # TOU aggregation
        tou_stats[tou_period]["import_kwh"] += grid_imp
        tou_stats[tou_period]["export_kwh"] += grid_exp
        tou_stats[tou_period]["import_cost"] += imp_cost
        tou_stats[tou_period]["export_credit"] += exp_credit
        tou_stats[tou_period]["charge_kwh"] += charge_kwh
        tou_stats[tou_period]["discharge_kwh"] += discharge_kwh

    # Finalize daily summaries
    daily_summary = []
    for date_str in sorted(daily_stats.keys()):
        d = daily_stats[date_str]
        cycles = d["battery_cycles"] / battery_capacity_kwh if battery_capacity_kwh > 0 else 0
        daily_summary.append({
            "date": date_str,
            "net_cost": round(d["cost"], 2),
            "import_kwh": round(d["import_kwh"], 1),
            "export_kwh": round(d["export_kwh"], 1),
            "battery_cycles": round(cycles, 2),
        })

    # Finalize TOU breakdown
    tou_breakdown = {}
    for period, stats in sorted(tou_stats.items()):
        tou_breakdown[period] = {
            "import_kwh": round(stats["import_kwh"], 1),
            "export_kwh": round(stats["export_kwh"], 1),
            "import_cost": round(stats["import_cost"], 2),
            "export_credit": round(stats["export_credit"], 2),
            "net_cost": round(stats["import_cost"] - stats["export_credit"], 2),
            "charge_kwh": round(stats["charge_kwh"], 1),
            "discharge_kwh": round(stats["discharge_kwh"], 1),
        }

    battery_cycles = total_discharge / battery_capacity_kwh if battery_capacity_kwh > 0 else 0

    return {
        "hourly_schedule": hourly_schedule,
        "daily_summary": daily_summary,
        "tou_breakdown": tou_breakdown,
        "totals": {
            "total_import_cost": round(total_import_cost, 2),
            "total_export_credit": round(total_export_credit, 2),
            "net_cost": round(total_import_cost - total_export_credit, 2),
            "total_import_kwh": round(sum(solution["grid_import"]), 1),
            "total_export_kwh": round(sum(solution["grid_export"]), 1),
            "total_charge_kwh": round(total_charge, 1),
            "total_discharge_kwh": round(total_discharge, 1),
            "battery_cycles": round(battery_cycles, 2),
        },
    }


def compute_baseline_cost(
    interval_data: list[dict],
    load: list[float],
    solar: list[float],
    import_rate: list[float],
    export_rate: list[float],
) -> dict:
    """
    Compute the no-battery baseline cost (solar self-consumption only, no storage).

    Excess solar is exported; shortfalls are imported from grid. No battery dispatch.

    Returns:
        Dict with total_import_cost, total_export_credit, net_cost.
    """
    total_import_cost = 0.0
    total_export_credit = 0.0
    total_import_kwh = 0.0
    total_export_kwh = 0.0

    for t in range(len(load)):
        net = load[t] - solar[t]
        if net > 0:
            # Need grid import
            total_import_kwh += net
            total_import_cost += net * import_rate[t]
        else:
            # Excess solar exported
            export = -net
            total_export_kwh += export
            total_export_credit += export * export_rate[t]

    return {
        "total_import_cost": round(total_import_cost, 2),
        "total_export_credit": round(total_export_credit, 2),
        "net_cost": round(total_import_cost - total_export_credit, 2),
        "total_import_kwh": round(total_import_kwh, 1),
        "total_export_kwh": round(total_export_kwh, 1),
    }


def compute_savings(optimized: dict, baseline: dict) -> dict:
    """
    Compute savings from optimization vs baseline.

    Returns:
        Dict with absolute savings, percentage, and breakdown.
    """
    savings = round(baseline["net_cost"] - optimized["totals"]["net_cost"], 2)
    pct = round(savings / baseline["net_cost"] * 100, 1) if baseline["net_cost"] > 0 else 0

    return {
        "savings_dollars": savings,
        "savings_pct": pct,
        "baseline_cost": baseline["net_cost"],
        "optimized_cost": optimized["totals"]["net_cost"],
        "import_reduction_kwh": round(
            baseline["total_import_kwh"] - optimized["totals"]["total_import_kwh"], 1
        ),
    }
