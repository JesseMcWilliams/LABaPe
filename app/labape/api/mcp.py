"""MCP over HTTP (design §59.5, batch X4): `POST /mcp` takes JSON-RPC messages and answers with JSON (the
streamable HTTP transport without server-initiated streams). The caller's bearer token is forwarded to
the API in-process, so every tool is the same REST call, with the same checks, a script would make.
"""
from __future__ import annotations

import json

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .. import mcp
from ..security import client_ip

router = APIRouter(tags=["mcp"])


def _sender(request: Request, auth: str):
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=request.app), base_url="http://labape.internal",
                               headers={"Authorization": auth, "X-Forwarded-For": client_ip(request)}, timeout=130)

    async def send(method, path, params, body):
        r = await client.request(method, path, params=params or None, json=body)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, r.text
    send.client = client
    return send


@router.post("/mcp", include_in_schema=False)
async def mcp_post(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return JSONResponse({"detail": "Send your API token as Authorization: Bearer ..."}, status_code=401,
                            headers={"WWW-Authenticate": 'Bearer realm="labape"'})
    try:
        msg = json.loads(await request.body())
    except ValueError:
        return JSONResponse(mcp._error(None, -32700, "Parse error"), status_code=400)
    send = _sender(request, auth)
    try:
        session = mcp.Session(send)
        batch = msg if isinstance(msg, list) else [msg]
        out = [r for r in [await session.handle(m) for m in batch] if r is not None]
    finally:
        await send.client.aclose()
    if not out:
        return Response(status_code=202)                      # only notifications
    return JSONResponse(out if isinstance(msg, list) else out[0])


@router.get("/mcp", include_in_schema=False)
async def mcp_get():
    return Response(status_code=405, headers={"Allow": "POST"})
