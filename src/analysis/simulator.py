"""System expansion modeler: add arrays, batteries, dispatch strategies.

Models battery charge/discharge by hour, solar production with clipping,
and two dispatch strategies (self-powered vs TOU-optimized).

Key design: Simulates BOTH current and proposed systems through the same
solar model, so systematic model errors cancel in the delta. The "savings"
figure is the difference between two simulations, not vs raw Green Button data.
"""

from __future__ import annotations

from collections import defaultdict
from src.rates.tou import classify_tou_period
from src.rates.nem import calculate_export_credit


# ── Solar production model ────────────────────────────────────────────

# Relative irradiance by hour for 37°N latitude. Sum to ~6.1.
_SOLAR_CURVE = {
    0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0,
    6: 0.02, 7: 0.10, 8: 0.25, 9: 0.45, 10: 0.65, 11: 0.82,
    12: 0.95, 13: 1.00, 14: 0.95, 15: 0.82, 16: 0.60, 17: 0.35,
    18: 0.12, 19: 0.02, 20: 0.0, 21: 0.0, 22: 0.0, 23: 0.0,
}
_CURVE_TOTAL = sum(_SOLAR_CURVE.values())

_MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

DEFAULT_PSH = {
    "Jan": 3.2, "Feb": 4.0, "Mar": 5.0, "Apr": 5.8, "May": 6.3, "Jun": 6.8,
    "Jul": 6.5, "Aug": 6.0, "Sep": 5.5, "Oct": 4.5, "Nov": 3.5, "Dec": 2.9,
}

DERATING = 0.85


def estimate_hourly_solar_kwh(month: int, hour: int, ac_capacity_kw: float,
                              psh_by_month: dict = None) -> float:
    """Estimate solar kWh for one hour from total AC capacity."""
    if psh_by_month is None:
        psh_by_month = DEFAULT_PSH
    psh = psh_by_month.get(_MONTH_NAMES[month - 1], 4.0)
    daily_kwh = ac_capacity_kw * psh * DERATING
    frac = _SOLAR_CURVE.get(hour, 0.0) / _CURVE_TOTAL if _CURVE_TOTAL else 0
    return daily_kwh * frac


def estimate_array_hourly_kwh(array: dict, month: int, hour: int,
                              psh_by_month: dict = None) -> float:
    """Estimate production for one array with inverter clipping."""
    if psh_by_month is None:
        psh_by_month = DEFAULT_PSH

    irradiance = _SOLAR_CURVE.get(hour, 0.0)
    if irradiance == 0:
        return 0.0

    max_irr = max(_SOLAR_CURVE.values())
    irr_frac = irradiance / max_irr

    panels = array.get("panels", 0)
    panel_w = array.get("panel_watts", 0)
    inv_type = array.get("type", "micro")
    inv_w = array.get("inverter_watts_ac", panel_w)

    if inv_type == "micro":
        dc_per_panel_kw = panel_w * irr_frac * DERATING / 1000
        ac_per_panel_kw = min(dc_per_panel_kw, inv_w / 1000)
        instant_ac_kw = ac_per_panel_kw * panels
    else:
        total_dc_kw = panels * panel_w * irr_frac * DERATING / 1000
        instant_ac_kw = min(total_dc_kw, inv_w / 1000)

    psh = psh_by_month.get(_MONTH_NAMES[month - 1], 4.0)
    ac_cap_kw = array.get("ac_watts", inv_w * panels) / 1000
    ideal_daily = ac_cap_kw * psh * DERATING
    ideal_hourly = ideal_daily * (irradiance / _CURVE_TOTAL) if _CURVE_TOTAL else 0

    unclipped_kw = panels * panel_w * irr_frac * DERATING / 1000
    clip_ratio = instant_ac_kw / unclipped_kw if unclipped_kw > 0 else 1.0

    return max(0, ideal_hourly * clip_ratio)


def estimate_system_solar(arrays: list, month: int, hour: int,
                          psh_by_month: dict = None) -> float:
    """Total solar kWh from all arrays for one hour."""
    return sum(estimate_array_hourly_kwh(a, month, hour, psh_by_month)
               for a in arrays)


# ── Battery dispatch ──────────────────────────────────────────────────


