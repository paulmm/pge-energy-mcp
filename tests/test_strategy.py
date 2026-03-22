"""Tests for seasonal strategy recommendations."""

import pytest
from pathlib import Path

from src.parsers.green_button import parse as gb_parse
from src.rates.engine import lookup_rates
from src.analysis.strategy import seasonal_strategy

TEST_DATA = Path(__file__).resolve().parent.parent / "test_data"


@pytest.fixture(scope="module")
def intervals():
    with open(TEST_DATA / "green_button_sample.csv") as f:
        return gb_parse(f.read())["intervals"]


@pytest.fixture(scope="module")
def ev2a_rates():
    return lookup_rates("EV2-A", "PCE", 2016, 3)


class TestSeasonalStrategy:
    def test_returns_both_seasons(self, intervals, ev2a_rates):
        r = seasonal_strategy(intervals, ev2a_rates)
        assert "summer" in r["seasons"]
        assert "winter" in r["seasons"]

    def test_rate_spreads_positive(self, intervals, ev2a_rates):
        r = seasonal_strategy(intervals, ev2a_rates)
        for season in ["summer", "winter"]:
            spread = r["rate_spreads"][season]
            assert spread["peak_offpeak_spread"] > 0
            assert spread["arbitrage_value_per_kwh"] > 0

    def test_ev2a_high_spread_triggers_tou_recommendation(self, intervals, ev2a_rates):
        """EV2-A has large peak/off-peak spread — should recommend TOU dispatch."""
        r = seasonal_strategy(intervals, ev2a_rates)
        tou_recs = [rec for rec in r["recommendations"]
                    if rec["category"] == "battery_dispatch"]
        assert len(tou_recs) >= 1
        assert any(rec["priority"] == "high" for rec in tou_recs)

    def test_winter_dependency_flagged(self, intervals, ev2a_rates):
        """Winter imports >> summer — should flag solar expansion."""
        r = seasonal_strategy(intervals, ev2a_rates)
        solar_recs = [rec for rec in r["recommendations"]
                      if rec["category"] == "solar_expansion"]
        assert len(solar_recs) >= 1

    def test_monthly_trends_complete(self, intervals, ev2a_rates):
        r = seasonal_strategy(intervals, ev2a_rates)
        months = {t["month"] for t in r["monthly_trends"]}
        assert months == set(range(1, 13))

    def test_overnight_usage_flagged(self, intervals, ev2a_rates):
        """Heavy overnight usage months should get EV charging info."""
        r = seasonal_strategy(intervals, ev2a_rates)
        ev_recs = [rec for rec in r["recommendations"]
                   if rec["category"] == "ev_charging"]
        assert len(ev_recs) >= 1
