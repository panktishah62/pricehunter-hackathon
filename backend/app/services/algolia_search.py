from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import asyncio
import logging
import re
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Algolia caps hitsPerPage at 1000; asking for more silently truncates.
MAX_HITS_PER_PAGE = 1000

TOKEN_RE = re.compile(r"[a-z0-9]+")


def is_enabled(*, write: bool = False) -> bool:
    if not settings.algolia_enabled or not settings.algolia_app_id:
        return False
    if write:
        return bool(settings.algolia_admin_api_key)
    return bool(settings.algolia_search_api_key or settings.algolia_admin_api_key)


def _api_key(*, write: bool = False) -> str:
    if write:
        return settings.algolia_admin_api_key
    return settings.algolia_search_api_key or settings.algolia_admin_api_key


def _headers(*, write: bool = False) -> dict[str, str]:
    return {
        "X-Algolia-Application-Id": settings.algolia_app_id,
        "X-Algolia-API-Key": _api_key(write=write),
    }


def _index_url(index_name: str, path: str = "") -> str:
    suffix = f"/{path.lstrip('/')}" if path else ""
    return f"https://{settings.algolia_app_id}.algolia.net/1/indexes/{index_name}{suffix}"


def _timestamp(value: Any) -> int | None:
    if isinstance(value, datetime):
        item = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(item.timestamp())
    if isinstance(value, str):
        try:
            item = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        item = item if item.tzinfo else item.replace(tzinfo=timezone.utc)
        return int(item.timestamp())
    return None


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        item = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return item.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _tokens(*values: Any) -> list[str]:
    seen: set[str] = set()
    tokens: list[str] = []
    for value in values:
        if isinstance(value, dict):
            iterable = value.values()
        elif isinstance(value, list):
            iterable = value
        else:
            iterable = [value]
        for item in iterable:
            for token in TOKEN_RE.findall(str(item or "").lower()):
                if len(token) <= 1 or token in seen:
                    continue
                seen.add(token)
                tokens.append(token)
    return tokens


def _first_service_city(offering: dict[str, Any]) -> str:
    for location in offering.get("service_locations") or []:
        if isinstance(location, dict) and location.get("city"):
            return _clean(location["city"])
        if isinstance(location, str) and location.strip():
            return _clean(location)
    return ""


