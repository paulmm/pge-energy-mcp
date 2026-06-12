"""PG&E observed holidays — weekday-only peak schedules treat them as off-peak."""

from datetime import date

from src.rates.holidays import is_pge_holiday
from src.rates.tou import classify_tou_period


class TestHolidayCalendar:
    def test_fixed_holidays(self):
        assert is_pge_holiday(date(2026, 1, 1))     # New Year's (Thu)
        assert is_pge_holiday(date(2026, 7, 4))     # Independence Day
        assert is_pge_holiday(date(2026, 11, 11))   # Veterans Day
        assert is_pge_holiday(date(2026, 12, 25))   # Christmas

    def test_floating_holidays_2026(self):
        assert is_pge_holiday(date(2026, 2, 16))    # Presidents Day (3rd Mon Feb)
        assert is_pge_holiday(date(2026, 5, 25))    # Memorial Day (last Mon May)
        assert is_pge_holiday(date(2026, 9, 7))     # Labor Day (1st Mon Sep)
        assert is_pge_holiday(date(2026, 11, 26))   # Thanksgiving (4th Thu Nov)

    def test_sunday_holiday_observed_monday(self):
        # July 4 2027 is a Sunday -> observed Monday July 5
        assert is_pge_holiday(date(2027, 7, 5))

    def test_ordinary_day_is_not_holiday(self):
        assert not is_pge_holiday(date(2026, 6, 12))


class TestTouHolidayClassification:
    def test_etoud_holiday_weekday_peak_hour_is_offpeak(self):
        # Christmas 2026 is a Friday (day_of_week=4); 5-8PM would be peak
        period, _ = classify_tou_period(18, 12, 4, schedule="E-TOU-D",
                                        date_str="2026-12-25")
        assert period == "off_peak"

    def test_etoud_normal_weekday_peak_unchanged(self):
        period, _ = classify_tou_period(18, 12, 4, schedule="E-TOU-D",
                                        date_str="2026-12-18")
        assert period == "peak"

    def test_ev2a_holiday_still_has_peak(self):
        # EV2-A peak applies every day including holidays
        period, _ = classify_tou_period(18, 12, 4, schedule="EV2-A",
                                        date_str="2026-12-25")
        assert period == "peak"
