"""FastAPI application entrypoint for Voice Khata."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.deps import get_container
from app.routers import auth, ledger, privacy, voice


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Initialize SQLite schema, encryption cipher, and synthetic kirana seed data
    get_container()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Voice Khata API (PS2)",
        description="Multilingual Kirana Voice Ledger powered by Gemini 3.8 Flash / 4 Argon / 3.8 Live + Gnani Adapter",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth.router)
    app.include_router(voice.router)
    app.include_router(voice.ws_router)
    app.include_router(ledger.router)
    app.include_router(privacy.router)

    @app.get("/health")
    def health() -> dict:
        return {"status": "healthy"}

    return app


app = create_app()

