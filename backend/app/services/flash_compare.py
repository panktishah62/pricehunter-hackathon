from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from app.config import settings
from app.models.schemas import StructuredQuery, UnifiedResult
from app.services.browser_use_client import BrowserUseClient, BrowserUseError, classify_browser_error
from app.services.flash_price_parser import FlashPriceEntry, parse_flash_store_rows
from app.services.online_discovery import PlatformStrategy
from app.services.product_registry import classify_product_type


logger = logging.getLogger(__name__)

# Matches the flash.co loading/home page that appears during the "brewing" phase.
FLASH_HOME_RE = re.compile(r"https://(?:www\.)?flash\.co/home")
VIEW_ALL_STORES_RE = re.compile(r"View all\s+\d+\s+stores", re.IGNORECASE)

# Polling interval (ms) and max wait (ms) for the SPA "brewing" phase.
_SPA_POLL_INTERVAL_MS = 2000
_SPA_MAX_WAIT_MS = 60000


@dataclass(frozen=True)
class FlipkartSeed:
    title: str
    url: str
    score: int


class FlashCompareError(RuntimeError):
    def __init__(self, message: str, *, code: str = "flash_compare_error") -> None:
        super().__init__(message)
        self.code = code


async def search_flash_compare(query: StructuredQuery) -> list[UnifiedResult]:
    if not settings.flash_compare_enabled:
        return []
    if not settings.browser_use_api_key:
        logger.info("Flash compare disabled because BROWSER_USE_API_KEY is missing.")
        return []
    if not settings.serpapi_api_key:
        logger.info("Flash compare disabled because SERPAPI_API_KEY is missing.")
        return []
    enabled_categories = {
        category.strip().lower()
        for category in settings.flash_compare_categories.split(",")
        if category.strip()
    }
    if query.category.lower() not in enabled_categories:
        logger.info("Flash compare skipped for category=%s", query.category)
        return []
    # Check product-type-level eligibility from the registry.
    spec = classify_product_type(query.product, query.category)
    if not spec.flash_compare_eligible:
        logger.info(
            "Flash compare skipped for product_type=%s (%s) — not eligible",
            spec.product_type_id,
            spec.display_name,
        )
        return []

    seed = await discover_flipkart_seed(query.product)
    if not seed:
        logger.info("Flash compare could not find a Flipkart seed URL for %s", query.product)
        return []

    last_error: Exception | None = None
    attempts = max(1, settings.browser_use_retry_attempts)
    for attempt in range(1, attempts + 1):
        try:
            logger.info("Flash compare Browser Use attempt %s/%s", attempt, attempts)
            entries, metadata = await _run_flash_browser(seed.url)
            return [_to_unified_result(entry, seed, metadata) for entry in entries]
        except FlashCompareError as exc:
            last_error = exc
            logger.warning("Flash compare attempt %s/%s failed [%s]: %s", attempt, attempts, exc.code, exc)
            # Don't retry on concurrent-session limits — short backoff won't
            # help when sessions are stuck for 60s+.  We match on the error
            # code from BrowserUseClient *and* a broad set of keywords so
            # this keeps working if the upstream message wording changes
            # (e.g. "Too many concurrent active sessions", "session quota
            # exceeded", "active session limit").
            if exc.code == "session_create_failed":
                msg = str(exc).lower()
                if any(kw in msg for kw in ("concurrent", "active session", "session limit", "quota")):
                    logger.warning("Flash compare aborting retries: concurrent session limit hit")
                    break
            if attempt < attempts:
                await asyncio.sleep(min(2 * attempt, 8))
        except Exception as exc:  # pragma: no cover - external provider
            last_error = exc
            logger.warning("Flash compare attempt %s/%s failed: %s", attempt, attempts, exc)
            if attempt < attempts:
                await asyncio.sleep(min(2 * attempt, 8))

    if last_error:
        logger.warning("Flash compare failed after %s attempts: %s", attempts, last_error)
    return []


