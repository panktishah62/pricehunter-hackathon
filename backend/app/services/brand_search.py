"""Brand-aware search helpers: detect, strip, match, and user-facing miss copy."""

from __future__ import annotations

import re

from app.models.schemas import StructuredQuery, UnifiedResult

_SPLIT_RE = re.compile(r"[^a-z0-9]+")

# Common catalog spellings / typos buyers use interchangeably.
_BRAND_ALIASES: dict[str, tuple[str, ...]] = {
    "probot": ("probott", "pro bottle"),
    "probott": ("probot",),
    "tupperware": ("tupper ware",),
}

_LOCATIONISH = {
    "abad",
    "ahmedabad",
    "amdavad",
    "rajkot",
    "mumbai",
    "delhi",
    "bangalore",
    "bengaluru",
    "chennai",
    "hyderabad",
    "pune",
    "surat",
    "india",
}


def normalize_brand(brand: str | None) -> str:
    return _SPLIT_RE.sub(" ", (brand or "").strip().lower()).strip()


def brand_tokens(brand: str | None) -> list[str]:
    return [token for token in _SPLIT_RE.split(normalize_brand(brand)) if len(token) >= 2]


def brand_match_variants(brand: str | None) -> list[str]:
    base = normalize_brand(brand)
    if not base:
        return []
    variants = {base, *(_BRAND_ALIASES.get(base) or ())}
    return sorted(variants, key=len, reverse=True)


def clean_display_specs(query: StructuredQuery) -> list[str]:
    """Specs safe to show in user-facing brand-miss copy (no location/quantity junk)."""
    brand_norm = normalize_brand(query.brand)
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in query.search_specs or []:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen or key == brand_norm:
            continue
        tokens = [t for t in _SPLIT_RE.split(key) if t]
        if not tokens:
            continue
        if any(t in _LOCATIONISH for t in tokens):
            continue
        if key in _LOCATIONISH:
            continue
        # Drop whole short replies that mix qty + location ("10, 1L, abad").
        if "," in text and any(t in _LOCATIONISH for t in tokens):
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def product_without_brand(product: str | None, brand: str | None) -> str:
    """Strip a known brand phrase from the product head for generic retrieval."""
    text = (product or "").strip()
    brand_norm = normalize_brand(brand)
    if not text or not brand_norm:
        return text
    pattern = re.compile(rf"\b{re.escape(brand_norm)}\b", re.IGNORECASE)
    stripped = pattern.sub(" ", text)
    stripped = re.sub(r"\s+", " ", stripped).strip(" -,/|")
    return stripped or text


def text_matches_brand(text: str | None, brand: str | None) -> bool:
    hay = normalize_brand(text)
    if not hay or not normalize_brand(brand):
        return False
    hay_tokens = set(brand_tokens(hay))
    for variant in brand_match_variants(brand):
        tokens = brand_tokens(variant)
        if not tokens:
            continue
        if all(token in hay for token in tokens):
            return True
        # Close spellings: probot ↔ probott
        if len(tokens) == 1:
            needle = tokens[0]
            for word in hay_tokens:
                if needle == word:
                    return True
                if len(needle) >= 5 and len(word) >= 5 and (needle in word or word in needle):
                    return True
    return False


def result_matches_brand(result: UnifiedResult, brand: str | None) -> bool:
    if not normalize_brand(brand):
        return False
    preview = (result.attributes or {}).get("product_preview") if result.attributes else {}
    preview = preview if isinstance(preview, dict) else {}
    attr_blob = " ".join(
        f"{k} {v}" for k, v in (result.attributes or {}).items() if k != "product_preview" and v
    )
    blobs = [
        result.name,
        result.notes,
        result.city,
        preview.get("product_name"),
        preview.get("supplier_name"),
        preview.get("brand"),
        attr_blob,
    ]
    return any(text_matches_brand(str(blob), brand) for blob in blobs if blob)


