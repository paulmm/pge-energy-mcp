"""Optional bearer-token authentication for the MCP HTTP transport.

When the MCP_AUTH_TOKEN environment variable is set, every HTTP request
must carry "Authorization: Bearer <token>". When unset, all requests pass
through unchanged (open mode — suitable only for deployments that hold no
credentials or stored user data).

Implemented as pure ASGI so it wraps FastMCP's streamable-http app without
depending on FastMCP internals, and passes lifespan/websocket scopes through.
"""

from __future__ import annotations

import hmac
import os

_EXEMPT_PATHS = {"/favicon.ico", "/icon.svg"}

_UNAUTHORIZED_BODY = (
    b'{"error": "unauthorized", '
    b'"message": "This server requires Authorization: Bearer <token>. '
    b'Ask the operator for the MCP_AUTH_TOKEN value."}'
)


class BearerAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Read per-request so the operator can rotate the token without
        # code changes and tests can monkeypatch the environment.
        token = os.environ.get("MCP_AUTH_TOKEN", "")
        if not token or scope.get("path") in _EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        provided = ""
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                provided = value.decode("latin-1")
                break

        expected = f"Bearer {token}"
        if hmac.compare_digest(provided.encode(), expected.encode()):
            await self.app(scope, receive, send)
            return

        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b"Bearer"),
            ],
        })
        await send({"type": "http.response.body", "body": _UNAUTHORIZED_BODY})
