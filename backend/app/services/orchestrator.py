from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Optional

from app.models.schemas import SearchResponse, SearchStrategy, StructuredQuery, UnifiedResult
from app.services import location_ranker, offline_pipeline, online_pipeline, persistence, query_structurer

logger = logging.getLogger(__name__)


class UnsupportedCategoryError(ValueError):
    """Raised when a query falls outside the currently supported categories."""


async def _persist_safely(coroutine, context: str) -> None:
    try:
        await coroutine
    except Exception as exc:  # pragma: no cover - depends on external service
        logger.warning("Persistence skipped for %s: %s", context, exc)


async def store_results(query: StructuredQuery, results: list[UnifiedResult]) -> None:
    logger.info("Legacy store_results called for product=%s with %s result(s)", query.product, len(results))


async def run_search_structured(
    structured_query: StructuredQuery,
    search_strategy: SearchStrategy = "both",
    request_metadata: dict[str, str | None] | None = None,
    session_id: str | None = None,
) -> SearchResponse:
    start_time = time.time()
    search_id = str(uuid.uuid4())
    if not query_structurer.is_supported_category(structured_query.category):
        raise UnsupportedCategoryError(query_structurer.unsupported_category_message())
    logger.info(
        "Running orchestrated search for product=%s strategy=%s",
        structured_query.product,
        search_strategy,
    )
    await _persist_safely(
        persistence.initialize_search_session(
            search_id=search_id,
            query=structured_query,
            search_strategy=search_strategy,
            request_metadata=request_metadata,
            session_id=session_id,
            source_flow="api",
        ),
        f"search init {search_id}",
    )

    online_results: list[UnifiedResult] | Exception = []
    offline_results: list[UnifiedResult] | Exception = []

    if search_strategy == "both":
        online_results, offline_results = await asyncio.gather(
            online_pipeline.run(structured_query, search_id=search_id),
            offline_pipeline.run(structured_query, search_id=search_id),
            return_exceptions=True,
        )
    elif search_strategy == "online":
        online_results = await online_pipeline.run(structured_query, search_id=search_id)
    else:
        offline_results = await offline_pipeline.run(structured_query, search_id=search_id)

    if isinstance(online_results, Exception):
        logger.warning("Online pipeline failed: %s", online_results)
        online_results = []
    if isinstance(offline_results, Exception):
        logger.warning("Offline pipeline failed: %s", offline_results)
        offline_results = []

    all_results = online_results + offline_results
    ranked_results = location_ranker.rank_results_by_location(structured_query, all_results)

    total_time = time.time() - start_time
    await _persist_safely(
        persistence.complete_search_session(
            search_id=search_id,
            query=structured_query,
            final_results=ranked_results,
            status="completed",
            total_time_seconds=round(total_time, 2),
        ),
        f"search completion {search_id}",
    )
    logger.info(
        "Search complete: total=%s online=%s offline=%s in %.2fs",
        len(ranked_results),
        len(online_results),
        len(offline_results),
        total_time,
    )

    return SearchResponse(
        query=structured_query,
        results=ranked_results,
        online_count=len(online_results),
        offline_count=len(offline_results),
        total_time_seconds=round(total_time, 2),
        search_strategy=search_strategy,
    )


async def run_search(
    raw_query: str,
    location: Optional[str] = None,
    request_metadata: dict[str, str | None] | None = None,
) -> SearchResponse:
    start_time = time.time()
    logger.info("Running orchestrated search for query=%s", raw_query)

    structured_query = await query_structurer.structure_query(raw_query)
    if location:
        structured_query.location = location

    result = await run_search_structured(
        structured_query,
        search_strategy="both",
        request_metadata=request_metadata,
    )
    result.total_time_seconds = round(time.time() - start_time, 2)
    return result


async def trigger_search_pipeline(
    phone: str,
    product: str,
    collected: dict,
    *,
    session_id: str | None = None,
    request_id: str | None = None,
) -> None:
    """Entry point called from chat_session after requirement is confirmed.

    Delegates to the vendor_search progressive delivery pipeline which handles:
    1. LLM query generation
    2. Parallel search (Google Places + IndiaMart + Online)
    3. LLM relevance filtering
    4. Ranking
    5. Voice calls to top vendors
    6. Progressive WhatsApp delivery of results
    """
    from app.services.vendor_search import run_search_and_deliver

    logger.info(
        "Triggering vendor search pipeline: phone=%s product=%s location=%s",
        phone,
        product,
        collected.get("location"),
    )
    await run_search_and_deliver(
        phone=phone,
        product=product,
        collected=collected,
        session_id=session_id,
        request_id=request_id,
    )
