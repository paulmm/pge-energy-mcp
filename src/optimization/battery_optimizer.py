"""Battery dispatch optimizer: main entry point.

Uses Pyomo + CBC to find the cost-minimizing battery charge/discharge schedule
given hourly load, solar production, and TOU electricity rates.

Replaces the heuristic dispatcher in src/analysis/simulator.py with a
mathematically optimal solution.
"""

from __future__ import annotations

from src.rates.tou import classify_tou_period
from src.rates.nem import calculate_export_credit
from src.analysis.simulator import estimate_system_solar, DEFAULT_PSH


def optimize_dispatch(
    interval_data: list[dict],
    system_config: dict,
    rate_config: dict,
    nem_version: str = "NEM2",
    horizon_days: int = 7,
) -> dict:
    """
    Find optimal battery dispatch schedule to minimize electricity cost.

    Takes hourly interval data (same shape as Green Button parser output),
    uses the rate engine for TOU rates per interval, builds and solves
    a Pyomo MILP, and returns the optimal schedule with savings analysis.

    Args:
        interval_data: Hourly records from parse_green_button. Each dict has:
            date, hour, month, day_of_week, import_kwh, export_kwh.
        system_config: {
            "arrays": [{panels, panel_watts, inverter_watts_ac, type, ac_watts}],
            "batteries": [{kwh, kw, efficiency, status}],
            "psh_by_month": {"Jan": 3.2, ...},  # optional
        }
        rate_config: Output from lookup_rates() — effective_rates, tou_windows, etc.
        nem_version: "NEM2" (full retail export) or "NEM3" (avoided cost export).
        horizon_days: Number of days to optimize (default 7). Data is truncated
            to this many days if longer.

    Returns:
        Dict with:
            - schedule: formatted hourly schedule
            - savings: comparison vs no-battery baseline
            - summary: totals and daily breakdown
            - model_status: solver status info
    """
    # Extract system parameters early for validation before heavy imports
    arrays = system_config.get("arrays", [])
    batteries = system_config.get("batteries", [])
    psh = system_config.get("psh_by_month", DEFAULT_PSH)

    # Aggregate battery fleet (working units only)
    total_capacity = 0.0
    total_power = 0.0
    eff_weighted = 0.0
    for b in batteries:
        if b.get("status", "working") != "working":
            continue
        cap = b.get("kwh", 13.5)
        total_capacity += cap
        total_power += b.get("kw", 5.0)
        eff_weighted += cap * b.get("efficiency", 0.90)

    if total_capacity == 0:
        return {
            "error": "No working batteries in system configuration.",
            "hint": "Add at least one battery with status 'working' to system_config.batteries.",
        }

    fleet_efficiency = eff_weighted / total_capacity

    # Check for Pyomo availability
    try:
        import pyomo.environ as pyo
    except ImportError:
        return {
            "error": "Pyomo is not installed. Install with: pip install pyomo",
            "hint": "Also install CBC solver: brew install cbc (macOS) or apt-get install coinor-cbc (Linux)",
        }

    # Check for solver availability
    try:
        solver = pyo.SolverFactory("cbc")
        if not solver.available():
            raise RuntimeError("CBC not available")
    except Exception:
        # Try glpk as fallback
        try:
            solver = pyo.SolverFactory("glpk")
            if not solver.available():
                raise RuntimeError("GLPK not available")
            solver_name = "glpk"
        except Exception:
            return {
                "error": "No compatible solver found. Install CBC or GLPK.",
                "hint": "Install CBC: brew install cbc (macOS) or apt-get install coinor-cbc (Linux). "
                        "Install GLPK: brew install glpk (macOS) or apt-get install glpk-utils (Linux).",
            }
    else:
        solver_name = "cbc"

    # Rate lookup setup
    effective_rates = rate_config["effective_rates"]
    schedule_config = {
        "tou_windows": rate_config["tou_windows"],
        "summer_months": rate_config["summer_months"],
    }

    # Truncate to horizon
    max_hours = horizon_days * 24
    data = interval_data[:max_hours]
    hours = len(data)

    if hours == 0:
        return {"error": "No interval data provided."}

    # Build per-hour arrays
    load = []
    solar = []
    import_rate_arr = []
    export_rate_arr = []

    for iv in data:
        hour = iv["hour"]
        month = iv["month"]
        dow = iv.get("day_of_week", 0)

        # Estimate home load from Green Button data + solar model
        modeled_solar = estimate_system_solar(arrays, month, hour, psh)
        home_load = iv["import_kwh"] + modeled_solar - iv["export_kwh"]
        home_load = max(0, home_load)

        load.append(home_load)
        solar.append(modeled_solar)

        # Get TOU rate for this hour
        period, season = classify_tou_period(hour, month, dow,
                                             schedule_config=schedule_config)
        rate = effective_rates.get(season, {}).get(period, 0.0)
        import_rate_arr.append(rate)

        # Export credit depends on NEM version
        if nem_version == "NEM2":
            export_rate_arr.append(rate)  # full retail
        elif nem_version == "NEM3":
            from src.rates.nem import get_acc_rate
            export_rate_arr.append(get_acc_rate(hour, month))
        else:
            export_rate_arr.append(rate)

        # Annotate interval with TOU period for schedule formatter
        iv["tou_period"] = f"{season}_{period}"

    # Build and solve model
    from src.optimization.model_builder import build_model, solve_model, extract_solution
    from src.optimization.schedule_formatter import (
        format_schedule, compute_baseline_cost, compute_savings,
    )

    model = build_model(
        hours=hours,
        load=load,
        solar=solar,
        import_rate=import_rate_arr,
        export_rate=export_rate_arr,
        battery_capacity_kwh=total_capacity,
        battery_max_power_kw=total_power,
        battery_efficiency=fleet_efficiency,
        initial_soc=0.0,
    )

    try:
        results, solved_model = solve_model(model, solver_name=solver_name)
    except Exception as e:
        return {
            "error": f"Solver failed: {str(e)}",
            "hint": "Try reducing horizon_days or check that interval data is valid.",
        }

    # Check solver status
    from pyomo.opt import TerminationCondition
    tc = results.solver.termination_condition

    if tc not in (TerminationCondition.optimal, TerminationCondition.feasible):
        return {
            "error": f"Optimization did not find a feasible solution. Status: {tc}",
            "solver_status": str(tc),
        }

    # Extract solution
    solution = extract_solution(solved_model, hours)

    # Format schedule
    schedule = format_schedule(
        solution=solution,
        interval_data=data,
        import_rate=import_rate_arr,
        export_rate=export_rate_arr,
        battery_capacity_kwh=total_capacity,
    )

    # Compute baseline (no battery)
    baseline = compute_baseline_cost(
        interval_data=data,
        load=load,
        solar=solar,
        import_rate=import_rate_arr,
        export_rate=export_rate_arr,
    )

    # Compute savings
    savings = compute_savings(schedule, baseline)

    return {
        "schedule": schedule["hourly_schedule"],
        "daily_summary": schedule["daily_summary"],
        "tou_breakdown": schedule["tou_breakdown"],
        "totals": schedule["totals"],
        "savings": savings,
        "baseline": baseline,
        "model_status": {
            "solver": solver_name,
            "termination": str(tc),
            "hours_optimized": hours,
            "horizon_days": horizon_days,
            "battery_capacity_kwh": total_capacity,
            "battery_max_power_kw": total_power,
            "battery_efficiency": fleet_efficiency,
        },
    }
