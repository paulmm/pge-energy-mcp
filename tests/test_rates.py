"""Tests for the rate engine — the most critical module."""

import pytest
from src.rates.engine import lookup_rates
from src.rates.tou import classify_tou_period, get_schedule_config, classify_season


# ── Rate engine: effective rate calculations ──────────────────────────


class TestEffectiveRates:
    """Verify effective_rate = pge_delivery + cca_generation + pcia_vintage."""

    def test_ev2a_pce_2016_winter_offpeak(self):
        """Golden number from CLAUDE.md: $0.13012 + $0.03936 + $0.03687 = $0.20635."""
        r = lookup_rates("EV2-A", "PCE", 2016, 3)
        assert r["effective_rates"]["winter"]["off_peak"] == pytest.approx(0.20635, abs=1e-5)

    def test_ev2a_pce_2016_summer_peak(self):
        """Golden number from CLAUDE.md: $0.34979 + $0.12291 + $0.03687 = $0.50957."""
        r = lookup_rates("EV2-A", "PCE", 2016, 3)
        assert r["effective_rates"]["summer"]["peak"] == pytest.approx(0.50957, abs=1e-5)

    def test_ev2a_pce_all_periods_have_rates(self):
        r = lookup_rates("EV2-A", "PCE", 2016, 3)
        for season in ["summer", "winter"]:
            for period in ["peak", "partial_peak", "off_peak"]:
                rate = r["effective_rates"][season][period]
                assert rate > 0, f"Missing rate for {season} {period}"

    def test_eelec_pce_has_rates(self):
        r = lookup_rates("E-ELEC", "PCE", 2016, 3)
        for season in ["summer", "winter"]:
            for period in ["peak", "partial_peak", "off_peak"]:
                assert r["effective_rates"][season][period] > 0

    def test_bundled_uses_total_rate(self):
        """Bundled customers pay total_bundled rate — no PCIA."""
        r = lookup_rates("EV2-A", "PGE_BUNDLED", income_tier=3)
        assert r["vintage_year"] is None
        assert r["components"]["pcia_per_kwh"] == 0.0
        # Bundled winter off-peak should be higher than CCA
        assert r["effective_rates"]["winter"]["off_peak"] == pytest.approx(0.22558, abs=1e-5)

    def test_bundled_rate_equals_delivery_plus_generation(self):
        """Bundled total should equal delivery + generation components."""
        r = lookup_rates("EV2-A", "PGE_BUNDLED", income_tier=3)
        delivery = r["components"]["delivery"]["winter"]["off_peak"]
        generation = r["components"]["generation"]["winter"]["off_peak"]
        total = r["effective_rates"]["winter"]["off_peak"]
        assert delivery + generation == pytest.approx(total, abs=1e-4)


# ── Rate engine: PCIA vintage handling ────────────────────────────────


class TestPCIA:
    def test_vintage_2016(self):
        r = lookup_rates("EV2-A", "PCE", 2016, 3)
        assert r["components"]["pcia_per_kwh"] == pytest.approx(0.03687, abs=1e-5)

    def test_vintage_2025_is_credit(self):
        """2025+ vintages are negative (credit)."""
        r = lookup_rates("EV2-A", "PCE", 2025, 3)
        assert r["components"]["pcia_per_kwh"] < 0

    def test_vintage_affects_effective_rate(self):
        r_2016 = lookup_rates("EV2-A", "PCE", 2016, 3)
        r_2025 = lookup_rates("EV2-A", "PCE", 2025, 3)
        # 2025 vintage pays less than 2016
        assert (r_2025["effective_rates"]["winter"]["off_peak"]
                < r_2016["effective_rates"]["winter"]["off_peak"])


# ── Rate engine: base services charge ─────────────────────────────────


class TestBSC:
    def test_tier_3_standard(self):
        r = lookup_rates("EV2-A", "PCE", 2016, 3)
        assert r["base_services_charge_daily"] == pytest.approx(0.79343, abs=1e-5)

    def test_tier_1_care_is_cheapest(self):
        r1 = lookup_rates("EV2-A", "PCE", 2016, 1)
        r3 = lookup_rates("EV2-A", "PCE", 2016, 3)
        assert r1["base_services_charge_daily"] < r3["base_services_charge_daily"]

    def test_all_tiers(self):
        for tier in [1, 2, 3]:
            r = lookup_rates("EV2-A", "PCE", 2016, tier)
            assert r["base_services_charge_daily"] > 0


