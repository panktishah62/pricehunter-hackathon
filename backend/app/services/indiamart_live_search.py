"""Standalone IndiaMART directory live search (research / market-test).

Builds city+product slug URLs, fetches listing HTML, parses supplier cards.
Does not use Apify and does not touch the main Zwig search/ingest pipelines.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from html import unescape
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def _resolve_repo_root_and_helper() -> tuple[Path, Path]:
    """Resolve monorepo root in dev and /app backend root in Cloud Run.

    Dev path:  <repo>/pricehunter/backend/app/services/this.py → parents[4] == <repo>
    Docker:    /app/app/services/this.py → parents[2] == /app  (parents[4] crashes)
    """
    here = Path(__file__).resolve()
    helper_rel = Path("tools") / "scrapers" / "indiamart" / "fetch_indiamart_html.mjs"
    for parent in here.parents:
        helper = parent / helper_rel
        if helper.exists():
            return parent, helper
    # Packaged backend image: keep import safe even if the helper isn't baked in yet.
    backend_root = here.parents[2] if len(here.parents) > 2 else here.parent
    return backend_root, backend_root / helper_rel


REPO_ROOT, FETCH_HELPER = _resolve_repo_root_and_helper()

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# Common product phrases → IndiaMART directory slugs (often singular / abbreviated).
PRODUCT_SLUG_ALIASES: dict[str, list[str]] = {
    "kitchen utensils": ["kitchen-utensil", "steel-utensils", "cookware"],
    "kitchen utensil": ["kitchen-utensil"],
    "led bulbs": ["led-bulb", "led-lights"],
    "led bulb": ["led-bulb"],
    "stainless steel pipes": ["ss-pipes", "stainless-steel-pipe"],
    "ss pipes": ["ss-pipes"],
    "pharma machinery": ["pharmaceutical-machines", "packaging-machine"],
    "pharmaceutical machinery": ["pharmaceutical-machines"],
    "electric motors": ["electric-motors", "electric-motor"],
    "electric motor": ["electric-motors", "electric-motor"],
    "door handles": ["door-handles", "door-handle"],
    "door handle": ["door-handles", "door-handle"],
}

# Common display-name → IndiaMART city slug overrides.
CITY_SLUGS: dict[str, str] = {
    "delhi": "delhi",
    "new delhi": "delhi",
    "bengaluru": "bengaluru",
    "bangalore": "bengaluru",
    "mumbai": "mumbai",
    "bombay": "mumbai",
    "chennai": "chennai",
    "madras": "chennai",
    "hyderabad": "hyderabad",
    "ahmedabad": "ahmedabad",
    "pune": "pune",
    "kolkata": "kolkata",
    "calcutta": "kolkata",
    "jaipur": "jaipur",
    "surat": "surat",
    "rajkot": "rajkot",
    "vadodara": "vadodara",
    "baroda": "vadodara",
    "indore": "indore",
    "nagpur": "nagpur",
    "lucknow": "lucknow",
    "kanpur": "kanpur",
    "coimbatore": "coimbatore",
    "chandigarh": "chandigarh",
    "noida": "noida",
    "gurgaon": "gurgaon",
    "gurugram": "gurgaon",
    "faridabad": "faridabad",
    "ghaziabad": "ghaziabad",
    "bhopal": "bhopal",
    "patna": "patna",
    "visakhapatnam": "visakhapatnam",
    "vizag": "visakhapatnam",
}

FetchMode = Literal["auto", "http", "cloud"]


@dataclass
class IndiaMartCard:
    supplier_name: str
    supplier_url: str | None
    product_name: str | None
    product_url: str | None
    price: str | None
    price_unit: str | None
    city: str | None
    address: str | None
    years_in_business: str | None
    rating: float | None
    rating_count: int | None
    response_rate: str | None
    badges: list[str]
    attributes: dict[str, str]
    image_url: str | None
    phones: list[str]
    listing_text: str


OnCardEnriched = Callable[[IndiaMartCard], Awaitable[None] | None]
OnLiveResult = Callable[[dict[str, Any]], Awaitable[None] | None]


def slugify(value: str) -> str:
    text = unescape(value or "").strip().lower()
    text = re.sub(r"[^\w\s-]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    return text


def city_slug(city: str) -> str:
    key = re.sub(r"\s+", " ", (city or "").strip().lower())
    if key in CITY_SLUGS:
        return CITY_SLUGS[key]
    # Strip trailing state if present: "Rajkot, Gujarat"
    key = key.split(",")[0].strip()
    if key in CITY_SLUGS:
        return CITY_SLUGS[key]
    return slugify(key)


def product_slug(product: str) -> str:
    return slugify(product)


def _singularize_slug(slug: str) -> str | None:
    if not slug or "-" not in slug and not slug.endswith("s"):
        if slug.endswith("s") and len(slug) > 3 and not slug.endswith("ss"):
            return slug[:-1]
        return None
    parts = slug.split("-")
    last = parts[-1]
    if last.endswith("ies") and len(last) > 4:
        parts[-1] = last[:-3] + "y"
        return "-".join(parts)
    if last.endswith("s") and not last.endswith("ss") and len(last) > 3:
        parts[-1] = last[:-1]
        return "-".join(parts)
    return None


def product_slug_candidates(product: str) -> list[str]:
    """Ordered IndiaMART product slug guesses for a free-text product query."""
    key = re.sub(r"\s+", " ", (product or "").strip().lower())
    primary = product_slug(product)
    out: list[str] = []
    seen: set[str] = set()

    def add(value: str | None) -> None:
        if not value or value in seen:
            return
        seen.add(value)
        out.append(value)

    add(primary)
    for alias in PRODUCT_SLUG_ALIASES.get(key, []):
        add(alias)
    singular = _singularize_slug(primary)
    add(singular)
    if singular:
        # Also try alias-style singular of multi-word phrases already covered above.
        add(_singularize_slug(singular))
    return out


def build_search_url(product: str, city: str, *, product_slug_value: str | None = None) -> str:
    pslug = product_slug_value or product_slug(product)
    return f"https://dir.indiamart.com/{city_slug(city)}/{pslug}.html"


def _is_not_found_page(html: str) -> bool:
    title_match = re.search(r"<title>([^<]+)</title>", html or "", flags=re.I)
    title = _clean(title_match.group(1)) if title_match else ""
    if re.search(r"page not found", title, flags=re.I):
        return True
    return bool(re.search(r"\b404\b.*not found|page you(?:'|’)re looking for", html or "", flags=re.I))


async def resolve_listing_page(
    product: str,
    city: str,
    *,
    fetch_mode: FetchMode = "auto",
) -> tuple[str, str, str, str]:
    """Try product slug candidates until a real listing page is found.

    Returns (html, fetch_source, search_url, product_slug_used).
    """
    errors: list[str] = []
    for pslug in product_slug_candidates(product):
        url = build_search_url(product, city, product_slug_value=pslug)
        try:
            html, source = await fetch_listing_html(url, mode=fetch_mode)
        except Exception as exc:
            errors.append(f"{pslug}: {exc}")
            continue
        if _is_not_found_page(html):
            errors.append(f"{pslug}: 404")
            continue
        cards = parse_listing_cards(html, fallback_city=city)
        if cards:
            return html, source, url, pslug
        # Valid page but empty parse — still return it (better than inventing another slug).
        return html, source, url, pslug

    detail = "; ".join(errors[:6]) or "no slug candidates"
    raise RuntimeError(f"No IndiaMART listing page found for product={product!r} city={city!r} ({detail})")


def _is_blocked_html(html: str) -> bool:
    if not html or len(html) < 500:
        return True
    return bool(
        re.search(r"<title>\s*429\s*</title>", html, re.I)
        or re.search(r"Too Many Requests", html, re.I)
        or re.search(r"Access Denied", html, re.I)
    )


async def fetch_html_http(url: str, timeout_s: float = 30.0) -> str:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-IN,en;q=0.9",
        },
        timeout=timeout_s,
    ) as client:
        resp = await client.get(url)
        html = resp.text or ""
        if resp.status_code >= 400 or _is_blocked_html(html):
            raise RuntimeError(f"HTTP fetch failed status={resp.status_code} len={len(html)}")
        return html


def fetch_html_cloud(url: str, page_delay_ms: int = 2000) -> str:
    if not FETCH_HELPER.exists():
        raise FileNotFoundError(f"Missing fetch helper: {FETCH_HELPER}")
    with tempfile.TemporaryDirectory(prefix="im_live_") as tmp:
        out = Path(tmp) / "page.html"
        cmd = [
            "node",
            str(FETCH_HELPER),
            "--browser-mode",
            "cloud",
            "--url",
            url,
            "--out",
            str(out),
            "--page-delay-ms",
            str(page_delay_ms),
            "--proxy-country-code",
            "in",
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0 or not out.exists():
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"Cloud fetch failed: {err or 'unknown error'}")
        html = out.read_text(encoding="utf-8", errors="ignore")
        if _is_blocked_html(html):
            raise RuntimeError("Cloud fetch returned blocked/empty HTML")
        return html


async def fetch_listing_html(url: str, mode: FetchMode = "auto") -> tuple[str, str]:
    """Return (html, fetch_source)."""
    if mode == "http":
        return await fetch_html_http(url), "http"
    if mode == "cloud":
        return fetch_html_cloud(url), "cloud"

    try:
        return await fetch_html_http(url), "http"
    except Exception as exc:
        logger.info("HTTP fetch failed for %s (%s); falling back to cloud", url, exc)
        return fetch_html_cloud(url), "cloud_fallback"


def _clean(value: Any) -> str:
    text = unescape(str(value or ""))
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _phones(text: str) -> list[str]:
    matches = re.findall(r"(?:\+?91[-.\s]?)?[6-9]\d{4}[-.\s]?\d{5}", text or "")
    out: list[str] = []
    seen: set[str] = set()
    for match in matches:
        digits = re.sub(r"\D+", "", match)
        if len(digits) >= 10:
            key = digits[-10:]
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out


def _price(text: str) -> str | None:
    match = re.search(
        r"(?:₹|Rs\.?\s*)\s*([\d,]+(?:\.\d+)?(?:\s*/\s*[A-Za-z]+)?)",
        text or "",
        flags=re.I,
    )
    if not match:
        return None
    return _clean(match.group(0)).replace("Rs.", "₹")


def _rating(text: str) -> tuple[float | None, int | None]:
    match = re.search(r"\b([0-5](?:\.\d)?)\s*\(\s*(\d+)\s*\)", text or "")
    if not match:
        return None, None
    try:
        return float(match.group(1)), int(match.group(2))
    except ValueError:
        return None, None


def _indiamart_supplier_url(href: str) -> str | None:
    try:
        parsed = urlparse(href)
    except Exception:
        return None
    host = (parsed.hostname or "").lower()
    if host not in {"www.indiamart.com", "indiamart.com", "m.indiamart.com"}:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return None
    slug = parts[0].lower()
    if slug in {"proddetail", "company", "search", "city", "dir"}:
        return None
    if slug.endswith(".html"):
        return None
    return f"https://www.indiamart.com/{slug}/"


def _parse_template7_card(card_el, *, fallback_city: str | None = None) -> IndiaMartCard | None:
    product_a = card_el.select_one("a.template7-product-name[href], a.prdtitle[href]")
    product_url = None
    product_name = ""
    if product_a:
        product_url = urljoin("https://www.indiamart.com/", product_a.get("href") or "")
        product_name = _clean(product_a.get_text(" ", strip=True))
    if not product_name:
        heading = card_el.find(["h2", "h3"])
        product_name = _clean(heading.get_text(" ", strip=True) if heading else "")
    if not product_name and not product_url:
        return None

    seller_a = card_el.select_one("a.template7-seller-name[href]")
    supplier_name = _clean(seller_a.get_text(" ", strip=True)) if seller_a else ""
    supplier_url = None
    if seller_a and seller_a.get("href"):
        href = urljoin("https://www.indiamart.com/", seller_a["href"])
        supplier_url = _indiamart_supplier_url(href) or href

    addr_el = card_el.select_one("p.seller-addr")
    address = _clean(addr_el.get_text(" ", strip=True)) if addr_el else None
    city = fallback_city
    years_in_business = None
    if address:
        # e.g. "Rajkot · 15 yrs" or "Deals in Rajkot · 14 yrs" or "Vavdi, Rajkot · 8 yrs"
        yrs = re.search(r"(\d+\s*yrs?)", address, flags=re.I)
        if yrs:
            years_in_business = _clean(yrs.group(1))
        city_part = re.split(r"\s*·\s*", address)[0]
        city_part = re.sub(r"^(Deals in|Located in)\s+", "", city_part, flags=re.I)
        if "," in city_part:
            city = _clean(city_part.split(",")[-1])
        elif city_part:
            city = _clean(city_part)

    price_el = card_el.select_one("span.prc")
    unit_el = card_el.select_one("span.prcut")
    price = _clean(price_el.get_text(" ", strip=True)) if price_el else None
    price_unit = _clean(unit_el.get_text(" ", strip=True)) if unit_el else None
    if price and price_unit:
        price = f"{price} / {price_unit}"
    if not price:
        price = _price(_clean(card_el.get_text(" ", strip=True)))

    rating_el = card_el.select_one("div.dag5 span.b, div.dag5")
    rating = None
    rating_count = None
    if rating_el:
        rating, rating_count = _rating(_clean(rating_el.get_text(" ", strip=True)))
    if rating is None:
        rating, rating_count = _rating(_clean(card_el.get_text(" ", strip=True)))

    rr_el = card_el.select_one(".response-rate-text")
    response_rate = _clean(rr_el.get_text(" ", strip=True)) if rr_el else None

    badges: list[str] = []
    for sel in (
        ".Star-badge",
        ".trustseal-pill",
        ".verified-exporter-pill",
        ".product-badge",
        ".template7-trust-row span",
    ):
        for el in card_el.select(sel):
            label = _clean(el.get_text(" ", strip=True))
            if label and label not in badges:
                badges.append(label)

    attributes: dict[str, str] = {}
    for dt in card_el.select("dt.template7-isq-label"):
        key = _clean(dt.get_text(" ", strip=True))
        dd = dt.find_next_sibling("dd")
        if key and dd:
            attributes[key] = _clean(dd.get_text(" ", strip=True))

    img = card_el.select_one("img")
    image_url = None
    if img:
        image_url = img.get("src") or img.get("data-src") or img.get("data-original")
        if image_url:
            image_url = urljoin("https://dir.indiamart.com/", image_url)

    text = _clean(card_el.get_text(" ", strip=True))
    return IndiaMartCard(
        supplier_name=supplier_name or product_name,
        supplier_url=supplier_url,
        product_name=product_name or None,
        product_url=product_url if product_url and "/proddetail/" in product_url else None,
        price=price,
        price_unit=price_unit,
        city=city,
        address=address,
        years_in_business=years_in_business,
        rating=rating,
        rating_count=rating_count,
        response_rate=response_rate,
        badges=badges,
        attributes=attributes,
        image_url=image_url,
        phones=_phones(text),
        listing_text=text[:1200],
    )


def _parse_generic_card(
    card_el,
    *,
    product_anchor,
    fallback_city: str | None = None,
) -> IndiaMartCard | None:
    href = urljoin("https://dir.indiamart.com/", product_anchor.get("href") or "")
    text = _clean(card_el.get_text(" ", strip=True))
    if len(text) < 40:
        return None

    product_name = _clean(product_anchor.get_text(" ", strip=True) or product_anchor.get("title") or "")
    supplier_url = None
    supplier_name = ""
    for link in card_el.find_all("a", href=True):
        full = urljoin("https://www.indiamart.com/", link["href"])
        candidate = _indiamart_supplier_url(full)
        if not candidate:
            continue
        supplier_url = candidate
        label = _clean(link.get_text(" ", strip=True))
        if label and label.lower() not in {"call now", "contact supplier", "get best price"}:
            supplier_name = label
            break

    if not supplier_name and not product_name:
        return None

    price = _price(text)
    rating, rating_count = _rating(text)
    img = card_el.find("img")
    image_url = None
    if img:
        image_url = img.get("src") or img.get("data-src") or img.get("data-original")
        if image_url:
            image_url = urljoin("https://dir.indiamart.com/", image_url)

    city = fallback_city
    address = None
    city_match = re.search(
        r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s*,\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
        text,
    )
    if city_match:
        address = _clean(city_match.group(0))
        city = city_match.group(1)

    return IndiaMartCard(
        supplier_name=supplier_name or product_name,
        supplier_url=supplier_url,
        product_name=product_name or None,
        product_url=href if "/proddetail/" in href else None,
        price=price,
        price_unit=None,
        city=city,
        address=address,
        years_in_business=None,
        rating=rating,
        rating_count=rating_count,
        response_rate=None,
        badges=[],
        attributes={},
        image_url=image_url,
        phones=_phones(text),
        listing_text=text[:1200],
    )


def parse_listing_cards(html: str, *, fallback_city: str | None = None) -> list[IndiaMartCard]:
    soup = BeautifulSoup(html, "html.parser")
    cards: list[IndiaMartCard] = []
    seen: set[str] = set()

    template_cards = soup.select("article.template7-product-card")
    if template_cards:
        parsed_cards = [
            _parse_template7_card(el, fallback_city=fallback_city) for el in template_cards
        ]
    else:
        parsed_cards = []
        for anchor in soup.select('a[href*="/proddetail/"]'):
            card_el = anchor
            for _ in range(8):
                parent = card_el.parent
                if parent is None or parent.name in {"body", "html", "[document]"}:
                    break
                card_el = parent
                probe = _clean(card_el.get_text(" ", strip=True))
                if len(probe) > 80 and re.search(
                    r"contact supplier|call now|trustseal|response rate|₹|rs\.?",
                    probe,
                    flags=re.I,
                ):
                    break
            parsed_cards.append(
                _parse_generic_card(card_el, product_anchor=anchor, fallback_city=fallback_city)
            )

    for card in parsed_cards:
        if card is None:
            continue
        dedupe_key = "|".join(
            [
                (card.supplier_url or "").lower(),
                (card.product_url or card.product_name or "").lower(),
                (card.price or "").lower(),
            ]
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        cards.append(card)

    return cards


def extract_phones_from_html(html: str) -> list[str]:
    """Pull supplier phones from product/supplier page HTML."""
    out: list[str] = []
    seen: set[str] = set()

    def _add(raw: str | None) -> None:
        for phone in _phones(raw or ""):
            if phone not in seen:
                seen.add(phone)
                out.append(phone)

    for match in re.finditer(
        r'"(?:pnsNumber|sellerPns|mobile_no|glusr_usr_ph_mobile|contact_number)"\s*:\s*"([^"]+)"',
        html or "",
        flags=re.I,
    ):
        _add(match.group(1))

    for match in re.finditer(r'tel:([+\d\-\s]+)', html or "", flags=re.I):
        _add(match.group(1))

    # Fallback: visible body text (product pages sometimes expose PNS in plain text).
    if not out:
        text = BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)
        _add(text)

    return out


def _phone_lookup_urls(card: IndiaMartCard) -> list[str]:
    urls: list[str] = []
    if card.supplier_url:
        root = card.supplier_url.rstrip("/") + "/"
        urls.append(root)
        urls.append(urljoin(root, "aboutus.html"))
    if card.product_url:
        urls.append(card.product_url)
    # Prefer supplier page (stable pnsNumber) then product page.
    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        key = url.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(url)
    return deduped


async def _emit_card(
    callback: OnCardEnriched | None,
    card: IndiaMartCard,
) -> None:
    if not callback:
        return
    maybe = callback(card)
    if maybe is not None:
        await maybe


async def enrich_cards_with_phones(
    cards: list[IndiaMartCard],
    *,
    fetch_mode: FetchMode = "auto",
    phone_limit: int | None = None,
    on_card_enriched: OnCardEnriched | None = None,
    emit_only_with_phone: bool = True,
) -> dict[str, Any]:
    """Second-pass phone enrichment. Listing cards do not contain numbers.

    When ``on_card_enriched`` is set, each card is emitted as soon as its phone
    pass finishes (by default only if a phone was found).
    """
    limit = len(cards) if phone_limit is None else max(0, min(phone_limit, len(cards)))
    cache: dict[str, list[str]] = {}
    attempted = 0
    resolved = 0
    emitted = 0

    for card in cards[:limit]:
        cache_key = (card.supplier_url or card.product_url or card.supplier_name or "").lower()
        if not cache_key:
            continue
        if cache_key in cache:
            card.phones = list(cache[cache_key])
            if card.phones:
                resolved += 1
            if card.phones or not emit_only_with_phone:
                await _emit_card(on_card_enriched, card)
                emitted += 1
            continue

        phones: list[str] = []
        for url in _phone_lookup_urls(card):
            attempted += 1
            try:
                html, _src = await fetch_listing_html(url, mode=fetch_mode)
                phones = extract_phones_from_html(html)
            except Exception as exc:
                logger.info("Phone enrich failed for %s (%s)", url, exc)
                continue
            if phones:
                break

        cache[cache_key] = phones
        card.phones = list(phones)
        if phones:
            resolved += 1
        if phones or not emit_only_with_phone:
            await _emit_card(on_card_enriched, card)
            emitted += 1

    return {
        "include_phones": True,
        "phone_enrich_attempted": attempted,
        "phone_enrich_resolved": resolved,
        "phone_enrich_unique_suppliers": len(cache),
        "phone_enrich_emitted": emitted,
    }


async def search_indiamart_live(
    *,
    product: str,
    city: str,
    max_results: int = 30,
    fetch_mode: FetchMode = "auto",
    include_phones: bool = True,
    phone_limit: int | None = None,
    on_result: OnLiveResult | None = None,
    emit_only_with_phone: bool = True,
) -> dict[str, Any]:
    product = _clean(product)
    city = _clean(city)
    if not product or not city:
        raise ValueError("product and city are required")

    html, fetch_source, search_url, product_slug_used = await resolve_listing_page(
        product,
        city,
        fetch_mode=fetch_mode,
    )
    cards = parse_listing_cards(html, fallback_city=city)
    limited = cards[: max(1, min(max_results, 100))]

    async def _on_card(card: IndiaMartCard) -> None:
        if not on_result:
            return
        maybe = on_result(asdict(card))
        if maybe is not None:
            await maybe

    phone_meta: dict[str, Any] = {"include_phones": False}
    if include_phones:
        # Prefer cloud for phone pages; HTTP often omits/masks PNS.
        phone_mode: FetchMode = "cloud" if fetch_mode == "auto" else fetch_mode
        phone_meta = await enrich_cards_with_phones(
            limited,
            fetch_mode=phone_mode,
            phone_limit=phone_limit,
            on_card_enriched=_on_card if on_result else None,
            emit_only_with_phone=emit_only_with_phone,
        )
    elif on_result:
        # No phone pass — still allow streaming listing cards if a caller wants that.
        for card in limited:
            await _on_card(card)

    page_title = ""
    title_match = re.search(r"<title>([^<]+)</title>", html, flags=re.I)
    if title_match:
        page_title = _clean(title_match.group(1))

    return {
        "ok": True,
        "product": product,
        "city": city,
        "city_slug": city_slug(city),
        "product_slug": product_slug(product),
        "product_slug_used": product_slug_used,
        "product_slug_candidates": product_slug_candidates(product),
        "search_url": search_url,
        "page_title": page_title,
        "fetch_source": fetch_source,
        "html_bytes": len(html.encode("utf-8", errors="ignore")),
        "count": len(limited),
        "total_parsed": len(cards),
        **phone_meta,
        "results": [asdict(card) for card in limited],
    }


def save_results_json(payload: dict[str, Any], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
