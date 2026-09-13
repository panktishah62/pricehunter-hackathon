from __future__ import annotations

import logging

from app.models.schemas import StructuredQuery, UnifiedResult
from app.services import persistence
from app.services.flash_compare import flash_platform_strategy, search_flash_compare

logger = logging.getLogger(__name__)


async def _persist_safely(coroutine, context: str) -> None:
    try:
        await coroutine
    except Exception as exc:  # pragma: no cover - depends on external service
        logger.warning("Persistence skipped for %s: %s", context, exc)


def _dedupe_results(results: list[UnifiedResult]) -> list[UnifiedResult]:
    deduped: list[UnifiedResult] = []
    seen: set[str] = set()
    for result in results:
        key = result.url or f"{result.name}:{result.price}:{result.delivery_time}:{result.source_type}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(result)
    return deduped


async def run(query: StructuredQuery, search_id: str | None = None) -> list[UnifiedResult]:
    logger.info("Starting online pipeline for %s", query.product)
    if not query.product:
        logger.info("Skipping online pipeline because no product was structured.")
        return []

    combined: list[UnifiedResult] = []

    try:
        flash_results = await search_flash_compare(query)
        if flash_results:
            if search_id:
                await _persist_safely(
                    persistence.record_online_results(
                        search_id=search_id,
                        query=query,
                        strategy=flash_platform_strategy(),
                        results=flash_results,
                    ),
                    "flash compare results",
                )
            combined.extend(flash_results)
    except Exception as exc:  # pragma: no cover - external provider
        logger.warning("Flash compare provider failed: %s", exc)

    combined = _dedupe_results(combined)
    logger.info("Online pipeline completed with %s results", len(combined))
    return combined
