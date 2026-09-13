from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.database import (
    catalog_categories_collection,
    catalog_products_collection,
    current_price_snapshots_collection,
    price_observations_collection,
    supplier_vendors_collection,
    catalog_vendor_interactions_collection,
    vendor_capabilities_collection,
    vendor_offerings_collection,
)
from app.config import settings
from app.models.schemas import StructuredQuery, UnifiedResult, VendorInfo
from app.services import algolia_search, product_relevance, query_normalization, search_specs


TOKEN_RE = re.compile(r"[a-z0-9]+")
DEFAULT_RESULT_LIMIT = 12
DEFAULT_OFFERING_SCAN_LIMIT = 2000
DEFAULT_ALGOLIA_CANDIDATE_LIMIT = 2000
DEFAULT_SUPPLIER_RESULT_LIMIT = 500
LEGACY_CATEGORY_TO_CANONICAL_ROOT = {
    "medicine": "pharma",
    "medical": "pharma",
    "drug": "pharma",
    "drugs": "pharma",
    "pharma": "pharma",
    "pharmaceutical": "pharma",
    "pharmaceuticals": "pharma",
    "electronics": "electronics",
    "electronic": "electronics",
    "industrial": "industrial",
    "industry": "industrial",
    "medical_device": "medical_devices",
    "medical_devices": "medical_devices",
    "medical devices": "medical_devices",
    "hospital_equipment": "medical_devices",
    "hospital equipment": "medical_devices",
    "gold": "gold",
    "bullion": "gold",
}
CANONICAL_ROOT_CATEGORIES = {
    "agriculture",
    "automotive",
    "bags_luggage",
    "beauty_personal_care",
    "cleaning_supplies",
    "decor_collectibles",
    "electronics",
    "food_beverages",
    "furniture",
    "gifts",
    "gold",
    "home_kitchen",
    "industrial",
    "media_entertainment",
    "medical_devices",
    "office_supplies",
    "packaging",
    "pet_supplies",
    "pharma",
    "services",
    "sports_fitness",
    "textiles",
    "toys",
}

TOKEN_SUBCATEGORY_HINTS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("pharma", ("tablet", "tablets", "tab"), "pharma_tablets"),
    ("pharma", ("capsule", "capsules", "cap"), "pharma_capsules"),
    ("pharma", ("syrup", "suspension"), "pharma_syrups"),
    ("pharma", ("injection", "injectable", "vial", "ampoule"), "pharma_injections"),
    ("pharma", ("drop", "drops", "spray", "ophthalmic"), "pharma_drops_sprays"),
    ("electronics", ("airpods", "earbuds", "earphone", "earphones", "headphone", "speaker", "speakers", "audio"), "electronics_audio"),
    ("electronics", ("mobile", "phone", "smartphone"), "electronics_mobile_devices"),
    ("electronics", ("charger", "cover", "case", "mobilecover"), "electronics_mobile_accessories"),
    ("electronics", ("laptop", "desktop", "motherboard", "server"), "electronics_computers"),
    ("electronics", ("cctv", "camera", "surveillance", "security"), "electronics_cctv_security"),
    ("medical_devices", ("laryngoscope", "surgical", "catheter"), "medical_devices_surgical"),
    ("industrial", ("plc", "controller", "automation"), "industrial_automation"),
    ("industrial", ("valve", "pipe", "fitting"), "industrial_pipes_valves"),
    ("industrial", ("generator", "genset"), "industrial_generators"),
    ("gold", ("bullion", "999", "995", "24k", "bis"), "gold_bullion"),
)

CATEGORY_TOKEN_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("medical_devices", ("laryngoscope", "catheter", "surgical", "gloves", "glove", "syringe", "scanner", "diagnostic")),
    ("pharma", ("tablet", "tablets", "capsule", "capsules", "syrup", "injection", "medicine", "drug", "pharma")),
    ("electronics", ("bluetooth", "speaker", "speakers", "cctv", "camera", "charger", "laptop", "mobile", "earbuds")),
    ("gold", ("gold", "bullion", "999", "995", "24k", "bis")),
)

QUERY_NOISE_TOKENS = {
    "tablet",
    "tablets",
    "tab",
    "capsule",
    "capsules",
    "cap",
    "syrup",
    "injection",
    "injectable",
    "drops",
    "drop",
    "spray",
    "set",
    "gen",
    "mg",
    "mcg",
    "gm",
    "gram",
    "grams",
    "kg",
    "ml",
    "ltr",
    "piece",
    "pieces",
    "pcs",
    "bullion",
}

TOKEN_EQUIVALENTS = {
    # IndiaMART/vendor copy frequently uses the molecule name while buyers use
    # the brand/common spelling. Keep this token-level so strict filtering still
    # blocks unrelated "tablet" products.
    "sultamycin": {"sultamycin", "sultamicillin"},
    "sultamicillin": {"sultamicillin", "sultamycin"},
    "tosylate": {"tosylate", "tosilate", "tocillate"},
    "tosilate": {"tosilate", "tosylate", "tocillate"},
    "tocillate": {"tocillate", "tosylate", "tosilate"},
}


@dataclass
class ProductIntent:
    category_id: str
    subcategory_id: str | None
    product_id: str | None
    product_key: str | None
    canonical_name: str
    raw_query: str
    normalized_tokens: list[str]
    attributes: dict[str, str] = field(default_factory=dict)
    missing_attributes: list[str] = field(default_factory=list)
    follow_up_questions: list[str] = field(default_factory=list)
    category_doc: dict[str, Any] | None = None
    product_doc: dict[str, Any] | None = None


@dataclass
class VendorOfferingCandidate:
    offering: dict[str, Any]
    vendor: dict[str, Any] | None
    price_snapshot: dict[str, Any] | None
    score: float
    score_parts: dict[str, float]


@dataclass
class VendorCapabilityCandidate:
    capability: dict[str, Any]
    vendor: dict[str, Any] | None
    score: float
    score_parts: dict[str, float]


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        return value
    return None


def _snapshot_price(snapshot: dict[str, Any]) -> float | None:
    price = snapshot.get("latest_price")
    if not isinstance(price, (int, float)):
        price = snapshot.get("best_recent_price")
    return float(price) if isinstance(price, (int, float)) else None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


def _is_gold_live_offering(offering: dict[str, Any]) -> bool:
    offering_id = str(offering.get("offering_id") or "")
    return offering_id.startswith("gold_live:") or offering.get("source") == "gold_live_rate_snapshot"


def _snapshot_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _gold_live_snapshot_is_usable(snapshot: dict[str, Any] | None) -> bool:
    if not snapshot or snapshot.get("source_type") != "live_api":
        return False
    return _snapshot_price(snapshot) is not None or any(
        _as_float(snapshot.get(key)) is not None
        for key in ("sell_rate", "buy_rate")
    )


def _offering_display_price(offering: dict[str, Any], snapshot: dict[str, Any]) -> float | None:
    if _is_gold_live_offering(offering):
        if not _gold_live_snapshot_is_usable(snapshot):
            return None
        attrs = offering.get("attributes") or {}
        # Gold live-rate cards should show the dealer website sell rate. Generic
        # call prices are still stored as observations, but should not replace
        # the live board price shown to buyers.
        for value in (
            snapshot.get("sell_rate"),
            attrs.get("sell_rate"),
            snapshot.get("buy_rate"),
            attrs.get("buy_rate"),
        ):
            price = _as_float(value)
            if price is not None and price > 0:
                return price
        if snapshot.get("source_type") == "live_api":
            return _snapshot_price(snapshot)
        return None
    return _snapshot_price(snapshot)


def _gold_result_score(query: StructuredQuery, offering: dict[str, Any], vendor: dict[str, Any], snapshot: dict[str, Any]) -> float:
    product_text = " ".join(
        str(value or "")
        for value in [
            offering.get("product_name_raw"),
            offering.get("normalized_product_name"),
            " ".join(offering.get("normalized_tokens") or []),
            offering.get("attributes") or {},
        ]
    )
    query_tokens = normalize_tokens(" ".join(str(value or "") for value in [query.raw_query, query.product, query.category]))
    token_score = _token_match_score(query_tokens, product_text)
    location_text = normalize_text(" ".join(str(value or "") for value in [vendor.get("city"), vendor.get("address"), offering.get("service_locations")]))
    query_city = normalize_text(query.location if query.location != "unknown" else "")
    location_boost = 0.35 if query_city and query_city in location_text else 0.0
    observed_at = _snapshot_datetime(snapshot.get("last_observed_at")) or _snapshot_datetime(snapshot.get("rate_timestamp"))
    recency_boost = 0.0
    if observed_at:
        age_hours = max((datetime.now(timezone.utc) - observed_at).total_seconds() / 3600, 0)
        recency_boost = max(0.0, 0.15 - min(age_hours, 72) / 72 * 0.15)
    return min(0.99, 0.55 + (token_score * 0.3) + location_boost + recency_boost)


def _offering_product_name(offering: dict[str, Any], fallback: str | None = None) -> str:
    return str(
        _first_non_empty(
            offering.get("product_name_raw"),
            offering.get("normalized_product_name"),
            offering.get("canonical_product_name"),
            fallback,
            "Product offering",
        )
    )


def _offering_image_url(offering: dict[str, Any]) -> str | None:
    attrs = offering.get("attributes") or {}
    images = offering.get("images") or []
    return _first_non_empty(
        offering.get("image_url"),
        attrs.get("product_image"),
        attrs.get("image_url"),
        images[0] if images else None,
    )


