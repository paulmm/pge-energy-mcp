"""Tests for battery dispatch optimizer.

Tests that the Pyomo MILP model builds correctly, produces feasible solutions
with lower cost than baseline, respects physical constraints, and handles
missing dependencies gracefully.
"""

import pytest
from datetime import datetime, timedelta


# ── Test data helpers ────────────────────────────────────────────────


def _make_interval_data(days: int = 2, base_import: float = 1.5,
                        base_export: float = 0.0) -> list[dict]:
    """Generate synthetic hourly interval data for testing."""
    start = datetime(2025, 7, 15, 0, 0)  # Summer Tuesday
    intervals = []
    for d in range(days):
        for h in range(24):
            dt = start + timedelta(days=d, hours=h)
            # Daytime has solar export, nighttime has import
            if 9 <= h <= 16:
                imp = 0.2
                exp = 2.5
            elif 16 < h <= 21:
                # Peak hours: high import
                imp = 3.0
                exp = 0.0
            else:
                imp = base_import
                exp = base_export
            intervals.append({
                "date": dt.strftime("%Y-%m-%d"),
                "hour": h,
                "month": dt.month,
                "day_of_week": dt.weekday(),
                "import_kwh": imp,
                "export_kwh": exp,
            })
    return intervals


def _make_system_config(num_batteries: int = 1, battery_status: str = "working"):
    """Generate a test system configuration."""
    return {
        "arrays": [
            {
                "name": "Test Array",
                "panels": 10,
                "panel_watts": 400,
                "inverter_watts_ac": 380,
                "type": "micro",
                "ac_watts": 3800,
            }
        ],
        "batteries": [
            {
                "type": "Powerwall 2",
                "kwh": 13.5,
                "kw": 5.0,
                "efficiency": 0.90,
                "status": battery_status,
            }
            for _ in range(num_batteries)
        ],
    }


def _make_rate_config():
    """Generate EV2-A-like rate config for testing."""
    return {
        "schedule": "EV2-A",
        "provider": "PCE",
        "vintage_year": 2016,
        "effective_rates": {
            "summer": {
                "peak": 0.50957,
                "partial_peak": 0.44488,
                "off_peak": 0.20635,
            },
            "winter": {
                "peak": 0.34795,
                "partial_peak": 0.33157,
                "off_peak": 0.20635,
            },
        },
        "base_services_charge_daily": 0.79343,
        "tou_windows": {
            "peak": {"hours": [16, 17, 18, 19, 20], "weekdays_only": False},
            "partial_peak": {"hours": [15, 21, 22, 23], "weekdays_only": False},
            "off_peak": {"hours": list(range(0, 15)), "weekdays_only": False},
        },
        "summer_months": [6, 7, 8, 9],
    }


# ── Check Pyomo + solver availability ────────────────────────────────


def _pyomo_available():
    try:
        import pyomo.environ
        return True
    except ImportError:
        return False


def _solver_available():
    if not _pyomo_available():
        return False
    import pyomo.environ as pyo
    for name in ("cbc", "glpk"):
        try:
            s = pyo.SolverFactory(name)
            if s.available():
                return True
        except Exception:
            continue
    return False


requires_pyomo = pytest.mark.skipif(
    not _pyomo_available(), reason="Pyomo not installed"
)
requires_solver = pytest.mark.skipif(
    not _solver_available(), reason="No LP solver (CBC/GLPK) available"
)


# ── Tests ────────────────────────────────────────────────────────────


class TestModelBuilder:
    """Test that the Pyomo model builds without error."""

    @requires_pyomo
    def test_model_builds(self):
        from src.optimization.model_builder import build_model

        hours = 48
        model = build_model(
            hours=hours,
            load=[1.5] * hours,
            solar=[0.0] * hours,
            import_rate=[0.30] * hours,
            export_rate=[0.30] * hours,
            battery_capacity_kwh=13.5,
            battery_max_power_kw=5.0,
            battery_efficiency=0.90,
            initial_soc=0.0,
        )

        # Verify model has expected components
        assert hasattr(model, "charge")
        assert hasattr(model, "discharge")
        assert hasattr(model, "grid_import")
        assert hasattr(model, "grid_export")
        assert hasattr(model, "soc")
        assert hasattr(model, "is_charging")
        assert hasattr(model, "cost")
        assert hasattr(model, "energy_balance")
        assert hasattr(model, "soc_dynamics")
        assert hasattr(model, "cycling")

    @requires_pyomo
    def test_model_with_varying_rates(self):
        """Model builds with time-varying rates (TOU structure)."""
        from src.optimization.model_builder import build_model

        hours = 24
        # Simulate TOU: cheap overnight, expensive afternoon
        rates = []
        for h in range(24):
            if 16 <= h <= 20:
                rates.append(0.51)  # peak
            elif h in (15, 21, 22, 23):
                rates.append(0.44)  # partial peak
            else:
                rates.append(0.21)  # off peak
        model = build_model(
            hours=hours,
            load=[2.0] * hours,
            solar=[0.0] * 7 + [1.0, 2.0, 3.0, 4.0, 5.0, 5.0, 4.0, 3.0, 2.0, 1.0] + [0.0] * 7,
            import_rate=rates,
            export_rate=rates,
            battery_capacity_kwh=13.5,
            battery_max_power_kw=5.0,
            battery_efficiency=0.90,
        )
        assert model is not None


