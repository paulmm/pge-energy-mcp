"""Usage profile routes."""

from __future__ import annotations

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from web.session import get_session
from src.analysis.usage import profile

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

COOKIE_NAME = "pge_session"


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    sid = request.cookies.get(COOKIE_NAME)
    data = get_session(sid) if sid else None
    has_data = data is not None and "parsed" in data
    return templates.TemplateResponse("profile.html", {
        "request": request,
        "has_data": has_data,
    })


@router.post("/profile/analyze", response_class=HTMLResponse)
async def run_profile(
    request: Request,
    schedule: str = Form("EV2-A"),
):
    sid = request.cookies.get(COOKIE_NAME)
    data = get_session(sid) if sid else None
    if not data or "parsed" not in data:
        return HTMLResponse(
            '<p class="error">No data uploaded. Please upload a Green Button CSV first.</p>',
            status_code=400,
        )

    intervals = data["parsed"]["intervals"]
    results = profile(intervals, schedule=schedule)

    return templates.TemplateResponse("partials/profile_results.html", {
        "request": request,
        "results": results,
    })
