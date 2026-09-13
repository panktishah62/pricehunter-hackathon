"""Generic ("auto") gold live-rate adapter.

A single, vendor-agnostic adapter that discovers where any vendor site exposes
its live rate and extracts it — instead of a hand-written adapter per platform.

Design:

  Discovery ladder (cheapest → heaviest):
    1. static HTML            fetch page, LLM-extract rates
    2. local browser render   capture XHR/JSON feeds, websocket frames, iframe DOM
    3. stealth browser        Browser Use (residential proxy) for bot-protected sites
    4. rate sub-page follow   chase a rate-looking link one level deep

  Recipe cache (discover once, replay cheap):
    On success we persist a "recipe" — the winning `source` (feed endpoint /
    page) plus an optional deterministic `parse_spec` — into the vendor
    channel's metadata.fetch_config. Later refreshes replay it with no browser
    and (when a parse_spec exists) no LLM.

  Layered replay fallback (self-healing):
    1. deterministic parse_spec  → 2. LLM parse of live feed (re-derives spec)
    → 3. browser re-render. Any breakage backs off to the next tier.

This module is intentionally free of imports from ``live_rates`` to avoid a
circular import; ``live_rates.refresh_auto_channel`` drives it and records rows.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import zlib
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from openai import AsyncOpenAI
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
)
FETCH_TIMEOUT_SECONDS = 20
RENDER_TIMEOUT_MS = 30_000
RENDER_SETTLE_MS = 6_000
MAX_LLM_CHARS = 18_000
# Hard caps so one slow/hanging site can't stall a batch.
LLM_TIMEOUT_SECONDS = 30          # per OpenAI call
DISCOVERY_TIMEOUT_SECONDS = 120   # whole discovery ladder for one site
REPLAY_TIMEOUT_SECONDS = 60       # whole replay for one recipe
RATE_KEYWORDS = ("gold", "silver", "999", "995", "22k", "24k", "rate", "bid", "ask", "buy", "sell")
RATE_LINK_KEYWORDS = (
    "live", "rate", "rates", "today", "todays", "price", "prices",
    "spot", "bullion", "gold-rate", "goldrate", "liverate",
)
STRONG_RATE_PHRASES = ("liverate", "live rate", "live-rate", "live gold", "livegold", "todays rate", "today rate")


# ── Extraction schema ──────────────────────────────────────────────────────


class RateRow(BaseModel):
    label: str
    buy_rate: float | None = None
    sell_rate: float | None = None
    purity: str | None = None
    unit: str | None = None


class RateExtraction(BaseModel):
    found: bool
    rates: list[RateRow]


EXTRACTION_SYSTEM_PROMPT = """
You extract precious-metal live rates from a vendor web page or its data feed.
You are given raw text (HTML text, JSON, or CSV) scraped from one vendor site.

Return ONLY the gold/silver rate rows that are actually present in the input.

Field rules (follow exactly):
- sell_rate = the price the VENDOR SELLS at / the customer-facing rate. On rate
  boards this is the prominently shown number, often labelled "SELL", "Rate",
  "Ask", or just the big price. In JSON feeds prefer keys like sell/ask.
- buy_rate = the price the vendor BUYS at, labelled "BUY"/"Bid"/keys buy/bid.
  If only one rate is shown, put it in sell_rate and leave buy_rate null.
- purity = a fineness/karat LABEL ONLY: one of 999, 995, 24K, 22K, 18K, etc.
  NEVER put a price/number that isn't a purity here. If unclear, use null.
- unit = a weight unit only (g, 10g, 100g, kg). If unclear, use null.
- Do NOT invent or guess numbers. Ignore high/low/premium/change columns unless
  no buy/sell is present. If no concrete rate exists, set found=false.
