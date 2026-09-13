from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)

IBJA_ALL_JEWELLERS_URL = "https://ibjaverified.com/all-jewellers/"
_CITY_ALIASES = {
    "mumbai": "Mumbai",
    "delhi": "Delhi",
    "bangalore": "Bangalore",
    "bengaluru": "Bangalore",
    "bengalore": "Bangalore",
    "ahmedabad": "Ahmedabad",
    "chennai": "Chennai",
    "hyderabad": "Hyderabad",
    "agra": "Agra",
    "jaipur": "Jaipur",
    "jodhpur": "Jodhpur",
    "ludhiana": "Ludhiana",
    "kolkata": "Kolkata",
    "surat": "Surat",
    "rajkot": "Rajkot",
    "coimbatore": "Coimbatore",
    "indore": "Indore",
    "pune": "Pune",
}


@dataclass(frozen=True)
class IbjaVendorRecord:
    name: str
    address: str
    city: str
    phone_number: str
    specialisation: str
    listing_url: str
    certificate_url: str | None = None

    def to_flat_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "address": self.address,
            "city": self.city,
            "phone_number": self.phone_number,
            "specialisation": self.specialisation,
            "listing_url": self.listing_url,
            "certificate_url": self.certificate_url,
            "source_name": "ibja_verified",
        }


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _normalize_phone(phone_number: str) -> str:
    cleaned = _clean_text(phone_number)
    cleaned = cleaned.replace("tel:", "")
    digits = re.sub(r"[^\d+]", "", cleaned)
    return digits or cleaned


def _extract_city(address: str) -> str:
    normalized = address.lower()
    for alias, canonical in _CITY_ALIASES.items():
        if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", normalized):
            return canonical
    match = re.search(r",\s*([A-Za-z][A-Za-z\s.-]+?)(?:\s*[-,]?\s*\d{6}\b|\s*$)", address)
    if match:
        return _clean_text(match.group(1))
    return "unknown"


def _extract_total_pages(soup: BeautifulSoup) -> int:
    page_numbers: set[int] = {1}
    for anchor in soup.select("a[href*='/all-jewellers/page/']"):
        href = anchor.get("href") or ""
        match = re.search(r"/all-jewellers/page/(\d+)/", href)
        if match:
            page_numbers.add(int(match.group(1)))
    return max(page_numbers)


def _parse_vendor_article(article: Any) -> IbjaVendorRecord | None:
    listing_anchor = article.select_one("a[href*='/jeweller/']")
    if listing_anchor is None:
        return None

    title = article.select_one("h1")
    description = article.select_one(".business-description")
    address_node = article.select_one(".contact-detailsd")
    phone_anchor = article.select_one("a[href^='tel:']")
    certificate_anchor = article.select_one("a[href*='verified-certificate-public']")

    name = _clean_text(title.get_text(" ", strip=True) if title else "")
    address = _clean_text(address_node.get_text(" ", strip=True) if address_node else "")
    address = re.sub(r"^location_on\s*", "", address, flags=re.IGNORECASE)
    specialisation = _clean_text(description.get_text(" ", strip=True) if description else "")
    phone_raw = phone_anchor.get("href", "") if phone_anchor else ""
    phone_number = _normalize_phone(phone_raw)
    listing_url = urljoin(IBJA_ALL_JEWELLERS_URL, listing_anchor.get("href") or "")
    certificate_url = None
    if certificate_anchor is not None:
        certificate_url = urljoin(IBJA_ALL_JEWELLERS_URL, certificate_anchor.get("href") or "")

    if not name or not address or not phone_number or not re.search(r"\d", phone_number):
        return None

    return IbjaVendorRecord(
        name=name,
        address=address,
        city=_extract_city(address),
        phone_number=phone_number,
        specialisation=specialisation,
        listing_url=listing_url,
        certificate_url=certificate_url,
    )


async def _fetch_page(client: httpx.AsyncClient, page_number: int) -> str:
    url = IBJA_ALL_JEWELLERS_URL if page_number == 1 else urljoin(IBJA_ALL_JEWELLERS_URL, f"page/{page_number}/")
    response = await client.get(url)
    response.raise_for_status()
    return response.text


async def scrape_ibja_verified_vendors() -> list[dict[str, Any]]:
    timeout = httpx.Timeout(30.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        first_page_html = await _fetch_page(client, 1)
        first_page_soup = BeautifulSoup(first_page_html, "html.parser")
        total_pages = _extract_total_pages(first_page_soup)
        logger.info("IBJA verified scraper discovered %s page(s)", total_pages)

        page_html = {1: first_page_html}
        for page_number in range(2, total_pages + 1):
            page_html[page_number] = await _fetch_page(client, page_number)

    vendors_by_phone: dict[str, dict[str, Any]] = {}
    for page_number in range(1, total_pages + 1):
        soup = BeautifulSoup(page_html[page_number], "html.parser")
        articles = soup.select("article.jeweller")
        logger.info("IBJA verified page %s yielded %s article(s)", page_number, len(articles))
        for article in articles:
            record = _parse_vendor_article(article)
            if record is None:
                continue
            vendors_by_phone[record.phone_number] = record.to_flat_dict()

    vendors = list(vendors_by_phone.values())
    logger.info("IBJA verified scraper parsed %s unique vendor(s)", len(vendors))
    return vendors
