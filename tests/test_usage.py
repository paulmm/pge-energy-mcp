"""Usage profiling — EV charging detection."""

import pytest

from src.analysis.usage import detect_ev_charging, profile
from src.rates.tou import get_schedule_config


def _build_intervals(ev_nights):
    """30 days of flat 0.8 kWh/h baseload; on ev_nights, add 7.2 kWh/h
    during hours 0-3 (L2 charging signature)."""
    intervals = []
    for day in range(1, 31):
        dt = f"2026-01-{day:02d}"
        for hour in range(24):
            imp = 0.8
            if day in ev_nights and hour in (0, 1, 2, 3):
                imp += 7.2
            intervals.append({
                "date": dt, "hour": hour, "month": 1,
                "day_of_week": (day - 1) % 7,
                "import_kwh": imp, "export_kwh": 0.0,
            })
    return intervals


class TestEVDetection:
    def test_detects_sessions(self):
        ev_nights = {2, 5, 9, 12, 16, 19, 23, 26}
        result = detect_ev_charging(
            _build_intervals(ev_nights), get_schedule_config("EV2-A"))
        assert result["detected"] is True
        assert result["num_sessions"] == len(ev_nights)
        # 4 h x 7.2 kWh above baseload per session
        assert result["estimated_ev_kwh"] == pytest.approx(
            len(ev_nights) * 4 * 7.2, rel=0.1)
        assert result["typical_start_hour"] == 0
        # Overnight charging on EV2-A is 100% off-peak
        assert result["pct_in_off_peak"] == pytest.approx(100.0)

    def test_no_ev_no_detection(self):
        result = detect_ev_charging(
            _build_intervals(set()), get_schedule_config("EV2-A"))
        assert result["detected"] is False

    def test_profile_includes_ev_block(self):
        result = profile(_build_intervals({3, 7}))
        assert "ev_charging" in result