- Prefer gold rows; include silver rows too if present.
""".strip()


def _looks_relevant(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in RATE_KEYWORDS)


def _reduce_for_llm(text: str) -> str:
    text = text.strip()
    if len(text) <= MAX_LLM_CHARS:
        return text
    lines = text.splitlines()
    kept: list[str] = []
    for idx, line in enumerate(lines):
        if _looks_relevant(line):
            kept.extend(lines[max(0, idx - 1):min(len(lines), idx + 2)])
        if sum(len(item) for item in kept) >= MAX_LLM_CHARS:
            break
    reduced = "\n".join(kept) if kept else text
    return reduced[:MAX_LLM_CHARS]


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))


def _validate_rows(rows: list[RateRow]) -> list[RateRow]:
    """Reject implausible rows. A failed validation upstream is the signal to
    re-derive a spec or re-discover (self-healing)."""
    valid: list[RateRow] = []
    for row in rows:
        candidates = [value for value in (row.buy_rate, row.sell_rate) if value is not None]
        if not candidates:
            continue
        if all(value <= 0 or value > 10_000_000 for value in candidates):
            continue
        # Purity must be a fineness/karat label, never a price that leaked in.
        if row.purity is not None:
            p = str(row.purity).strip()
            if not re.search(r"(?:999(?:\.9)?|995|916|9999|24\s?k|22\s?k|18\s?k|14\s?k|24kt|22kt)", p, re.I):
                row.purity = None
        valid.append(row)
    return valid


def _client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.openai_api_key, timeout=LLM_TIMEOUT_SECONDS)


async def _extract_with_llm(client: AsyncOpenAI, content: str, source: str) -> list[RateRow]:
    reduced = _reduce_for_llm(content)
    if not reduced.strip():
        return []
    try:
        response = await client.responses.parse(
            model=settings.openai_model,
            input=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": f"Source: {source}\n\n{reduced}"},
            ],
            text_format=RateExtraction,
            temperature=0,
        )
    except Exception as exc:  # pragma: no cover - external API
        logger.warning("generic_adapter LLM extract failed for %s: %s", source, exc)
        return []
    parsed = response.output_parsed
    if parsed is None or not parsed.found:
        return []
    return _validate_rows(parsed.rates)


# ── Parse-spec: deterministic re-parse of a known feed (no LLM on replay) ──


def _to_float(raw: Any) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().replace(",", "")
    if not text or text in {"-", "N/A"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def _approx(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) < 0.01


def _get_path(data: Any, path: list) -> Any:
    for key in path:
        data = data[key]
    return data


def _iter_dict_arrays(data: Any, path: tuple = ()):  # type: ignore[no-untyped-def]
    if isinstance(data, list) and data and all(isinstance(item, dict) for item in data):
        yield list(path), data
    if isinstance(data, dict):
        for key, value in data.items():
            yield from _iter_dict_arrays(value, path + (key,))
    elif isinstance(data, list):
        for idx, value in enumerate(data):
            yield from _iter_dict_arrays(value, path + (idx,))


def _derive_json_spec(body: str, rows: list[RateRow]) -> dict[str, Any] | None:
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None
    for path, array in _iter_dict_arrays(data):
        for row in rows:
            target = row.buy_rate if row.buy_rate is not None else row.sell_rate
            if target is None:
                continue
            for entry in array:
                buy_key = next((k for k, v in entry.items() if _approx(_to_float(v), target)), None)
                if not buy_key:
                    continue
                label_key = next(
                    (k for k, v in entry.items() if isinstance(v, str) and v.strip() and _to_float(v) is None),
                    None,
                )
                sell_key = None
                if row.sell_rate is not None:
                    sell_key = next(
                        (k for k, v in entry.items() if k != buy_key and _approx(_to_float(v), row.sell_rate)),
                        None,
                    )
                return {
                    "format": "json", "array_path": path,
                    "label_key": label_key, "buy_key": buy_key, "sell_key": sell_key,
                }
    return None


def _derive_delimited_spec(body: str, rows: list[RateRow]) -> dict[str, Any] | None:
    for sep in ("\t", ","):
        parsed_lines = [
            [part.strip() for part in line.split(sep)]
            for line in body.splitlines()
            if len(line.split(sep)) >= 2
        ]
        if not parsed_lines:
            continue
        votes: dict[tuple, int] = {}
        for row in rows:
            target = row.buy_rate if row.buy_rate is not None else row.sell_rate
            if target is None:
                continue
            for parts in parsed_lines:
                nums = {i: _to_float(part) for i, part in enumerate(parts)}
                buy_col = next((i for i, v in nums.items() if _approx(v, target)), None)
                if buy_col is None:
                    continue
                label_col = next((i for i, part in enumerate(parts) if part and _to_float(part) is None), None)
                sell_col = None
                if row.sell_rate is not None:
                    sell_col = next(
                        (i for i, v in nums.items() if i != buy_col and _approx(v, row.sell_rate)), None
                    )
                key = (label_col, buy_col, sell_col)
                votes[key] = votes.get(key, 0) + 1
                break
        if votes:
            (label_col, buy_col, sell_col), _ = max(votes.items(), key=lambda item: item[1])
            return {
                "format": "delimited", "sep": sep,
                "label_col": label_col, "buy_col": buy_col, "sell_col": sell_col,
            }
    return None


def apply_parse_spec(body: str, spec: dict[str, Any]) -> list[RateRow]:
    out: list[RateRow] = []
    if spec.get("format") == "json":
        try:
            array = _get_path(json.loads(body), spec["array_path"])
        except (json.JSONDecodeError, ValueError, KeyError, IndexError, TypeError):
            return []
        for entry in array if isinstance(array, list) else []:
            if not isinstance(entry, dict):
                continue
            buy = _to_float(entry.get(spec["buy_key"])) if spec.get("buy_key") else None
            sell = _to_float(entry.get(spec["sell_key"])) if spec.get("sell_key") else None
            label = str(entry.get(spec["label_key"], "")).strip() if spec.get("label_key") else ""
            if buy is not None or sell is not None:
                out.append(RateRow(label=label or "rate", buy_rate=buy, sell_rate=sell))
    elif spec.get("format") == "delimited":
        sep = spec["sep"]
        lc, bc, sc = spec.get("label_col"), spec.get("buy_col"), spec.get("sell_col")
        cols = [c for c in (lc, bc, sc) if c is not None]
        if not cols:
            return []
        need = max(cols)
        for line in body.splitlines():
            parts = [part.strip() for part in line.split(sep)]
            if len(parts) <= need:
                continue
            buy = _to_float(parts[bc]) if bc is not None else None
            sell = _to_float(parts[sc]) if sc is not None else None
            label = parts[lc] if lc is not None else ""
            if (buy is not None or sell is not None) and label:
                out.append(RateRow(label=label, buy_rate=buy, sell_rate=sell))
    return _validate_rows(out)


def _spec_reproduces(produced: list[RateRow], expected: list[RateRow]) -> bool:
    if not produced:
        return False
    expected_values = {row.buy_rate for row in expected if row.buy_rate is not None}
    if not expected_values:
        return len(produced) >= 1
    produced_values = {row.buy_rate for row in produced if row.buy_rate is not None}
    hits = sum(1 for value in expected_values if any(_approx(value, p) for p in produced_values))
    return hits >= max(1, int(len(expected_values) * 0.6))


def derive_parse_spec(body: str, rows: list[RateRow]) -> dict[str, Any] | None:
    """Best-effort deterministic spec from a feed body + LLM rows. Returns None
    when it can't be derived reliably (replay then keeps using the LLM)."""
    for derive in (_derive_json_spec, _derive_delimited_spec):
        try:
            spec = derive(body, rows)
        except Exception:
            spec = None
        if not spec:
            continue
        try:
            produced = apply_parse_spec(body, spec)
        except Exception:
            continue
        if _spec_reproduces(produced, rows):
            return spec
    return None


