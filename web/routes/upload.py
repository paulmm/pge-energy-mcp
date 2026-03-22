"""Upload routes: landing page and Green Button CSV upload."""

from __future__ import annotations

from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from web.session import create_session, get_session, set_session_data
from src.parsers.green_button import parse as gb_parse

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

COOKIE_NAME = "pge_session"


def _get_or_create_session(request: Request) -> tuple[str, dict | None]:
    """Return (session_id, session_data). Creates session if needed."""
    sid = request.cookies.get(COOKIE_NAME)
    data = None
    if sid:
        data = get_session(sid)
    if data is None:
        sid = create_session()
        data = get_session(sid)
    return sid, data


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    sid, data = _get_or_create_session(request)
    has_data = data is not None and "parsed" in data
    summary = data.get("summary") if has_data else None
    response = templates.TemplateResponse("index.html", {
        "request": request,
        "has_data": has_data,
        "summary": summary,
    })
    response.set_cookie(COOKIE_NAME, sid, httponly=True, samesite="lax")
    return response


@router.post("/upload", response_class=HTMLResponse)
async def upload(request: Request, file: UploadFile = File(...)):
    sid, data = _get_or_create_session(request)

    content = await file.read()
    csv_text = content.decode("utf-8-sig")

    parsed = gb_parse(csv_text)
    set_session_data(sid, "parsed", parsed)
    set_session_data(sid, "summary", parsed["summary"])
    set_session_data(sid, "metadata", parsed["metadata"])

    response = templates.TemplateResponse("partials/upload_summary.html", {
        "request": request,
        "summary": parsed["summary"],
        "metadata": parsed["metadata"],
    })
    response.set_cookie(COOKIE_NAME, sid, httponly=True, samesite="lax")
    return response