def build_offering_record(
    offering: dict[str, Any],
    *,
    vendor: dict[str, Any] | None = None,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    vendor = vendor or {}
    snapshot = snapshot or {}
    product_name = offering.get("normalized_product_name") or offering.get("product_name_raw")
    city = _clean(vendor.get("city") or _first_service_city(offering))
    latest_price = snapshot.get("latest_price")
    best_recent_price = snapshot.get("best_recent_price")
    supply_confidence = float(offering.get("supply_confidence") or 0.0)
    evidence_score = float(offering.get("evidence_score") or 0.0)
    vendor_confidence = float(vendor.get("confidence_score") or 0.0)
    if vendor_confidence > 1:
        vendor_confidence = vendor_confidence / 100

    return {
        "objectID": offering.get("offering_id"),
        "record_type": "offering",
        "offering_id": offering.get("offering_id"),
        "vendor_id": offering.get("vendor_id"),
        "vendor_name": vendor.get("name") or vendor.get("company_name") or offering.get("vendor_name"),
        "product_id": offering.get("product_id"),
        "product_name": product_name,
        "product_name_raw": offering.get("product_name_raw"),
        "category_id": offering.get("category_id"),
        "subcategory_id": offering.get("subcategory_id"),
        "canonical_category_id": offering.get("canonical_category_id"),
        "canonical_subcategory_id": offering.get("canonical_subcategory_id"),
        "taxonomy_node_id": offering.get("taxonomy_node_id"),
        "taxonomy_path": offering.get("taxonomy_path") or [],
        "taxonomy_path_labels": offering.get("taxonomy_path_labels") or [],
        "taxonomy_root_id": offering.get("taxonomy_root_id"),
        "needs_taxonomy_review": bool(offering.get("needs_taxonomy_review")),
        "city": city,
        "service_city": city,
        "source": offering.get("source"),
        "source_url": offering.get("source_url"),
        "attributes": _json_safe(offering.get("attributes") or {}),
        "tokens": _tokens(
            product_name,
            offering.get("product_name_raw"),
            offering.get("normalized_tokens") or [],
            vendor.get("name") or vendor.get("company_name"),
            city,
            offering.get("attributes") or {},
            offering.get("taxonomy_path_labels") or [],
        ),
        "has_price": isinstance(latest_price, (int, float)) or isinstance(best_recent_price, (int, float)),
        "latest_price": latest_price if isinstance(latest_price, (int, float)) else None,
        "best_recent_price": best_recent_price if isinstance(best_recent_price, (int, float)) else None,
        "currency": snapshot.get("currency") or "INR",
        "unit": snapshot.get("unit"),
        "availability": snapshot.get("availability", True),
        "last_observed_at": _timestamp(snapshot.get("last_observed_at")),
        "last_seen_at": _timestamp(offering.get("last_seen_at")),
        "price_confidence": float(snapshot.get("confidence") or 0.0),
        "supply_confidence": supply_confidence,
        "evidence_score": evidence_score,
        "vendor_confidence": vendor_confidence,
        "rank_score": round((supply_confidence * 0.45) + (evidence_score * 0.25) + (vendor_confidence * 0.30), 6),
    }


def build_capability_record(capability: dict[str, Any], *, vendor: dict[str, Any] | None = None) -> dict[str, Any]:
    vendor = vendor or {}
    vendor_name = capability.get("vendor_name") or vendor.get("name") or vendor.get("company_name")
    city = _clean(capability.get("city") or vendor.get("city"))
    confidence = float(capability.get("capability_confidence") or 0.0)
    routing_confidence = float(capability.get("routing_confidence") or confidence)
    vendor_quality_bucket = capability.get("vendor_quality_bucket") or "unscored"
    quality_bucket_score = {
        "focused": 1.0,
        "broad_but_usable": 0.6,
        "needs_manual_review": 0.15,
        "unscored": 0.5,
    }.get(str(vendor_quality_bucket), 0.5)
    offering_count = int(capability.get("offering_count") or 0)
    priced_count = int(capability.get("priced_offering_count") or 0)
    coverage_score = min(max(float(offering_count), 0.0) / 100.0, 1.0)
    object_id = capability.get("capability_id") or f"capability:{capability.get('vendor_id')}:{capability.get('taxonomy_node_id')}"

    return {
        "objectID": object_id,
        "record_type": "capability",
        "vendor_id": capability.get("vendor_id"),
        "vendor_name": vendor_name,
        "taxonomy_node_id": capability.get("taxonomy_node_id"),
        "taxonomy_path": capability.get("taxonomy_path") or [],
        "taxonomy_path_labels": capability.get("taxonomy_path_labels") or [],
        "canonical_category_id": capability.get("canonical_category_id"),
        "canonical_subcategory_id": capability.get("canonical_subcategory_id"),
        "capability_type": capability.get("capability_type"),
        "capability_sources": capability.get("capability_sources") or [],
        "city": city,
        "service_city": city,
        "phone": capability.get("phone") or vendor.get("phone") or vendor.get("phone_primary"),
        "website": capability.get("website") or vendor.get("website"),
        "sample_products": capability.get("sample_products") or [],
        "service_locations": capability.get("service_locations") or [],
        "offering_count": offering_count,
        "priced_offering_count": priced_count,
        "capability_confidence": confidence,
        "routing_confidence": routing_confidence,
        "quality_status": capability.get("quality_status") or "unscored",
        "vendor_quality_bucket": vendor_quality_bucket,
        "quality_bucket_score": quality_bucket_score,
        "quality_flags": capability.get("quality_flags") or [],
        "last_seen_at": _timestamp(capability.get("updated_at") or capability.get("last_seen_at")),
        "tokens": _tokens(
            vendor_name,
            city,
            capability.get("taxonomy_node_id"),
            capability.get("taxonomy_path") or [],
            capability.get("taxonomy_path_labels") or [],
            capability.get("sample_products") or [],
        ),
        "rank_score": round(
            (routing_confidence * 0.50)
            + (quality_bucket_score * 0.25)
            + (coverage_score * 0.20)
            + (min(priced_count / 25, 1.0) * 0.05),
            6,
        ),
    }


def _filters(*, taxonomy_node_id: str | None, canonical_category_id: str | None, city: str | None) -> str:
    filters: list[str] = []
    if taxonomy_node_id:
        filters.append(f'taxonomy_path:"{taxonomy_node_id}"')
    elif canonical_category_id:
        filters.append(f'canonical_category_id:"{canonical_category_id}"')
    if city and city != "unknown":
        # City is intentionally not strict-filtered yet. We rank by city in Mongo
        # after retrieval so pan-India vendors remain available.
        pass
    return " AND ".join(filters)


async def _request_json(
    method: str,
    index_name: str,
    path: str,
    payload: dict[str, Any],
    *,
    write: bool,
) -> dict[str, Any]:
    if not is_enabled(write=write):
        return {}
    async with httpx.AsyncClient(timeout=settings.algolia_request_timeout_seconds) as client:
        response = await client.request(
            method,
            _index_url(index_name, path),
            headers=_headers(write=write),
            json=payload,
        )
        response.raise_for_status()
        return response.json()


async def save_records(index_name: str, records: list[dict[str, Any]], *, action: str = "addObject") -> None:
    if not records or not is_enabled(write=True):
        return
    requests = [{"action": action, "body": record} for record in records if record.get("objectID")]
    if not requests:
        return
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            await _request_json("POST", index_name, "batch", {"requests": requests}, write=True)
            return
        except (httpx.TimeoutException, httpx.TransportError):
            if attempt >= attempts:
                raise
            await asyncio.sleep(1.5 * attempt)


async def search_offering_ids(
    *,
    query_text: str,
    taxonomy_node_id: str | None = None,
    canonical_category_id: str | None = None,
    city: str | None = None,
    limit: int = 200,
) -> list[str]:
    if not is_enabled():
        return []
    payload = {
        "query": query_text,
        "hitsPerPage": limit,
        "attributesToRetrieve": ["offering_id"],
        "filters": _filters(
            taxonomy_node_id=taxonomy_node_id,
            canonical_category_id=canonical_category_id,
            city=city,
        ),
    }
    try:
        response = await _request_json("POST", settings.algolia_offerings_index, "query", payload, write=False)
    except Exception as exc:  # pragma: no cover - external service
        logger.warning("Algolia offering search skipped: %s", exc)
        return []
    return [
        str(hit.get("offering_id") or hit.get("objectID"))
        for hit in response.get("hits") or []
        if hit.get("offering_id") or hit.get("objectID")
    ]


@dataclass
class FacetedSearchResult:
    """Full Algolia response slice: ordered hits plus facet distribution."""

    hits: list[dict[str, Any]] = field(default_factory=list)
    nb_hits: int = 0
    facets: dict[str, dict[str, int]] = field(default_factory=dict)


async def search_offerings_faceted(
    *,
    query_text: str,
    city: str | None = None,
    taxonomy_node_id: str | None = None,
    limit: int = MAX_HITS_PER_PAGE,
    facet_attributes: tuple[str, ...] = ("taxonomy_node_id",),
    attributes_to_retrieve: tuple[str, ...] = ("offering_id", "vendor_id"),
    optional_words: tuple[str, ...] = (),
) -> FacetedSearchResult:
    """Algolia-first offering search.

    Sends the user's raw query text and trusts Algolia's ranking:
    - ``city`` is an optionalFilters boost (local vendors rank up, pan-India
      vendors stay in the pool).
    - ``taxonomy_node_id`` is a hard facetFilter, only set after the user
      answered the disambiguation question.
    - ``facet_attributes`` returns facet counts used to detect ambiguity and
      build the "what type of X?" options.
    - ``optional_words``: descriptor words from the query ("fancy", "premium")
      that boost matching records but are not required to match, so "fancy
      travel bags" stays a superset-ranked view of "travel bags".
    """
    if not is_enabled():
        return FacetedSearchResult()
    payload: dict[str, Any] = {
        "query": query_text,
        "hitsPerPage": max(0, min(limit, MAX_HITS_PER_PAGE)),
        "attributesToRetrieve": list(attributes_to_retrieve),
        "facets": list(facet_attributes),
        # `tokens` and `attributes` are polluted by page-level scrape context
        # (sibling product names, page titles like attributes.category_name,
        # URL fragments), which makes unrelated records "match". Category
        # labels (`taxonomy_path_labels`) are excluded too: every record in a
        # category matches its label words ("Travel Bags", "Audio & Speakers"),
        # which lets noise words hijack the query ("fancy travel bags" ->
        # purses with "fancy" in the name). Search only the record's own
        # name fields until records are cleaned at sync time.
        "restrictSearchableAttributes": [
            "product_name",
            "product_name_raw",
            "vendor_name",
        ],
        # Index default is allOptional, which silently degrades zero-result
        # queries into single-word matches ("sultamycin tablets" -> every
        # product containing "tablets"). Require all words: an honest zero lets
        # the caller fall back to the legacy path (spelling variants) and
        # external discovery instead of returning confident junk.
        "removeWordsIfNoResults": "none",
    }
    if optional_words:
        payload["optionalWords"] = [word for word in optional_words if word]
    if taxonomy_node_id:
        payload["facetFilters"] = [f"taxonomy_node_id:{taxonomy_node_id}"]
    if city and city.strip().lower() != "unknown":
        payload["optionalFilters"] = [f"city:{_clean(city)}"]
    try:
        response = await _request_json("POST", settings.algolia_offerings_index, "query", payload, write=False)
    except Exception as exc:  # pragma: no cover - external service
        logger.warning("Algolia faceted offering search skipped: %s", exc)
        return FacetedSearchResult()
    facets: dict[str, dict[str, int]] = {}
    for attribute, counts in (response.get("facets") or {}).items():
        if isinstance(counts, dict):
            facets[attribute] = {str(value): int(count) for value, count in counts.items()}
    return FacetedSearchResult(
        hits=[hit for hit in response.get("hits") or [] if isinstance(hit, dict)],
        nb_hits=int(response.get("nbHits") or 0),
        facets=facets,
    )


async def search_capability_vendor_ids(
    *,
    query_text: str,
    taxonomy_node_id: str | None = None,
    canonical_category_id: str | None = None,
    city: str | None = None,
    limit: int = 200,
) -> list[str]:
    if not is_enabled():
        return []
    payload = {
        "query": query_text,
        "hitsPerPage": limit,
        "attributesToRetrieve": ["vendor_id"],
        # See search_offerings_faceted: never degrade a zero-result query into
        # generic single-word matches (this is how a "sultamycin tablets"
        # search shortlisted computer shops for phone calls).
        "removeWordsIfNoResults": "none",
        "filters": _filters(
            taxonomy_node_id=taxonomy_node_id,
            canonical_category_id=canonical_category_id,
            city=city,
        ),
    }
    try:
        response = await _request_json("POST", settings.algolia_capabilities_index, "query", payload, write=False)
    except Exception as exc:  # pragma: no cover - external service
        logger.warning("Algolia capability search skipped: %s", exc)
        return []
    seen: set[str] = set()
    vendor_ids: list[str] = []
    for hit in response.get("hits") or []:
        vendor_id = hit.get("vendor_id")
        if vendor_id and vendor_id not in seen:
            seen.add(str(vendor_id))
            vendor_ids.append(str(vendor_id))
    return vendor_ids


async def configure_default_settings() -> None:
    if not is_enabled(write=True):
        return
    offering_settings = {
        "searchableAttributes": [
            "unordered(product_name)",
            "unordered(product_name_raw)",
            "unordered(tokens)",
            "unordered(vendor_name)",
            "unordered(attributes)",
            "unordered(taxonomy_path_labels)",
        ],
        "attributesForFaceting": [
            "filterOnly(record_type)",
            "filterOnly(canonical_category_id)",
            "filterOnly(canonical_subcategory_id)",
            # Real facets (not filterOnly) so queries can retrieve facet counts
            # for the chat disambiguation step ("what type of camera?").
            "taxonomy_node_id",
            "filterOnly(taxonomy_path)",
            "city",
            "filterOnly(service_city)",
            "filterOnly(has_price)",
            "filterOnly(availability)",
            "filterOnly(needs_taxonomy_review)",
        ],
        "customRanking": ["desc(has_price)", "desc(rank_score)", "desc(last_observed_at)", "desc(last_seen_at)"],
        "typoTolerance": True,
        # "bag" and "bags" are the same word to buyers; don't spend typo
        # tolerance (or lose exactness) on plural variance.
        "ignorePlurals": ["en"],
        "removeWordsIfNoResults": "allOptional",
    }
    capability_settings = {
        "searchableAttributes": [
            "unordered(vendor_name)",
            "unordered(tokens)",
            "unordered(sample_products)",
            "unordered(taxonomy_path_labels)",
            "unordered(city)",
        ],
        "attributesForFaceting": [
            "filterOnly(record_type)",
            "filterOnly(canonical_category_id)",
            "filterOnly(canonical_subcategory_id)",
            "filterOnly(taxonomy_node_id)",
            "filterOnly(taxonomy_path)",
            "filterOnly(city)",
            "filterOnly(service_city)",
            "filterOnly(capability_type)",
            "filterOnly(quality_status)",
            "filterOnly(vendor_quality_bucket)",
        ],
        "customRanking": [
            "desc(quality_bucket_score)",
            "desc(rank_score)",
            "desc(routing_confidence)",
            "desc(offering_count)",
            "desc(priced_offering_count)",
            "desc(last_seen_at)",
        ],
        "typoTolerance": True,
        "removeWordsIfNoResults": "allOptional",
    }
    await _request_json("PUT", settings.algolia_offerings_index, "settings", offering_settings, write=True)
    await _request_json("PUT", settings.algolia_capabilities_index, "settings", capability_settings, write=True)