def _supplier_profile(vendor: dict[str, Any]) -> dict[str, Any]:
    profiles = vendor.get("source_profiles") or []
    return profiles[0] if profiles and isinstance(profiles[0], dict) else {}


def _supplier_verification(vendor: dict[str, Any]) -> dict[str, Any]:
    profile = _supplier_profile(vendor)
    return (
        vendor.get("verification")
        or profile.get("verification")
        or {}
    )


def _supplier_business_details(vendor: dict[str, Any]) -> dict[str, Any]:
    profile = _supplier_profile(vendor)
    return profile.get("business_details") or {}


def _supplier_description(vendor: dict[str, Any]) -> str | None:
    profile = _supplier_profile(vendor)
    about_us = profile.get("about_us") or {}
    why_us = about_us.get("why_us") or {}
    identity = profile.get("vendor_identity") or {}
    return _first_non_empty(
        why_us.get("description"),
        identity.get("description"),
    )


def _is_internal_spec_key(key: str) -> bool:
    compact = re.sub(r"[^a-z0-9]+", "", str(key or "").lower())
    if not compact:
        return True
    if "indiamart" in compact:
        return True
    if compact in {"id", "mcat", "catid", "mcatid", "source", "sourceurl", "url", "link", "unit", "units"}:
        return True
    if compact.endswith("id") and any(token in compact for token in ("cat", "mcat", "listing", "product", "supplier", "vendor")):
        return True
    return False


def _offering_specs(offering: dict[str, Any], vendor: dict[str, Any], snapshot: dict[str, Any]) -> list[dict[str, str]]:
    attrs = offering.get("attributes") or {}
    skip_keys = {
        "product_image",
        "image_url",
        "product_url",
        "url",
        "source_url",
        "description",
        "category_name",
        "unit",
        "units",
    }
    specs: list[dict[str, str]] = []
    for key, value in attrs.items():
        if key in skip_keys or value in (None, "", [], {}) or _is_internal_spec_key(str(key)):
            continue
        label = str(key).replace("_", " ").strip().title()
        if _is_internal_spec_key(label):
            continue
        specs.append({"label": label, "value": str(value)})
        if len(specs) >= 10:
            break

    if offering.get("moq") and not any(item["label"].lower() == "moq" for item in specs):
        specs.append({"label": "MOQ", "value": str(offering["moq"])})
    if snapshot.get("availability") is not None:
        specs.append({"label": "Availability", "value": "In Stock" if snapshot.get("availability", True) else "Unavailable"})
    business_types = vendor.get("business_types") or []
    if business_types:
        specs.append({"label": "Supplier Type", "value": ", ".join(str(item) for item in business_types[:3])})
    return specs[:12]


def _offering_preview_payload(
    offering: dict[str, Any],
    vendor: dict[str, Any],
    snapshot: dict[str, Any],
    product_name: str,
    price: float | None,
    currency: str,
) -> dict[str, Any]:
    verification = _supplier_verification(vendor)
    business_details = _supplier_business_details(vendor)
    trust_badges: list[str] = []
    if verification.get("is_verified_supplier"):
        trust_badges.append("Verified supplier")
    if vendor.get("gst") or verification.get("gst_masked"):
        trust_badges.append("GST verified")
    if verification.get("trustseal_years"):
        trust_badges.append(f"TrustSEAL {verification['trustseal_years']} yrs")
    legal_status = business_details.get("legal_status")
    if legal_status:
        trust_badges.append(str(legal_status))

    city = vendor.get("city") or _first_service_city(offering)
    attrs = offering.get("attributes") or {}
    description = _first_non_empty(
        offering.get("description"),
        (offering.get("attributes") or {}).get("description"),
        _supplier_description(vendor),
        f"{product_name} supplied by {vendor.get('name') or offering.get('vendor_name') or 'this supplier'}"
        + (f" in {city}" if city else ""),
    )

    return {
        "offering_id": offering.get("offering_id"),
        "product_id": offering.get("product_id"),
        "title": product_name,
        "image_url": _offering_image_url(offering),
        "price": price,
        "currency": currency,
        "unit": _first_non_empty(attrs.get("unit"), attrs.get("units"), attrs.get("price_unit")),
        "available": "In Stock" if snapshot.get("availability", True) else "Unavailable",
        "description": description,
        "specs": _offering_specs(offering, vendor, snapshot),
        "supplier_name": vendor.get("name") or offering.get("vendor_name"),
        "supplier_phone": vendor.get("phone") or vendor.get("phone_primary"),
        "supplier_address": vendor.get("address"),
        "supplier_city": city,
        "supplier_rating": verification.get("rating"),
        "supplier_rating_count": verification.get("rating_count"),
        "response_rate": verification.get("response_rate_percent"),
        "trust_badges": trust_badges[:4],
    }