async def discover_flipkart_seed(product: str) -> FlipkartSeed | None:
    params = {
        "engine": "google",
        "api_key": settings.serpapi_api_key,
        "q": f"site:flipkart.com {product}",
        "google_domain": "google.co.in",
        "gl": "in",
        "hl": "en",
        "location": "India",
        "num": 10,
    }
    attempts = max(1, settings.flash_serpapi_retry_attempts)
    payload: dict[str, Any] | None = None
    async with httpx.AsyncClient(timeout=settings.flash_serpapi_timeout_seconds) as client:
        for attempt in range(1, attempts + 1):
            try:
                response = await client.get(settings.serpapi_base_url, params=params)
                response.raise_for_status()
                payload = response.json()
                break
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                body = exc.response.text[:200]
                logger.warning(
                    "SerpAPI Flipkart seed request failed with status=%s on attempt %s/%s: %s",
                    status_code,
                    attempt,
                    attempts,
                    body,
                )
                if status_code < 500 or attempt == attempts:
                    return None
            except Exception as exc:  # pragma: no cover - external provider
                logger.warning("SerpAPI Flipkart seed request failed on attempt %s/%s: %s", attempt, attempts, exc)
                if attempt == attempts:
                    return None
            await asyncio.sleep(min(2 * attempt, 8))

    if payload is None:
        return None

    candidates: list[FlipkartSeed] = []
    for item in payload.get("organic_results", []):
        if not isinstance(item, dict):
            continue
        url = str(item.get("link") or "").strip()
        title = str(item.get("title") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        if "flipkart.com" not in url:
            continue
        score = _score_flipkart_candidate(product, title, url, snippet)
        if score <= 0:
            continue
        candidates.append(FlipkartSeed(title=title, url=url, score=score))

    candidates.sort(key=lambda candidate: candidate.score, reverse=True)
    return candidates[0] if candidates else None


async def _run_flash_browser(flipkart_url: str) -> tuple[list[FlashPriceEntry], dict[str, Any]]:
    """Navigate flash.co and extract price comparison rows.

    The approach is intentionally resilient to flash.co UI/route changes:
      1. Navigate to ``flash.co/<flipkart_url>`` — flash.co redirects to a
         ``/home?q=…`` loading page ("Flash AI is brewing…").
      2. **Poll** until the URL leaves ``/home`` (the SPA navigates client-side
         to a product page once processing finishes).  This avoids hard-coding
         any specific product-page route pattern.
      3. Once on the product page, wait for network-idle and try to click a
         "View all stores" button if one exists.  If it doesn't, scrape prices
         directly from whatever page we landed on.
      4. Extract ``<a>`` elements whose text contains a ₹ price.
    """
    client = BrowserUseClient()
    session: dict[str, Any] | None = None
    session_id: str | None = None
    browser = None
    flash_url = f"https://flash.co/{flipkart_url}"

    try:
        session = await client.create_session()
        session_id = session.get("id")
        logger.info("Browser Use session created: id=%s live=%s", session_id, session.get("liveUrl"))

        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(session["cdpUrl"])
            context = browser.contexts[0] if browser.contexts else await browser.new_context(locale="en-IN")
            page = context.pages[0] if context.pages else await context.new_page()
            page.set_default_timeout(settings.flash_browser_timeout_ms)

            # ── Step 1: navigate ────────────────────────────────────
            await page.goto(flash_url, wait_until="domcontentloaded", timeout=settings.flash_browser_timeout_ms)

            # ── Step 2: wait for SPA to leave /home ─────────────────
            product_url = await _wait_for_product_page(page)
            logger.info("Flash SPA navigated to %s", product_url)

            # ── Step 3: settle & optionally expand stores ───────────
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(1500)

            # Try clicking "View all N stores" if the button exists.
            compare_url = product_url
            try:
                view_all_btn = page.locator("button").filter(has_text=VIEW_ALL_STORES_RE).first
                await view_all_btn.wait_for(state="visible", timeout=5000)
                await view_all_btn.click()
                # Give the page a moment to navigate / expand.
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(1500)
                compare_url = page.url
                logger.info("Flash 'View all stores' clicked, now on %s", compare_url)
            except PlaywrightTimeoutError:
                # Button not found — that's fine, scrape from current page.
                logger.info("Flash 'View all stores' button not found, scraping item page directly")

            # ── Step 4: extract price rows ──────────────────────────
            rows = await page.evaluate(
                """
                () => Array.from(document.querySelectorAll('a')).map((link) => ({
                  text: (link.innerText || '').replace(/\\s+/g, ' ').trim(),
                  href: link.href || null,
                })).filter((row) => /₹\\s*[0-9][0-9,]*/.test(row.text))
                """
            )
            await browser.close()
            browser = None
    except (PlaywrightTimeoutError, BrowserUseError) as exc:
        code = getattr(exc, "code", None) or classify_browser_error(exc)
        raise FlashCompareError(str(exc), code=code) from exc
    except FlashCompareError:
        raise
    except Exception as exc:
        raise FlashCompareError(str(exc), code=classify_browser_error(exc)) from exc
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        if session_id:
            stop_metadata = await client.stop_session(session_id)
            if stop_metadata:
                logger.info(
                    "Browser Use session stopped: id=%s proxyUsedMb=%s proxyCost=%s browserCost=%s",
                    session_id,
                    stop_metadata.get("proxyUsedMb"),
                    stop_metadata.get("proxyCost"),
                    stop_metadata.get("browserCost"),
                )

    entries = parse_flash_store_rows(rows)
    if not entries:
        raise FlashCompareError("Flash compare returned no merchant rows", code="no_compare_results")
    metadata = {
        "flash_url": flash_url,
        "summary_url": product_url,
        "compare_url": compare_url,
        "browser_use_session_id": session_id,
        "browser_use_proxy_country": settings.browser_use_proxy_country,
    }
    return entries, metadata


async def _wait_for_product_page(page: Any) -> str:
    """Poll until flash.co's SPA finishes processing and lands on the final
    product page.

    Flash.co's navigation chain:
      ``/home?q=…`` (brewing) → ``/product-search/…`` (researching, shows
      "Research Progress N%") → ``/item/…`` (final page with all stores).

    Some products skip ``/product-search/`` and go straight to ``/item/``.
    The key insight is that ``/product-search/`` shows a progress indicator
    while flash.co is still researching — we must wait for that to finish
    before scraping.

    Strategy:
      1. Wait for the URL to leave ``/home``.
      2. If the page is still researching (progress indicator visible),
         keep polling until it finishes or the URL changes.
      3. Return the final stable URL.

    The total wall-clock time is capped at ``_SPA_MAX_WAIT_MS``.
    """
    elapsed_ms = 0
    last_url: str | None = None

    while elapsed_ms < _SPA_MAX_WAIT_MS:
        current_url = page.url

        if FLASH_HOME_RE.search(current_url) or "flash.co" not in current_url:
            # Still on the loading/home page — reset.
            last_url = None
        elif current_url == last_url:
            # URL is stable.  But if the page is still showing a research
            # progress indicator, flash.co hasn't finished — keep waiting.
            if _looks_intermediate(current_url) and elapsed_ms + _SPA_POLL_INTERVAL_MS <= _SPA_MAX_WAIT_MS:
                still_researching = await _page_is_researching(page)
                if still_researching:
                    # Page is still loading — don't return yet.
                    await page.wait_for_timeout(_SPA_POLL_INTERVAL_MS)
                    elapsed_ms += _SPA_POLL_INTERVAL_MS
                    # Check if URL changed while we waited.
                    new_url = page.url
                    if new_url != current_url and "flash.co" in new_url and not FLASH_HOME_RE.search(new_url):
                        logger.info("Flash SPA navigated while researching: %s -> %s", current_url, new_url)
                        last_url = new_url
                    continue
            # Either: (a) research finished / URL is final, (b) URL is not
            # intermediate, or (c) time budget exhausted.  In all cases we
            # return the best URL we have — downstream scraping may still
            # get partial data if (c) applies.
            return current_url
        else:
            # First time seeing this non-home URL; record and confirm on
            # next poll.
            last_url = current_url

        await page.wait_for_timeout(_SPA_POLL_INTERVAL_MS)
        elapsed_ms += _SPA_POLL_INTERVAL_MS

    # If we got a non-home URL but it never fully stabilised, use it anyway.
    if last_url and not FLASH_HOME_RE.search(last_url):
        logger.warning("Flash SPA URL did not fully stabilise, using %s", last_url)
        return last_url

    raise FlashCompareError(
        f"Flash SPA did not navigate away from /home within {_SPA_MAX_WAIT_MS}ms (stuck on {page.url})",
        code="spa_navigation_timeout",
    )


async def _page_is_researching(page: Any) -> bool:
    """Return True if flash.co is still showing a research progress indicator.

    On ``/product-search/`` pages, flash.co displays "Research Progress N%"
    and "Estimated time remaining" while it gathers price data.  We should
    not scrape until this phase completes.
    """
    try:
        return await page.evaluate("""
            () => {
                const text = document.body?.innerText || '';
                return /Research Progress/i.test(text) || /Estimated time remaining/i.test(text);
            }
        """)
    except Exception as exc:
        # evaluate() can fail mid-navigation (e.g. SPA transitioning between
        # routes).  Assume research is still active so we keep waiting rather
        # than prematurely scraping an incomplete page.
        logger.debug("_page_is_researching evaluate() failed, assuming still researching: %s", exc)
        return True


def _looks_intermediate(url: str) -> bool:
    """Return True if the URL looks like a known intermediate flash.co route
    that typically transitions to a final page (e.g. ``/product-search/``).

    Final-looking routes (``/item/``, ``/price-compare/``, ``/product-compare/``,
    ``/product-details/``) return False so the caller can skip the grace period.
    """
    # If it matches a known final pattern, it's not intermediate.
    if re.search(r"/(?:item|price-compare|product-compare|product-details)/", url):
        return False
    # /product-search/ is the known intermediate route.
    if "/product-search/" in url:
        return True
    # Unknown route — be conservative and treat as intermediate.
    return True


def _to_unified_result(entry: FlashPriceEntry, seed: FlipkartSeed, metadata: dict[str, Any]) -> UnifiedResult:
    return UnifiedResult(
        source_type="online",
        name=entry.site,
        price=entry.price,
        currency="INR",
        delivery_time=None,
        availability=True,
        confidence=0.86,
        url=entry.url,
        notes=None,
        is_mock=False,
    )


def _score_flipkart_candidate(product: str, title: str, url: str, snippet: str) -> int:
    lowered_title = title.lower()
    lowered_url = url.lower()
    lowered_snippet = snippet.lower()
    product_tokens = [token for token in re.findall(r"[a-z0-9]+", product.lower()) if len(token) > 1]

    score = 0
    if "/p/" in lowered_url:
        score += 8
    if "/search" in lowered_url:
        score -= 5
    if "flipkart.com" in lowered_url:
        score += 3
    for token in product_tokens:
        if token in lowered_title:
            score += 3
        elif token in lowered_url or token in lowered_snippet:
            score += 1
    if any(bad in lowered_title for bad in ("case", "cover", "tempered glass", "charger")):
        score -= 5
    return score


def flash_platform_strategy() -> PlatformStrategy:
    return PlatformStrategy(
        platform_name="Flash Compare",
        platform_id="flash_compare",
        search_query="flash compare",
        expected_category="electronics",
    )
