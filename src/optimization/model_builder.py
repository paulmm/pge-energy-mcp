"""Pyomo model construction for battery dispatch optimization.

Builds a mixed-integer linear program (MILP) that minimizes electricity cost
by optimally scheduling battery charge/discharge across TOU periods.

Decision variables per hour t:
    charge[t]       — kWh charged into battery
    discharge[t]    — kWh discharged from battery
    grid_import[t]  — kWh purchased from grid
    grid_export[t]  — kWh sold/credited to grid
    soc[t]          — state of charge (kWh)
    is_charging[t]  — binary, 1 if charging allowed

Objective: minimize total grid cost (import cost minus export credits).

Constraints enforce energy balance, SOC limits, power limits, round-trip
efficiency, mutual exclusion of charge/discharge, and cycling neutrality.
"""

from __future__ import annotations


def build_model(
    hours: int,
    load: list[float],
    solar: list[float],
    import_rate: list[float],
    export_rate: list[float],
    battery_capacity_kwh: float,
    battery_max_power_kw: float,
    battery_efficiency: float,
    initial_soc: float = 0.0,
):
    """
    Build the Pyomo ConcreteModel for battery dispatch optimization.

    Args:
        hours: Number of hourly time steps (e.g. 168 for 7 days).
        load: Home load per hour (kWh). Length = hours.
        solar: Solar production per hour (kWh). Length = hours.
        import_rate: Grid import cost per hour ($/kWh). Length = hours.
        export_rate: Grid export credit per hour ($/kWh). Length = hours.
        battery_capacity_kwh: Total usable capacity of battery fleet.
        battery_max_power_kw: Max charge/discharge rate of fleet (kW = kWh/hr).
        battery_efficiency: Round-trip efficiency (e.g. 0.90).
        initial_soc: Starting state of charge (kWh).

    Returns:
        Pyomo ConcreteModel ready to solve.
    """
    import pyomo.environ as pyo

    # One-way efficiency: sqrt of round-trip so charge * eta stored, discharge / eta delivered
    # But the standard formulation: soc[t] = soc[t-1] + charge[t] * eta - discharge[t]
    # where eta is the one-way charging efficiency. For 90% round-trip, charging eta = 0.95
    # and discharging loses 0.95 implicitly. Simpler: use eta on charge side only.
    eta = battery_efficiency ** 0.5  # one-way efficiency

    model = pyo.ConcreteModel("BatteryDispatch")

    # Sets
    model.T = pyo.RangeSet(0, hours - 1)

    # Parameters (indexed)
    model.home_load = pyo.Param(model.T, initialize=dict(enumerate(load)))
    model.solar_prod = pyo.Param(model.T, initialize=dict(enumerate(solar)))
    model.import_rate = pyo.Param(model.T, initialize=dict(enumerate(import_rate)))
    model.export_rate = pyo.Param(model.T, initialize=dict(enumerate(export_rate)))

    # Decision variables
    model.charge = pyo.Var(model.T, domain=pyo.NonNegativeReals,
                           bounds=(0, battery_max_power_kw))
    model.discharge = pyo.Var(model.T, domain=pyo.NonNegativeReals,
                              bounds=(0, battery_max_power_kw))
    model.grid_import = pyo.Var(model.T, domain=pyo.NonNegativeReals)
    model.grid_export = pyo.Var(model.T, domain=pyo.NonNegativeReals)
    model.soc = pyo.Var(model.T, domain=pyo.NonNegativeReals,
                        bounds=(0, battery_capacity_kwh))

    # Binary for mutual exclusion: can't charge and discharge simultaneously
    model.is_charging = pyo.Var(model.T, domain=pyo.Binary)

    # Objective: minimize import cost minus export credit
    def objective_rule(m):
        return sum(
            m.grid_import[t] * m.import_rate[t] - m.grid_export[t] * m.export_rate[t]
            for t in m.T
        )
    model.cost = pyo.Objective(rule=objective_rule, sense=pyo.minimize)

    # Energy balance: load + charge + export = solar + discharge + import
    def energy_balance_rule(m, t):
        return (m.home_load[t] + m.charge[t] + m.grid_export[t]
                == m.solar_prod[t] + m.discharge[t] + m.grid_import[t])
    model.energy_balance = pyo.Constraint(model.T, rule=energy_balance_rule)

    # SOC dynamics: soc[t] = soc[t-1] + charge[t]*eta - discharge[t]/eta
    def soc_rule(m, t):
        if t == 0:
            return m.soc[t] == initial_soc + m.charge[t] * eta - m.discharge[t] / eta
        return m.soc[t] == m.soc[t - 1] + m.charge[t] * eta - m.discharge[t] / eta
    model.soc_dynamics = pyo.Constraint(model.T, rule=soc_rule)

    # Mutual exclusion: charge only when is_charging=1, discharge only when is_charging=0
    def charge_binary_rule(m, t):
        return m.charge[t] <= battery_max_power_kw * m.is_charging[t]
    model.charge_binary = pyo.Constraint(model.T, rule=charge_binary_rule)

    def discharge_binary_rule(m, t):
        return m.discharge[t] <= battery_max_power_kw * (1 - m.is_charging[t])
    model.discharge_binary = pyo.Constraint(model.T, rule=discharge_binary_rule)

    # Cycling constraint: end SOC >= initial SOC (don't artificially drain)
    def cycling_rule(m):
        last_t = hours - 1
        return m.soc[last_t] >= initial_soc
    model.cycling = pyo.Constraint(rule=cycling_rule)

    return model


def solve_model(model, solver_name: str = "cbc", time_limit: int = 120):
    """
    Solve the Pyomo model.

    Args:
        model: Built Pyomo ConcreteModel.
        solver_name: Solver to use (default "cbc").
        time_limit: Max solve time in seconds.

    Returns:
        (results, model) tuple. Check results.solver.termination_condition.
    """
    import pyomo.environ as pyo

    solver = pyo.SolverFactory(solver_name)
    if solver_name == "cbc":
        solver.options["seconds"] = time_limit
    elif solver_name == "glpk":
        solver.options["tmlim"] = time_limit

    results = solver.solve(model, tee=False)
    return results, model


def extract_solution(model, hours: int) -> dict:
    """
    Extract the solution values from a solved Pyomo model.

    Returns:
        Dict with lists keyed by variable name, each of length `hours`.
    """
    import pyomo.environ as pyo

    def _val(var, t):
        v = pyo.value(var[t])
        return round(v, 4) if v is not None else 0.0

    return {
        "charge": [_val(model.charge, t) for t in range(hours)],
        "discharge": [_val(model.discharge, t) for t in range(hours)],
        "grid_import": [_val(model.grid_import, t) for t in range(hours)],
        "grid_export": [_val(model.grid_export, t) for t in range(hours)],
        "soc": [_val(model.soc, t) for t in range(hours)],
        "is_charging": [_val(model.is_charging, t) for t in range(hours)],
    }
