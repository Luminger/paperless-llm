"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.errors import register_error_handlers
from app.api.routes import (
    audit,
    entities,
    jobs,
    proposals,
    sessions,
    webhooks,
)
from app.api.routes import (
    settings as settings_routes,
)
from app.api.schemas import HealthOut, MetaOut
from app.config import get_settings
from app.db.migrations import run_migrations
from app.db.session import dispose_engine
from app.services.steps import recover, workers

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings().data_dir.mkdir(parents=True, exist_ok=True)
    await run_migrations()
    stats = await recover()
    if any(stats.values()):
        log.warning("startup recovery: %s", stats)
    import asyncio

    from app.services.paperless_log import drain, writer_loop

    traffic_writer = asyncio.create_task(writer_loop())
    await workers.start()
    yield
    await workers.stop()
    traffic_writer.cancel()
    from app.db.session import session_scope

    try:
        async with session_scope() as db:  # final flush
            await drain(db)
            await db.commit()
    except Exception:  # noqa: BLE001
        pass
    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(title="paperless-llm", lifespan=lifespan)
    register_error_handlers(app)

    @app.middleware("http")
    async def _actor_middleware(request, call_next):
        # Work caused by an API request is attributed to the user;
        # background work keeps the contextvar default ("system").
        from app.services.actor import actor_var

        token = actor_var.set("user")
        try:
            return await call_next(request)
        finally:
            actor_var.reset(token)

    app.include_router(proposals.router)
    app.include_router(sessions.router)
    app.include_router(entities.router)
    app.include_router(settings_routes.router)
    app.include_router(jobs.router)
    app.include_router(webhooks.router)
    app.include_router(audit.router)

    @app.get("/api/health")
    async def health() -> HealthOut:
        return HealthOut(status="ok")

    @app.get("/api/meta")
    async def meta() -> MetaOut:
        p = get_settings().paperless
        return MetaOut(paperless_url=(p.external_url or p.base_url).rstrip("/"))

    # Serve the built frontend when present (production container),
    # with an SPA fallback so deep links (/sessions/2) survive reloads.
    dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> FileResponse:
            file = (dist / path).resolve()
            if path and file.is_file() and file.is_relative_to(dist):
                return FileResponse(file)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