@dataclass
class ProductVendorRoute:
    query: StructuredQuery
    intent: ProductIntent
    candidates: list[VendorOfferingCandidate] = field(default_factory=list)
    capability_candidates: list[VendorCapabilityCandidate] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.intent.missing_attributes

    def to_results(self, *, limit: int = DEFAULT_RESULT_LIMIT) -> list[UnifiedResult]:
        results: list[UnifiedResult] = []
        seen_vendor_ids: set[str] = set()
        for candidate in self.candidates[:limit]:
            offering = candidate.offering
            vendor = candidate.vendor or {}
            snapshot = candidate.price_snapshot or {}
            price = _offering_display_price(offering, snapshot)

            vendor_name = vendor.get("name") or offering.get("vendor_name") or "Unknown vendor"
            vendor_id = vendor.get("vendor_id") or offering.get("vendor_id")
            if vendor_id:
                seen_vendor_ids.add(str(vendor_id))
            product_name = (
                offering.get("normalized_product_name")
                or offering.get("product_name_raw")
                or self.intent.canonical_name
            )
            notes = [
                f"Product match score: {candidate.score:.2f}",
            ]
            if offering.get("moq"):
                notes.append(f"MOQ: {offering['moq']}")
            if snapshot.get("last_observed_at"):
                notes.append(f"Last price: {snapshot['last_observed_at']}")

            results.append(
                UnifiedResult(
                    source_type="offline",
                    result_type="vendor_offering",
                    vendor_id=vendor_id,
                    name=f"{vendor_name} | {product_name}",
                    price=price,
                    currency=snapshot.get("currency") or "INR",
                    availability=bool(snapshot.get("availability", True)),
                    confidence=max(0.0, min(candidate.score, 0.99)),
                    city=vendor.get("city") or _first_service_city(offering),
                    url=offering.get("source_url") or vendor.get("website"),
                    phone=vendor.get("phone") or vendor.get("phone_primary"),
                    address=vendor.get("address"),
                    notes=" | ".join(notes),
                )
            )
        remaining = max(limit - len(results), 0)
        for candidate in self.capability_candidates:
            if remaining <= 0:
                break
            capability = candidate.capability
            vendor = candidate.vendor or {}
            vendor_id = vendor.get("vendor_id") or capability.get("vendor_id")
            if vendor_id and str(vendor_id) in seen_vendor_ids:
                continue
            vendor_name = vendor.get("name") or capability.get("vendor_name") or "Unknown vendor"
            sample_products = [str(item) for item in capability.get("sample_products") or [] if item]
            product_hint = sample_products[0] if sample_products else self.intent.canonical_name
            notes = [
                f"Supplier capability match: {candidate.score:.2f}",
                "Exact stored price not found yet; use this supplier for live quote outreach.",
            ]
            if sample_products:
                notes.append(f"Examples: {', '.join(sample_products[:3])}")
            results.append(
                UnifiedResult(
                    source_type="offline",
                    result_type="vendor_capability",
                    vendor_id=vendor_id,
                    name=f"{vendor_name} | {product_hint}",
                    price=None,
                    currency="INR",
                    availability=True,
                    confidence=max(0.0, min(candidate.score, 0.95)),
                    city=vendor.get("city") or _first_service_city(capability),
                    url=vendor.get("website"),
                    phone=vendor.get("phone") or vendor.get("phone_primary") or capability.get("phone"),
                    address=vendor.get("address"),
                    notes=" | ".join(notes),
                )
            )
            if vendor_id:
                seen_vendor_ids.add(str(vendor_id))
            remaining -= 1
        return results

    def to_supplier_results(
        self,
        *,
        limit: int = DEFAULT_SUPPLIER_RESULT_LIMIT,
        include_capability_matches: bool = False,
    ) -> list[UnifiedResult]:
        """Return one DB-backed row per matched supplier for MVP supplier discovery."""

        grouped: dict[str, list[VendorOfferingCandidate]] = {}
        capability_by_vendor: dict[str, VendorCapabilityCandidate] = {}
        vendor_docs: dict[str, dict[str, Any]] = {}

        for candidate in self.candidates:
            vendor = candidate.vendor or {}
            offering = candidate.offering
            vendor_id = str(vendor.get("vendor_id") or offering.get("vendor_id") or "")
            if not vendor_id:
                continue
            grouped.setdefault(vendor_id, []).append(candidate)
            vendor_docs[vendor_id] = vendor

        for candidate in self.capability_candidates:
            vendor = candidate.vendor or {}
            capability = candidate.capability
            vendor_id = str(vendor.get("vendor_id") or capability.get("vendor_id") or "")
            if not vendor_id or vendor_id in grouped:
                continue
            capability_by_vendor[vendor_id] = candidate
            vendor_docs[vendor_id] = vendor

        results: list[UnifiedResult] = []
        for vendor_id, candidates in grouped.items():
            if len(results) >= limit:
                break
            candidates.sort(key=lambda item: item.score, reverse=True)
            best = candidates[0]
            vendor = best.vendor or vendor_docs.get(vendor_id) or {}
            offering = best.offering
            vendor_name = vendor.get("name") or offering.get("vendor_name")
            if not vendor_name:
                continue
            snapshot = best.price_snapshot or {}
            price = snapshot.get("latest_price")
            if not isinstance(price, (int, float)):
                price = snapshot.get("best_recent_price")
            if not isinstance(price, (int, float)):
                price = None
            product_examples: list[str] = []
            for candidate in candidates:
                name = (
                    candidate.offering.get("normalized_product_name")
                    or candidate.offering.get("product_name_raw")
                    or ""
                )
                if name and name not in product_examples:
                    product_examples.append(str(name))
                if len(product_examples) >= 3:
                    break
            notes = [
                f"{len(candidates)} matching supplier option{'s' if len(candidates) != 1 else ''}",
                f"Best match score: {best.score:.2f}",
            ]
            if product_examples:
                notes.append(f"Examples: {', '.join(product_examples)}")
            if snapshot.get("last_observed_at"):
                notes.append(f"Last price: {snapshot['last_observed_at']}")

            results.append(
                UnifiedResult(
                    source_type="offline",
                    result_type="vendor_offering",
                    vendor_id=vendor_id,
                    name=vendor_name,
                    price=float(price) if isinstance(price, (int, float)) else None,
                    currency=snapshot.get("currency") or "INR",
                    availability=bool(snapshot.get("availability", True)),
                    confidence=max(0.0, min(best.score, 0.99)),
                    city=vendor.get("city") or _first_service_city(offering),
                    url=offering.get("source_url") or vendor.get("website"),
                    phone=vendor.get("phone") or vendor.get("phone_primary"),
                    address=vendor.get("address"),
                    notes=" | ".join(notes),
                )
            )

        if not include_capability_matches:
            return results

        for vendor_id, candidate in capability_by_vendor.items():
            if len(results) >= limit:
                break
            capability = candidate.capability
            vendor = candidate.vendor or vendor_docs.get(vendor_id) or {}
            vendor_name = vendor.get("name") or capability.get("vendor_name")
            if not vendor_name:
                continue
            sample_products = [str(item) for item in capability.get("sample_products") or [] if item]
            notes = [
                "Supplier capability match",
                f"Best match score: {candidate.score:.2f}",
            ]
            if sample_products:
                notes.append(f"Examples: {', '.join(sample_products[:3])}")
            results.append(
                UnifiedResult(
                    source_type="offline",
                    result_type="vendor_capability",
                    vendor_id=vendor_id,
                    name=vendor_name,
                    price=None,
                    currency="INR",
                    availability=True,
                    confidence=max(0.0, min(candidate.score, 0.95)),
                    city=vendor.get("city") or _first_service_city(capability),
                    url=vendor.get("website"),
                    phone=vendor.get("phone") or vendor.get("phone_primary") or capability.get("phone"),
                    address=vendor.get("address"),
                    notes=" | ".join(notes),
                )
            )

        return results

    def to_offering_results(self, *, limit: int = DEFAULT_SUPPLIER_RESULT_LIMIT) -> list[UnifiedResult]:
        """Return one rich product-card result for every matched DB offering."""

        results: list[UnifiedResult] = []
        seen_gold_vendor_ids: set[str] = set()
        is_gold_bullion_route = self.intent.subcategory_id == "gold_bullion"
        for candidate in self.candidates:
            if len(results) >= limit:
                break
            offering = candidate.offering
            vendor = candidate.vendor or {}
            vendor_name = vendor.get("name") or offering.get("vendor_name")
            if not vendor_name:
                continue
            vendor_id = vendor.get("vendor_id") or offering.get("vendor_id")
            if is_gold_bullion_route and vendor_id:
                vendor_key = str(vendor_id)
                if vendor_key in seen_gold_vendor_ids:
                    continue
                seen_gold_vendor_ids.add(vendor_key)
            snapshot = candidate.price_snapshot or {}
            price = _offering_display_price(offering, snapshot)
            currency = snapshot.get("currency") or "INR"
            product_name = _offering_product_name(offering, self.intent.canonical_name)
            offering_attrs = offering.get("attributes") or {}
            notes = [
                f"Product match score: {candidate.score:.2f}",
            ]
            if offering.get("moq"):
                notes.append(f"MOQ: {offering['moq']}")
            if snapshot.get("last_observed_at"):
                notes.append(f"Last price: {snapshot['last_observed_at']}")

            results.append(
                UnifiedResult(
                    source_type="offline",
                    result_type="vendor_offering",
                    vendor_id=vendor_id,
                    name=f"{vendor_name} | {product_name}",
                    price=price,
                    currency=currency,
                    availability=bool(snapshot.get("availability", True)),
                    confidence=max(0.0, min(candidate.score, 0.99)),
                    city=vendor.get("city") or _first_service_city(offering),
                    url=offering.get("source_url") or vendor.get("website"),
                    phone=vendor.get("phone") or vendor.get("phone_primary"),
                    address=vendor.get("address"),
                    notes=" | ".join(notes),
                    attributes={
                        "offering_id": offering.get("offering_id"),
                        "product_id": offering.get("product_id") or self.intent.product_id,
                        "taxonomy_node_id": offering.get("taxonomy_node_id"),
                        "taxonomy_path": offering.get("taxonomy_path") or [],
                        "legacy_vendor_id": offering_attrs.get("legacy_vendor_id"),
                        "product_preview": _offering_preview_payload(
                            offering,
                            vendor,
                            snapshot,
                            product_name,
                            price,
                            currency,
                        ),
                    },
                )
            )
        return results

    def to_vendors(
        self,
        *,
        limit: int = DEFAULT_RESULT_LIMIT,
        include_capability_matches: bool = False,
    ) -> list[VendorInfo]:
        vendors: list[VendorInfo] = []
        seen: set[str] = set()
        for candidate in self.candidates:
            vendor = candidate.vendor or {}
            offering = candidate.offering
            vendor_id = vendor.get("vendor_id") or offering.get("vendor_id")
            phone = vendor.get("phone") or vendor.get("phone_primary")
            key = str(vendor_id or phone or vendor.get("name") or "")
            if not key or key in seen or not phone:
                continue
            seen.add(key)
            vendors.append(
                VendorInfo(
                    vendor_id=vendor_id,
                    name=vendor.get("name") or offering.get("vendor_name") or "Unknown vendor",
                    phone=str(phone),
                    address=vendor.get("address") or "",
                    city=vendor.get("city") or _first_service_city(offering),
                    confidence_score=round(candidate.score * 100, 2),
                    website=vendor.get("website") or offering.get("source_url"),
                    offering_id=offering.get("offering_id"),
                    product_id=offering.get("product_id") or self.intent.product_id,
                    category_id=(
                        offering.get("canonical_subcategory_id")
                        or offering.get("taxonomy_node_id")
                        or offering.get("canonical_category_id")
                        or offering.get("category_id")
                        or self.intent.category_id
                    ),
                    is_mock=False,
                )
            )
            if len(vendors) >= limit:
                break
        if not include_capability_matches:
            return vendors
        if len(vendors) >= limit:
            return vendors
        for candidate in self.capability_candidates:
            vendor = candidate.vendor or {}
            capability = candidate.capability
            vendor_id = vendor.get("vendor_id") or capability.get("vendor_id")
            phone = vendor.get("phone") or vendor.get("phone_primary") or capability.get("phone")
            key = str(vendor_id or phone or vendor.get("name") or capability.get("vendor_name") or "")
            if not key or key in seen or not phone:
                continue
            seen.add(key)
            vendors.append(
                VendorInfo(
                    vendor_id=vendor_id,
                    name=vendor.get("name") or capability.get("vendor_name") or "Unknown vendor",
                    phone=str(phone),
                    address=vendor.get("address") or "",
                    city=vendor.get("city") or _first_service_city(capability),
                    confidence_score=round(candidate.score * 100, 2),
                    website=vendor.get("website") or capability.get("website"),
                    category_id=capability.get("canonical_subcategory_id") or capability.get("taxonomy_node_id") or self.intent.category_id,
                    is_mock=False,
                )
            )
            if len(vendors) >= limit:
                break
        return vendors


def normalize_text(value: str | None) -> str:
    return " ".join(TOKEN_RE.findall((value or "").lower()))


def normalize_tokens(value: str | None) -> list[str]:
    stop_words = {
        "i",
        "want",
        "need",
        "buy",
        "procure",
        "purchase",
        "for",
        "near",
        "me",
        "in",
        "the",
        "and",
        "with",
        "best",
        "cheap",
        "cheapest",
        "supplier",
        "suppliers",
        "vendor",
        "vendors",
    }
    return [token for token in TOKEN_RE.findall((value or "").lower()) if len(token) > 1 and token not in stop_words]


