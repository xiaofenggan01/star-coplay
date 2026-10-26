from __future__ import annotations

from aiohttp import web


def setup_health_routes(app: web.Application) -> None:
    app.router.add_get("/api/ping", _ping)


async def _ping(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})
