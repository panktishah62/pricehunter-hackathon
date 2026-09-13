from __future__ import annotations

import json
import re
import socket
from datetime import datetime, timedelta, timezone
from html import unescape
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.models.schemas import ProductPreviewResponse, ProductPreviewSpec

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
)
FETCH_TIMEOUT_SECONDS = 20
CACHE_TTL = timedelta(minutes=30)
MAX_IMAGES = 4
MAX_SPECS = 12
MAX_COMPANY_DETAILS = 8
MAX_DESCRIPTION_CHARS = 700

_CACHE: dict[str, tuple[datetime, ProductPreviewResponse]] = {}


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        value = " ".join(str(item) for item in value if item)
    text = BeautifulSoup(unescape(str(value)), "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _first(*values: Any) -> str:
    for value in values:
        cleaned = _clean(value)
        if cleaned:
            return cleaned
    return ""


def _truthy(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    lowered = str(value).strip().lower()
    if lowered in {"true", "yes", "1", "verified"}:
        return True
    if lowered in {"false", "no", "0"}:
        return False
    return None


def _spec(label: Any, value: Any) -> ProductPreviewSpec | None:
    clean_label = _clean(label)
    clean_value = _clean(value)
    if not clean_label or not clean_value:
        return None
    return ProductPreviewSpec(label=clean_label, value=clean_value)


def _spec_list(items: Any, *, label_keys: tuple[str, ...], value_keys: tuple[str, ...], limit: int) -> list[ProductPreviewSpec]:
    specs: list[ProductPreviewSpec] = []
    if not isinstance(items, list):
        return specs
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        label = _first(*(item.get(key) for key in label_keys))
        value = _first(*(item.get(key) for key in value_keys))
        spec = _spec(label, value)
        if not spec:
            continue
        key = f"{spec.label.lower()}:{spec.value.lower()}"
        if key in seen:
            continue
        seen.add(key)
        specs.append(spec)
        if len(specs) >= limit:
            break
    return specs


def _image_urls(data: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    urls: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value.startswith(("http://", "https://")) and value not in urls:
            urls.append(value)

    for item in data.get("ITEM_IMG") or []:
        if not isinstance(item, dict):
            continue
        for key in ("IMAGE_1000x1000", "IMAGE_500X500", "IMAGE_ORIGINAL", "IMAGE_250x250"):
            add(item.get(key))

    for key in ("PC_ITEM_IMG_1000x1000", "PC_ITEM_IMG_ORIGINAL", "PC_IMG_SMALL_600X600", "PC_ITEM_IMG_SMALL"):
        add(data.get(key))

    for selector in ('meta[property="og:image"]', 'meta[name="twitter:image"]'):
        tag = soup.select_one(selector)
        add(tag.get("content") if tag else None)

    return urls[:MAX_IMAGES]


def _extract_next_data(soup: BeautifulSoup) -> dict[str, Any]:
    script = soup.select_one("#__NEXT_DATA__")
    if not script:
        return {}
    try:
        payload = json.loads(script.get_text())
    except json.JSONDecodeError:
        return {}
    data = (
        payload.get("props", {})
        .get("pageProps", {})
        .get("serviceRes", {})
        .get("Data", [])
    )
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return {}


def _price_and_unit(data: dict[str, Any], soup: BeautifulSoup) -> tuple[str | None, str | None]:
    price = _first(data.get("PRODUCT_PRICE"), data.get("PRICE_SEO"))
    if not price:
        text = soup.get_text(" ", strip=True)
        match = re.search(r"(₹\s*[\d,]+(?:\.\d+)?\s*/?\s*[A-Za-z ]{0,20})", text)
        price = _clean(match.group(1)) if match else ""
    unit = _first(data.get("PC_ITEM_MOQ_UNIT_TYPE"))
    if not unit and price and "/" in price:
        unit = price.split("/", 1)[1].strip()
    return price or None, unit or None


def _meta_content(soup: BeautifulSoup, selector: str) -> str:
    tag = soup.select_one(selector)
    return _clean(tag.get("content")) if tag and tag.get("content") else ""


def _parse_preview(url: str, html: str) -> ProductPreviewResponse:
    soup = BeautifulSoup(html, "html.parser")
    data = _extract_next_data(soup)

    images = _image_urls(data, soup)
    price, unit = _price_and_unit(data, soup)
    specs = _spec_list(
        data.get("ISQ"),
        label_keys=("FK_IM_SPEC_MASTER_DESC", "TITLE", "label", "name"),
        value_keys=("SUPPLIER_RESPONSE_DETAIL", "IM_SPEC_OPTIONS_DESC", "DATA", "value"),
        limit=MAX_SPECS,
    )
    company_details = _spec_list(
        data.get("Basic_Information"),
        label_keys=("TITLE", "label", "name"),
        value_keys=("DATA", "value"),
        limit=MAX_COMPANY_DETAILS,
    )

    gst = ""
    statutory = _spec_list(
        data.get("Statutory_Profile"),
        label_keys=("TITLE",),
        value_keys=("DATA",),
        limit=50,
    )
    for item in statutory:
        if "gst" in item.label.lower():
            gst = item.value
            break

    title = _first(data.get("PC_ITEM_DISPLAY_NAME"), data.get("PC_ITEM_NAME"), _meta_content(soup, 'meta[property="og:title"]'), soup.title.get_text(" ", strip=True) if soup.title else "")
    description = _first(data.get("PC_ITEM_DESC_SMALL"), _meta_content(soup, 'meta[property="og:description"]'), _meta_content(soup, 'meta[name="description"]'))
    if len(description) > MAX_DESCRIPTION_CHARS:
        description = description[:MAX_DESCRIPTION_CHARS].rsplit(" ", 1)[0] + "..."

    rating_count = data.get("SELLER_RATING_COUNTS")
    if isinstance(rating_count, dict):
        rating_count = rating_count.get("total_count")

    trust_badges: list[str] = []
    if _truthy(data.get("GST_VERIFIED")) or _truthy(data.get("GST_VERIFIED_FLAG")):
        trust_badges.append("GST verified")
    legal_status = _first(data.get("GL_LEGAL_STATUS_VAL"))
    if legal_status:
        trust_badges.append(legal_status)

    available = next((spec.value for spec in specs if spec.label.lower() == "availability"), None)

    return ProductPreviewResponse(
        url=url,
        title=title or None,
        price=price,
        unit=unit,
        image_url=images[0] if images else None,
        images=images,
        specs=specs,
        description=description or None,
        supplier_name=_first(data.get("COMPANYNAME"), data.get("GLUSR_CUST_NAME")) or None,
        supplier_city=_first(data.get("CITY"), data.get("GLUSR_USR_DISTRICT")) or None,
        supplier_address=_first(data.get("LONG_ADDRESS"), data.get("SHORT_ADDRESS"), data.get("ADDRESS")) or None,
        supplier_phone=_first(data.get("MOBILE_PNS")) or None,
        supplier_rating=_first(data.get("SELLER_RATING"), data.get("SUPPLIER_RATING")) or None,
        supplier_rating_count=_first(rating_count) or None,
        response_rate=_first(data.get("PNS_RATIO")) or None,
        gst=gst or None,
        gst_verified=_truthy(data.get("GST_VERIFIED") or data.get("GST_VERIFIED_FLAG")),
        trust_badges=trust_badges,
        company_details=company_details,
        available=available,
    )


def _is_private_host(hostname: str) -> bool:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return True
    for info in infos:
        try:
            parsed_ip = ip_address(info[4][0])
        except ValueError:
            return True
        if parsed_ip.is_private or parsed_ip.is_loopback or parsed_ip.is_link_local:
            return True
    return False


def _validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only public http/https product URLs are supported.")
    if _is_private_host(parsed.hostname):
        raise ValueError("Private or local URLs are not supported.")
    return url


async def fetch_product_preview(url: str) -> ProductPreviewResponse:
    normalized_url = _validate_url(url.strip())
    cached = _CACHE.get(normalized_url)
    now = datetime.now(timezone.utc)
    if cached and now - cached[0] < CACHE_TTL:
        return cached[1]

    async with httpx.AsyncClient(follow_redirects=True, timeout=FETCH_TIMEOUT_SECONDS) as client:
        response = await client.get(normalized_url, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        preview = _parse_preview(str(response.url), response.text)
        _CACHE[normalized_url] = (now, preview)
        return preview