def _first_service_city(offering: dict[str, Any]) -> str | None:
    locations = offering.get("service_locations") or []
    if not isinstance(locations, list):
        return None
    for location in locations:
        if isinstance(location, dict) and location.get("city"):
            return str(location["city"])
        if isinstance(location, str) and location.strip():
            return location.strip()
    return None


def _city_match_score(offering: dict[str, Any], vendor: dict[str, Any] | None, location: str | None) -> float:
    if not location or location == "unknown":
        return 0.5
    wanted = normalize_text(location)
    candidates = [vendor.get("city") if vendor else None, vendor.get("address") if vendor else None]
    for service_location in offering.get("service_locations") or []:
        if isinstance(service_location, dict):
            candidates.extend([service_location.get("city"), service_location.get("state"), service_location.get("raw")])
        elif isinstance(service_location, str):
            candidates.append(service_location)
    normalized_candidates = [normalize_text(value) for value in candidates if value]
    if any(wanted and wanted in candidate for candidate in normalized_candidates):
        return 1.0
    if any("pan india" in candidate or "india" == candidate for candidate in normalized_candidates):
        return 0.75
    return 0.25


def _extract_attributes(text: str) -> dict[str, str]:
    normalized = text.lower()
    attrs: dict[str, str] = {}

    strength = re.search(r"\b(\d+(?:\.\d+)?)\s?(mg|mcg|g|gm|gram|grams|kg|ml|l|litre|litres|iu)\b", normalized)
    if strength:
        attrs["strength_or_size"] = f"{strength.group(1)}{strength.group(2)}"
        if strength.group(2) in {"g", "gm", "gram", "grams", "kg"}:
            grams = float(strength.group(1))
            if strength.group(2) == "kg":
                grams *= 1000
            attrs["quantity_grams"] = f"{grams:g}"

    pack = re.search(r"\b(\d+)\s?(tablets?|tabs?|capsules?|caps?|strips?|bottles?|pieces?|pcs)\b", normalized)
    if pack:
        attrs["pack_size"] = f"{pack.group(1)} {pack.group(2)}"

    purities = [match.replace(" ", "") for match in re.findall(r"\b(999\s?bis|999|995|24\s?k|22\s?k|18\s?k|916)\b", normalized)]
    if purities:
        attrs["purity"] = next((item for item in purities if item in {"999bis", "999", "995", "916"}), purities[0])

    return attrs


def _infer_subcategory(category_id: str | None, tokens: list[str]) -> str | None:
    canonical_root = LEGACY_CATEGORY_TO_CANONICAL_ROOT.get(category_id or "", category_id or "")
    token_set = set(tokens)
    for root, hints, subcategory_id in TOKEN_SUBCATEGORY_HINTS:
        if canonical_root == root and any(hint in token_set for hint in hints):
            return subcategory_id
    return None


def _infer_category_from_tokens(tokens: list[str]) -> str | None:
    token_set = set(tokens)
    for root, hints in CATEGORY_TOKEN_HINTS:
        if any(hint in token_set for hint in hints):
            return root
    return None


def _is_generic_subcategory(value: str | None) -> bool:
    normalized = (value or "").strip().lower()
    return not normalized or normalized.endswith("_generic") or normalized in {"generic", "medicine_generic"}


def _attribute_match_score(query_attrs: dict[str, str], offering_attrs: dict[str, Any]) -> float:
    if not query_attrs:
        return 0.7
    if not offering_attrs:
        return 0.3
    weighted_matches = 0.0
    total_weight = 0.0
    for key, value in query_attrs.items():
        weight = 2.0 if key == "quantity_grams" else 1.5 if key == "purity" else 1.0
        total_weight += weight
        offer_value = normalize_text(str(offering_attrs.get(key) or ""))
        if normalize_text(value) and normalize_text(value) in offer_value:
            weighted_matches += weight
    return weighted_matches / max(total_weight, 1.0)


def _token_variants(token: str) -> set[str]:
    variants = {token}
    if token.endswith("s") and len(token) > 3:
        variants.add(token[:-1])
    elif len(token) > 3:
        variants.add(f"{token}s")
    variants.update(TOKEN_EQUIVALENTS.get(token, set()))
    return {variant for variant in variants if variant}


def _token_match_score(query_tokens: list[str], *texts: str | None) -> float:
    if not query_tokens:
        return 0.0
    haystack = set(normalize_tokens(" ".join(text for text in texts if text)))
    if not haystack:
        return 0.0
    hits = sum(1 for token in query_tokens if _token_variants(token) & haystack)
    return hits / max(len(query_tokens), 1)


def _specific_product_match(intent: ProductIntent) -> bool:
    if not intent.product_id or not intent.product_doc:
        return False
    query_tokens = set(intent.normalized_tokens)
    product_tokens = set(
        token
        for token in normalize_tokens(
            " ".join(
                [
                    str(intent.product_doc.get("canonical_name") or ""),
                    " ".join(intent.product_doc.get("aliases") or []),
                ]
            )
        )
        if token not in QUERY_NOISE_TOKENS
    )
    if not product_tokens:
        return False
    coverage = len(query_tokens & product_tokens) / max(len(product_tokens), 1)
    return coverage >= 0.7


def _candidate_is_blocked(intent: ProductIntent, offering: dict[str, Any]) -> bool:
    text = normalize_text(
        " ".join(
            str(value or "")
            for value in [
                offering.get("product_name_raw"),
                offering.get("normalized_product_name"),
                offering.get("category_id"),
                offering.get("subcategory_id"),
            ]
        )
    )
    tokens = set(intent.normalized_tokens)
    if intent.subcategory_id == "gold_bullion":
        blocked = {
            "usb",
            "drive",
            "lighter",
            "packaging",
            "pouch",
            "agarbatti",
            "cart",
            "mask",
            "patch",
            "chair",
            "lamp",
            "printer",
            "barcode",
        }
        if any(token in text.split() for token in blocked):
            return True
    if intent.subcategory_id == "electronics_audio" and "apple" in tokens:
        non_audio = {"face", "wash", "gel", "fruit", "cream", "mask", "soap", "shampoo"}
        if any(token in text.split() for token in non_audio):
            return True
    if intent.subcategory_id == "pharma_tablets":
        if any(token in text.split() for token in {"injection", "injectable", "vial", "syrup"}):
            return True
    return False


def _offering_has_required_tokens(intent: ProductIntent, offering: dict[str, Any]) -> bool:
    useful_tokens = [token for token in intent.normalized_tokens if token not in QUERY_NOISE_TOKENS]
    if not useful_tokens:
        return True
    text = normalize_text(
        " ".join(
            str(value or "")
            for value in [
                offering.get("product_name_raw"),
                offering.get("normalized_product_name"),
                " ".join(str(token) for token in offering.get("normalized_tokens") or []),
                " ".join(f"{key} {value}" for key, value in (offering.get("attributes") or {}).items() if value is not None),
            ]
        )
    )
    text_tokens = set(text.split())
    for token in useful_tokens[:4]:
        if intent.subcategory_id == "gold_bullion" and token in {"24k", "bis"} and text_tokens & {"995", "999", "999bis"}:
            continue
        variants = _token_variants(token)
        if not any(
            item == variant
            or item == variant.rstrip("s")
            or item.startswith(variant.rstrip("s"))
            for item in text_tokens
            for variant in variants
        ):
            return False
    return True


def _freshness_score(value: Any) -> float:
    if not isinstance(value, datetime):
        return 0.4
    timestamp = value
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    age_days = max((datetime.now(timezone.utc) - timestamp).total_seconds() / 86400, 0)
    return max(0.0, min(1.0, 1.0 - age_days / 90))


def _price_score(snapshot: dict[str, Any] | None) -> float:
    if not snapshot:
        return 0.3
    if isinstance(snapshot.get("latest_price"), (int, float)) or isinstance(snapshot.get("best_recent_price"), (int, float)):
        return 1.0
    return 0.4


def _question_schema_missing(category_doc: dict[str, Any] | None, attrs: dict[str, str]) -> tuple[list[str], list[str]]:
    schema = (category_doc or {}).get("question_schema") or {}
    required = schema.get("required_attributes") or []
    missing: list[str] = []
    questions: list[str] = []
    for item in required:
        if isinstance(item, str):
            key = item
            question = f"Please share {item.replace('_', ' ')}."
        elif isinstance(item, dict):
            key = str(item.get("key") or "")
            question = str(item.get("question") or f"Please share {key.replace('_', ' ')}.")
        else:
            continue
        if key and not attrs.get(key):
            missing.append(key)
            questions.append(question)
    return missing, questions


