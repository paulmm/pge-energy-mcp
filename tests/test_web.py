"""Tests for the FastAPI web application."""

import io
import pytest
from fastapi.testclient import TestClient

from web.app import create_web_app


@pytest.fixture
def client():
    app = create_web_app()
    return TestClient(app)


@pytest.fixture
def sample_csv():
    """Minimal valid Green Button CSV for testing."""
    return (
        "\ufeff"
        "Name,TEST USER\n"
        "Address,\"123 TEST ST, CITY CA 94000\"\n"
        "Account Number,1234567890\n"
        "Service,Service 1\n"
        "\n"
        "TYPE,DATE,START TIME,END TIME,IMPORT (kWh),EXPORT (kWh),COST,NOTES\n"
        "Electric usage,2025-06-01,00:00,00:59,1.50,0.00,$0.45\n"
        "Electric usage,2025-06-01,01:00,01:59,1.20,0.00,$0.36\n"
        "Electric usage,2025-06-01,02:00,02:59,1.10,0.00,$0.33\n"
        "Electric usage,2025-06-01,03:00,03:59,1.00,0.00,$0.30\n"
        "Electric usage,2025-06-01,04:00,04:59,0.80,0.00,$0.24\n"
        "Electric usage,2025-06-01,05:00,05:59,0.60,0.00,$0.18\n"
        "Electric usage,2025-06-01,06:00,06:59,0.30,0.10,$0.09\n"
        "Electric usage,2025-06-01,07:00,07:59,0.10,0.50,$0.03\n"
        "Electric usage,2025-06-01,08:00,08:59,0.00,1.20,$0.00\n"
        "Electric usage,2025-06-01,09:00,09:59,0.00,1.80,$0.00\n"
        "Electric usage,2025-06-01,10:00,10:59,0.00,2.10,$0.00\n"
        "Electric usage,2025-06-01,11:00,11:59,0.00,2.30,$0.00\n"
        "Electric usage,2025-06-01,12:00,12:59,0.00,2.40,$0.00\n"
        "Electric usage,2025-06-01,13:00,13:59,0.00,2.20,$0.00\n"
        "Electric usage,2025-06-01,14:00,14:59,0.00,1.90,$0.00\n"
        "Electric usage,2025-06-01,15:00,15:59,0.20,1.00,$0.08\n"
        "Electric usage,2025-06-01,16:00,16:59,0.80,0.30,$0.35\n"
        "Electric usage,2025-06-01,17:00,17:59,1.50,0.00,$0.65\n"
        "Electric usage,2025-06-01,18:00,18:59,2.00,0.00,$0.90\n"
        "Electric usage,2025-06-01,19:00,19:59,1.80,0.00,$0.78\n"
        "Electric usage,2025-06-01,20:00,20:59,1.60,0.00,$0.60\n"
        "Electric usage,2025-06-01,21:00,21:59,1.40,0.00,$0.52\n"
        "Electric usage,2025-06-01,22:00,22:59,1.30,0.00,$0.39\n"
        "Electric usage,2025-06-01,23:00,23:59,1.20,0.00,$0.36\n"
    )


class TestAppCreation:
    def test_app_creates_successfully(self):
        app = create_web_app()
        assert app is not None
        assert app.title == "PG&E Energy Analyzer"

    def test_routes_registered(self, client):
        routes = [r.path for r in client.app.routes]
        assert "/" in routes
        assert "/compare" in routes
        assert "/profile" in routes
        assert "/trueup" in routes


class TestLandingPage:
    def test_index_returns_200(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "Upload Green Button Data" in response.text

    def test_index_sets_session_cookie(self, client):
        response = client.get("/")
        assert "pge_session" in response.cookies

    def test_index_shows_no_data_badge(self, client):
        response = client.get("/")
        assert "No Data" in response.text


class TestUpload:
    def test_upload_accepts_csv(self, client, sample_csv):
        response = client.post(
            "/upload",
            files={"file": ("test.csv", sample_csv.encode(), "text/csv")},
        )
        assert response.status_code == 200
        assert "Data Parsed Successfully" in response.text
        assert "24" in response.text  # 24 intervals

    def test_upload_shows_summary(self, client, sample_csv):
        response = client.post(
            "/upload",
            files={"file": ("test.csv", sample_csv.encode(), "text/csv")},
        )
        assert "Total Import" in response.text
        assert "Total Export" in response.text
        assert "2025-06-01" in response.text


class TestCompare:
    def test_compare_page_returns_200(self, client):
        response = client.get("/compare")
        assert response.status_code == 200
        assert "Rate Plan Comparison" in response.text

    def test_compare_without_data_shows_error(self, client):
        response = client.get("/compare")
        assert "No usage data loaded" in response.text

    def test_compare_with_data_returns_results(self, client, sample_csv):
        # Upload first
        client.post(
            "/upload",
            files={"file": ("test.csv", sample_csv.encode(), "text/csv")},
        )
        # Then compare
        response = client.post(
            "/compare",
            data={
                "schedules": ["EV2-A", "E-ELEC"],
                "provider": "PCE",
                "vintage_year": "2016",
                "income_tier": "3",
                "nem_version": "NEM2",
            },
        )
        assert response.status_code == 200
        assert "Annual Total" in response.text


class TestProfile:
    def test_profile_page_returns_200(self, client):
        response = client.get("/profile")
        assert response.status_code == 200
        assert "Usage Profile" in response.text

    def test_profile_without_data_shows_error(self, client):
        response = client.get("/profile")
        assert "No usage data loaded" in response.text

    def test_profile_with_data(self, client, sample_csv):
        client.post(
            "/upload",
            files={"file": ("test.csv", sample_csv.encode(), "text/csv")},
        )
        response = client.post(
            "/profile/analyze",
            data={"schedule": "EV2-A"},
        )
        assert response.status_code == 200
        assert "Peak Exposure" in response.text


class TestTrueUp:
    def test_trueup_page_returns_200(self, client):
        response = client.get("/trueup")
        assert response.status_code == 200
        assert "True-Up Projection" in response.text

    def test_trueup_without_data_shows_error(self, client):
        response = client.get("/trueup")
        assert "No usage data loaded" in response.text

    def test_trueup_with_data(self, client, sample_csv):
        client.post(
            "/upload",
            files={"file": ("test.csv", sample_csv.encode(), "text/csv")},
        )
        response = client.post(
            "/trueup/project",
            data={
                "schedule": "EV2-A",
                "provider": "PCE",
                "vintage_year": "2016",
                "income_tier": "3",
                "nem_version": "NEM2",
                "true_up_month": "1",
            },
        )
        assert response.status_code == 200
        assert "True-Up Summary" in response.text
        assert "Monthly Breakdown" in response.text