# ── Fetch + SSRF guard ─────────────────────────────────────────────────────


def _validate_public_url(url: str) -> str:
    """Reuse the SSRF guard from product_preview (public http/https only)."""
    from app.services.product_preview import _validate_url
    return _validate_url(url)


async def _fetch_text_ex(url: str) -> tuple[str | None, bool]:
    """Fetch text and report whether the failure was a *connectivity* error
    (our network/IP couldn't reach the host) vs. a real empty/error page.

    Returns (text_or_None, connect_failed). connect_failed=True signals the
    caller to escalate to the stealth tier (a different network/IP) rather than
    wasting a local render from the same blocked IP, or mislabelling "no rate"."""
    headers = {"User-Agent": USER_AGENT}
    try:
        # Verify TLS by default so cross-domain/sibling fetches can't be MITM'd.
        async with httpx.AsyncClient(follow_redirects=True, timeout=FETCH_TIMEOUT_SECONDS) as http:
            resp = await http.get(url, headers=headers)
            resp.raise_for_status()
            return resp.text, False
    except httpx.ConnectError as exc:
        if "CERTIFICATE" in str(exc).upper() or "SSL" in str(exc).upper():
            # Broken/self-signed cert — retry insecurely (parity with bespoke adapters).
            logger.warning("generic_adapter: TLS verify failed for %s; retrying insecurely", url)
            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=FETCH_TIMEOUT_SECONDS, verify=False) as http:
                    resp = await http.get(url, headers=headers)
                    resp.raise_for_status()
                    return resp.text, False
            except Exception:
                return None, True
        # Connection refused / DNS / unreachable from our IP → escalate.
        return None, True
    except httpx.HTTPStatusError:
        # Got a response (4xx/5xx) — host is reachable, just no/blocked content.
        return None, False
    except httpx.TransportError:
        # Connection reset / read/write/pool/protocol error — transport-level
        # failure from our network (e.g. ERR_CONNECTION_RESET). Escalate.
        return None, True
    except Exception:
        return None, False


