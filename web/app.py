"""FastAPI application factory for the PG&E Energy web interface.

The web app shares the src/ engine with the MCP server, providing a
browser-based alternative for upload, comparison, profiling, and true-up
projection.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from web.routes import upload, compare, profile, trueup


def create_web_app() -> FastAPI:
    """Create and configure the FastAPI web application."""
    app = FastAPI(
        title="PG&E Energy Analyzer",
        description="Analyze PG&E solar + battery energy usage",
    )

    # Static files
    app.mount("/static", StaticFiles(directory="web/static"), name="static")

    # Routes
    app.include_router(upload.router)
    app.include_router(compare.router)
    app.include_router(profile.router)
    app.include_router(trueup.router)

    return app