async def _find_category(query: StructuredQuery, tokens: list[str]) -> dict[str, Any] | None:
    query_subcategory_id = None if _is_generic_subcategory(query.subcategory_id) else query.subcategory_id
    category = (query_subcategory_id or query.category or "").strip().lower()
    if category:
        canonical_root = LEGACY_CATEGORY_TO_CANONICAL_ROOT.get(category)
        if canonical_root:
            doc = await catalog_categories_collection.find_one({"category_id": canonical_root})
            return doc or {
                "category_id": canonical_root,
                "parent_id": None,
                "display_name": canonical_root.replace("_", " ").title(),
                "level": 0,
                "aliases": [category, canonical_root],
                "question_schema": {},
                "ranking_config": {},
                "matching_config": {},
            }

        # Exact IDs are authoritative. The legacy categories collection still
        # contains many scraped display names under old IDs such as
        # "electronics:agriculture"; broad root queries must not resolve to
        # those stale display-name matches.
        doc = await catalog_categories_collection.find_one({"category_id": category})
        if doc:
            return doc
        if category in CANONICAL_ROOT_CATEGORIES:
            return {
                "category_id": category,
                "parent_id": None,
                "display_name": category.replace("_", " ").title(),
                "level": 0,
                "aliases": [category],
                "question_schema": {},
                "ranking_config": {},
                "matching_config": {},
            }

        doc = await catalog_categories_collection.find_one(
            {
                "category_id": {"$not": {"$regex": ":"}},
                "display_name": {"$regex": f"^{re.escape(category)}$", "$options": "i"},
            }
        )
        if doc:
            return doc

    if tokens:
        token_query: dict[str, Any] = {"aliases": {"$in": tokens}}
        if category:
            token_query = {
                "$and": [
                    {
                        "$or": [
                            {"category_id": category},
                            {"parent_id": category},
                        ]
                    },
                    token_query,
                    {"category_id": {"$not": {"$regex": ":"}}},
                ]
            }
        else:
            token_query = {"$and": [token_query, {"category_id": {"$not": {"$regex": ":"}}}]}
        doc = await catalog_categories_collection.find_one(token_query)
        if doc:
            return doc

    if query.category:
        return {
            "category_id": query.category,
            "parent_id": None,
            "display_name": query.category.title(),
            "level": 0,
            "aliases": [query.category],
            "question_schema": {},
            "ranking_config": {},
            "matching_config": {},
        }
    return None


async def _find_product(query: StructuredQuery, category_id: str, tokens: list[str]) -> dict[str, Any] | None:
    filters: list[dict[str, Any]] = []
    if tokens:
        filters.append({"normalized_tokens": {"$in": tokens}})
        filters.append({"aliases": {"$in": tokens}})
    if query.product:
        filters.append({"canonical_name": {"$regex": re.escape(query.product), "$options": "i"}})
    if not filters:
        return None

    category_filter: dict[str, Any]
    if ":" in category_id:
        category_filter = {"category_id": category_id}
    else:
        category_filter = {
            "$or": [
                {"category_id": category_id},
                {"category_id": {"$regex": f"^{re.escape(category_id)}:"}},
            ]
        }

    docs = await catalog_products_collection.find(
        {
            "$and": [category_filter, {"$or": filters}],
            "is_active": {"$ne": False},
        }
    ).to_list(length=50)
    if not docs:
        return None

    docs.sort(
        key=lambda doc: _token_match_score(
            tokens,
            doc.get("canonical_name"),
            " ".join(doc.get("aliases") or []),
        ),
        reverse=True,
    )
    return docs[0]


async def build_product_intent(query: StructuredQuery) -> ProductIntent:
    raw_text = (query.product or query.raw_query).strip()
    location_tokens = set(normalize_tokens(query.location if query.location != "unknown" else ""))
    tokens = [token for token in normalize_tokens(raw_text) if token not in location_tokens]
    attrs = _extract_attributes(raw_text)
    category_doc = await _find_category(query, tokens)
    category_id = LEGACY_CATEGORY_TO_CANONICAL_ROOT.get((category_doc or {}).get("category_id") or "", (category_doc or {}).get("category_id") or query.category)
    inferred_root = _infer_category_from_tokens(tokens)
    if inferred_root and (
        not category_id
        or category_id not in CANONICAL_ROOT_CATEGORIES
        or _is_generic_subcategory(query.subcategory_id)
        or category_id in {"medicine", "medical", "drug", "drugs"}
    ):
        category_id = inferred_root
    query_subcategory_id = None if _is_generic_subcategory(query.subcategory_id) else query.subcategory_id
    doc_subcategory_id = None if _is_generic_subcategory((category_doc or {}).get("subcategory_id")) else (category_doc or {}).get("subcategory_id")
    subcategory_id = query_subcategory_id or doc_subcategory_id or _infer_subcategory(category_id, tokens)
    focused_tokens = [token for token in tokens if token not in QUERY_NOISE_TOKENS]
    if focused_tokens:
        tokens = focused_tokens
    product_doc = await _find_product(query, category_id, tokens) if category_id else None
    if product_doc:
        attrs = {**(product_doc.get("attributes") or {}), **attrs}

    missing, questions = _question_schema_missing(category_doc, attrs)
    return ProductIntent(
        category_id=category_id,
        subcategory_id=subcategory_id,
        product_id=(product_doc or {}).get("product_id"),
        product_key=(product_doc or {}).get("product_key"),
        canonical_name=(product_doc or {}).get("canonical_name") or query.product,
        raw_query=query.raw_query,
        normalized_tokens=tokens,
        attributes={key: str(value) for key, value in attrs.items() if value is not None},
        missing_attributes=missing,
        follow_up_questions=questions,
        category_doc=category_doc,
        product_doc=product_doc,
    )


def _offering_query(intent: ProductIntent, location: str | None) -> dict[str, Any]:
    hard_filters: list[dict[str, Any]] = []
    soft_filters: list[dict[str, Any]] = []
    if intent.product_id and _specific_product_match(intent):
        hard_filters.append({"product_id": intent.product_id})
    else:
        if intent.product_id:
            soft_filters.append({"product_id": intent.product_id})
        if intent.subcategory_id:
            hard_filters.append(
                {
                    "$or": [
                        {"subcategory_id": intent.subcategory_id},
                        {"canonical_subcategory_id": intent.subcategory_id},
                        {"taxonomy_node_id": intent.subcategory_id},
                        {"taxonomy_path": intent.subcategory_id},
                    ]
                }
            )
        token_clause = _offering_token_match_clause(intent.normalized_tokens)
        if token_clause:
            soft_filters.append(token_clause)

    category_clause: dict[str, Any]
    canonical_root = LEGACY_CATEGORY_TO_CANONICAL_ROOT.get(intent.category_id, intent.category_id)
    if ":" in intent.category_id:
        category_clause = {
            "$or": [
                {"category_id": intent.category_id},
                {"canonical_category_id": canonical_root},
                {"canonical_subcategory_id": intent.category_id},
                {"taxonomy_node_id": intent.category_id},
                {"taxonomy_path": intent.category_id},
            ]
        }
    else:
        category_clause = {
            "$or": [
                {"category_id": intent.category_id},
                {"category_id": {"$regex": f"^{re.escape(intent.category_id)}:"}},
                {"canonical_category_id": canonical_root},
                {"canonical_subcategory_id": intent.category_id},
                {"taxonomy_node_id": intent.category_id},
                {"taxonomy_path": canonical_root},
                {"taxonomy_path": intent.category_id},
            ]
        }

    base: dict[str, Any] = {
        "is_active": {"$ne": False},
    }
    and_filters: list[dict[str, Any]] = [category_clause, *hard_filters]
    if soft_filters:
        and_filters.append({"$or": soft_filters})
    if len(and_filters) == 1:
        base.update(and_filters[0])
    else:
        base["$and"] = and_filters
    return base


def _taxonomy_match_clause(intent: ProductIntent) -> dict[str, Any]:
    canonical_root = LEGACY_CATEGORY_TO_CANONICAL_ROOT.get(intent.category_id, intent.category_id)
    if intent.subcategory_id:
        return {
            "$or": [
                {"canonical_subcategory_id": intent.subcategory_id},
                {"taxonomy_node_id": intent.subcategory_id},
                {"taxonomy_path": intent.subcategory_id},
            ]
        }
    return {
        "$or": [
            {"canonical_category_id": canonical_root},
            {"taxonomy_path": canonical_root},
            {"taxonomy_node_id": canonical_root},
        ]
    }


def _token_text_match_clause(tokens: list[str]) -> dict[str, Any] | None:
    useful_tokens = [token for token in tokens if token not in QUERY_NOISE_TOKENS]
    if not useful_tokens:
        return None
    return {
        "$or": [
            {"normalized_tokens": {"$in": useful_tokens}},
            {"sample_products": {"$in": useful_tokens}},
            {"taxonomy_path_labels": {"$in": useful_tokens}},
        ]
    }


def _offering_token_match_clause(tokens: list[str]) -> dict[str, Any] | None:
    useful_tokens = [token for token in tokens if token not in QUERY_NOISE_TOKENS]
    if {"995", "999", "999bis"} & set(useful_tokens):
        useful_tokens = [token for token in useful_tokens if token not in {"24k", "bis"}]
    if not useful_tokens:
        return None
    if len(useful_tokens) == 1:
        return {"normalized_tokens": {"$in": sorted(_token_variants(useful_tokens[0]))}}
    token_requirements: list[dict[str, Any]] = []
    for token in useful_tokens[:4]:
        token_requirements.append({"normalized_tokens": {"$in": sorted(_token_variants(token))}})
    return {"$and": token_requirements}


def _algolia_query_text(intent: ProductIntent) -> str:
    parts = [
        intent.canonical_name,
        " ".join(intent.normalized_tokens),
        " ".join(str(value) for value in intent.attributes.values() if value),
    ]
    return " ".join(part for part in parts if part).strip() or intent.raw_query