class TestOptimalSolution:
    """Test that solved model produces valid, cost-reducing results."""

    @requires_solver
    def test_optimal_lower_than_baseline(self):
        """Optimized schedule should cost less than no-battery baseline."""
        from src.optimization.battery_optimizer import optimize_dispatch

        data = _make_interval_data(days=2)
        system = _make_system_config(num_batteries=1)
        rates = _make_rate_config()

        result = optimize_dispatch(data, system, rates, nem_version="NEM2",
                                   horizon_days=2)

        assert "error" not in result, f"Optimizer returned error: {result.get('error')}"
        assert result["savings"]["savings_dollars"] >= 0, (
            f"Optimization should not increase cost. Savings: {result['savings']}"
        )

    @requires_solver
    def test_soc_within_bounds(self):
        """SOC should stay between 0 and battery capacity at all hours."""
        from src.optimization.battery_optimizer import optimize_dispatch

        data = _make_interval_data(days=2)
        system = _make_system_config(num_batteries=1)
        rates = _make_rate_config()

        result = optimize_dispatch(data, system, rates, horizon_days=2)
        assert "error" not in result

        capacity = 13.5
        for entry in result["schedule"]:
            soc_kwh = entry["soc_pct"] / 100 * capacity
            assert soc_kwh >= -0.01, f"SOC below 0: {soc_kwh}"
            assert soc_kwh <= capacity + 0.01, f"SOC above capacity: {soc_kwh}"

    @requires_solver
    def test_no_simultaneous_charge_discharge(self):
        """Battery should not charge and discharge in the same hour."""
        from src.optimization.model_builder import build_model, solve_model, extract_solution

        hours = 48
        rates = [0.21] * 15 + [0.44] + [0.51] * 5 + [0.44] * 3 + [0.21] * 15 + [0.44] + [0.51] * 5 + [0.44] * 3
        model = build_model(
            hours=hours,
            load=[2.0] * hours,
            solar=[0.0] * 7 + [3.0] * 10 + [0.0] * 7 + [0.0] * 7 + [3.0] * 10 + [0.0] * 7,
            import_rate=rates,
            export_rate=rates,
            battery_capacity_kwh=13.5,
            battery_max_power_kw=5.0,
            battery_efficiency=0.90,
        )
        results, solved = solve_model(model)
        solution = extract_solution(solved, hours)

        for t in range(hours):
            charge = solution["charge"][t]
            discharge = solution["discharge"][t]
            assert not (charge > 0.01 and discharge > 0.01), (
                f"Hour {t}: simultaneous charge ({charge}) and discharge ({discharge})"
            )

    @requires_solver
    def test_two_batteries_more_capacity(self):
        """Two batteries should enable more arbitrage than one."""
        from src.optimization.battery_optimizer import optimize_dispatch

        data = _make_interval_data(days=3)
        rates = _make_rate_config()

        result_1 = optimize_dispatch(data, _make_system_config(1), rates, horizon_days=3)
        result_2 = optimize_dispatch(data, _make_system_config(2), rates, horizon_days=3)

        assert "error" not in result_1
        assert "error" not in result_2

        # Two batteries should save at least as much as one
        assert result_2["savings"]["savings_dollars"] >= result_1["savings"]["savings_dollars"] - 0.1


class TestNEMVersions:
    """Test that NEM2 vs NEM3 produce different dispatch schedules."""

    @requires_solver
    def test_nem2_vs_nem3_different(self):
        """NEM2 (full retail) vs NEM3 (flat $0.08) should yield different costs."""
        from src.optimization.battery_optimizer import optimize_dispatch

        data = _make_interval_data(days=2)
        system = _make_system_config()
        rates = _make_rate_config()

        result_nem2 = optimize_dispatch(data, system, rates, nem_version="NEM2",
                                        horizon_days=2)
        result_nem3 = optimize_dispatch(data, system, rates, nem_version="NEM3",
                                        horizon_days=2)

        assert "error" not in result_nem2
        assert "error" not in result_nem3

        cost_nem2 = result_nem2["totals"]["net_cost"]
        cost_nem3 = result_nem3["totals"]["net_cost"]

        # NEM3 has lower export credits, so cost should differ
        assert cost_nem2 != cost_nem3, (
            f"NEM2 and NEM3 produced identical costs: {cost_nem2}"
        )


