"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db import init_db
from app.paths import frontend_dir
from app.routers import attachments, capabilities, chat, conversations, models, system
from app.services import ollama, searxng_manager

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("buddy")


async def _prepare_search() -> None:
    """Bring Buddy's own SearXNG up in the background.

    Detached from startup on purpose: the very first run clones a repository and
    installs dependencies, which takes minutes. Blocking boot on that would make
    the whole app look hung, and search has a working fallback in the meantime.
    """
    try:
        ok, err = await searxng_manager.start()
        if ok:
            logger.info("built-in search ready at %s", searxng_manager.local_url())
        else:
            logger.info("built-in search not ready (%s); using fallback for now", err)
    except Exception:  # noqa: BLE001 - a detached task must never vanish silently
        logger.exception("preparing built-in search failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Probe Ollama, initialize the database, and start the built-in search."""
    init_db()
    status = await ollama.get_status()
    if status.running:
        logger.info("Ollama %s reachable at %s", status.version, settings.ollama_base)
    else:
        logger.warning("Ollama not reachable: %s", status.error)

    search_task: asyncio.Task | None = None
    if settings.searxng_managed:
        search_task = asyncio.create_task(_prepare_search())

    try:
        yield
    finally:
        # Buddy started this process, so Buddy stops it - leaving an orphaned
        # SearXNG holding the port would break the next run.
        if search_task is not None and not search_task.done():
            search_task.cancel()
        await asyncio.to_thread(searxng_manager.stop)


app = FastAPI(
    title="Buddy",
    description="Hardware-aware local model picker and chat client for Ollama.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)

app.include_router(system.router)
app.include_router(models.router)
app.include_router(chat.router)
app.include_router(conversations.router)
app.include_router(attachments.router)
app.include_router(capabilities.router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe.

    The desktop shell polls this before showing its window: the backend has to
    import pandas and matplotlib, which takes a couple of seconds, and a window
    shown any earlier would render errors against a socket nothing is listening
    on yet.
    """
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Frontend
#
# Packaged, the backend serves the built UI itself, so the app is same-origin
# and CORS stops applying. Mounted last: every API router above already claimed
# its prefix, and this catch-all must not shadow them.
# --------------------------------------------------------------------------- #

_web_dir = frontend_dir()

if _web_dir.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=str(_web_dir / "assets")),
        name="assets",
    )

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(_web_dir / "index.html"))

    @app.get("/{asset_path:path}")
    async def spa(asset_path: str) -> FileResponse:
        """Serve a bundled file, falling back to index.html.

        The fallback is what lets the UI keep client-side routes working on a
        hard refresh; the traversal check keeps that from serving files outside
        the bundle.
        """
        candidate = (_web_dir / asset_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(_web_dir.resolve()):
            return FileResponse(str(candidate))
        return FileResponse(str(_web_dir / "index.html"))

else:

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"name": "Buddy API", "docs": "/docs"}
