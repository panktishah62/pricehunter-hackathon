from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import re
import threading
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin

import requests

import contextvars

from app.config import settings
from app.database import zwig_live_rate_snapshots_collection, zwig_vendor_channels_collection
from app.models.schemas import StructuredQuery
from app.categories.gold import generic_adapter
from app.categories.gold.channels import (
    CHANNEL_TYPE_WEBSITE,
    build_website_channel_metadata,
    is_website_channel_type,
    normalize_website_url,
    record_live_rate_snapshot,
    upsert_vendor_channel,
)
from app.categories.gold.ingestion import GOLD_BULLION_CATEGORY_ID, city_from_address

logger = logging.getLogger(__name__)

CHIRAYU_API_ADAPTER = "chirayu_api"
LMX_BROADCASTRATES_ADAPTER = "lmx_broadcastrates"
LIVERATE_JS_SOCKET_ADAPTER = "liverate_js_socket"
AUTO_ADAPTER = "auto"
AUTO_SOURCE_NAME = "generic_auto"
LIVE_RATE_SNAPSHOT_TTL_SECONDS = 90
LIVE_RATE_MAX_REFRESH_VENDORS = 20
LIVE_RATE_FETCH_CONCURRENCY = 5
HTTP_TIMEOUT_SECONDS = 20.0
SOCKET_WAIT_SECONDS = 10.0
USER_AGENT = "Mozilla/5.0 (compatible; PriceHunterBot/1.0)"

# Per-query budget for inline discovery (browser+LLM) by the generic adapter.
# Holds a single-element mutable list so concurrent refresh tasks (which inherit
# a copy of the context) share and decrement the same counter within one query.
_inline_discovery_budget: contextvars.ContextVar[list[int]] = contextvars.ContextVar(
    "gold_inline_discovery_budget"
)


@dataclass
class LiveRateRefreshSummary:
    attempted: int = 0
    refreshed: int = 0
    skipped_fresh: int = 0
    failed: int = 0
    refreshed_vendor_ids: list[str] = field(default_factory=list)


def _canonical_city(value: str | None) -> str:
    return city_from_address(value).strip()


def _metadata(channel_doc: dict[str, Any]) -> dict[str, Any]:
    return channel_doc.get("metadata") or {}


def _channel_supports_live_rates(channel_doc: dict[str, Any]) -> bool:
    metadata = _metadata(channel_doc)
    return bool(metadata.get("supports_live_rates", False))


def _channel_adapter_name(channel_doc: dict[str, Any]) -> str:
    metadata = _metadata(channel_doc)
    return str(
        metadata.get("fetch_adapter")
        or metadata.get("adapter_family")
        or ""
    ).strip().lower()


async def _latest_snapshot_age_seconds(vendor_id: str) -> float | None:
    snapshot = await zwig_live_rate_snapshots_collection.find_one(
        {"vendor_id": vendor_id, "category_id": GOLD_BULLION_CATEGORY_ID, "is_active": {"$ne": False}},
        sort=[("fetched_at", -1)],
    )
    if not snapshot:
        return None
    fetched_at = snapshot.get("fetched_at")
    if fetched_at is None:
        return None
    if isinstance(fetched_at, datetime) and fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return max((datetime.now(timezone.utc) - fetched_at).total_seconds(), 0.0)


def _extract_local_storage_config(html: str) -> dict[str, str]:
    config: dict[str, str] = {}
    for key, value in re.findall(r'localStorage\.([A-Za-z0-9_]+)\s*=\s*"([^"]*)";', html):
        config[key] = value
    return config


def _sanitize_broadcast_host(host: str | None) -> str:
    """Normalize a Chirayu/VOTS broadcast host for URL construction.

    Some vendor pages publish `ipAddressBCast` WITH a scheme (and occasionally a
    trailing slash or path), e.g. 'https://bcast.arihanthjewellers.com'. Naively
    building `f"https://{host}:{port}/..."` then yields a double-scheme URL
    ('https://https://bcast...') that requests parses with host='https' → DNS
    failure. Strip any leading scheme and trailing slashes so a bare host
    remains."""
    cleaned = (host or "").strip()
    cleaned = re.sub(r"^https?://", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^https?//", "", cleaned, flags=re.IGNORECASE)  # missing-colon variant
    return cleaned.strip("/").strip()


def _chirayu_fetch_targets(
    channel_doc: dict[str, Any],
    html_config: dict[str, str],
) -> list[tuple[str, list[str]]]:
    metadata = _metadata(channel_doc)
    fetch_config = metadata.get("fetch_config") or {}
    configured_api_url = fetch_config.get("api_url")
    if configured_api_url:
        script_name = (metadata.get("script_names") or ["Live Script"])[0]
        return [(str(script_name), [str(configured_api_url)])]

    host = _sanitize_broadcast_host(html_config.get("ipAddressBCast"))
    port = html_config.get("step3StreamingPort")
    if not host or not port:
        return []

    templates: list[tuple[str, list[str]]] = []
    for label, key in (
        ("default", "defaultScripTemplateId"),
        ("coins", "coinsScripTemplateId"),
    ):
        template_id = html_config.get(key)
        if not template_id:
            continue
        urls: list[str] = []
        for candidate_port in [port, "7768", "7767", html_config.get("step3HTTPPort")]:
            if not candidate_port:
                continue
            candidate_url = (
                f"https://{host}:{candidate_port}/VOTSBroadcastStreaming/Services/xml/GetLiveRateByTemplateID/{template_id}"
            )
            if candidate_url not in urls:
                urls.append(candidate_url)
        templates.append((label, urls))
    return templates


def _coerce_float(raw: str | int | float | None) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    value = raw.strip()
    if not value or value == "-":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _infer_product_type(script_name: str) -> str | None:
    upper = script_name.upper()
    # Some boards abbreviate (e.g. "GLD 999 IMP", "SLV 999 1KG BAR").
    if "GOLD" in upper or "GLD" in upper:
        return "gold"
    if "SILVER" in upper or "SLV" in upper:
        return "silver"
    return None


def _infer_purity(script_name: str) -> str | None:
    upper = script_name.upper()
    if "999" in upper:
        return "999"
    if "995" in upper:
        return "995"
    if "24K" in upper or "24 K" in upper:
        return "24K"
    if "22K" in upper or "22 K" in upper:
        return "22K"
    return None


def _infer_quantity_grams(script_name: str) -> float | None:
    upper = script_name.upper()
    kg_match = re.search(r"(\d+(?:\.\d+)?)\s*KG", upper)
    if kg_match:
        return float(kg_match.group(1)) * 1000
    g_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:GM|GMS|GRAM|GRAMS|G)\b", upper)
    if g_match:
        return float(g_match.group(1))
    return None


