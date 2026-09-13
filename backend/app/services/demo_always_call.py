from __future__ import annotations

import re

from app.config import settings
from app.models.schemas import StructuredQuery, VendorInfo


def is_demo_always_call_enabled() -> bool:
    return bool(settings.demo_always_call_enabled and settings.demo_always_call_phone.strip())


def _normalize_phone(phone: str | None) -> str:
    return re.sub(r"\D+", "", phone or "")


def _demo_vendor_name(query: StructuredQuery) -> str:
    text = f"{query.category} {query.product} {query.raw_query}".lower()
    if any(token in text for token in ("gold", "bullion", "jewel", "jewellery", "jewelry", "silver")):
        return "Arham Bullion"
    if any(token in text for token in ("medicine", "medical", "pharma", "tablet", "capsule", "syrup", "drug")):
        return "VN Medical Agencies"
    if any(token in text for token in ("mobile", "phone", "accessor", "earphone", "headphone", "charger", "electronics")):
        return "VN Electronics"
    if any(token in text for token in ("cosmetic", "beauty", "skincare", "makeup")):
        return "VN Beauty Supplies"
    if any(token in text for token in ("gift", "decor", "lamp", "home", "furniture")):
        return "VN Enterprises"
    return "VN Enterprises"


def demo_vendor_for_query(query: StructuredQuery) -> VendorInfo | None:
    if not is_demo_always_call_enabled():
        return None
    phone = settings.demo_always_call_phone.strip()
    name = _demo_vendor_name(query)
    city = (settings.demo_always_call_city or query.location or "").strip() or None
    return VendorInfo(
        vendor_id=f"demo-always-call:{_normalize_phone(phone) or phone}",
        name=name,
        phone=phone,
        address=f"{city} demo supplier" if city else "Demo supplier",
        city=city,
        confidence_score=0.99,
        channel_types=["call"],
        call_available=True,
        preferred_contact_channel="call",
        is_mock=False,
    )
