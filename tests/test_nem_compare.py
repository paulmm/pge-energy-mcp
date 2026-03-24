"""Tests for NEM version comparison (NEM 2 vs NEM 3)."""

import pytest
from pathlib import Path

from src.parsers.green_button import parse as gb_parse
from src.analysis.nem_compare import compare_nem_versions

TEST_DATA = Path(__file__).resolve().parent.parent / "test_data"


@pytest.fixture(scope="module")
def intervals():
    with open(TEST_DATA / "green_button_sample.csv") as f:
        return gb_parse(f.read())["intervals"]


PLAN = {"schedule": "EV2-A", "provider": "PCE", "vintage_year": 2016, "income_tier": 3}


class TestNEMComparison:
    def test_nem3_more_expensive(self, intervals):
        """NEM 3 should always cost more than NEM 2 for solar exporters."""
        r = compare_nem_versions(intervals, PLAN)
        assert r["nem3"]["annual_total"] > r["nem2"]["annual_total"]

    def test_annual_increase_positive(self, intervals):
        r = compare_nem_versions(intervals, PLAN)
        assert r["transition_impact"]["annual_increase"] > 0

    def test_credit_loss_equals_increase(self, intervals):
        """The annual increase should equal the credit loss (import cost is the same)."""
        r = compare_nem_versions(intervals, PLAN)
        assert r["transition_impact"]["credit_loss"] == pytest.approx(
            r["transition_impact"]["annual_increase"], abs=0.05)

    def test_credit_retention_under_100(self, intervals):
        r = compare_nem_versions(intervals, PLAN)
        assert 0 < r["transition_impact"]["credit_retention_pct"] < 100

    def test_nem2_credit_higher_than_nem3(self, intervals):
        r = compare_nem_versions(intervals, PLAN)
        assert r["nem2"]["total_export_credit"] > r["nem3"]["total_export_credit"]

    def test_import_cost_same(self, intervals):
        """Import cost should be identical under both NEM versions."""
        r = compare_nem_versions(intervals, PLAN)
        nem2_import = r["nem2"]["annual_total"] - r["common"]["base_services_charge"] + r["nem2"]["total_export_credit"]
        nem3_import = r["nem3"]["annual_total"] - r["common"]["base_services_charge"] + r["nem3"]["total_export_credit"]
        assert nem2_import == pytest.approx(nem3_import, abs=0.05)

    def test_period_breakdown_has_exports(self, intervals):
        r = compare_nem_versions(intervals, PLAN)
        assert len(r["period_breakdown"]) > 0
        for key, data in r["period_breakdown"].items():
            assert data["export_kwh"] > 0
            assert data["nem2_credit"] > data["nem3_credit"]

    def test_monthly_breakdown_covers_all_months(self, intervals):
        r = compare_nem_versions(intervals, PLAN)
        assert len(r["monthly_breakdown"]) == 13  # Mar 2025 - Mar 2026

    def test_worst_months_sorted(self, intervals):
        r = compare_nem_versions(intervals, PLAN)
        worst = r["worst_months"]
        for i in range(len(worst) - 1):
            assert worst[i]["credit_loss"] >= worst[i + 1]["credit_loss"]

    def test_insights_generated(self, intervals):
        r = compare_nem_versions(intervals, PLAN)
        assert len(r["insights"]) >= 3

    def test_acc_summary_included(self, intervals):
        r = compare_nem_versions(intervals, PLAN)
        assert "acc_summary" in r
        assert r["acc_summary"]["annual_average"] > 0

    def test_eelec_also_works(self, intervals):
        """E-ELEC should also show NEM3 as more expensive."""
        plan = {"schedule": "E-ELEC", "provider": "PCE",
                "vintage_year": 2016, "income_tier": 3}
        r = compare_nem_versions(intervals, plan)
        assert r["nem3"]["annual_total"] > r["nem2"]["annual_total"]

    def test_increase_in_reasonable_range(self, intervals):
        """For a typical solar customer, NEM3 increase should be $200-$2000/yr."""
        r = compare_nem_versions(intervals, PLAN)
        increase = r["transition_impact"]["annual_increase"]
        assert 200 < increase < 2000
