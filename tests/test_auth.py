"""Tests for the optional bearer-token auth middleware."""

from starlette.testclient import TestClient

from src.auth import BearerAuthMiddleware


async def ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"ok"})


def _client():
    return TestClient(BearerAuthMiddleware(ok_app))


class TestOpenMode:
    def test_passthrough_when_no_token_configured(self, monkeypatch):
        monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
        assert _client().get("/mcp").status_code == 200


class TestAuthEnforced:
    def test_rejects_missing_header(self, monkeypatch):
        monkeypatch.setenv("MCP_AUTH_TOKEN", "sekret")
        resp = _client().get("/mcp")
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"] == "Bearer"

    def test_rejects_wrong_token(self, monkeypatch):
        monkeypatch.setenv("MCP_AUTH_TOKEN", "sekret")
        resp = _client().get("/mcp", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    def test_accepts_correct_token(self, monkeypatch):
        monkeypatch.setenv("MCP_AUTH_TOKEN", "sekret")
        resp = _client().get("/mcp", headers={"Authorization": "Bearer sekret"})
        assert resp.status_code == 200

    def test_icon_paths_exempt(self, monkeypatch):
        monkeypatch.setenv("MCP_AUTH_TOKEN", "sekret")
        assert _client().get("/favicon.ico").status_code == 200
        assert _client().get("/icon.svg").status_code == 200