async def _fetch_text(url: str) -> str | None:
    text, _ = await _fetch_text_ex(url)
    return text


def _local_safe(url: str) -> bool:
    """True if the host is safe to hit from OUR network (public http/https, not
    a private/loopback IP). Used to gate the local httpx + local-render tiers.
    The stealth tier is NOT gated by this: Browser Use runs on its own external
    network, so it can't reach our internals, and it resolves DNS remotely — so
    a host that fails OUR DNS (or that we'd refuse) can still be tried there."""
    try:
        _validate_public_url(url)
        return True
    except ValueError:
        return False


def _url_variants(url: str) -> list[str]:
    """Ordered URL variants to try for one vendor: https first (secure), then
    the https apex/www flip, then the same set over http as a last resort.

    Two real-world cases this recovers:
      - stale CSV URLs that are www/http mirrors while the live socket site is
        the apex https (or vice versa) → the apex/www flip catches it;
      - sites whose https endpoint resets the connection but whose http
        endpoint serves a (often JS-rendered) rate board, e.g. an IIS/ASP.NET
        host with a broken/absent TLS listener → the http fallback catches it.
    Same path is preserved across all variants."""
    parsed = urlparse(url)
    host = parsed.netloc
    path = parsed.path or "/"
    suffix = f"{path}" + (f"?{parsed.query}" if parsed.query else "")
    bare = host[4:] if host.lower().startswith("www.") else host
    flip = bare if host.lower().startswith("www.") else f"www.{bare}"

    variants: list[str] = []
    # https first (host, flipped host, bare apex), then the same over http so a
    # site with a broken TLS listener but a working http board is still reached.
    for scheme in ("https", "http"):
        for h in (host, flip, bare):
            v = f"{scheme}://{h}{suffix}"
            if v not in variants:
                variants.append(v)
    return variants


def _same_site(a: str, b: str) -> bool:
    host_a = urlparse(a).netloc.lower().removeprefix("www.")
    host_b = urlparse(b).netloc.lower().removeprefix("www.")
    return host_a == host_b