class BatteryFleet:
    """Aggregated battery fleet with physical constraints."""

    def __init__(self, batteries: list):
        self.capacity = 0.0
        self.max_power = 0.0
        self.efficiency = 0.90
        self.soc = 0.0

        total_cap = 0.0
        for b in batteries:
            if b.get("status", "working") != "working":
                continue
            cap = b.get("kwh", 13.5)
            total_cap += cap
            self.max_power += b.get("kw", 5.0)

        self.capacity = total_cap
        if total_cap > 0:
            eff_sum = sum(b.get("kwh", 13.5) * b.get("efficiency", 0.90)
                         for b in batteries if b.get("status", "working") == "working")
            self.efficiency = eff_sum / total_cap

    @property
    def active(self) -> bool:
        return self.capacity > 0

    def charge(self, kwh_available: float) -> float:
        """Charge battery. Returns kWh consumed from source."""
        space = self.capacity - self.soc
        max_charge = min(kwh_available, self.max_power, space / self.efficiency)
        if max_charge <= 0:
            return 0.0
        self.soc += max_charge * self.efficiency
        return max_charge

    def discharge(self, kwh_needed: float) -> float:
        """Discharge battery. Returns kWh delivered to load."""
        deliverable = min(kwh_needed, self.max_power, self.soc)
        if deliverable <= 0:
            return 0.0
        self.soc -= deliverable
        return deliverable

    def reset(self):
        self.soc = 0.0


def _dispatch_self_powered(home_net: float, battery: BatteryFleet) -> tuple:
    """Self-powered: battery absorbs excess solar, covers shortfalls."""
    if home_net > 0:
        delivered = battery.discharge(home_net)
        return home_net - delivered, 0.0
    else:
        excess = -home_net
        consumed = battery.charge(excess)
        return 0.0, excess - consumed


def _dispatch_tou(home_net: float, battery: BatteryFleet, period: str) -> tuple:
    """TOU-optimized: charge from grid off-peak, discharge during peak."""
    if home_net <= 0:
        excess = -home_net
        consumed = battery.charge(excess)
        return 0.0, excess - consumed

    if period == "peak":
        delivered = battery.discharge(home_net)
        return home_net - delivered, 0.0
    elif period == "partial_peak":
        # Only discharge if well-charged — save capacity for true peak
        if battery.soc > battery.capacity * 0.7:
            delivered = battery.discharge(home_net)
            return home_net - delivered, 0.0
        return home_net, 0.0
    else:
        # Off-peak: serve load AND charge battery from grid
        grid_for_load = home_net
        grid_for_battery = battery.charge(battery.max_power)
        return grid_for_load + grid_for_battery, 0.0


# ── Core simulation pass ─────────────────────────────────────────────


def _simulate_system(home_loads: list, arrays: list, batteries: list,
                     strategy: str, psh: dict, effective_rates: dict,
                     schedule_config: dict, bsc_daily: float,
                     nem_version: str, interval_data: list) -> dict:
    """
    Run one simulation pass: apply solar + battery to estimated home loads.

    Returns cost breakdown dict.
    """
    battery = BatteryFleet(batteries)
    battery.reset()

    total_import = 0.0
    total_export = 0.0
    import_cost = 0.0
    export_credit = 0.0
    period_import = defaultdict(float)
    period_export = defaultdict(float)
    monthly = defaultdict(lambda: {"import": 0.0, "export": 0.0,
                                    "cost": 0.0, "credit": 0.0})
    days = set()

    for i, iv in enumerate(interval_data):
        hour = iv["hour"]
        month = iv["month"]
        dow = iv["day_of_week"]
        days.add(iv["date"])

        period, season = classify_tou_period(hour, month, dow,
                                             schedule_config=schedule_config)
        rate = effective_rates.get(season, {}).get(period, 0.0)

        home_load = home_loads[i]
        solar = estimate_system_solar(arrays, month, hour, psh)
        home_net = home_load - solar

        if battery.active:
            if strategy == "tou_optimized":
                grid_imp, grid_exp = _dispatch_tou(home_net, battery, period)
            else:
                grid_imp, grid_exp = _dispatch_self_powered(home_net, battery)
        else:
            grid_imp = max(0, home_net)
            grid_exp = max(0, -home_net)

        imp_cost = grid_imp * rate
        exp_cred = calculate_export_credit(grid_exp, rate, nem_version)

        total_import += grid_imp
        total_export += grid_exp
        import_cost += imp_cost
        export_credit += exp_cred

        key = f"{season}_{period}"
        period_import[key] += grid_imp
        period_export[key] += grid_exp
        monthly[month]["import"] += grid_imp
        monthly[month]["export"] += grid_exp
        monthly[month]["cost"] += imp_cost
        monthly[month]["credit"] += exp_cred

    num_days = len(days)
    bsc = bsc_daily * num_days
    net_energy = import_cost - export_credit

    peak_kwh = sum(v for k, v in period_import.items()
                   if "_peak" in k and "off_peak" not in k and "partial" not in k)
    peak_pct = round(peak_kwh / total_import * 100, 1) if total_import > 0 else 0

    return {
        "annual_total": round(net_energy + bsc, 2),
        "net_energy_cost": round(net_energy, 2),
        "total_import_cost": round(import_cost, 2),
        "total_export_credit": round(export_credit, 2),
        "base_services_charge": round(bsc, 2),
        "total_import_kwh": round(total_import, 1),
        "total_export_kwh": round(total_export, 1),
        "peak_exposure_pct": peak_pct,
        "tou_breakdown": {
            k: {"import_kwh": round(period_import[k], 1),
                "export_kwh": round(period_export.get(k, 0), 1)}
            for k in sorted(set(period_import) | set(period_export))
        },
        "monthly_breakdown": [
            {"month": m,
             "import_kwh": round(monthly[m]["import"], 1),
             "export_kwh": round(monthly[m]["export"], 1),
             "net_cost": round(monthly[m]["cost"] - monthly[m]["credit"], 2)}
            for m in sorted(monthly.keys())
        ],
    }