async def _find_capability_docs(intent: ProductIntent, location: str | None) -> list[dict[str, Any]]:
    canonical_root = LEGACY_CATEGORY_TO_CANONICAL_ROOT.get(intent.category_id, intent.category_id)
    query_text = _algolia_query_text(intent)
    vendor_ids: list[str] = []
    if algolia_search.is_enabled():
        vendor_ids = await algolia_search.search_capability_vendor_ids(
            query_text=query_text,
            taxonomy_node_id=intent.subcategory_id or None,
            canonical_category_id=canonical_root,
            city=location,
            limit=DEFAULT_ALGOLIA_CANDIDATE_LIMIT,
        )

    taxonomy_clause = _taxonomy_match_clause(intent)
    token_clause = _token_text_match_clause(intent.normalized_tokens)
    base_filters: list[dict[str, Any]] = [
        taxonomy_clause,
        {"quality_status": {"$ne": "rejected"}},
    ]
    if token_clause:
        base_filters.append(token_clause)

    docs: list[dict[str, Any]] = []
    if vendor_ids:
        docs = await vendor_capabilities_collection.find(
            {
                "$and": [
                    {"vendor_id": {"$in": vendor_ids}},
                    taxonomy_clause,
                    {"quality_status": {"$ne": "rejected"}},
                ]
            }
        ).to_list(length=DEFAULT_ALGOLIA_CANDIDATE_LIMIT)
        by_vendor: dict[str, list[dict[str, Any]]] = {}
        for doc in docs:
            by_vendor.setdefault(str(doc.get("vendor_id")), []).append(doc)
        ordered: list[dict[str, Any]] = []
        for vendor_id in vendor_ids:
            rows = by_vendor.get(vendor_id) or []
            rows.sort(
                key=lambda row: (
                    float(row.get("routing_confidence") or row.get("capability_confidence") or 0),
                    int(row.get("priced_offering_count") or 0),
                    int(row.get("offering_count") or 0),
                ),
                reverse=True,
            )
            if rows:
                ordered.append(rows[0])
        if ordered:
            return ordered[:DEFAULT_ALGOLIA_CANDIDATE_LIMIT]

    docs = await vendor_capabilities_collection.find(
        {"$and": base_filters}
    ).sort("routing_confidence", -1).to_list(length=DEFAULT_ALGOLIA_CANDIDATE_LIMIT)
    if docs:
        return docs

    # If token filtering is too strict for a sparse category, keep a category
    # fallback so vendors with broad but relevant capabilities can still be
    # contacted for a live quote.
    return await vendor_capabilities_collection.find(
        {"$and": [taxonomy_clause, {"quality_status": {"$ne": "rejected"}}]}
    ).sort("routing_confidence", -1).to_list(length=DEFAULT_OFFERING_SCAN_LIMIT)


async def _find_offering_docs(intent: ProductIntent, location: str | None) -> list[dict[str, Any]]:
    """Load candidate offerings.

    Algolia is used only as a fast fuzzy candidate retriever. Mongo remains the
    source of truth and the scorer below still performs final ranking.
    """
    canonical_root = LEGACY_CATEGORY_TO_CANONICAL_ROOT.get(intent.category_id, intent.category_id)
    algolia_ids: list[str] = []
    capability_vendor_ids: list[str] = []
    if algolia_search.is_enabled():
        algolia_ids = await algolia_search.search_offering_ids(
            query_text=_algolia_query_text(intent),
            taxonomy_node_id=intent.subcategory_id or None,
            canonical_category_id=canonical_root,
            city=location,
            limit=DEFAULT_ALGOLIA_CANDIDATE_LIMIT,
        )
        capability_vendor_ids = await algolia_search.search_capability_vendor_ids(
            query_text=_algolia_query_text(intent),
            taxonomy_node_id=intent.subcategory_id or None,
            canonical_category_id=canonical_root,
            city=location,
            limit=DEFAULT_ALGOLIA_CANDIDATE_LIMIT,
        )

    mongo_fallback_docs: list[dict[str, Any]] = []
    capability_docs: list[dict[str, Any]] = []
    if capability_vendor_ids:
        scoped_query = _offering_query(intent, location)
        scoped_query = {"$and": [scoped_query, {"vendor_id": {"$in": capability_vendor_ids}}]}
        capability_docs = await vendor_offerings_collection.find(scoped_query).sort("supply_confidence", -1).to_list(length=DEFAULT_OFFERING_SCAN_LIMIT)
    if algolia_ids:
        docs = await vendor_offerings_collection.find(
            {
                "offering_id": {"$in": algolia_ids},
                "is_active": {"$ne": False},
            }
        ).to_list(length=DEFAULT_ALGOLIA_CANDIDATE_LIMIT)
        by_id = {doc.get("offering_id"): doc for doc in docs if doc.get("offering_id")}
        ordered_docs = [by_id[offering_id] for offering_id in algolia_ids if offering_id in by_id]
        if len(ordered_docs) >= DEFAULT_OFFERING_SCAN_LIMIT:
            return ordered_docs
        mongo_fallback_docs = await vendor_offerings_collection.find(
            _offering_query(intent, location)
        ).sort("supply_confidence", -1).to_list(length=DEFAULT_OFFERING_SCAN_LIMIT)
        seen_ids = {doc.get("offering_id") for doc in ordered_docs if doc.get("offering_id")}
        combined = ordered_docs
        for doc in [*capability_docs, *mongo_fallback_docs]:
            offering_id = doc.get("offering_id")
            if offering_id and offering_id in seen_ids:
                continue
            combined.append(doc)
            if offering_id:
                seen_ids.add(offering_id)
        if combined:
            return combined[:DEFAULT_ALGOLIA_CANDIDATE_LIMIT]

    if capability_docs:
        return capability_docs

    return mongo_fallback_docs or await vendor_offerings_collection.find(
        _offering_query(intent, location)
    ).sort("supply_confidence", -1).to_list(length=DEFAULT_OFFERING_SCAN_LIMIT)


async def find_gold_live_rate_results(
    query: StructuredQuery,
    *,
    limit: int = DEFAULT_SUPPLIER_RESULT_LIMIT,
    scan_limit: int = 5000,
) -> list[UnifiedResult]:
    """Return the latest stored Gold live-rate row per vendor.

    Gold live boards are volatile and adapter refresh can fail for a given
    request. This helper reads the canonical current snapshot table directly,
    so a failed refresh can still fall back to the latest stored dealer rate
    instead of falling through to generic marketplace listings.
    """

    snapshot_docs = await current_price_snapshots_collection.find(
        {
            "offering_id": {"$regex": "^gold_live:"},
            "source_type": "live_api",
        },
        {"_id": 0},
    ).sort("last_observed_at", -1).limit(scan_limit).to_list(length=scan_limit)
    snapshot_docs = [doc for doc in snapshot_docs if _gold_live_snapshot_is_usable(doc)]
    if not snapshot_docs:
        return []

    offering_ids = sorted({doc.get("offering_id") for doc in snapshot_docs if doc.get("offering_id")})
    vendor_ids = sorted({doc.get("vendor_id") for doc in snapshot_docs if doc.get("vendor_id")})
    offering_docs = await vendor_offerings_collection.find(
        {
            "offering_id": {"$in": offering_ids},
            "is_active": {"$ne": False},
        },
        {"_id": 0},
    ).to_list(length=len(offering_ids))
    vendor_docs = await supplier_vendors_collection.find(
        {
            "vendor_id": {"$in": vendor_ids},
            "is_active": {"$ne": False},
        },
        {"_id": 0},
    ).to_list(length=len(vendor_ids))
    offerings_by_id = {doc.get("offering_id"): doc for doc in offering_docs if doc.get("offering_id")}
    vendors_by_id = {doc.get("vendor_id"): doc for doc in vendor_docs if doc.get("vendor_id")}

    best_by_vendor: dict[str, tuple[float, datetime, UnifiedResult]] = {}
    for snapshot in snapshot_docs:
        offering = offerings_by_id.get(snapshot.get("offering_id"))
        vendor = vendors_by_id.get(snapshot.get("vendor_id"))
        if not offering or not vendor:
            continue
        vendor_id = str(vendor.get("vendor_id") or offering.get("vendor_id") or "")
        vendor_name = vendor.get("name") or offering.get("vendor_name")
        if not vendor_id or not vendor_name:
            continue
        price = _offering_display_price(offering, snapshot)
        if price is None:
            continue
        product_name = _offering_product_name(offering, "Gold live rate")
        currency = snapshot.get("currency") or "INR"
        score = _gold_result_score(query, offering, vendor, snapshot)
        observed_at = (
            _snapshot_datetime(snapshot.get("last_observed_at"))
            or _snapshot_datetime(snapshot.get("rate_timestamp"))
            or datetime.min.replace(tzinfo=timezone.utc)
        )
        offering_attrs = offering.get("attributes") or {}
        notes = [f"Live board score: {score:.2f}"]
        if snapshot.get("last_observed_at"):
            notes.append(f"Last price: {snapshot['last_observed_at']}")
        result = UnifiedResult(
            source_type="offline",
            result_type="vendor_offering",
            vendor_id=vendor_id,
            name=f"{vendor_name} | {product_name}",
            price=price,
            currency=currency,
            availability=bool(snapshot.get("availability", True)),
            confidence=score,
            city=vendor.get("city") or _first_service_city(offering),
            url=offering.get("source_url") or vendor.get("website"),
            phone=vendor.get("phone") or vendor.get("phone_primary"),
            address=vendor.get("address"),
            notes=" | ".join(notes),
            attributes={
                "offering_id": offering.get("offering_id"),
                "product_id": offering.get("product_id"),
                "taxonomy_node_id": offering.get("taxonomy_node_id"),
                "taxonomy_path": offering.get("taxonomy_path") or [],
                "legacy_vendor_id": offering_attrs.get("legacy_vendor_id"),
                "product_preview": _offering_preview_payload(
                    offering,
                    vendor,
                    snapshot,
                    product_name,
                    price,
                    currency,
                ),
            },
        )
        existing = best_by_vendor.get(vendor_id)
        if existing is None or (score, observed_at) > (existing[0], existing[1]):
            best_by_vendor[vendor_id] = (score, observed_at, result)

    ranked = sorted(best_by_vendor.values(), key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in ranked[:limit]]


