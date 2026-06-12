"""Bill validation: recompute expected bill components from interval data."""

import pytest

from src.analysis.bill_validation import validate_bill


def _intervals(year_month="2026-01", days=30, import_per_hour=1.2,
               export_hours=(11, 12, 13), export_kwh=0.8):
    intervals = []
    for day in range(1, days + 1):
        dt = f"{year_month}-{day:02d}"
        for hour in range(24):
            intervals.append({
                "date": dt, "hour": hour, "month": int(year_month[5:7]),
                "day_of_week": (day - 1) % 7,
                "import_kwh": import_per_hour,
                "export_kwh": export_kwh if hour in export_hours else 0.0,
            })
    return intervals


PLAN = {"schedule": "EV2-A", "provider": "PCE", "vintage_year": 2016,
        "income_tier": 3}


class TestValidateBill:
    def test_components_present_and_consistent(self):
        result = validate_bill(
            _intervals(), PLAN, "2026-01-01", "2026-01-30",
            actual_charges={"total": None})
        exp = result["expected"]
        for key in ("pge_delivery", "generation", "pcia", "nbc_on_exports",
                    "base_services_charge", "total"):
            assert key in exp
        assert exp["total"] == pytest.approx(
            exp["pge_delivery"] + exp["generation"] + exp["pcia"]
            + exp["nbc_on_exports"] + exp["base_services_charge"], abs=0.05)

    def test_deltas_against_actuals(self):
        first = validate_bill(_intervals(), PLAN, "2026-01-01", "2026-01-30",
                              actual_charges={"total": None})
        actual_total = first["expected"]["total"]
        result = validate_bill(_intervals(), PLAN, "2026-01-01", "2026-01-30",
                               actual_charges={"total": actual_total})
        assert result["deltas"]["total"]["delta"] == pytest.approx(0.0, abs=0.01)
        assert result["match_quality"] == "good"

    def test_poor_match_flagged(self):
        result = validate_bill(_intervals(), PLAN, "2026-01-01", "2026-01-30",
                               actual_charges={"total": 9999.0})
        assert result["match_quality"] == "poor"
        assert result["notes"]  # explains the biggest contributor

    def test_no_data_in_period_errors(self):
        result = validate_bill(_intervals(), PLAN, "2025-06-01", "2025-06-30",
                               actual_charges={"total": 100.0})
        assert "error" in result
