"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import init_db
from app.routers import attachments, capabilities, chat, conversations, models, system
from app.services import ollama

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("buddy")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Probe Ollama and initialize the conversation database at startup."""
    init_db()
    status = await ollama.get_status()
    if status.running:
        logger.info("Ollama %s reachable at %s", status.version, settings.ollama_base)
    else:
        logger.warning("Ollama not reachable: %s", status.error)
    yield


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


@app.get("/")
async def root() -> dict[str, str]:
    return {"name": "Buddy API", "docs": "/docs"}