def _algolia_first_applies(query: StructuredQuery) -> bool:
    """Algolia-first mode: on for everything except gold (gold has its own
    live-rate pipeline) and only when Algolia is actually reachable."""
    if not settings.algolia_first_search or not algolia_search.is_enabled():
        return False
    canonical_root = LEGACY_CATEGORY_TO_CANONICAL_ROOT.get(
        (query.category or "").strip().lower(), (query.category or "").strip().lower()
    )
    return canonical_root != "gold"


def _positional_score(index: int, total: int, *, top: float = 0.99, floor: float = 0.35) -> float:
    """Monotonically decreasing score that preserves Algolia's hit order when
    downstream code sorts by score (all sorts used are stable)."""
    if total <= 1:
        return top
    return round(max(floor, top - (index / (total - 1)) * (top - floor)), 4)


async def _find_vendor_offerings_algolia_first(
    query: StructuredQuery,
    *,
    limit: int = DEFAULT_RESULT_LIMIT,
) -> ProductVendorRoute:
    """Algolia-first retrieval: send the user's words to Algolia, keep Algolia's
    ranking, and hydrate full documents from Mongo (source of truth) only for
    display data. No hint tables, no token re-scoring."""
    # Singularize in code: the index's ignorePlurals does not merge forms for
    # this catalog ("cartons" ~40 hits vs "carton" ~1,000+).
    query_text = query_normalization.singularize_phrase(
        (query.product or query.raw_query or "").strip()
    )
    location = (query.location or "").strip()
    city = location if location and location.lower() != "unknown" else None
    facet_id = query.taxonomy_facet_id
    # Descriptors ("fancy", "premium") boost matches but must not shrink the
    # candidate pool: they go into the query with optionalWords so "fancy
    # travel bags" ranks fancy ones up within the full "travel bags" set.
    optional_words = search_specs.optional_search_tokens(query.descriptors, query.search_specs)
    search_text = f"{query_text} {' '.join(optional_words)}".strip() if optional_words else query_text

    intent = ProductIntent(
        category_id=LEGACY_CATEGORY_TO_CANONICAL_ROOT.get(
            (query.category or "").strip().lower(), (query.category or "").strip().lower() or "electronics"
        ),
        subcategory_id=facet_id or query.subcategory_id,
        product_id=None,
        product_key=None,
        canonical_name=query.product or query_text,
        raw_query=query.raw_query,
        normalized_tokens=normalize_tokens(query_text),
    )
    if not query_text:
        return ProductVendorRoute(query=query, intent=intent, candidates=[])

    faceted = await algolia_search.search_offerings_faceted(
        query_text=search_text,
        city=city,
        taxonomy_node_id=facet_id,
        limit=algolia_search.MAX_HITS_PER_PAGE,
        optional_words=optional_words,
    )
    algolia_hits = list(faceted.hits)
    # Compound variant: "monocarton" is also indexed as "mono carton". Search
    # both forms and merge so neither spelling's suppliers are missed.
    split_query = query_normalization.split_compounds(query_text)
    if split_query:
        split_search = f"{split_query} {' '.join(optional_words)}".strip() if optional_words else split_query
        split_faceted = await algolia_search.search_offerings_faceted(
            query_text=split_search,
            city=city,
            taxonomy_node_id=facet_id,
            limit=algolia_search.MAX_HITS_PER_PAGE,
            optional_words=optional_words,
        )
        if split_faceted.hits:
            algolia_hits.extend(split_faceted.hits)
            if not faceted.hits:
                query_text = split_query
    offering_ids: list[str] = []
    seen_offering_ids: set[str] = set()
    for hit in algolia_hits:
        offering_id = str(hit.get("offering_id") or hit.get("objectID") or "")
        if offering_id and offering_id not in seen_offering_ids:
            seen_offering_ids.add(offering_id)
            offering_ids.append(offering_id)

    capability_vendor_ids = await algolia_search.search_capability_vendor_ids(
        query_text=query_text,
        taxonomy_node_id=facet_id,
        canonical_category_id=None,
        city=city,
        limit=DEFAULT_SUPPLIER_RESULT_LIMIT,
    )

    if not offering_ids and not capability_vendor_ids:
        return ProductVendorRoute(query=query, intent=intent, candidates=[])

    ordered_offerings: list[dict[str, Any]] = []
    if offering_ids:
        offering_docs = await vendor_offerings_collection.find(
            {"offering_id": {"$in": offering_ids}, "is_active": {"$ne": False}}
        ).to_list(length=len(offering_ids))
        by_offering_id = {doc.get("offering_id"): doc for doc in offering_docs if doc.get("offering_id")}
        ordered_offerings = [by_offering_id[offering_id] for offering_id in offering_ids if offering_id in by_offering_id]
        # Algolia cannot tell core products from accessories/siblings (a
        # "Ceiling Fan Regulator" and a real ceiling fan are identical text
        # matches for "ceiling fan"). Stable-sort by head-noun tier so core
        # products lead while Algolia's order is preserved within each tier.
        ordered_offerings.sort(
            key=lambda doc: search_specs.rank_key(
                query_text,
                _offering_product_name(doc, ""),
                descriptors=query.descriptors or None,
                search_specs=query.search_specs or None,
                match_tier_fn=product_relevance.match_tier,
            )
        )

    vendor_ids = [doc.get("vendor_id") for doc in ordered_offerings if doc.get("vendor_id")]
    vendor_ids.extend(capability_vendor_ids)
    vendors = await supplier_vendors_collection.find(
        {"vendor_id": {"$in": vendor_ids}, "is_active": {"$ne": False}}
    ).to_list(length=1000)
    vendors_by_id = {vendor.get("vendor_id"): vendor for vendor in vendors if vendor.get("vendor_id")}

    snapshots = await current_price_snapshots_collection.find(
        {"offering_id": {"$in": offering_ids}}
    ).to_list(length=max(len(offering_ids), 1)) if offering_ids else []
    snapshot_by_offering = {
        snapshot.get("offering_id"): snapshot
        for snapshot in sorted(snapshots, key=lambda item: item.get("last_observed_at") or datetime.min, reverse=True)
        if snapshot.get("offering_id")
    }

    candidates: list[VendorOfferingCandidate] = []
    total_offerings = len(ordered_offerings)
    for index, offering in enumerate(ordered_offerings):
        candidates.append(
            VendorOfferingCandidate(
                offering=offering,
                vendor=vendors_by_id.get(offering.get("vendor_id")),
                price_snapshot=snapshot_by_offering.get(offering.get("offering_id")),
                score=_positional_score(index, total_offerings),
                score_parts={"algolia_rank": float(index + 1)},
            )
        )

    capability_candidates: list[VendorCapabilityCandidate] = []
    if capability_vendor_ids:
        offering_vendor_ids = {str(doc.get("vendor_id")) for doc in ordered_offerings if doc.get("vendor_id")}
        capability_docs = await vendor_capabilities_collection.find(
            {"vendor_id": {"$in": capability_vendor_ids}, "quality_status": {"$ne": "rejected"}}
        ).to_list(length=DEFAULT_SUPPLIER_RESULT_LIMIT)
        best_by_vendor: dict[str, dict[str, Any]] = {}
        for doc in capability_docs:
            vendor_id = str(doc.get("vendor_id") or "")
            current = best_by_vendor.get(vendor_id)
            doc_rank = float(doc.get("routing_confidence") or doc.get("capability_confidence") or 0)
            current_rank = float((current or {}).get("routing_confidence") or (current or {}).get("capability_confidence") or 0)
            if current is None or doc_rank > current_rank:
                best_by_vendor[vendor_id] = doc
        ordered_capabilities = [
            best_by_vendor[vendor_id]
            for vendor_id in capability_vendor_ids
            if vendor_id in best_by_vendor and vendor_id not in offering_vendor_ids
        ]
        total_capabilities = len(ordered_capabilities)
        for index, capability in enumerate(ordered_capabilities):
            capability_candidates.append(
                VendorCapabilityCandidate(
                    capability=capability,
                    vendor=vendors_by_id.get(capability.get("vendor_id")),
                    score=_positional_score(index, total_capabilities, top=0.95),
                    score_parts={"algolia_rank": float(index + 1)},
                )
            )

    return ProductVendorRoute(
        query=query,
        intent=intent,
        candidates=candidates[:limit],
        capability_candidates=capability_candidates[:limit],
    )


async def find_vendor_offerings(
    query: StructuredQuery,
    *,
    limit: int = DEFAULT_RESULT_LIMIT,
) -> ProductVendorRoute:
    if _algolia_first_applies(query):
        route = await _find_vendor_offerings_algolia_first(query, limit=limit)
        if route.candidates or route.capability_candidates:
            return route
        # Algolia empty or unreachable: fall through to the legacy Mongo path.
    return await _find_vendor_offerings_legacy(query, limit=limit)