def _infer_unit(script_name: str) -> str | None:
    upper = script_name.upper()
    if "KG" in upper:
        return "kg"
    if re.search(r"\b(?:GM|GMS|GRAM|GRAMS|G)\b", upper):
        return "g"
    return None


def _parse_chirayu_rates(raw_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower() == "not found.":
            continue
        parts = [part.strip() for part in stripped.split("\t") if part.strip()]
        if len(parts) < 3:
            continue
        if parts[0].isdigit():
            script_code = parts[0]
            script_name = parts[1]
            values = parts[2:]
        else:
            script_code = ""
            script_name = parts[0]
            values = parts[1:]
        buy_rate = _coerce_float(values[0]) if len(values) >= 1 else None
        sell_rate = _coerce_float(values[1]) if len(values) >= 2 else None
        day_high = _coerce_float(values[2]) if len(values) >= 3 else None
        day_low = _coerce_float(values[3]) if len(values) >= 4 else None
        rows.append(
            {
                "script_code": script_code,
                "script_name": script_name,
                "buy_rate": buy_rate,
                "sell_rate": sell_rate,
                "day_high": day_high,
                "day_low": day_low,
                "product_type": _infer_product_type(script_name),
                "purity": _infer_purity(script_name),
                "quantity_grams": _infer_quantity_grams(script_name),
                "unit": _infer_unit(script_name),
            }
        )
    return rows


async def _fetch_text(url: str) -> str:
    response = await asyncio.to_thread(
        requests.get,
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=HTTP_TIMEOUT_SECONDS,
        verify=False,
    )
    response.raise_for_status()
    return response.text


async def _post_text(url: str, data: dict[str, Any]) -> str:
    response = await asyncio.to_thread(
        requests.post,
        url,
        data=data,
        headers={"User-Agent": USER_AGENT},
        timeout=HTTP_TIMEOUT_SECONDS,
        verify=False,
    )
    response.raise_for_status()
    return response.text


def _extract_lmx_config(html: str) -> dict[str, str]:
    api_url = None
    client = None
    api_match = re.search(r'var\s+bcurl\s*=\s*"([^"]+broadcastrates[^"]*)"', html, re.I)
    if api_match:
        api_url = api_match.group(1).strip()
    client_match = re.search(r'var\s+bcclient\s*=\s*"([^"]+)"', html, re.I)
    if client_match:
        client = client_match.group(1).strip()
    return {"api_url": api_url or "", "client": client or ""}


def _choose_lmx_script_name(fields: list[str]) -> str:
    symbol = fields[1] if len(fields) > 1 else ""
    descriptor = fields[2] if len(fields) > 2 else ""
    if symbol and not symbol.isdigit() and descriptor.upper() in {"GOLD", "SILVER", "INR(₹)", "GOLD($)", "SILVER($)"}:
        return symbol
    if descriptor and len(descriptor) > len(symbol) and not descriptor.isdigit():
        return descriptor
    return symbol or descriptor or "LMX Live Rate"


def _parse_lmx_rates(raw_text: str) -> list[dict[str, Any]]:
    stripped_text = raw_text.strip()
    if not stripped_text:
        return []
    if stripped_text.startswith("{"):
        try:
            payload = json.loads(stripped_text)
        except json.JSONDecodeError:
            payload = {}
        if payload.get("error"):
            return []

    rows: list[dict[str, Any]] = []
    reader = csv.reader(io.StringIO(raw_text), delimiter="\t", quotechar='"')
    for fields in reader:
        fields = [field.strip() for field in fields]
        if len(fields) < 3:
            continue
        script_name = _choose_lmx_script_name(fields)
        combined_name = " ".join(part for part in fields[:3] if part)
        buy_rate = _coerce_float(fields[3]) if len(fields) > 3 else None
        sell_rate = _coerce_float(fields[4]) if len(fields) > 4 else None
        day_high = _coerce_float(fields[5]) if len(fields) > 5 else None
        day_low = _coerce_float(fields[6]) if len(fields) > 6 else None
        rows.append(
            {
                "script_code": fields[0],
                "script_symbol": fields[1] if len(fields) > 1 else "",
                "script_name": script_name,
                "buy_rate": buy_rate,
                "sell_rate": sell_rate,
                "day_high": day_high,
                "day_low": day_low,
                "product_type": _infer_product_type(combined_name),
                "purity": _infer_purity(combined_name),
                "quantity_grams": _infer_quantity_grams(combined_name),
                "unit": _infer_unit(combined_name),
            }
        )
    return rows


def _extract_script_url(html: str, suffix: str, base_url: str) -> str | None:
    match = re.search(rf'<script[^>]+src=["\']([^"\']*{re.escape(suffix)}[^"\']*)["\']', html, re.I)
    if not match:
        return None
    return urljoin(base_url, match.group(1))


def _discover_liverate_socket_config(html: str, custom_js: str, live_js: str, base_url: str) -> dict[str, Any]:
    socket_url_match = re.search(r'var\s+adminsocketurl\s*=\s*"([^"]+)"', custom_js, re.I)
    project_name_match = re.search(r'var\s+prjName\s*=\s*"([^"]+)"', custom_js, re.I)
    socket_url = socket_url_match.group(1).strip() if socket_url_match else ""
    project_name = project_name_match.group(1).strip() if project_name_match else ""

    if "ClientHeaderDetails" in custom_js or "adminsocket.emit('Client'" in custom_js:
        variant = "mahavir_message"
        compression = "plain"
        connect_emits = ["Client", "room"]
        header_event = "ClientHeaderDetails"
        rates_event = "message"
    else:
        raw_mode = "inflateRaw(data" in custom_js or "inflateRaw(data" in live_js
        variant = "mainproduct_raw" if raw_mode else "mainproduct_inflate"
        compression = "raw" if raw_mode else "zlib"
        connect_emits = ["client"]
        if "emit('room'" in live_js or 'emit("room"' in live_js:
            connect_emits.append("room")
        if "emit('GetClientCity'" in live_js or 'emit("GetClientCity"' in live_js:
            connect_emits.append("GetClientCity")
        header_event = "clientDetails"
        rates_event = "mainProduct"

    return {
        "socket_url": socket_url,
        "project_name": project_name,
        "variant": variant,
        "compression": compression,
        "connect_emits": connect_emits,
        "header_event": header_event,
        "rates_event": rates_event,
        "custom_js_url": _extract_script_url(html, "custom.js", base_url),
        "live_js_url": _extract_script_url(html, "Liverate.js", base_url),
        "requires_browser": False,
    }


def _inflate_socket_payload(data: Any, mode: str) -> Any:
    if isinstance(data, (dict, list)):
        return data
    if isinstance(data, str):
        raw = data.encode("latin1", errors="ignore")
    elif isinstance(data, bytearray):
        raw = bytes(data)
    else:
        raw = data
    if not isinstance(raw, (bytes, bytearray)):
        return data
    if mode == "plain":
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return raw.decode("utf-8", errors="ignore")
    try:
        if mode == "raw":
            inflated = zlib.decompress(raw, -zlib.MAX_WBITS)
        else:
            inflated = zlib.decompress(raw)
    except Exception:
        try:
            inflated = zlib.decompress(raw, -zlib.MAX_WBITS)
        except Exception:
            return data
    text = inflated.decode("utf-8", errors="ignore")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _is_displayed_rate(raw_value: Any) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    if raw_value is None:
        return True
    value = str(raw_value).strip().lower()
    return value not in {"false", "0", "no", ""}


def _normalize_socket_rate_rows(payload: Any, config: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(payload, list):
        return rows

    variant = config.get("variant")
    for item in payload:
        if not isinstance(item, dict):
            continue
        if variant == "mahavir_message":
            is_display = _is_displayed_rate(item.get("IsDisplay"))
            if not is_display:
                continue
            symbol = str(item.get("Symbol") or "").strip()
            source = str(item.get("Source") or "").strip()
            script_name = symbol or source or "Live Rate"
            combined = " ".join(part for part in [symbol, source] if part)
            rows.append(
                {
                    "script_name": script_name,
                    "buy_rate": _coerce_float(item.get("Bid")),
                    "sell_rate": _coerce_float(item.get("Ask")),
                    "day_high": _coerce_float(item.get("High")),
                    "day_low": _coerce_float(item.get("Low")),
                    "product_type": _infer_product_type(combined),
                    "purity": _infer_purity(combined),
                    "quantity_grams": _infer_quantity_grams(combined),
                    "unit": _infer_unit(combined),
                    "raw_stock": item.get("Stock"),
                }
            )
            continue

        is_display = _is_displayed_rate(item.get("isView"))
        if not is_display:
            continue
        name = str(item.get("name") or "").strip()
        desc = str(item.get("desc") or "").strip()
        source = str(item.get("src") or "").strip()
        script_name = desc or name or source or "Live Rate"
        combined = " ".join(part for part in [name, desc, source] if part)
        rows.append(
            {
                "script_name": script_name,
                "buy_rate": _coerce_float(item.get("bid")),
                "sell_rate": _coerce_float(item.get("ask")),
                "day_high": _coerce_float(item.get("high")),
                "day_low": _coerce_float(item.get("low")),
                "product_type": _infer_product_type(combined),
                "purity": _infer_purity(combined),
                "quantity_grams": _infer_quantity_grams(combined),
                "unit": _infer_unit(combined),
                "city_id": item.get("cityId"),
            }
        )
    return rows


def _fetch_liverate_socket_rows_sync(config: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        import socketio  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on env
        raise RuntimeError("python-socketio is required for liverate_js_socket adapter") from exc

    received_rows: list[dict[str, Any]] = []
    done = threading.Event()

    sio = socketio.Client(ssl_verify=False, reconnection=False, logger=False, engineio_logger=False)

    @sio.event
    def connect() -> None:  # pragma: no cover - network behavior depends on vendor server
        project_name = config["project_name"]
        for event_name in config.get("connect_emits", []):
            sio.emit(event_name, project_name)

    @sio.on(config["header_event"])
    def _on_header(data: Any) -> None:  # pragma: no cover - network behavior depends on vendor server
        # Header is not needed for normalized price extraction today.
        _ = data

    @sio.on(config["rates_event"])
    def _on_rates(data: Any) -> None:  # pragma: no cover - network behavior depends on vendor server
        payload = data
        if config.get("variant") == "mahavir_message":
            if isinstance(data, dict):
                payload = data.get("Rate") or []
        else:
            payload = _inflate_socket_payload(data, config.get("compression", "zlib"))
        rows = _normalize_socket_rate_rows(payload, config)
        if rows:
            received_rows.clear()
            received_rows.extend(rows)
            done.set()

    sio.connect(config["socket_url"], wait_timeout=SOCKET_WAIT_SECONDS)
    try:
        done.wait(SOCKET_WAIT_SECONDS)
    finally:
        try:
            sio.disconnect()
        except Exception:
            pass
    return received_rows


async def fetch_chirayu_channel_rates(channel_doc: dict[str, Any]) -> list[dict[str, Any]]:
    website_url = normalize_website_url(channel_doc.get("channel_value"))
    if not website_url:
        return []

    html = await _fetch_text(website_url)
    html_config = _extract_local_storage_config(html)
    fetch_targets = _chirayu_fetch_targets(channel_doc, html_config)
    if not fetch_targets:
        return []

    parsed_rows: list[dict[str, Any]] = []
    for target_label, target_urls in fetch_targets:
        payload = None
        target_url = None
        last_error: Exception | None = None
        for candidate_url in target_urls:
            try:
                payload = await _fetch_text(candidate_url)
                target_url = candidate_url
                break
            except Exception as exc:
                last_error = exc
        if payload is None or target_url is None:
            logger.warning("Chirayu rate fetch failed for %s target=%s: %s", website_url, target_label, last_error)
            continue
        target_rows = _parse_chirayu_rates(payload)
        for row in target_rows:
            row["target_label"] = target_label
            # Display/source URL is the public page, NOT the raw broadcast API.
            # This also keeps record_live_rate_snapshot's channel upsert keyed to
            # the same public-page channel (no junk API-keyed channel, and the
            # configured fetch_adapter/api_url are preserved via metadata merge).
            row["source_url"] = website_url
        parsed_rows.extend(target_rows)

    if html_config:
        configured = _metadata(channel_doc).get("fetch_config") or {}
        fetch_config = {
            "broadcast_host": html_config.get("ipAddressBCast") or configured.get("broadcast_host"),
            "broadcast_port": html_config.get("step3StreamingPort") or configured.get("broadcast_port"),
            "default_template_id": html_config.get("defaultScripTemplateId") or configured.get("default_template_id"),
            "coins_template_id": html_config.get("coinsScripTemplateId") or configured.get("coins_template_id"),
            "requires_browser": False,
        }
        # Preserve a manually-configured direct API URL: some boards (e.g. RARA)
        # load their broadcast host/template via JS, so it can't be derived from
        # the static HTML. Without this, the self-upsert would wipe api_url and
        # the next refresh would fetch nothing.
        if configured.get("api_url"):
            fetch_config["api_url"] = configured["api_url"]
        await upsert_vendor_channel(
            vendor_id=channel_doc["vendor_id"],
            category_id=channel_doc["category_id"],
            channel_type=CHANNEL_TYPE_WEBSITE,
            channel_value=website_url,
            city=channel_doc.get("city"),
            priority=channel_doc.get("priority", 1),
            is_active=channel_doc.get("is_active", True),
            metadata=build_website_channel_metadata(
                source_name=_metadata(channel_doc).get("source_name"),
                fetch_adapter=CHIRAYU_API_ADAPTER,
                fetch_config=fetch_config,
                script_names=[row["script_name"] for row in parsed_rows[:20]],
                city_scope=[channel_doc.get("city")] if channel_doc.get("city") else None,
            ),
        )

    return parsed_rows


async def fetch_lmx_channel_rates(channel_doc: dict[str, Any]) -> list[dict[str, Any]]:
    website_url = normalize_website_url(channel_doc.get("channel_value"))
    if not website_url:
        return []

    metadata = _metadata(channel_doc)
    configured = metadata.get("fetch_config") or {}
    api_url = str(configured.get("api_url") or "").strip()
    client = str(configured.get("client") or "").strip()
    html = await _fetch_text(website_url)
    discovered = _extract_lmx_config(html)
    if not api_url:
        api_url = discovered.get("api_url", "")
    if not client:
        client = discovered.get("client", "")
    if not api_url or not client:
        return []

    payload = await _post_text(api_url, {"client": client})
    rows = _parse_lmx_rates(payload)
    await upsert_vendor_channel(
        vendor_id=channel_doc["vendor_id"],
        category_id=channel_doc["category_id"],
        channel_type=CHANNEL_TYPE_WEBSITE,
        channel_value=website_url,
        city=channel_doc.get("city"),
        priority=channel_doc.get("priority", 1),
        is_active=channel_doc.get("is_active", True),
        metadata=build_website_channel_metadata(
            source_name=metadata.get("source_name"),
            fetch_adapter=LMX_BROADCASTRATES_ADAPTER,
            fetch_config={"api_url": api_url, "client": client, "requires_browser": False},
            script_names=[row["script_name"] for row in rows[:20]],
            city_scope=[channel_doc.get("city")] if channel_doc.get("city") else None,
            additional_metadata={
                "adapter_family": metadata.get("adapter_family"),
                "adapter_family_confidence": metadata.get("adapter_family_confidence"),
                "classification_notes": metadata.get("classification_notes"),
            },
        ),
    )
    for row in rows:
        row["source_url"] = api_url
    return rows


async def fetch_liverate_socket_channel_rates(channel_doc: dict[str, Any]) -> list[dict[str, Any]]:
    website_url = normalize_website_url(channel_doc.get("channel_value"))
    if not website_url:
        return []

    metadata = _metadata(channel_doc)
    configured = metadata.get("fetch_config") or {}
    html = await _fetch_text(website_url)

    if configured.get("socket_url") and configured.get("project_name") and configured.get("variant"):
        socket_config = dict(configured)
    else:
        custom_js_url = _extract_script_url(html, "custom.js", website_url)
        live_js_url = _extract_script_url(html, "Liverate.js", website_url)
        if not custom_js_url or not live_js_url:
            return []
        custom_js = await _fetch_text(custom_js_url)
        live_js = await _fetch_text(live_js_url)
        socket_config = _discover_liverate_socket_config(html, custom_js, live_js, website_url)

    if not socket_config.get("socket_url") or not socket_config.get("project_name"):
        return []

    rows = await asyncio.to_thread(_fetch_liverate_socket_rows_sync, socket_config)
    await upsert_vendor_channel(
        vendor_id=channel_doc["vendor_id"],
        category_id=channel_doc["category_id"],
        channel_type=CHANNEL_TYPE_WEBSITE,
        channel_value=website_url,
        city=channel_doc.get("city"),
        priority=channel_doc.get("priority", 1),
        is_active=channel_doc.get("is_active", True),
        metadata=build_website_channel_metadata(
            source_name=metadata.get("source_name"),
            fetch_adapter=LIVERATE_JS_SOCKET_ADAPTER,
            fetch_config=socket_config,
            script_names=[row["script_name"] for row in rows[:20]],
            city_scope=[channel_doc.get("city")] if channel_doc.get("city") else None,
            additional_metadata={
                "adapter_family": metadata.get("adapter_family"),
                "adapter_family_confidence": metadata.get("adapter_family_confidence"),
                "classification_notes": metadata.get("classification_notes"),
            },
        ),
    )
    for row in rows:
        row["source_url"] = socket_config.get("socket_url") or website_url
    return rows


async def _record_gold_rows(
    *,
    channel_doc: dict[str, Any],
    rows: list[dict[str, Any]],
    source_name: str,
) -> int:
    city = channel_doc.get("city") or ""
    vendor_id = channel_doc["vendor_id"]
    category_id = channel_doc["category_id"]
    inserted = 0
    for row in rows:
        if row.get("product_type") not in {"gold", "silver", None}:
            continue
        if not row.get("product_type"):
            row["product_type"] = "gold"
        buy_rate = row.get("buy_rate")
        sell_rate = row.get("sell_rate")
        if (buy_rate is None or buy_rate <= 0) and (sell_rate is None or sell_rate <= 0):
            continue
        await record_live_rate_snapshot(
            vendor_id=vendor_id,
            category_id=category_id,
            city=city,
            script_name=row["script_name"],
            source_name=source_name,
            source_url=row.get("source_url") or channel_doc.get("channel_value"),
            buy_rate=buy_rate,
            sell_rate=sell_rate,
            purity=row.get("purity"),
            product_type=row.get("product_type"),
            quantity_grams=row.get("quantity_grams"),
            unit=row.get("unit"),
            raw_payload=row,
        )
        inserted += 1
    return inserted


async def refresh_chirayu_channel(channel_doc: dict[str, Any]) -> int:
    rows = await fetch_chirayu_channel_rates(channel_doc)
    return await _record_gold_rows(channel_doc=channel_doc, rows=rows, source_name=CHIRAYU_API_ADAPTER)


async def refresh_lmx_channel(channel_doc: dict[str, Any]) -> int:
    rows = await fetch_lmx_channel_rates(channel_doc)
    return await _record_gold_rows(channel_doc=channel_doc, rows=rows, source_name=LMX_BROADCASTRATES_ADAPTER)


async def refresh_liverate_socket_channel(channel_doc: dict[str, Any]) -> int:
    rows = await fetch_liverate_socket_channel_rates(channel_doc)
    return await _record_gold_rows(channel_doc=channel_doc, rows=rows, source_name=LIVERATE_JS_SOCKET_ADAPTER)


async def _persist_auto_recipe(channel_doc: dict[str, Any], fetch_config: dict[str, Any]) -> None:
    """Store the discovered/refreshed recipe in the channel's metadata.fetch_config
    (same place the bespoke adapters persist their config)."""
    await upsert_vendor_channel(
        vendor_id=channel_doc["vendor_id"],
        category_id=channel_doc["category_id"],
        channel_type=CHANNEL_TYPE_WEBSITE,
        channel_value=channel_doc.get("channel_value"),
        city=channel_doc.get("city"),
        priority=channel_doc.get("priority", 1),
        is_active=channel_doc.get("is_active", True),
        metadata=build_website_channel_metadata(
            source_name=_metadata(channel_doc).get("source_name"),
            fetch_adapter=AUTO_ADAPTER,
            fetch_config=fetch_config,
            city_scope=[channel_doc.get("city")] if channel_doc.get("city") else None,
        ),
    )


def _generic_rows_to_record_rows(generic_rows: list[dict[str, Any]], source_url: str) -> list[dict[str, Any]]:
    record_rows: list[dict[str, Any]] = []
    for row in generic_rows:
        label = row.get("label") or "Live Rate"
        # Category is gold bullion; treat anything not clearly silver as gold so
        # rows like "GLD 999 IMP" aren't dropped by the gold-only record filter.
        product_type = "silver" if _infer_product_type(label) == "silver" else "gold"
        record_rows.append(
            {
                "script_name": label,
                "buy_rate": row.get("buy_rate"),
                "sell_rate": row.get("sell_rate"),
                "product_type": product_type,
                "purity": row.get("purity") or _infer_purity(label),
                "quantity_grams": _infer_quantity_grams(label),
                "unit": row.get("unit") or _infer_unit(label),
                "source_url": source_url,
            }
        )
    return record_rows


# ── Generic-adapter rate validation ─────────────────────────────────────────
# The generic ("auto") adapter extracts rates with an LLM / heuristic parse, so
# unlike the curated bespoke adapters it can pick up rows that must NEVER reach
# the live board: futures contracts, demo/test scripts, and mis-parsed numbers
# (a change %, an index, a partial value). These guards run ONLY on auto rows.
_NON_SPOT_MARKERS = (
    "demo", "not for trade", "dummy", "sample", "test ",
    "fut", "future", "mcx", "comex", "nymex",
    # Foreign-currency / international quotes must not show as INR retail rates.
    "$", "usd", "ounce", "/oz",
)
# Plausible bands for the RAW displayed value across common units (per g / 10g /
# 100g / kg / tola). Gold spot is ~₹14k/g, so even a per-gram quote is well
# above ₹5k — anything lower is a mis-parse. Silver retail is ~₹90/g (₹90k/kg),
# so a value below ~₹70 is an international/FX/mis-parsed figure, not INR retail.
# Upper bounds allow a per-kg quote (~₹1.5cr) with headroom.
_GOLD_RATE_MIN, _GOLD_RATE_MAX = 5_000.0, 50_000_000.0
_SILVER_RATE_MIN, _SILVER_RATE_MAX = 70.0, 50_000_000.0


def _auto_rate_row_is_valid(row: dict[str, Any]) -> bool:
    """True only for a buyable spot retail rate in a plausible range. Rejects
    futures/demo scripts and out-of-band (mis-parsed) values."""
    name = (row.get("script_name") or "").strip().lower()
    if not name:
        return False
    if any(marker in name for marker in _NON_SPOT_MARKERS):
        return False
    if row.get("product_type") == "silver":
        lo, hi = _SILVER_RATE_MIN, _SILVER_RATE_MAX
    else:
        lo, hi = _GOLD_RATE_MIN, _GOLD_RATE_MAX
    positives: list[float] = []
    for key in ("sell_rate", "buy_rate"):
        value = row.get(key)
        if value is None:
            continue
        try:
            num = float(value)
        except (TypeError, ValueError):
            return False
        if num > 0:
            positives.append(num)
    if not positives:
        return False
    # A single wildly out-of-band value means the parse is unreliable for the
    # whole row, so require EVERY provided positive rate to be in band.
    return all(lo <= num <= hi for num in positives)


def _filter_valid_auto_rows(record_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept = [row for row in record_rows if _auto_rate_row_is_valid(row)]
    dropped = len(record_rows) - len(kept)
    if dropped:
        logger.info("generic_adapter: dropped %d invalid auto rate row(s) of %d", dropped, len(record_rows))
    return kept


async def refresh_auto_channel(channel_doc: dict[str, Any], *, allow_browser: bool = False) -> int:
    """Generic adapter: replay a cached recipe (cheap) or, on a miss/stale recipe,
    discover inline (budget-limited, no stealth) and cache the result.

    ``allow_browser`` is False on the live per-query path (HTTP-only, no inline
    Chromium in prod). The offline browser-rate poller passes True so socket/JS
    feeds (needs_browser recipes) render via local Chromium and get snapshotted."""
    if not settings.gold_generic_adapter_enabled:
        return 0
    url = normalize_website_url(channel_doc.get("channel_value"))
    if not url:
        return 0

    fetch_config = dict(_metadata(channel_doc).get("fetch_config") or {})
    fetch_config.setdefault("url", url)

    rows: list[dict[str, Any]] = []
    source: str | None = None
    recipe_to_persist: dict[str, Any] | None = None

    # 1) Replay an existing recipe.
    if fetch_config.get("source"):
        result = await generic_adapter.replay(fetch_config, allow_browser=allow_browser)
        rows = result.get("rows") or []
        source = result.get("source")
        if rows and result.get("parse_spec"):
            fetch_config["parse_spec"] = result["parse_spec"]
            recipe_to_persist = fetch_config

    # 2) Inline discovery on miss/stale, bounded by the per-query budget.
    if not rows:
        budget = _inline_discovery_budget.get(None)
        if budget is None:
            # Called outside refresh_website_live_rates_for_query (no budget set);
            # discovery is intentionally skipped. Log so the no-op is visible.
            logger.info(
                "generic_adapter: no inline-discovery budget in context for vendor=%s; "
                "skipping discovery (seed offline or call within a query refresh).",
                channel_doc.get("vendor_id"),
            )
        elif budget and budget[0] > 0:
            budget[0] -= 1
            discovered = await generic_adapter.discover(url, use_stealth=False, allow_browser=allow_browser)
            if discovered:
                rows = discovered.get("rows") or []
                source = discovered.get("source")
                fetch_config.update(
                    {
                        "source": discovered.get("source"),
                        "parse_spec": discovered.get("parse_spec"),
                        "needs_browser": discovered.get("needs_browser"),
                        "discovered_via": discovered.get("discovered_via"),
                        "url": discovered.get("url") or url,
                    }
                )
                recipe_to_persist = fetch_config

    if not rows or not source:
        return 0

    if recipe_to_persist is not None:
        try:
            await _persist_auto_recipe(channel_doc, recipe_to_persist)
        except Exception as exc:  # pragma: no cover - persistence best effort
            logger.warning("generic_adapter recipe persist failed for vendor=%s: %s", channel_doc.get("vendor_id"), exc)

    record_rows = _generic_rows_to_record_rows(rows, source)
    record_rows = _filter_valid_auto_rows(record_rows)
    if not record_rows:
        return 0
    return await _record_gold_rows(channel_doc=channel_doc, rows=record_rows, source_name=AUTO_SOURCE_NAME)


ADAPTER_REFRESHERS: dict[str, Any] = {
    CHIRAYU_API_ADAPTER: refresh_chirayu_channel,
    LMX_BROADCASTRATES_ADAPTER: refresh_lmx_channel,
    LIVERATE_JS_SOCKET_ADAPTER: refresh_liverate_socket_channel,
    AUTO_ADAPTER: refresh_auto_channel,
}


async def refresh_website_live_rates_for_query(
    query: StructuredQuery,
    *,
    force: bool = False,
    on_vendor_refreshed: Callable[[str], Awaitable[None]] | None = None,
) -> LiveRateRefreshSummary:
    city = _canonical_city(query.location)
    summary = LiveRateRefreshSummary()
    # Reset the per-query inline-discovery budget for the generic adapter.
    _inline_discovery_budget.set([settings.gold_generic_inline_discovery_budget])

    channel_docs = await zwig_vendor_channels_collection.find(
        {
            "category_id": GOLD_BULLION_CATEGORY_ID,
            "channel_type": CHANNEL_TYPE_WEBSITE,
            "is_active": {"$ne": False},
        }
    ).sort("priority", 1).to_list(length=500)

    eligible_channels: list[dict[str, Any]] = []
    for channel_doc in channel_docs:
        if not is_website_channel_type(channel_doc.get("channel_type")):
            continue
        if not _channel_supports_live_rates(channel_doc):
            continue
        adapter_name = _channel_adapter_name(channel_doc)
        if adapter_name not in ADAPTER_REFRESHERS:
            continue
        channel_city = _canonical_city(channel_doc.get("city"))
        if city and channel_city and channel_city.lower() != city.lower():
            continue
        eligible_channels.append(channel_doc)

    # Rank so TRUSTED bespoke adapters (chirayu/lmx/liverate) are refreshed
    # FIRST and never starved out of the cap by the hundreds of generic "auto"
    # channels. (Regression: once the generic adapter was enabled + seeded
    # broadly, auto channels filled all LIVE_RATE_MAX_REFRESH_VENDORS slots and
    # bespoke feeds went stale — which also disabled the outlier guard that
    # depends on a fresh trusted consensus.) Order: bespoke → auto-with-recipe
    # (cheap, reliable replay) → bare auto (no recipe yet; may yield nothing).
    def _refresh_rank(channel_doc: dict[str, Any]) -> int:
        adapter_name = _channel_adapter_name(channel_doc)
        if adapter_name != AUTO_ADAPTER:
            return 0  # bespoke — trusted producers, always refreshed first
        has_recipe = bool((_metadata(channel_doc).get("fetch_config") or {}).get("source"))
        return 1 if has_recipe else 2

    eligible_channels.sort(key=lambda c: (_refresh_rank(c), c.get("priority", 100)))
    eligible_channels = eligible_channels[:LIVE_RATE_MAX_REFRESH_VENDORS]

    semaphore = asyncio.Semaphore(LIVE_RATE_FETCH_CONCURRENCY)

    async def _refresh_one(channel_doc: dict[str, Any]) -> None:
        nonlocal summary
        vendor_id = channel_doc["vendor_id"]
        age_seconds = await _latest_snapshot_age_seconds(vendor_id)
        if not force and age_seconds is not None and age_seconds <= LIVE_RATE_SNAPSHOT_TTL_SECONDS:
            summary.skipped_fresh += 1
            return

        adapter_name = _channel_adapter_name(channel_doc)
        refresher = ADAPTER_REFRESHERS.get(adapter_name)
        if refresher is None:
            return

        summary.attempted += 1
        try:
            async with semaphore:
                inserted = await refresher(channel_doc)
            if inserted:
                summary.refreshed += 1
                vendor_id_str = str(vendor_id)
                summary.refreshed_vendor_ids.append(vendor_id_str)
                if on_vendor_refreshed is not None:
                    await on_vendor_refreshed(vendor_id_str)
            else:
                summary.failed += 1
        except Exception as exc:  # pragma: no cover - network behavior depends on vendors
            logger.warning("Gold live rate refresh failed for vendor=%s adapter=%s: %s", vendor_id, adapter_name, exc)
            summary.failed += 1

    await asyncio.gather(*(_refresh_one(channel_doc) for channel_doc in eligible_channels))
    return summary


async def poll_browser_live_rates(
    *,
    city: str | None = None,
    limit: int = 300,
    concurrency: int = 3,
) -> LiveRateRefreshSummary:
    """Offline poller for socket/JS (needs_browser) gold feeds.

    These can't be replayed cheaply over HTTP, and we don't render at query time
    in prod. This renders them server-side via local Chromium (allow_browser=True)
    and writes live-rate snapshots, so the live board (which reads the latest
    snapshot, no browser) can show them. Intended to run as a scheduled Cloud Run
    Job, NOT inside the API request path.
    """
    summary = LiveRateRefreshSummary()
    if not settings.gold_generic_adapter_enabled:
        return summary

    query: dict[str, Any] = {
        "category_id": GOLD_BULLION_CATEGORY_ID,
        "channel_type": CHANNEL_TYPE_WEBSITE,
        "is_active": {"$ne": False},
        "metadata.fetch_adapter": AUTO_ADAPTER,
        "metadata.fetch_config.needs_browser": True,
    }
    channel_docs = await zwig_vendor_channels_collection.find(query).limit(limit).to_list(length=limit)

    if city:
        target = _canonical_city(city)
        channel_docs = [
            doc for doc in channel_docs
            if not _canonical_city(doc.get("city"))
            or (_canonical_city(doc.get("city")) or "").lower() == (target or "").lower()
        ]

    # Shared discovery budget so a broken recipe can re-discover (allow_browser=True
    # below renders), bounded to one re-discovery per channel.
    _inline_discovery_budget.set([len(channel_docs)])
    semaphore = asyncio.Semaphore(concurrency)

    async def _poll_one(channel_doc: dict[str, Any]) -> None:
        nonlocal summary
        summary.attempted += 1
        vendor_id = channel_doc.get("vendor_id")
        try:
            async with semaphore:
                inserted = await refresh_auto_channel(channel_doc, allow_browser=True)
            if inserted:
                summary.refreshed += 1
                if vendor_id:
                    summary.refreshed_vendor_ids.append(str(vendor_id))
            else:
                summary.failed += 1
        except Exception as exc:  # pragma: no cover - depends on vendor sites
            logger.warning("Browser live-rate poll failed for vendor=%s: %s", vendor_id, exc)
            summary.failed += 1

    await asyncio.gather(*(_poll_one(doc) for doc in channel_docs))
    logger.info(
        "Browser live-rate poll complete: attempted=%d refreshed=%d failed=%d (city=%s)",
        summary.attempted, summary.refreshed, summary.failed, city or "all",
    )
    return summary


# Trusted bespoke adapters that the offline poller keeps fresh regardless of
# query traffic. These are HTTP-only (no browser) and NOT gated on the generic
# adapter flag — they are the vetted anchor feeds the outlier guard depends on.
BESPOKE_POLL_ADAPTERS = (
    CHIRAYU_API_ADAPTER,
    LMX_BROADCASTRATES_ADAPTER,
    LIVERATE_JS_SOCKET_ADAPTER,
)


async def poll_bespoke_live_rates(
    *,
    city: str | None = None,
    limit: int = 1000,
    concurrency: int = 5,
) -> LiveRateRefreshSummary:
    """Offline poller for the TRUSTED bespoke gold feeds (chirayu/lmx/liverate).

    The per-query refresh only freshens the cities that are actually queried, so
    low-traffic cities' bespoke feeds go stale — and a stale trusted feed
    disables the national consensus the outlier guard relies on. This keeps ALL
    bespoke channels fresh on a schedule, independent of query traffic. HTTP-only
    (no browser), so it's cheap. Intended to run as a scheduled Cloud Run Job,
    NOT inside the API request path.
    """
    summary = LiveRateRefreshSummary()
    query: dict[str, Any] = {
        "category_id": GOLD_BULLION_CATEGORY_ID,
        "channel_type": CHANNEL_TYPE_WEBSITE,
        "is_active": {"$ne": False},
        "metadata.fetch_adapter": {"$in": list(BESPOKE_POLL_ADAPTERS)},
    }
    channel_docs = await zwig_vendor_channels_collection.find(query).limit(limit).to_list(length=limit)

    if city:
        target = (_canonical_city(city) or "").lower()
        channel_docs = [
            doc for doc in channel_docs
            if not _canonical_city(doc.get("city"))
            or (_canonical_city(doc.get("city")) or "").lower() == target
        ]

    semaphore = asyncio.Semaphore(concurrency)

    async def _poll_one(channel_doc: dict[str, Any]) -> None:
        summary.attempted += 1
        vendor_id = channel_doc.get("vendor_id")
        adapter_name = _channel_adapter_name(channel_doc)
        refresher = ADAPTER_REFRESHERS.get(adapter_name)
        if refresher is None:
            summary.failed += 1
            return
        try:
            async with semaphore:
                inserted = await refresher(channel_doc)
            if inserted:
                summary.refreshed += 1
                if vendor_id:
                    summary.refreshed_vendor_ids.append(str(vendor_id))
            else:
                summary.failed += 1
        except Exception as exc:  # pragma: no cover - depends on vendor sites
            logger.warning("Bespoke live-rate poll failed for vendor=%s: %s", vendor_id, exc)
            summary.failed += 1

    await asyncio.gather(*(_poll_one(doc) for doc in channel_docs))
    logger.info(
        "Bespoke live-rate poll complete: attempted=%d refreshed=%d failed=%d (city=%s)",
        summary.attempted, summary.refreshed, summary.failed, city or "all",
    )
    return summary
