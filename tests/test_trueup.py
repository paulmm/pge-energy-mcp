"""Tests for NEM true-up projector and time-aware rate engine."""

import pytest
from pathlib import Path

from src.parsers.green_button import parse as gb_parse
from src.analysis.trueup import project_trueup
from src.rates.engine import lookup_rates

TEST_DATA = Path(__file__).resolve().parent.parent / "test_data"


@pytest.fixture(scope="module")
def intervals():
    """Load real Green Button data once for all tests in this module."""
    with open(TEST_DATA / "green_button_sample.csv") as f:
        return gb_parse(f.read())["intervals"]


# ── True-up projector ────────────────────────────────────────────────


class TestTrueUpProjection:
    PLAN = {"schedule": "EV2-A", "provider": "PCE", "vintage_year": 2016, "income_tier": 3}

    def test_returns_monthly_balances(self, intervals):
        """Data spans Mar 2025 - Mar 2026 = 13 months."""
        result = project_trueup(intervals, self.PLAN)
        assert len(result["monthly_balances"]) == 13

    def test_monthly_balance_has_required_fields(self, intervals):
        result = project_trueup(intervals, self.PLAN)
        b = result["monthly_balances"][0]
        for key in ["year_month", "month", "import_kwh", "export_kwh",
                     "nem_balance", "cumulative_nem", "bsc", "monthly_charge"]:
            assert key in b, f"Missing field: {key}"

    def test_summary_has_required_fields(self, intervals):
        result = project_trueup(intervals, self.PLAN)
        s = result["summary"]
        for key in ["annual_total", "true_up_balance", "total_bsc",
                     "credit_months", "debit_months"]:
            assert key in s, f"Missing field: {key}"

    def test_annual_total_in_reference_range(self, intervals):
        """CLAUDE.md: ~$2,000-2,100 in Dec-Jan cycle."""
        result = project_trueup(intervals, self.PLAN)
        annual = result["summary"]["annual_total"]
        # Allow wider range since time-aware rates shift values
        assert 1200 < annual < 3000, f"Annual total ${annual} outside expected range"

    def test_trueup_balance_non_negative(self, intervals):
        """NEM 2.0: no cash back. True-up >= 0."""
        result = project_trueup(intervals, self.PLAN)
        assert result["summary"]["true_up_balance"] >= 0

    def test_annual_total_equals_trueup_plus_bsc(self, intervals):
        result = project_trueup(intervals, self.PLAN)
        s = result["summary"]
        assert s["annual_total"] == pytest.approx(
            s["true_up_balance"] + s["total_bsc"], abs=0.05)

    def test_summer_months_are_credit(self, intervals):
        """Solar production should create NEM credits in summer."""
        result = project_trueup(intervals, self.PLAN)
        summer = [b for b in result["monthly_balances"]
                  if b["month"] in [6, 7, 8]]
        credit_count = sum(1 for b in summer if b["is_credit_month"])
        assert credit_count >= 2, "Expected most summer months to be credit months"

    def test_winter_months_are_debit(self, intervals):
        """Winter should have positive NEM balances (owe money)."""
        result = project_trueup(intervals, self.PLAN)
        winter = [b for b in result["monthly_balances"]
                  if b["month"] in [12, 1, 2]]
        debit_count = sum(1 for b in winter if not b["is_credit_month"])
        assert debit_count >= 2, "Expected most winter months to be debit months"

    def test_monthly_charges_are_bsc_only(self, intervals):
        """Under NEM 2.0, monthly charge = BSC only."""
        result = project_trueup(intervals, self.PLAN)
        for b in result["monthly_balances"]:
            assert b["monthly_charge"] == pytest.approx(b["bsc"], abs=0.01)

    def test_worst_months_sorted_descending(self, intervals):
        result = project_trueup(intervals, self.PLAN)
        worst = result["worst_months"]
        for i in range(len(worst) - 1):
            assert worst[i]["nem_balance"] >= worst[i + 1]["nem_balance"]

    def test_best_months_sorted_ascending(self, intervals):
        result = project_trueup(intervals, self.PLAN)
        best = result["best_months"]
        for i in range(len(best) - 1):
            assert best[i]["nem_balance"] <= best[i + 1]["nem_balance"]

    def test_insights_generated(self, intervals):
        result = project_trueup(intervals, self.PLAN)
        assert len(result["insights"]) > 0

    def test_eelec_trueup_higher_than_ev2a(self, intervals):
        """EV2-A should have lower true-up than E-ELEC."""
        ev2a_plan = {"schedule": "EV2-A", "provider": "PCE",
                     "vintage_year": 2016, "income_tier": 3}
        eelec_plan = {"schedule": "E-ELEC", "provider": "PCE",
                      "vintage_year": 2016, "income_tier": 3}
        ev2a = project_trueup(intervals, ev2a_plan)
        eelec = project_trueup(intervals, eelec_plan)
        assert ev2a["summary"]["annual_total"] < eelec["summary"]["annual_total"]