async def _find_vendor_offerings_legacy(
    query: StructuredQuery,
    *,
    limit: int = DEFAULT_RESULT_LIMIT,
) -> ProductVendorRoute:
    intent = await build_product_intent(query)
    if intent.missing_attributes:
        return ProductVendorRoute(query=query, intent=intent, candidates=[])

    offering_docs = await _find_offering_docs(intent, query.location)
    capability_docs = await _find_capability_docs(intent, query.location)

    if not offering_docs and not capability_docs:
        return ProductVendorRoute(query=query, intent=intent, candidates=[])

    vendor_ids = [
        doc.get("vendor_id")
        for doc in [*offering_docs, *capability_docs]
        if doc.get("vendor_id")
    ]
    snapshot_filters = [{"offering_id": {"$in": [doc.get("offering_id") for doc in offering_docs if doc.get("offering_id")]}}]
    if intent.product_id:
        snapshot_filters.append({"product_id": intent.product_id})

    vendors = await supplier_vendors_collection.find({"vendor_id": {"$in": vendor_ids}, "is_active": {"$ne": False}}).to_list(length=500)
    vendors_by_id = {vendor.get("vendor_id"): vendor for vendor in vendors if vendor.get("vendor_id")}

    snapshots = await current_price_snapshots_collection.find({"$or": snapshot_filters}).to_list(length=500)
    snapshot_by_offering = {
        snapshot.get("offering_id"): snapshot
        for snapshot in sorted(snapshots, key=lambda item: item.get("last_observed_at") or datetime.min, reverse=True)
        if snapshot.get("offering_id")
    }

    candidates: list[VendorOfferingCandidate] = []
    for offering in offering_docs:
        if _candidate_is_blocked(intent, offering):
            continue
        if not _offering_has_required_tokens(intent, offering):
            continue
        vendor = vendors_by_id.get(offering.get("vendor_id"))
        snapshot = snapshot_by_offering.get(offering.get("offering_id"))
        if _is_gold_live_offering(offering) and not _gold_live_snapshot_is_usable(snapshot):
            continue
        token_score = _token_match_score(
            intent.normalized_tokens,
            offering.get("normalized_product_name"),
            offering.get("product_name_raw"),
            " ".join(offering.get("normalized_tokens") or []),
        )
        attr_score = _attribute_match_score(intent.attributes, offering.get("attributes") or {})
        location_score = _city_match_score(offering, vendor, query.location)
        supply_score = max(0.0, min(float(offering.get("supply_confidence") or 0.5), 1.0))
        evidence_score = max(0.0, min(float(offering.get("evidence_score") or 0.5), 1.0))
        freshness_score = _freshness_score(offering.get("last_seen_at"))
        price_score = _price_score(snapshot)

        score_parts = {
            "token": token_score,
            "attribute": attr_score,
            "location": location_score,
            "supply": supply_score,
            "evidence": evidence_score,
            "freshness": freshness_score,
            "price": price_score,
        }
        score = (
            token_score * 0.30
            + attr_score * 0.15
            + location_score * 0.10
            + supply_score * 0.20
            + evidence_score * 0.10
            + freshness_score * 0.05
            + price_score * 0.10
        )
        if math.isfinite(score):
            candidates.append(
                VendorOfferingCandidate(
                    offering=offering,
                    vendor=vendor,
                    price_snapshot=snapshot,
                    score=max(0.0, min(score, 1.0)),
                    score_parts=score_parts,
                )
            )

    candidates.sort(key=lambda item: item.score, reverse=True)

    offering_vendor_ids = {
        str((candidate.vendor or {}).get("vendor_id") or candidate.offering.get("vendor_id"))
        for candidate in candidates
        if (candidate.vendor or {}).get("vendor_id") or candidate.offering.get("vendor_id")
    }
    capability_candidates: list[VendorCapabilityCandidate] = []
    seen_capability_vendors: set[str] = set()
    for capability in capability_docs:
        vendor_id = str(capability.get("vendor_id") or "")
        if not vendor_id or vendor_id in offering_vendor_ids or vendor_id in seen_capability_vendors:
            continue
        seen_capability_vendors.add(vendor_id)
        vendor = vendors_by_id.get(capability.get("vendor_id"))
        token_score = _token_match_score(
            intent.normalized_tokens,
            capability.get("vendor_name"),
            " ".join(str(item) for item in capability.get("sample_products") or []),
            " ".join(str(item) for item in capability.get("taxonomy_path_labels") or []),
            capability.get("taxonomy_node_id"),
        )
        taxonomy_score = 1.0 if intent.subcategory_id and intent.subcategory_id in set(capability.get("taxonomy_path") or []) else 0.85
        location_score = _city_match_score(capability, vendor, query.location)
        capability_score = max(0.0, min(float(capability.get("routing_confidence") or capability.get("capability_confidence") or 0.55), 1.0))
        coverage_score = min(float(capability.get("offering_count") or 0) / 50.0, 1.0)
        price_coverage_score = min(float(capability.get("priced_offering_count") or 0) / 20.0, 1.0)
        score_parts = {
            "token": token_score,
            "taxonomy": taxonomy_score,
            "location": location_score,
            "capability": capability_score,
            "coverage": coverage_score,
            "price_coverage": price_coverage_score,
        }
        score = (
            token_score * 0.25
            + taxonomy_score * 0.25
            + location_score * 0.15
            + capability_score * 0.20
            + coverage_score * 0.10
            + price_coverage_score * 0.05
        )
        if math.isfinite(score):
            capability_candidates.append(
                VendorCapabilityCandidate(
                    capability=capability,
                    vendor=vendor,
                    score=max(0.0, min(score, 0.95)),
                    score_parts=score_parts,
                )
            )

    capability_candidates.sort(key=lambda item: item.score, reverse=True)
    return ProductVendorRoute(
        query=query,
        intent=intent,
        candidates=candidates[:limit],
        capability_candidates=capability_candidates[:limit],
    )


async def record_price_observation(
    *,
    vendor_id: str,
    offering_id: str | None,
    category_id: str,
    product_id: str | None = None,
    query_id: str | None = None,
    price: float | None = None,
    currency: str = "INR",
    unit: str | None = None,
    availability: bool | None = None,
    source_type: str = "call",
    source_url: str | None = None,
    call_id: str | None = None,
    notes: str | None = None,
    confidence: float = 0.7,
    observed_at: datetime | None = None,
) -> str:
    """Persist one price signal and update the current snapshot.

    This is the write side of the routing architecture. Callers can use it for
    scrape, call, live API, or manual price updates.
    """
    observed = observed_at or datetime.now(timezone.utc)
    observation_id = ":".join(
        [
            "price",
            vendor_id,
            offering_id or product_id or category_id,
            source_type,
            str(int(observed.timestamp() * 1000)),
        ]
    )
    observation = {
        "observation_id": observation_id,
        "vendor_id": vendor_id,
        "offering_id": offering_id,
        "product_id": product_id,
        "category_id": category_id,
        "query_id": query_id,
        "price": price,
        "currency": currency,
        "unit": unit,
        "availability": availability,
        "source_type": source_type,
        "source_url": source_url,
        "call_id": call_id,
        "notes": notes,
        "confidence": max(0.0, min(confidence, 1.0)),
        "observed_at": observed,
        "created_at": observed,
    }
    await price_observations_collection.update_one(
        {"observation_id": observation_id},
        {"$setOnInsert": observation},
        upsert=True,
    )

    is_gold_live_observation = bool(offering_id and offering_id.startswith("gold_live:"))
    should_update_current_snapshot = not (is_gold_live_observation and source_type != "live_api")

    if offering_id and should_update_current_snapshot:
        snapshot_filter = {"vendor_id": vendor_id, "offering_id": offering_id}
        snapshot_update: dict[str, Any] = {
            "vendor_id": vendor_id,
            "offering_id": offering_id,
            "product_id": product_id,
            "category_id": category_id,
            "latest_price": price,
            "currency": currency,
            "unit": unit,
            "availability": availability if availability is not None else True,
            "confidence": max(0.0, min(confidence, 1.0)),
            "last_observed_at": observed,
            "source_type": source_type,
            "source_url": source_url,
            "call_id": call_id,
            "updated_at": observed,
        }
        existing = await current_price_snapshots_collection.find_one(snapshot_filter)
        best_recent = existing.get("best_recent_price") if existing else None
        if isinstance(price, (int, float)):
            if not isinstance(best_recent, (int, float)) or price < best_recent:
                snapshot_update["best_recent_price"] = price
            else:
                snapshot_update["best_recent_price"] = best_recent

        await current_price_snapshots_collection.update_one(
            snapshot_filter,
            {
                "$set": snapshot_update,
                "$setOnInsert": {"created_at": observed},
            },
            upsert=True,
        )

    if query_id:
        await catalog_vendor_interactions_collection.insert_one(
            {
                "vendor_id": vendor_id,
                "query_id": query_id,
                "offering_id": offering_id,
                "product_id": product_id,
                "category_id": category_id,
                "interaction_type": source_type,
                "outcome": "price_observed" if price is not None else "contacted",
                "quoted_price": price,
                "availability": availability,
                "notes": notes,
                "occurred_at": observed,
                "created_at": observed,
            }
        )

    return observation_id
