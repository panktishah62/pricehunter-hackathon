from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_database, init_zwig_vendors_database, ping_database
from app.routers import (
    chat,
    indiamart_research,
    location,
    premium_demo,
    push,
    search,
    supplier_crm,
    supplier_pilot,
    voice_lab,
    webhooks,
    whatsapp,
    whatsapp_flows,
)
from app.amplitude import get_amplitude_client
from app.observability import init_sentry
from app.services.whatsapp import close_whatsapp_http_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

init_sentry()

app = FastAPI(title="PriceHunter API", version="1.0.0")

allowed_origins = [
    origin.strip()
    for origin in settings.frontend_origins.split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search.router)
app.include_router(chat.router)
app.include_router(location.router)
app.include_router(webhooks.router)
app.include_router(whatsapp.router)
app.include_router(whatsapp_flows.router)
app.include_router(premium_demo.router)
app.include_router(voice_lab.router)
app.include_router(push.router)
app.include_router(supplier_crm.router)
app.include_router(supplier_pilot.router)
app.include_router(indiamart_research.router)


@app.on_event("startup")
async def startup_event() -> None:
    logging.getLogger(__name__).info(
        "Startup config: OpenAI=%s GooglePlaces=%s SerpAPI=%s VoiceProvider=%s Bolna=%s ElevenLabs=%s MockVoice=%s",
        bool(settings.openai_api_key),
        bool(settings.google_places_api_key),
        bool(settings.serpapi_api_key),
        settings.voice_provider,
        bool(settings.bolna_api_key and settings.bolna_agent_id),
        bool(settings.elevenlabs_api_key and settings.elevenlabs_agent_id and settings.elevenlabs_agent_phone_number_id),
        settings.mock_voice_calls,
    )
    await ping_database()
    await init_database()
    await init_zwig_vendors_database()


@app.on_event("shutdown")
async def shutdown_whatsapp_http() -> None:
    await close_whatsapp_http_client()
    client = get_amplitude_client()
    if client:
        client.shutdown()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _frontend_dist() -> Path:
    configured = os.environ.get("FRONTEND_DIST", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


FRONTEND_DIST = _frontend_dist()
if FRONTEND_DIST.is_dir():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