def _brand_token(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    labels = [label for label in host.split(".") if label not in ("com", "in", "co", "net", "org", "www")]
    return max(labels, key=len) if labels else ""


def _rate_link_candidates(html: str, base_url: str, *, limit: int = 3) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    base_token = _brand_token(base_url)
    scored: dict[str, int] = {}
    for anchor in soup.find_all("a", href=True):
        full = urljoin(base_url, anchor["href"])
        if not full.startswith(("http://", "https://")) or full.rstrip("/") == base_url.rstrip("/"):
            continue
        haystack = f"{anchor['href']} {anchor.get_text(' ', strip=True)}".lower()
        score = sum(keyword in haystack for keyword in RATE_LINK_KEYWORDS)
        if "gold" in haystack and score:
            score += 1
        if not score:
            continue
        same = _same_site(full, base_url)
        strong = any(phrase in haystack for phrase in STRONG_RATE_PHRASES)
        sibling = bool(base_token) and base_token in urlparse(full).netloc.lower()
        if same:
            scored[full] = max(scored.get(full, 0), score)
        elif strong or sibling:
            scored[full] = max(scored.get(full, 0), score + (2 if strong else 0))
    ranked = sorted(scored.items(), key=lambda item: item[1], reverse=True)
    return [url for url, _ in ranked[:limit]]


# ── Discovery tiers ────────────────────────────────────────────────────────


def _inflate_ws_frame(payload: Any) -> str | None:
    """Decode a websocket frame to text, decompressing zlib/raw-deflate binary
    frames. socket.io rate feeds push the actual numbers as zlib-compressed
    binary attachments (the text frames only carry placeholders), so without
    this the rate data is invisible to keyword/LLM extraction."""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, (bytes, bytearray)):
        raw = bytes(payload)
        for attempt in (lambda d: zlib.decompress(d), lambda d: zlib.decompress(d, -zlib.MAX_WBITS)):
            try:
                return attempt(raw).decode("utf-8", "ignore")
            except Exception:
                continue
        # Not compressed (or unknown) — best-effort plain decode.
        return raw.decode("utf-8", "ignore")
    return None


async def _capture_and_extract(client: AsyncOpenAI, page: Any, url: str) -> tuple[list[RateRow], str | None, str | None, str | None]:
    captured: list[tuple[str, str]] = []
    ws_frames: dict[str, list[str]] = {}

    async def _on_response(response: Any) -> None:
        try:
            ctype = (response.headers or {}).get("content-type", "").lower()
            # Skip assets that can't be a rate feed but often contain "rate"/"gold"
            # in paths/classes (CSS, JS, images, fonts) — they were eating the
            # time budget with junk LLM calls.
            if any(token in ctype for token in ("css", "javascript", "image/", "font", "video", "audio")):
                return
            if not any(token in ctype for token in ("json", "csv", "xml", "plain", "text")):
                return
            body = await response.text()
            if not body:
                return
            # Skip full HTML pages / error pages (real feeds are JSON/CSV/tab/xml,
            # not a rendered document). XML feeds (<?xml) are kept.
            if body.lstrip()[:14].lower().startswith(("<!doctype", "<html")):
                return
            if _looks_relevant(body):
                captured.append((response.url, body))
        except Exception:
            pass

    def _on_websocket(ws: Any) -> None:
        def _record(payload: Any) -> None:
            text = _inflate_ws_frame(payload)
            if text and _looks_relevant(text):
                ws_frames.setdefault(ws.url, []).append(text)
        ws.on("framereceived", lambda payload: _record(payload))

    page.on("response", _on_response)
    page.on("websocket", _on_websocket)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=RENDER_TIMEOUT_MS)
    except Exception as exc:
        logger.debug("generic_adapter navigation note for %s: %s", url, exc)
    await page.wait_for_timeout(RENDER_SETTLE_MS)

    frame_texts: list[str] = []
    for frame in page.frames:
        try:
            frame_texts.append(await frame.inner_text("body"))
        except Exception:
            continue
    dom_text = "\n".join(text for text in frame_texts if text)

    # Rendered HTML (best effort) so the caller can follow rate sub-page links
    # even when the original fetch was blocked and only the browser reached it.
    try:
        rendered_html = await page.content()
    except Exception:
        rendered_html = None

    # Extraction order, high-signal first. Return the winning body so the caller
    # can derive a parse-spec from the *same* bytes the rows came from.
    # 1) WebSocket feeds (socket.io rate streams, already decompressed).
    #    Snapshot the dict — `framereceived` callbacks keep mutating ws_frames
    #    during the awaits below, which would otherwise raise "dictionary changed
    #    size during iteration".
    for socket_url, frames in list(ws_frames.items()):
        blob = "\n".join(frames[-80:])
        rows = await _extract_with_llm(client, blob, source=f"websocket {socket_url}")
        if rows:
            return rows, socket_url, blob, rendered_html
    # 2) HTTP/XHR feeds — deduped by endpoint (cache-buster query stripped) and
    #    capped so a noisy page can't exhaust the time budget.
    seen_endpoints: set[str] = set()
    for endpoint, body in list(captured):
        key = endpoint.split("?", 1)[0]
        if key in seen_endpoints:
            continue
        seen_endpoints.add(key)
        if len(seen_endpoints) > 10:
            break
        rows = await _extract_with_llm(client, body, source=f"network response {endpoint}")
        if rows:
            return rows, endpoint, body, rendered_html
    # 3) Rendered DOM text fallback.
    rows = await _extract_with_llm(client, dom_text, source=f"rendered DOM of {url}")
    return rows, (url if rows else None), (dom_text if rows else None), rendered_html


