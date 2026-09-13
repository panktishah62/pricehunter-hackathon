"""Amplitude analytics client helper for PriceHunter."""

from __future__ import annotations

from functools import lru_cache

from amplitude import Amplitude, Config

from app.config import settings


@lru_cache(maxsize=1)
def get_amplitude_client() -> Amplitude | None:
    """Return a singleton Amplitude client, or None if disabled."""
    if not settings.amplitude_api_key or settings.amplitude_disabled:
        return None
    config = Config(server_url=settings.amplitude_host)
    return Amplitude(settings.amplitude_api_key, configuration=config)
