from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


PRICE_RE = re.compile(r"₹\s*([0-9][0-9,]*(?:\.\d{1,2})?)")
NON_PRODUCT_PRICE_PREFIX_RE = re.compile(
    r"(delivery|shipping|ship|fee|charge|charges|cashback|coupon|emi|monthly|month|\bmo\b|save|saved|discount)",
    re.IGNORECASE,
)
NON_PRODUCT_PRICE_SUFFIX_RE = re.compile(
    r"(?:/\s*)?(month|mo)\b|over\s+flipkart",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FlashPriceEntry:
    site: str
    price: float
    price_text: str
    url: str | None
    raw_text: str


def parse_flash_store_rows(rows: list[dict[str, Any]]) -> list[FlashPriceEntry]:
    entries: list[FlashPriceEntry] = []
    seen: set[tuple[str, float, str | None]] = set()

    for row in rows:
        text = str(row.get("text") or "").strip()
        if not text:
            continue

        price_matches = list(PRICE_RE.finditer(text))
        if not price_matches:
            continue

        price_match = _select_product_price_match(text, price_matches)
        if not price_match:
            continue
        site = _extract_site_name(text, price_match.start())
        if not site:
            continue

        price_text = price_match.group(0).replace(" ", "")
        price = float(price_match.group(1).replace(",", ""))
        url = _unwrap_redirect_url(str(row.get("href") or "").strip() or None)
        key = (site.lower(), price, url)
        if key in seen:
            continue
        seen.add(key)

        entries.append(
            FlashPriceEntry(
                site=site,
                price=price,
                price_text=price_text,
                url=url,
                raw_text=text,
            )
        )

    return sorted(entries, key=lambda entry: entry.price)


def _select_product_price_match(text: str, price_matches: list[re.Match[str]]) -> re.Match[str] | None:
    candidates = [
        match
        for match in price_matches
        if not _is_non_product_price_context(text, match)
    ]
    if candidates:
        return candidates[0]
    return price_matches[0] if price_matches else None


def _is_non_product_price_context(text: str, price_match: re.Match[str]) -> bool:
    before = text[max(0, price_match.start() - 28):price_match.start()]
    after = text[price_match.end():price_match.end() + 28]
    if NON_PRODUCT_PRICE_PREFIX_RE.search(before) or NON_PRODUCT_PRICE_SUFFIX_RE.search(after):
        return True

    return False


def _extract_site_name(text: str, price_start: int) -> str | None:
    prefix = text[:price_start]
    prefix = re.sub(r"Save\s+₹[0-9][0-9,]*(?:\.\d{1,2})?\s+over\s+Flipkart!?", " ", prefix, flags=re.IGNORECASE)
    prefix = re.sub(r"You came from here", " ", prefix, flags=re.IGNORECASE)
    prefix = re.sub(r"LOWEST PRICE", " ", prefix, flags=re.IGNORECASE)
    prefix = re.sub(r"location_on", " ", prefix, flags=re.IGNORECASE)
    prefix = re.sub(r"[\u2022|]+", " ", prefix)
    prefix = re.sub(r"\s+", " ", prefix).strip()
    if not prefix:
        return None

    tokens = prefix.split()
    if len(tokens) > 1 and len(tokens[0]) == 1:
        tokens = tokens[1:]
    return " ".join(tokens).strip() or None


def _unwrap_redirect_url(url: str | None) -> str | None:
    if not url:
        return None

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for param in ("url", "u", "redirect", "redirect_url", "target", "destination"):
        values = query.get(param)
        if not values:
            continue
        candidate = unquote(values[0])
        if candidate.startswith(("http://", "https://")):
            return candidate

    return url