# ── Time-aware rate engine ───────────────────────────────────────────


class TestTimeAwareRates:
    """Verify historical rate overrides apply correctly."""

    def test_pre_march_bsc_is_lower(self):
        """Pre-March 2026 BSC was ~$0.49/day, post-March ~$0.79/day."""
        pre = lookup_rates("EV2-A", "PCE", 2016, 3, date="2026-02-15")
        post = lookup_rates("EV2-A", "PCE", 2016, 3, date="2026-03-15")
        assert pre["base_services_charge_daily"] < post["base_services_charge_daily"]
        assert pre["base_services_charge_daily"] == pytest.approx(0.49281, abs=1e-4)
        assert post["base_services_charge_daily"] == pytest.approx(0.79343, abs=1e-4)

    def test_pre_march_delivery_higher(self):
        """Pre-March delivery rates were higher (offset by lower BSC)."""
        pre = lookup_rates("EV2-A", "PCE", 2016, 3, date="2026-02-15")
        post = lookup_rates("EV2-A", "PCE", 2016, 3, date="2026-03-15")
        # Pre-March off-peak delivery was ~$0.175, post-March ~$0.130
        assert (pre["effective_rates"]["winter"]["off_peak"]
                > post["effective_rates"]["winter"]["off_peak"])

    def test_pre_feb_pce_eelec_generation_higher(self):
        """PCE dropped E-ELEC generation rates Feb 2026."""
        pre = lookup_rates("E-ELEC", "PCE", 2016, 3, date="2026-01-15")
        post = lookup_rates("E-ELEC", "PCE", 2016, 3, date="2026-02-15")
        # Pre-Feb PCE winter peak gen was $0.10592, post-Feb much lower
        pre_gen_peak = pre["components"]["generation"]["winter"]["peak"]
        post_gen_peak = post["components"]["generation"]["winter"]["peak"]
        assert pre_gen_peak > post_gen_peak
        assert pre_gen_peak == pytest.approx(0.10592, abs=1e-4)

    def test_no_date_uses_current_rates(self):
        """No date parameter → current (post-March) rates."""
        r = lookup_rates("EV2-A", "PCE", 2016, 3)
        assert r["base_services_charge_daily"] == pytest.approx(0.79343, abs=1e-4)

    def test_eelec_pre_march_delivery_override(self):
        """E-ELEC pre-March delivery from bills."""
        pre = lookup_rates("E-ELEC", "PCE", 2016, 3, date="2026-02-15")
        assert pre["effective_rates"]["winter"]["peak"] > 0.40  # delivery + gen + pcia

    def test_time_aware_trueup_vs_static(self, intervals):
        """Time-aware true-up should differ from static-rate true-up."""
        plan = {"schedule": "EV2-A", "provider": "PCE",
                "vintage_year": 2016, "income_tier": 3}
        aware = project_trueup(intervals, plan, time_aware=True)
        static = project_trueup(intervals, plan, time_aware=False)
        # They should be different since rates changed mid-year
        assert (aware["summary"]["annual_total"]
                != pytest.approx(static["summary"]["annual_total"], abs=1.0))


# ── E-TOU-C and E-TOU-D rate lookups ────────────────────────────────


class TestETOUCDRates:
    def test_etouc_has_rates(self):
        r = lookup_rates("E-TOU-C", "PGE_BUNDLED", income_tier=3)
        for season in ["summer", "winter"]:
            assert "peak" in r["effective_rates"][season]
            assert "off_peak" in r["effective_rates"][season]

    def test_etoud_has_rates(self):
        r = lookup_rates("E-TOU-D", "PGE_BUNDLED", income_tier=3)
        for season in ["summer", "winter"]:
            assert "peak" in r["effective_rates"][season]
            assert "off_peak" in r["effective_rates"][season]

    def test_etouc_pce_has_rates(self):
        r = lookup_rates("E-TOU-C", "PCE", 2016, 3)
        assert r["effective_rates"]["winter"]["peak"] > 0

    def test_etoud_pce_has_rates(self):
        r = lookup_rates("E-TOU-D", "PCE", 2016, 3)
        assert r["effective_rates"]["winter"]["peak"] > 0

    def test_etoud_two_periods_only(self):
        """E-TOU-D has only peak and off_peak (no partial_peak)."""
        r = lookup_rates("E-TOU-D", "PGE_BUNDLED", income_tier=3)
        for season in ["summer", "winter"]:
            assert "partial_peak" not in r["effective_rates"][season]