async def _tier_local_render(client: AsyncOpenAI, url: str) -> tuple[list[RateRow], str | None, str | None, str | None]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("generic_adapter: playwright not installed; skipping render tier")
        return [], None, None, None
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception as exc:
            logger.warning("generic_adapter: chromium launch failed: %s", exc)
            return [], None, None, None
        try:
            context = await browser.new_context(user_agent=USER_AGENT, ignore_https_errors=True)
            page = await context.new_page()
            return await _capture_and_extract(client, page, url)
        finally:
            await browser.close()


async def _tier_stealth_render(client: AsyncOpenAI, url: str) -> tuple[list[RateRow], str | None, str | None, str | None]:
    if not settings.browser_use_api_key:
        return [], None, None, None
    try:
        from playwright.async_api import async_playwright
        from app.services.browser_use_client import BrowserUseClient
    except ImportError as exc:
        logger.warning("generic_adapter: stealth deps missing: %s", exc)
        return [], None, None, None
    bu = BrowserUseClient()
    try:
        session = await bu.create_session()
    except Exception as exc:
        logger.warning("generic_adapter: Browser Use session failed: %s", exc)
        return [], None, None, None
    session_id = session.get("id")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(session["cdpUrl"])
            context = browser.contexts[0] if browser.contexts else await browser.new_context(locale="en-IN")
            page = context.pages[0] if context.pages else await context.new_page()
            return await _capture_and_extract(client, page, url)
    except Exception as exc:
        logger.warning("generic_adapter: stealth render failed: %s", exc)
        return [], None, None, None
    finally:
        if session_id:
            await bu.stop_session(session_id)


# ── Public API ─────────────────────────────────────────────────────────────


def _rows_to_dicts(rows: list[RateRow]) -> list[dict[str, Any]]:
    return [
        {
            "label": row.label,
            "buy_rate": row.buy_rate,
            "sell_rate": row.sell_rate,
            "purity": row.purity,
            "unit": row.unit,
        }
        for row in rows
    ]