def offering_doc_matches_brand(offering: dict, brand: str | None, *, vendor: dict | None = None) -> bool:
    if not normalize_brand(brand):
        return False
    attrs = offering.get("attributes") or {}
    blobs = [
        offering.get("product_name_raw"),
        offering.get("normalized_product_name"),
        offering.get("canonical_product_name"),
        attrs.get("brand"),
        attrs.get("manufacturer"),
        " ".join(str(t) for t in (offering.get("normalized_tokens") or []) if t),
        (vendor or {}).get("name"),
        (vendor or {}).get("company_name"),
    ]
    return any(text_matches_brand(str(blob), brand) for blob in blobs if blob)


def wants_specific_brand(query: StructuredQuery) -> bool:
    brand = (query.brand or "").strip()
    preference = (query.brand_preference or "").strip().lower()
    if preference == "any":
        return False
    if preference == "specific":
        return bool(brand)
    return bool(brand)


def brand_miss_message(query: StructuredQuery) -> str:
    brand = (query.brand or "").strip() or "that brand"
    product = product_without_brand(query.product, brand) or (query.product or "this product")
    return (
        f"No matches found locally for {brand} {product}. "
        f"I’ll check online stores next."
    )


def brand_fallback_prompt_message(query: StructuredQuery, *, same_city_count: int, other_city_count: int) -> str:
    brand = (query.brand or "").strip() or "that brand"
    product = product_without_brand(query.product, brand) or (query.product or "this product")
    specs = clean_display_specs(query)
    spec_bit = f" ({', '.join(specs[:3])})" if specs else ""
    total = same_city_count + other_city_count
    if total <= 0:
        return (
            f"I could not find other {product} suppliers to offer as alternatives to {brand}."
        )
    where = []
    if same_city_count:
        where.append("in your city")
    if other_city_count:
        where.append("in other cities")
    where_bit = " and ".join(where) if where else "nearby"
    return (
        f"I also have other {product} options{spec_bit} from suppliers {where_bit} "
        f"(not {brand}). Would you like to see those?"
    )


def ensure_brand_on_query(query: StructuredQuery) -> StructuredQuery:
    """Normalize brand fields: peel brand out of product / search_specs when needed."""
    brand = (query.brand or "").strip() or None
    preference = (query.brand_preference or "").strip().lower() or None
    specs = list(query.search_specs or [])

    if not brand and preference != "any":
        # Recover brand left only in search_specs (legacy extractor path).
        for item in specs:
            text = str(item).strip()
            # Prefer short proper-looking brand tokens over sizes/quantities.
            if not text or any(ch.isdigit() for ch in text):
                continue
            if len(text.split()) <= 3 and text.lower() not in {"any", "any brand", "no brand"}:
                # Heuristic: first non-numeric search_spec that looks like a name —
                # only promote when product also contains it.
                if text_matches_brand(query.product, text):
                    brand = text
                    break

    if brand:
        preference = preference or "specific"
        product = product_without_brand(query.product, brand)
        brand_norm = normalize_brand(brand)
        specs = [s for s in specs if normalize_brand(s) != brand_norm]
        # Keep brand in search_specs for ranking boosts (optionalWords / rank_key).
        if brand not in specs:
            specs = [brand, *specs]
        updates = {
            "brand": brand,
            "brand_preference": preference,
            "product": product or query.product,
            "search_specs": specs,
        }
        return query.model_copy(update=updates) if hasattr(query, "model_copy") else query.copy(update=updates)

    if preference in {"any", "unknown", "specific"}:
        updates = {"brand_preference": preference}
        return query.model_copy(update=updates) if hasattr(query, "model_copy") else query.copy(update=updates)
    return query


def brand_keyword_phrases(query: StructuredQuery) -> list[str]:
    """Preferred IndiaMART / Places keyword seeds when a brand is requested."""
    brand = (query.brand or "").strip()
    product = product_without_brand(query.product, brand) or (query.product or "").strip()
    if not brand or not product:
        return []
    return [f"{brand} {product}".strip(), brand]
