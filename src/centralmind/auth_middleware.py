"""Bearer-token auth middleware for the network-facing MCP HTTP transport."""

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Rejects any request whose `Authorization: Bearer <token>` header does
    not match the server's configured API key. Applied only to the network
    MCP transport — never to the loopback-only admin UI, which has its own
    token gate."""

    def __init__(self, app, api_key: str):
        super().__init__(app)
        self._api_key = api_key

    async def dispatch(self, request: Request, call_next):
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token, self._api_key):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)
