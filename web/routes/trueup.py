"""True-up projection routes."""

from __future__ import annotations

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from web.session import get_session
from src.analysis.trueup import project_trueup

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

COOKIE_NAME = "pge_session"

MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


@router.get("/trueup", response_class=HTMLResponse)
async def trueup_page(request: Request):
    sid = request.cookies.get(COOKIE_NAME)
    data = get_session(sid) if sid else None
    has_data = data is not None and "parsed" in data
    return templates.TemplateResponse("trueup.html", {
        "request": request,
        "has_data": has_data,
        "months": [(i, MONTH_NAMES[i]) for i in range(1, 13)],
    })


@router.post("/trueup/project", response_class=HTMLResponse)
async def run_trueup(
    request: Request,
    schedule: str = Form("EV2-A"),
    provider: str = Form("PGE_BUNDLED"),
    vintage_year: int = Form(2016),
    income_tier: int = Form(3),
    nem_version: str = Form("NEM2"),
    true_up_month: int = Form(1),
):
    sid = request.cookies.get(COOKIE_NAME)
    data = get_session(sid) if sid else None
    if not data or "parsed" not in data:
        return HTMLResponse(
            '<p class="error">No data uploaded. Please upload a Green Button CSV first.</p>',
            status_code=400,
        )

    intervals = data["parsed"]["intervals"]
    plan = {
        "schedule": schedule,
        "provider": provider,
        "vintage_year": vintage_year,
        "income_tier": income_tier,
    }

    results = project_trueup(intervals, plan, nem_version, true_up_month)

    return templates.TemplateResponse("partials/trueup_results.html", {
        "request": request,
        "results": results,
        "month_names": MONTH_NAMES,
    })
