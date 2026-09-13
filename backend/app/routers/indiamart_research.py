"""Research-only IndiaMART live search endpoint.

Standalone market-test API. Does not integrate with Zwig UI or existing
search / Apify / ingest pipelines.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.indiamart_live_search import (
    save_results_json,
    search_indiamart_live,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/research", tags=["indiamart-research"])


def _outputs_root() -> Path:
    """Dev monorepo root or /tmp in Cloud Run (parents[4] crashes in the image)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists() or (parent / "outputs").exists():
            return parent
    return Path("/tmp")


_OUTPUTS_ROOT = _outputs_root()
DEFAULT_OUT_DIR = _OUTPUTS_ROOT / "outputs" / "indiamart_live_search"

FetchMode = Literal["auto", "http", "cloud"]


class IndiaMartLiveSearchRequest(BaseModel):
    product: str = Field(min_length=1, max_length=160)
    city: str = Field(min_length=1, max_length=80)
    max_results: int = Field(default=30, ge=1, le=100)
    fetch_mode: FetchMode = "auto"
    save_json: bool = True
    # Listing cards hide phones; open supplier/product pages for PNS by default.
    include_phones: bool = True
    phone_limit: int | None = Field(
        default=None,
        ge=1,
        le=50,
        description="Max results to enrich with phones (defaults to max_results).",
    )


def _safe_filename(product: str, city: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    blob = f"{city}-{product}".lower()
    blob = re.sub(r"[^a-z0-9]+", "-", blob).strip("-")[:80] or "query"
    return f"{blob}-{stamp}.json"


@router.post("/indiamart-search")
async def indiamart_live_search(body: IndiaMartLiveSearchRequest) -> dict:
    try:
        payload = await search_indiamart_live(
            product=body.product,
            city=body.city,
            max_results=body.max_results,
            fetch_mode=body.fetch_mode,
            include_phones=body.include_phones,
            phone_limit=body.phone_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("IndiaMART live search failed")
        raise HTTPException(status_code=502, detail=f"IndiaMART live search failed: {exc}") from exc

    if body.save_json:
        out_path = DEFAULT_OUT_DIR / _safe_filename(body.product, body.city)
        save_results_json(payload, out_path)
        payload["saved_to"] = str(out_path)
    return payload


@router.get("/indiamart-search")
async def indiamart_live_search_get(
    product: str = Query(min_length=1, max_length=160),
    city: str = Query(min_length=1, max_length=80),
    max_results: int = Query(default=30, ge=1, le=100),
    fetch_mode: FetchMode = Query(default="auto"),
    save_json: bool = Query(default=True),
    include_phones: bool = Query(default=True),
    phone_limit: int | None = Query(default=None, ge=1, le=50),
) -> dict:
    return await indiamart_live_search(
        IndiaMartLiveSearchRequest(
            product=product,
            city=city,
            max_results=max_results,
            fetch_mode=fetch_mode,
            save_json=save_json,
            include_phones=include_phones,
            phone_limit=phone_limit,
        )
    )