# ── Rate engine: error handling ───────────────────────────────────────


class TestRateErrors:
    def test_unknown_schedule(self):
        with pytest.raises(ValueError, match="Unknown schedule"):
            lookup_rates("FAKE-PLAN", "PCE", 2016, 3)

    def test_unknown_provider(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            lookup_rates("EV2-A", "FAKE_PROVIDER", 2016, 3)

    def test_provider_missing_schedule(self):
        """SVCE has no rates defined yet."""
        with pytest.raises(ValueError, match="no rates"):
            lookup_rates("EV2-A", "SVCE", 2017, 3)


# ── TOU classification ───────────────────────────────────────────────


class TestTOUClassification:
    """EV2-A TOU: peak 4-9 PM, partial 3-4 PM & 9 PM-midnight, off midnight-3 PM. Daily."""

    def test_ev2a_offpeak_morning(self):
        period, _ = classify_tou_period(8, 1, 0, schedule="EV2-A")
        assert period == "off_peak"

    def test_ev2a_peak_evening(self):
        period, _ = classify_tou_period(17, 1, 0, schedule="EV2-A")
        assert period == "peak"

    def test_ev2a_partial_peak_3pm(self):
        period, _ = classify_tou_period(15, 1, 0, schedule="EV2-A")
        assert period == "partial_peak"

    def test_ev2a_partial_peak_9pm(self):
        period, _ = classify_tou_period(21, 1, 0, schedule="EV2-A")
        assert period == "partial_peak"

    def test_ev2a_peak_applies_weekends(self):
        """EV2-A peak is every day including weekends."""
        period, _ = classify_tou_period(17, 7, 5, schedule="EV2-A")  # Saturday
        assert period == "peak"

    def test_ev2a_peak_applies_sunday(self):
        period, _ = classify_tou_period(18, 7, 6, schedule="EV2-A")  # Sunday
        assert period == "peak"


class TestETOUDWeekdayOnly:
    """E-TOU-D: peak 5-8 PM weekdays ONLY. Weekends/holidays → off-peak."""

    def test_weekday_peak(self):
        period, _ = classify_tou_period(17, 6, 1, schedule="E-TOU-D")  # Tuesday
        assert period == "peak"

    def test_weekend_same_hour_is_offpeak(self):
        period, _ = classify_tou_period(17, 6, 5, schedule="E-TOU-D")  # Saturday
        assert period == "off_peak"

    def test_sunday_peak_hour_is_offpeak(self):
        period, _ = classify_tou_period(18, 6, 6, schedule="E-TOU-D")  # Sunday
        assert period == "off_peak"

    def test_weekday_non_peak_is_offpeak(self):
        period, _ = classify_tou_period(10, 6, 2, schedule="E-TOU-D")  # Wed morning
        assert period == "off_peak"


class TestSeasonClassification:
    """EV2-A summer = Jun-Sep (6-9). E-TOU-D summer = May-Oct (5-10)."""

    def test_ev2a_summer(self):
        _, season = classify_tou_period(12, 7, 0, schedule="EV2-A")
        assert season == "summer"

    def test_ev2a_winter(self):
        _, season = classify_tou_period(12, 1, 0, schedule="EV2-A")
        assert season == "winter"

    def test_ev2a_may_is_winter(self):
        """May is winter for EV2-A (summer starts June)."""
        _, season = classify_tou_period(12, 5, 0, schedule="EV2-A")
        assert season == "winter"

    def test_etoud_may_is_summer(self):
        """May is summer for E-TOU-D."""
        _, season = classify_tou_period(12, 5, 0, schedule="E-TOU-D")
        assert season == "summer"

    def test_etoud_october_is_summer(self):
        _, season = classify_tou_period(12, 10, 0, schedule="E-TOU-D")
        assert season == "summer"

    def test_ev2a_october_is_winter(self):
        """October is winter for EV2-A (summer ends Sept)."""
        _, season = classify_tou_period(12, 10, 0, schedule="EV2-A")
        assert season == "winter"


class TestAllHoursCovered:
    """Every hour of the day must classify into exactly one TOU period."""

    @pytest.mark.parametrize("schedule", ["EV2-A", "E-ELEC", "E-TOU-C", "E-TOU-D"])
    def test_all_24_hours_classified(self, schedule):
        for hour in range(24):
            period, season = classify_tou_period(hour, 7, 2, schedule=schedule)
            assert period in ("peak", "partial_peak", "off_peak")
            assert season in ("summer", "winter")