async def discover(url: str, *, use_stealth: bool = False, allow_browser: bool = True) -> dict[str, Any] | None:
    """Full discovery ladder. Returns a recipe dict + extracted rows, or None.

    ``allow_browser`` gates the local Chromium render tier. The offline seed job
    leaves it True; the live per-query refresh passes False so discovery is
    static-HTML only (no inline browser at query time in prod).

    Recipe shape (persisted into channel metadata.fetch_config):
        {"source", "parse_spec", "needs_browser", "discovered_via"}
    Result shape:
        {**recipe, "rows": [ {label, buy_rate, sell_rate, purity, unit}, ... ]}
    """
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if not urlparse(url).hostname:
        logger.info("generic_adapter discover rejected url %s: no hostname", url)
        return None
    if not settings.openai_api_key:
        logger.warning("generic_adapter: OPENAI_API_KEY not set; cannot extract")
        return None

    client = _client()

    async def _run(target: str, depth: int, *, stealth: bool) -> tuple[list[RateRow], str | None, str, str | None]:
        # Local tiers (httpx + local render) run on OUR infra, so gate them with
        # the SSRF guard. A host that fails our DNS or resolves private is left
        # to the stealth tier (external network, remote DNS) instead of aborting.
        local_ok = _local_safe(target)
        html, connect_failed = (None, True)
        if local_ok:
            # Fetch once; reuse the HTML for static extraction + sub-page discovery.
            html, connect_failed = await _fetch_text_ex(target)
        link_html = html
        if html:
            text = _html_to_text(html)
            rows = await _extract_with_llm(client, text, source=f"static HTML of {target}")
            if rows:
                return rows, target, "static-html", text

        if connect_failed:
            # Unreachable from our network (reset/refused/DNS-fail/private). A
            # local render would fail the same way, so only the stealth tier
            # (residential proxy, remote DNS) can help — and only in the stealth
            # pass. In the cheap pass we leave it for a sibling variant (e.g. http
            # when https resets) or the later stealth pass.
            if stealth:
                rows, source, body, rhtml = await _tier_stealth_render(client, target)
                if rows:
                    return rows, source, "stealth-browser", body
                link_html = link_html or rhtml
        else:
            if allow_browser:
                rows, source, body, rhtml = await _tier_local_render(client, target)
                if rows:
                    return rows, source, "rendered-browser", body
                link_html = link_html or rhtml
            if stealth:
                rows, source, body, rhtml2 = await _tier_stealth_render(client, target)
                if rows:
                    return rows, source, "stealth-browser", body
                link_html = link_html or rhtml2

        # Sub-page following (one level). Uses whatever HTML we obtained — static
        # fetch OR browser-rendered — so a connectivity-blocked site whose rate
        # lives on a sub-path is still followed (each sub-page re-escalates).
        if depth == 0 and link_html:
            for candidate in _rate_link_candidates(link_html, target):
                try:
                    _validate_public_url(candidate)
                except ValueError:
                    continue
                rows, source, via, body = await _run(candidate, depth + 1, stealth=stealth)
                if rows:
                    return rows, source, f"{via} (via sub-page)", body
        return [], None, "none", None

    async def _scan(variants: list[str], stealth: bool) -> tuple[list[RateRow], str | None, str, str | None, str]:
        for variant in variants:
            try:
                v_rows, v_source, v_via, v_body = await asyncio.wait_for(
                    _run(variant, 0, stealth=stealth), timeout=DISCOVERY_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                logger.warning("generic_adapter: discovery timed out for %s after %ss", variant, DISCOVERY_TIMEOUT_SECONDS)
                continue
            if v_rows and v_source:
                return v_rows, v_source, v_via, v_body, variant
        return [], None, "none", None, url

    all_variants = _url_variants(url)

    # Pass 1: cheap (static + local render) across ALL variants — https first,
    # then the apex/www flip, then http. This reaches sites whose https listener
    # is broken but whose http board renders, without spending a paid session.
    rows, source, via, body, used_url = await _scan(all_variants, stealth=False)

    # Pass 2: only if nothing cheap worked AND stealth is allowed. Try ONE https
    # and ONE http variant via the proxy — scheme-diverse so a broken-TLS site
    # whose http board renders is still recovered, while capping paid sessions at
    # two. (A site that resets https from everywhere needs the http attempt; a
    # site that just blocks our IP is reachable on its first variant.)
    if (not rows or not source) and use_stealth:
        https_v = next((v for v in all_variants if v.startswith("https://")), None)
        http_v = next((v for v in all_variants if v.startswith("http://")), None)
        stealth_variants = [v for v in (https_v, http_v) if v]
        rows, source, via, body, used_url = await _scan(stealth_variants, stealth=True)

    if not rows or not source:
        return None

    needs_browser = source.startswith("ws") or (source.rstrip("/") == used_url.rstrip("/") and "browser" in via)
    parse_spec = None
    # Derive the deterministic spec from the SAME body the rows came from — live
    # rates move between fetches, so a re-fetch would fail to reproduce.
    if not needs_browser and source.startswith(("http://", "https://")) and body:
        parse_spec = derive_parse_spec(body, rows)

    return {
        "source": source,
        "url": used_url,  # the variant that actually worked (replay renders this)
        "parse_spec": parse_spec,
        "needs_browser": needs_browser,
        "discovered_via": via,
        "rows": _rows_to_dicts(rows),
    }


async def replay(recipe: dict[str, Any], *, allow_browser: bool = False) -> dict[str, Any]:
    """Cheap replay of a cached recipe with layered self-healing fallback,
    bounded by an overall watchdog so a hanging feed can't stall a refresh.

    ``allow_browser`` gates the step-3 inline browser re-render. It is False in
    the live per-query path (no Chromium at query time in prod — browser-only
    feeds are refreshed by the offline seed job instead) and True offline."""
    try:
        return await asyncio.wait_for(
            _replay_impl(recipe, allow_browser=allow_browser), timeout=REPLAY_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        logger.warning("generic_adapter: replay timed out for %s", recipe.get("source"))
        return {"rows": [], "source": None, "parse_spec": None, "via": "timeout", "stale": True}


async def _replay_impl(recipe: dict[str, Any], *, allow_browser: bool = False) -> dict[str, Any]:
    """Cheap replay of a cached recipe with layered self-healing fallback.

    Returns {"rows": [...], "source": str|None, "parse_spec": dict|None,
             "via": str, "stale": bool}. ``parse_spec`` is non-None only when a
    fresh spec was (re)derived and should be persisted. ``stale`` is True when
    even the browser fallback failed (caller should consider re-discovery)."""
    source = recipe.get("source") or ""
    needs_browser = bool(recipe.get("needs_browser"))
    parse_spec = recipe.get("parse_spec")
    client = _client()

    if source.startswith(("http://", "https://")) and not needs_browser:
        body = await _fetch_text(source)
        if body and _looks_relevant(body):
            # 1) deterministic parse-spec
            if parse_spec:
                try:
                    rows = apply_parse_spec(body, parse_spec)
                except Exception:
                    rows = []
                if rows:
                    return {"rows": _rows_to_dicts(rows), "source": source,
                            "parse_spec": None, "via": "parse-spec", "stale": False}
            # 2) LLM parse + refresh spec
            rows = await _extract_with_llm(client, body, source=f"cached {source}")
            if rows:
                new_spec = derive_parse_spec(body, rows)
                return {"rows": _rows_to_dicts(rows), "source": source,
                        "parse_spec": new_spec, "via": "cached-llm", "stale": False}

    # 3) browser re-render fallback (socket / JS-painted feeds) — skipped in the
    # live query path (allow_browser=False, no inline browser in prod); these
    # feeds are refreshed by the offline seed job which renders and seeds rates.
    page_url = recipe.get("url") or source
    if allow_browser and page_url.startswith(("http://", "https://")):
        rows, used, _body, _html = await _tier_local_render(client, page_url)
        if rows:
            return {"rows": _rows_to_dicts(rows), "source": used or page_url,
                    "parse_spec": None, "via": "cached-render", "stale": False}

    return {"rows": [], "source": None, "parse_spec": None, "via": "none", "stale": True}
