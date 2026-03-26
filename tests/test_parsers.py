"""Tests for Green Button and Tesla CSV parsers."""

import pytest
from pathlib import Path

from src.parsers.green_button import parse as gb_parse, _clean_number
from src.parsers.tesla import parse as tesla_parse, _parse_header

TEST_DATA = Path(__file__).resolve().parent.parent / "test_data"


# ── Green Button parser ──────────────────────────────────────────────


class TestGreenButtonParse:
    @pytest.fixture
    def parsed(self):
        with open(TEST_DATA / "green_button_sample.csv") as f:
            return gb_parse(f.read())

    def test_metadata_extracted(self, parsed):
        m = parsed["metadata"]
        assert "name" in m and len(m["name"]) > 0
        assert "address" in m and len(m["address"]) > 0
        assert "account" in m and len(m["account"]) > 0

    def test_interval_count(self, parsed):
        """~365 days × 24 hours ≈ 8,760 intervals."""
        n = parsed["summary"]["num_intervals"]
        assert 8700 < n < 8800

    def test_date_range(self, parsed):
        dr = parsed["summary"]["date_range"]
        assert dr["start"] == "2025-03-20"
        assert dr["end"] == "2026-03-19"

    def test_total_import_positive(self, parsed):
        assert parsed["summary"]["total_import_kwh"] > 5000

    def test_total_export_positive(self, parsed):
        assert parsed["summary"]["total_export_kwh"] > 500

    def test_interval_fields(self, parsed):
        iv = parsed["intervals"][0]
        assert set(iv.keys()) == {
            "date", "hour", "month", "day_of_week",
            "import_kwh", "export_kwh", "cost"
        }

    def test_hour_range(self, parsed):
        hours = {iv["hour"] for iv in parsed["intervals"]}
        assert hours == set(range(24))

    def test_month_range(self, parsed):
        months = {iv["month"] for iv in parsed["intervals"]}
        assert months == set(range(1, 13))

    def test_day_of_week_range(self, parsed):
        dows = {iv["day_of_week"] for iv in parsed["intervals"]}
        assert dows == set(range(7))


class TestGreenButtonEdgeCases:
    def test_bom_handling(self):
        csv = "\ufeffName,TEST\nAccount Number,123\n\nTYPE,DATE,START TIME,END TIME,IMPORT (kWh),EXPORT (kWh),COST,NOTES\nElectric usage,2025-06-01,00:00,00:59,1.5,0.0,$0.50\n"
        result = gb_parse(csv)
        assert result["metadata"]["name"] == "TEST"
        assert len(result["intervals"]) == 1

    def test_negative_cost_export_credit(self):
        """Export hours have negative cost like -$1.18."""
        csv = "Name,TEST\n\nTYPE,DATE,START TIME,END TIME,IMPORT (kWh),EXPORT (kWh),COST,NOTES\nElectric usage,2025-06-01,12:00,12:59,0.0,3.8,-$1.18\n"
        result = gb_parse(csv)
        assert result["intervals"][0]["cost"] == pytest.approx(-1.18)
        assert result["intervals"][0]["export_kwh"] == pytest.approx(3.8)

    def test_zero_values(self):
        csv = "Name,TEST\n\nTYPE,DATE,START TIME,END TIME,IMPORT (kWh),EXPORT (kWh),COST,NOTES\nElectric usage,2025-06-01,03:00,03:59,0.00,0.00,$0.00\n"
        result = gb_parse(csv)
        iv = result["intervals"][0]
        assert iv["import_kwh"] == 0.0
        assert iv["export_kwh"] == 0.0
        assert iv["cost"] == 0.0


class TestCleanNumber:
    def test_dollar_sign(self):
        assert _clean_number("$1.02") == 1.02

    def test_negative_dollar(self):
        assert _clean_number("-$1.18") == -1.18

    def test_comma_thousands(self):
        assert _clean_number("$1,234.56") == 1234.56

    def test_plain_number(self):
        assert _clean_number("2.94") == 2.94

    def test_empty_string(self):
        assert _clean_number("") == 0.0

    def test_whitespace(self):
        assert _clean_number("  $0.50  ") == 0.50


# ── Tesla parser ─────────────────────────────────────────────────────


class TestTeslaParse:
    def test_2025_units_detected(self):
        with open(TEST_DATA / "tesla_year_2025.csv") as f:
            result = tesla_parse(f.read())
        units = result["column_units"]
        # 2025: Home(MWh), Vehicle(kWh), Powerwall(kWh), Solar(MWh), Grid(MWh)
        assert any("MWh" == u for u in units.values())
        assert any("kWh" == u for u in units.values())

    def test_2026_solar_is_kwh(self):
        """2026 changed Solar from MWh to kWh."""
        with open(TEST_DATA / "tesla_year_2026.csv") as f:
            result = tesla_parse(f.read())
        solar_col = [c for c in result["column_units"] if "Solar" in c][0]
        assert result["column_units"][solar_col] == "kWh"

    def test_2025_solar_is_mwh(self):
        with open(TEST_DATA / "tesla_year_2025.csv") as f:
            result = tesla_parse(f.read())
        solar_col = [c for c in result["column_units"] if "Solar" in c][0]
        assert result["column_units"][solar_col] == "MWh"

    def test_2025_all_normalized_to_kwh(self):
        """After parsing, all values should be in kWh regardless of source unit."""
        with open(TEST_DATA / "tesla_year_2025.csv") as f:
            result = tesla_parse(f.read())
        # Home was in MWh, should be ~12,850 kWh not ~12.85
        assert result["totals"]["home_kwh"] > 1000

    def test_2026_solar_not_double_converted(self):
        """Solar is already in kWh for 2026 — should NOT multiply by 1000."""
        with open(TEST_DATA / "tesla_year_2026.csv") as f:
            result = tesla_parse(f.read())
        # 3 months of solar should be ~1,500 kWh, not 1,500,000
        assert result["totals"]["solar_kwh"] < 10000

    def test_month_count_2025(self):
        with open(TEST_DATA / "tesla_year_2025.csv") as f:
            result = tesla_parse(f.read())
        # Should have 12 months (some may be zero)
        assert len(result["months"]) == 12

    def test_month_count_2026(self):
        with open(TEST_DATA / "tesla_year_2026.csv") as f:
            result = tesla_parse(f.read())
        assert len(result["months"]) == 3

    def test_totals_are_sum_of_months(self):
        with open(TEST_DATA / "tesla_year_2025.csv") as f:
            result = tesla_parse(f.read())
        month_solar = sum(m["solar_kwh"] for m in result["months"])
        assert month_solar == pytest.approx(result["totals"]["solar_kwh"], abs=1)


class TestTeslaHeaderParsing:
    def test_mwh_detected(self):
        header = "Date time,Home (MWh),Solar Energy (MWh)"
        mapping = _parse_header(header)
        for col, (key, unit) in mapping.items():
            assert unit == "MWh"

    def test_kwh_detected(self):
        header = "Date time,Vehicle (kWh),From Powerwall (kWh)"
        mapping = _parse_header(header)
        for col, (key, unit) in mapping.items():
            assert unit == "kWh"

    def test_mixed_units(self):
        header = "Date time,Home (MWh),Vehicle (kWh),Solar Energy (kWh),From Grid (MWh)"
        mapping = _parse_header(header)
        mwh_count = sum(1 for _, (_, u) in mapping.items() if u == "MWh")
        kwh_count = sum(1 for _, (_, u) in mapping.items() if u == "kWh")
        assert mwh_count == 2
        assert kwh_count == 2