# ── Main entry point ─────────────────────────────────────────────────


def simulate(interval_data: list, system_config: dict,
             rate_config: dict, nem_version: str = "NEM2") -> dict:
    """
    Simulate current vs proposed solar+battery system.

    Estimates underlying home load from Green Button data + solar model,
    then runs BOTH current and proposed systems through the same model
    so systematic errors cancel in the savings delta.

    Args:
        interval_data: Hourly records from parse_green_button
        system_config: {
            "current_system": {          # What the Green Button data reflects
                "arrays": [...],
                "batteries": [...],
                "strategy": "self_powered" | "tou_optimized"
            },
            "proposed_system": {         # What we want to model
                "arrays": [...],
                "batteries": [...],
                "strategy": "self_powered" | "tou_optimized"
            },
            "psh_by_month": {...},       # Optional, defaults to Brisbane
        }
        rate_config: Output from lookup_rates()
        nem_version: "NEM2" or "NEM3"

    Returns:
        Dict with current_simulated, proposed, savings, and breakdowns.
    """
    effective_rates = rate_config["effective_rates"]
    bsc_daily = rate_config["base_services_charge_daily"]
    schedule_config = {
        "tou_windows": rate_config["tou_windows"],
        "summer_months": rate_config["summer_months"],
    }
    psh = system_config.get("psh_by_month", DEFAULT_PSH)

    current = system_config.get("current_system", {})
    proposed = system_config.get("proposed_system", {})

    current_arrays = current.get("arrays", [])

    # ── Step 1: Estimate home loads from Green Button + solar model ──
    # home_load = grid_import + modeled_solar - grid_export
    # This is the underlying consumption the home needs regardless of system.
    home_loads = []
    for iv in interval_data:
        modeled_solar = estimate_system_solar(current_arrays, iv["month"],
                                              iv["hour"], psh)
        hl = iv["import_kwh"] + modeled_solar - iv["export_kwh"]
        home_loads.append(max(0, hl))

    # ── Step 2: Simulate current system ──
    current_result = _simulate_system(
        home_loads, current_arrays,
        current.get("batteries", []),
        current.get("strategy", "self_powered"),
        psh, effective_rates, schedule_config, bsc_daily,
        nem_version, interval_data,
    )

    # ── Step 3: Simulate proposed system ──
    proposed_result = _simulate_system(
        home_loads, proposed.get("arrays", current_arrays),
        proposed.get("batteries", []),
        proposed.get("strategy", "self_powered"),
        psh, effective_rates, schedule_config, bsc_daily,
        nem_version, interval_data,
    )

    # ── Step 4: Compute Green Button baseline for context ──
    gb_cost = _compute_gb_cost(interval_data, effective_rates,
                               schedule_config, bsc_daily, nem_version)

    # Savings: current_simulated - proposed_simulated (model errors cancel)
    sim_savings = round(current_result["annual_total"] - proposed_result["annual_total"], 2)

    # Self-consumption for proposed
    total_proposed_solar = sum(
        estimate_system_solar(proposed.get("arrays", current_arrays),
                              iv["month"], iv["hour"], psh)
        for iv in interval_data
    )
    self_consumed = total_proposed_solar - proposed_result["total_export_kwh"]
    sc_pct = round(self_consumed / total_proposed_solar * 100, 1) if total_proposed_solar > 0 else 0

    return {
        "current_simulated": current_result,
        "proposed": proposed_result,
        "estimated_savings": sim_savings,
        "self_consumption_pct": sc_pct,
        "green_button_baseline": {
            "annual_total": round(gb_cost, 2),
            "note": "Actual cost from Green Button data at this rate plan",
        },
        "model_calibration_error": round(current_result["annual_total"] - gb_cost, 2),
    }


def _compute_gb_cost(interval_data: list, effective_rates: dict,
                     schedule_config: dict, bsc_daily: float,
                     nem_version: str) -> float:
    """Compute cost from raw Green Button data."""
    total_cost = 0.0
    total_credit = 0.0
    days = set()
    for iv in interval_data:
        period, season = classify_tou_period(
            iv["hour"], iv["month"], iv["day_of_week"],
            schedule_config=schedule_config)
        rate = effective_rates.get(season, {}).get(period, 0.0)
        total_cost += iv["import_kwh"] * rate
        total_credit += calculate_export_credit(iv["export_kwh"], rate, nem_version)
        days.add(iv["date"])
    return total_cost - total_credit + bsc_daily * len(days)
