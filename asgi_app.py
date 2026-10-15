"""ASGI host for Flask + MCP Streamable HTTP on the same Render service."""
from __future__ import annotations

import contextlib
import os
from typing import AsyncIterator

from starlette.applications import Starlette
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

import app as core
from mcp_server import mcp

MCP_API_KEY = os.getenv("MCP_API_KEY", "").strip()

class _MCPAuth:
    def __init__(self, inner): self.inner = inner
    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            headers = {k.decode().lower(): v.decode() for k,v in scope.get("headers", [])}
            if not MCP_API_KEY:
                resp = JSONResponse({"error":"MCP_API_KEY is not configured"}, status_code=503)
                await resp(scope, receive, send); return
            if headers.get("authorization") != f"Bearer {MCP_API_KEY}":
                resp = JSONResponse({"error":"unauthorized"}, status_code=401)
                await resp(scope, receive, send); return
        await self.inner(scope, receive, send)

mcp_asgi = _MCPAuth(mcp.streamable_http_app(streamable_http_path="/", json_response=True, stateless_http=True))

async def health(request: Request):
    return JSONResponse({"ok": True, "mcp": bool(MCP_API_KEY), "brand": core.BRAND_NAME})

@contextlib.asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield

app = Starlette(
    routes=[
        Route("/mcp-health", health, methods=["GET"]),
        Mount("/mcp", app=mcp_asgi),
        Mount("/", app=WSGIMiddleware(core.app)),
    ],
    lifespan=lifespan,
)
