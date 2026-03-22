"""Tests for plan comparison and usage profiler against real data."""

import pytest
from pathlib import Path

from src.parsers.green_button import parse as gb_parse
from src.analysis.compare import compare
from src.analysis.usage import profile

TEST_DATA = Path(__file__).resolve().parent.parent / "test_data"


@pytest.fixture(scope="module")
def intervals():
    """Load real Green Button data once for all tests in this module."""
    with open(TEST_DATA / "green_button_sample.csv") as f:
        return gb_parse(f.read())["intervals"]


# ── Plan comparison ──────────────────────────────────────────────────


class TestPlanComparison:
    EELEC_PCE = {"schedule": "E-ELEC", "provider": "PCE", "vintage_year": 2016, "income_tier": 3}
    EV2A_PCE = {"schedule": "EV2-A", "provider": "PCE", "vintage_year": 2016, "income_tier": 3}

    def test_ev2a_cheaper_than_eelec(self, intervals):
        """Core acceptance criterion: EV2-A must save money over E-ELEC."""
        result = compare(intervals, [self.EELEC_PCE, self.EV2A_PCE], "NEM2")
        eelec_cost = result["plans"][0]["annual_total"]
        ev2a_cost = result["plans"][1]["annual_total"]
        assert ev2a_cost < eelec_cost

    def test_ev2a_savings_positive(self, intervals):
        result = compare(intervals, [self.EELEC_PCE, self.EV2A_PCE], "NEM2")
        assert result["max_savings"] > 0

    def test_cheapest_is_ev2a(self, intervals):
        result = compare(intervals, [self.EELEC_PCE, self.EV2A_PCE], "NEM2")
        assert result["cheapest_plan"]["schedule"] == "EV2-A"

    def test_baseline_savings_is_zero(self, intervals):
        result = compare(intervals, [self.EELEC_PCE, self.EV2A_PCE], "NEM2")
        assert result["plans"][0]["savings_vs_baseline"] == 0.0

    def test_annual_total_includes_bsc(self, intervals):
        result = compare(intervals, [self.EV2A_PCE], "NEM2")
        plan = result["plans"][0]
        assert plan["base_services_charge"] > 0
        assert plan["annual_total"] == pytest.approx(
            plan["net_energy_cost"] + plan["base_services_charge"], abs=0.05
        )

    def test_export_credits_reduce_cost(self, intervals):
        result = compare(intervals, [self.EV2A_PCE], "NEM2")
        plan = result["plans"][0]
        assert plan["total_export_credit"] > 0
        assert plan["net_energy_cost"] < plan["total_import_cost"]

    def test_tou_breakdown_present(self, intervals):
        result = compare(intervals, [self.EV2A_PCE], "NEM2")
        breakdown = result["plans"][0]["tou_breakdown"]
        assert len(breakdown) > 0
        # Should have summer and winter periods
        seasons = {k.split("_")[0] for k in breakdown.keys()}
        assert "summer" in seasons
        assert "winter" in seasons

    def test_season_summary_present(self, intervals):
        result = compare(intervals, [self.EV2A_PCE], "NEM2")
        ss = result["plans"][0]["season_summary"]
        assert "summer" in ss
        assert "winter" in ss
        assert ss["winter"]["net"] > ss["summer"]["net"]  # Winter dominates


class TestPlanComparisonBundled:
    def test_bundled_vs_cca(self, intervals):
        """Bundled PG&E should be more expensive than CCA+PCE for this customer."""
        plans = [
            {"schedule": "EV2-A", "provider": "PGE_BUNDLED", "income_tier": 3},
            {"schedule": "EV2-A", "provider": "PCE", "vintage_year": 2016, "income_tier": 3},
        ]
        result = compare(intervals, plans, "NEM2")
        bundled = result["plans"][0]["annual_total"]
        cca = result["plans"][1]["annual_total"]
        # CCA should be cheaper (PCE rates + old PCIA < bundled generation)
        assert cca < bundled


# ── Usage profiler ───────────────────────────────────────────────────


class TestUsageProfile:
    def test_peak_exposure_matches_reference(self, intervals):
        """CLAUDE.md says peak imports = 14.1%."""
        p = profile(intervals)
        assert p["peak_exposure_pct"] == pytest.approx(14.1, abs=1.0)

    def test_offpeak_dominates(self, intervals):
        """Most imports should be off-peak (>70%)."""
        p = profile(intervals)
        assert p["off_peak_pct"] > 70

    def test_tou_percentages_sum_to_100(self, intervals):
        p = profile(intervals)
        total = p["peak_exposure_pct"] + p["partial_peak_exposure_pct"] + p["off_peak_pct"]
        assert total == pytest.approx(100.0, abs=0.5)

    def test_overnight_baseload_reasonable(self, intervals):
        """CLAUDE.md reference: 1.73 kWh/hr overnight. Allow some variance."""
        p = profile(intervals)
        assert 1.0 < p["overnight_baseload_kwh_per_hr"] < 3.0

    def test_winter_imports_higher_than_summer(self, intervals):
        """CLAUDE.md: winter = 2.7x summer imports."""
        p = profile(intervals)
        winter = p["seasonal_daily_averages"]["winter"]["avg_daily_import_kwh"]
        summer = p["seasonal_daily_averages"]["summer"]["avg_daily_import_kwh"]
        assert winter > summer

    def test_monthly_trends_cover_all_months(self, intervals):
        p = profile(intervals)
        months = {t["month"] for t in p["monthly_trends"]}
        assert months == set(range(1, 13))

    def test_top_import_days_sorted(self, intervals):
        p = profile(intervals)
        days = p["top_import_days"]
        assert len(days) == 10
        # Should be sorted descending
        for i in range(len(days) - 1):
            assert days[i]["import_kwh"] >= days[i + 1]["import_kwh"]

    def test_totals_match_parser(self, intervals):
        p = profile(intervals)
        # Cross-check with parser totals
        total_import = sum(iv["import_kwh"] for iv in intervals)
        assert p["total_import_kwh"] == pytest.approx(total_import, abs=1.0)
