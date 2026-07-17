"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import entities, proposals, sessions
from app.config import get_settings
from app.db.session import dispose_engine, init_db
from app.services.pipeline import recover_interrupted_sessions

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings().data_dir.mkdir(parents=True, exist_ok=True)
    await init_db()
    recovered = await recover_interrupted_sessions()
    if recovered:
        log.warning("marked %d interrupted session(s) as failed", recovered)
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(title="paperless-llm", lifespan=lifespan)

    app.include_router(proposals.router)
    app.include_router(sessions.router)
    app.include_router(entities.router)

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

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
