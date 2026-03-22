"""Plan comparison routes."""

from __future__ import annotations

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from web.session import get_session
from src.analysis.compare import compare

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

COOKIE_NAME = "pge_session"

AVAILABLE_PLANS = ["EV2-A", "E-ELEC", "E-TOU-C", "E-TOU-D"]
PROVIDERS = [
    ("PGE_BUNDLED", "PG&E Bundled"),
    ("PCE", "Peninsula Clean Energy"),
    ("SVCE", "Silicon Valley Clean Energy"),
    ("MCE", "MCE Clean Energy"),
    ("SJCE", "San Jose Clean Energy"),
    ("EBCE", "East Bay Community Energy"),
]
VINTAGE_YEARS = list(range(2009, 2027))
INCOME_TIERS = [(1, "Tier 1 (CARE)"), (2, "Tier 2 (FERA)"), (3, "Tier 3 (Standard)")]


@router.get("/compare", response_class=HTMLResponse)
async def compare_page(request: Request):
    sid = request.cookies.get(COOKIE_NAME)
    data = get_session(sid) if sid else None
    has_data = data is not None and "parsed" in data
    return templates.TemplateResponse("compare.html", {
        "request": request,
        "has_data": has_data,
        "plans": AVAILABLE_PLANS,
        "providers": PROVIDERS,
        "vintage_years": VINTAGE_YEARS,
        "income_tiers": INCOME_TIERS,
    })


@router.post("/compare", response_class=HTMLResponse)
async def run_compare(
    request: Request,
    schedules: list[str] = Form(...),
    provider: str = Form("PGE_BUNDLED"),
    vintage_year: int = Form(2016),
    income_tier: int = Form(3),
    nem_version: str = Form("NEM2"),
):
    sid = request.cookies.get(COOKIE_NAME)
    data = get_session(sid) if sid else None
    if not data or "parsed" not in data:
        return HTMLResponse(
            '<p class="error">No data uploaded. Please upload a Green Button CSV first.</p>',
            status_code=400,
        )

    intervals = data["parsed"]["intervals"]
    plans = [
        {
            "schedule": s,
            "provider": provider,
            "vintage_year": vintage_year,
            "income_tier": income_tier,
        }
        for s in schedules
    ]

    results = compare(intervals, plans, nem_version)

    return templates.TemplateResponse("partials/compare_results.html", {
        "request": request,
        "results": results,
    })