class TestEdgeCases:
    """Test error handling and edge cases."""

    def test_no_working_batteries(self):
        """Should return helpful error if no working batteries."""
        from src.optimization.battery_optimizer import optimize_dispatch

        data = _make_interval_data(days=1)
        system = _make_system_config(battery_status="needs_repair")
        rates = _make_rate_config()

        result = optimize_dispatch(data, system, rates, horizon_days=1)
        assert "error" in result
        assert "working" in result["error"].lower() or "battery" in result["error"].lower()

    def test_empty_interval_data(self):
        """Should return error for empty data."""
        from src.optimization.battery_optimizer import optimize_dispatch

        system = _make_system_config()
        rates = _make_rate_config()

        result = optimize_dispatch([], system, rates, horizon_days=1)
        assert "error" in result

    def test_graceful_pyomo_import_failure(self, monkeypatch):
        """Should return error message if Pyomo not importable, not crash."""
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "pyomo.environ" or name == "pyomo":
                raise ImportError("No module named 'pyomo'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        # Need to reimport to trigger the mock
        import importlib
        import src.optimization.battery_optimizer as mod
        importlib.reload(mod)

        data = _make_interval_data(days=1)
        system = _make_system_config()
        rates = _make_rate_config()

        result = mod.optimize_dispatch(data, system, rates, horizon_days=1)
        assert "error" in result
        assert "pyomo" in result["error"].lower() or "install" in result["error"].lower()

        # Restore module
        importlib.reload(mod)


class TestScheduleFormatter:
    """Test the schedule formatter independently."""

    def test_format_basic_schedule(self):
        from src.optimization.schedule_formatter import format_schedule

        hours = 24
        solution = {
            "charge": [1.0] * 8 + [0.0] * 16,
            "discharge": [0.0] * 8 + [0.0] * 8 + [1.0] * 5 + [0.0] * 3,
            "grid_import": [2.0] * 8 + [0.0] * 8 + [1.0] * 5 + [1.5] * 3,
            "grid_export": [0.0] * 8 + [3.0] * 8 + [0.0] * 8,
            "soc": [i * 0.9 for i in range(1, 9)] + [7.2] * 8 + [7.2 - i for i in range(1, 6)] + [2.2] * 3,
            "is_charging": [1.0] * 8 + [0.0] * 16,
        }

        interval_data = []
        for h in range(24):
            interval_data.append({
                "date": "2025-07-15",
                "hour": h,
                "month": 7,
                "day_of_week": 1,
                "tou_period": "summer_off_peak" if h < 15 else "summer_peak",
            })

        import_rates = [0.21] * 15 + [0.51] * 5 + [0.44] * 4
        export_rates = import_rates

        result = format_schedule(
            solution, interval_data, import_rates, export_rates,
            battery_capacity_kwh=13.5,
        )

        assert len(result["hourly_schedule"]) == 24
        assert "totals" in result
        assert "daily_summary" in result
        assert "tou_breakdown" in result
        assert result["totals"]["net_cost"] is not None

    def test_compute_baseline_cost(self):
        from src.optimization.schedule_formatter import compute_baseline_cost

        load = [3.0] * 24
        solar = [0.0] * 8 + [5.0] * 8 + [0.0] * 8
        rates = [0.25] * 24

        baseline = compute_baseline_cost(
            interval_data=[{}] * 24,
            load=load,
            solar=solar,
            import_rate=rates,
            export_rate=rates,
        )

        # 8h night * 3kWh = 24 + 8h day * 0kWh + 8h evening * 3kWh = 24 => 48kWh import
        # 8h day: 5-3=2kWh export * 8 = 16kWh export
        assert baseline["total_import_kwh"] == 48.0
        assert baseline["total_export_kwh"] == 16.0
        assert baseline["net_cost"] == round(48.0 * 0.25 - 16.0 * 0.25, 2)

    def test_compute_savings(self):
        from src.optimization.schedule_formatter import compute_savings

        optimized = {"totals": {"net_cost": 8.00, "total_import_kwh": 30.0}}
        baseline = {"net_cost": 12.00, "total_import_kwh": 48.0}

        savings = compute_savings(optimized, baseline)
        assert savings["savings_dollars"] == 4.00
        assert savings["savings_pct"] == pytest.approx(33.3, abs=0.1)
        assert savings["import_reduction_kwh"] == 18.0
