"""Product type registry — single source of truth for category/subcategory behaviour.

Every downstream decision (precision questions, Google Places queries, Flash Compare
eligibility, vendor relevance scoring) is driven by this registry instead of scattered
hardcoded lists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProductTypeSpec:
    """Defines how a product subcategory behaves across the entire pipeline."""

    # Identity
    product_type_id: str
    parent_category: str  # "electronics" | "medicine" | "gold"
    display_name: str

    # Keywords that signal this product type in user text (lowercase).
    keywords: tuple[str, ...] = ()

    # Attributes the user MUST provide before search can start.
    required_attributes: tuple[str, ...] = ()
    # Attributes that are nice-to-have but not blocking.
    optional_attributes: tuple[str, ...] = ()

    # Example precise queries shown as suggested replies.
    example_queries: tuple[str, ...] = ()

    # Google Places: search query templates.  {product} and {location} are interpolated.
    places_search_templates: tuple[str, ...] = ()
    # Google Places: includedType values for filtering.
    places_types: tuple[str, ...] = ()

    # Flash Compare eligibility.
    flash_compare_eligible: bool = False

    # Brand hints when user says "any good brand".
    brand_hints: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Registry entries
# ---------------------------------------------------------------------------

_REGISTRY: list[ProductTypeSpec] = [
    # ── Electronics: Phones ──────────────────────────────────────────────
    ProductTypeSpec(
        product_type_id="smartphones",
        parent_category="electronics",
        display_name="Smartphones",
        keywords=("iphone", "phone", "mobile", "smartphone", "galaxy", "pixel", "redmi", "oneplus", "vivo", "oppo", "realme", "poco", "nothing phone"),
        required_attributes=("brand or model", "storage or variant"),
        optional_attributes=("color",),
        example_queries=("iPhone 16 128GB", "Samsung S24 Ultra 256GB", "OnePlus 13 12/256GB"),
        places_search_templates=(
            "{product} mobile store in {location}",
            "{product} phone dealer in {location}",
            "mobile phone store in {location}",
        ),
        places_types=("cell_phone_store", "electronics_store"),
        flash_compare_eligible=True,
        brand_hints=("Samsung", "OnePlus"),
    ),
    # ── Electronics: Laptops ─────────────────────────────────────────────
    ProductTypeSpec(
        product_type_id="laptops",
        parent_category="electronics",
        display_name="Laptops",
        keywords=("laptop", "macbook", "chromebook", "notebook", "thinkpad", "ideapad"),
        required_attributes=("brand or model", "processor or RAM or storage"),
        optional_attributes=("screen size", "color"),
        example_queries=("MacBook Air M3 16GB", "HP Pavilion i5 16GB 512GB SSD", "Lenovo IdeaPad Ryzen 5"),
        places_search_templates=(
            "{product} laptop store in {location}",
            "{product} computer dealer in {location}",
            "laptop store in {location}",
        ),
        places_types=("electronics_store", "computer_store"),
        flash_compare_eligible=True,
        brand_hints=("HP", "Lenovo"),
    ),
    # ── Electronics: Tablets ─────────────────────────────────────────────
    ProductTypeSpec(
        product_type_id="tablets",
        parent_category="electronics",
        display_name="Tablets",
        keywords=("tablet", "ipad", "tab"),
        required_attributes=("brand or model", "storage or variant"),
        optional_attributes=("color", "connectivity"),
        example_queries=("iPad 10th gen 64GB WiFi", "Samsung Tab S9 FE 128GB"),
        places_search_templates=(
            "{product} tablet store in {location}",
            "electronics store in {location}",
        ),
        places_types=("electronics_store", "cell_phone_store"),
        flash_compare_eligible=True,
        brand_hints=("Apple", "Samsung"),
    ),
    # ── Electronics: TVs ─────────────────────────────────────────────────
    ProductTypeSpec(
        product_type_id="televisions",
        parent_category="electronics",
        display_name="Televisions",
        keywords=("tv", "television", "led tv", "oled", "qled", "smart tv"),
        required_attributes=("brand", "screen size or display type"),
        optional_attributes=("budget",),
        example_queries=("Samsung 55 inch QLED 4K TV", "LG 43 inch LED TV under 30k"),
        places_search_templates=(
            "{product} TV store in {location}",
            "electronics store in {location}",
            "home appliance store in {location}",
        ),
        places_types=("electronics_store", "home_goods_store"),
        flash_compare_eligible=True,
        brand_hints=("Samsung", "LG"),
    ),
    # ── Electronics: Audio ───────────────────────────────────────────────
    ProductTypeSpec(
        product_type_id="audio",
        parent_category="electronics",
        display_name="Audio (Earbuds / Headphones / Speakers)",
        keywords=("earbuds", "earphone", "earphones", "headphone", "headphones", "speaker", "soundbar", "airdopes", "airpods", "neckband", "tws"),
        required_attributes=("brand or model",),
        optional_attributes=("features", "budget"),
        example_queries=("boAt Airdopes 141", "Sony WH-1000XM5", "JBL Flip 6"),
        places_search_templates=(
            "{product} audio store in {location}",
            "electronics store in {location}",
            "mobile accessories store in {location}",
        ),
        places_types=("electronics_store",),
        flash_compare_eligible=True,
        brand_hints=("boAt", "JBL"),
    ),
    # ── Electronics: Home Appliances ─────────────────────────────────────
    ProductTypeSpec(
        product_type_id="home_appliances",
        parent_category="electronics",
        display_name="Home Appliances",
        keywords=(
            "washing machine", "dishwasher", "fridge", "refrigerator", "microwave",
            "oven", "air conditioner", "ac", "geyser", "water heater", "water purifier",
            "vacuum", "air purifier", "cooler", "air cooler", "chimney",
            "induction", "mixer", "grinder", "mixer grinder", "juicer", "blender",
            "food processor",
        ),
        required_attributes=("type or model", "capacity or size", "budget range"),
        optional_attributes=("brand", "energy rating"),
        example_queries=("Bosch 12 place dishwasher under 40k", "LG 8kg front load washing machine", "1.5 ton split AC under 35k"),
        places_search_templates=(
            "{product} home appliance store in {location}",
            "{product} appliance dealer in {location}",
            "home appliance store in {location}",
        ),
        places_types=("home_goods_store", "electronics_store", "furniture_store"),
        flash_compare_eligible=True,
        brand_hints=("LG", "Samsung"),
    ),
    # ── Electronics: Personal Care ───────────────────────────────────────
    ProductTypeSpec(
        product_type_id="personal_care_electronics",
        parent_category="electronics",
        display_name="Personal Care Electronics",
        keywords=("trimmer", "shaver", "hair dryer", "straightener", "electric toothbrush", "toothbrush", "epilator", "grooming"),
        required_attributes=("brand or model",),
        optional_attributes=("features", "budget"),
        example_queries=("Philips OneBlade trimmer", "Oral-B Pro electric toothbrush"),
        places_search_templates=(
            "{product} electronics store in {location}",
            "personal care appliance store in {location}",
            "electronics store in {location}",
        ),
        places_types=("electronics_store",),
        flash_compare_eligible=True,
        brand_hints=("Philips", "Braun"),
    ),
    # ── Electronics: Chargers & Accessories ──────────────────────────────
    ProductTypeSpec(
        product_type_id="chargers_accessories",
        parent_category="electronics",
        display_name="Chargers & Accessories",
        keywords=("charger", "power bank", "cable", "adapter", "case", "cover", "screen guard", "tempered glass", "stand", "mount", "iphone charger", "usb-c charger", "type-c charger", "c type charger"),
        required_attributes=("device or type", "wattage or specification"),
        optional_attributes=("brand",),
        example_queries=("65W USB-C charger for OnePlus", "20000mAh power bank"),
        places_search_templates=(
            "{product} mobile accessories store in {location}",
            "mobile accessories store in {location}",
        ),
        places_types=("electronics_store", "cell_phone_store"),
        flash_compare_eligible=True,
        brand_hints=("Anker", "Spigen"),
    ),
    # ── Electronics: Wearables ───────────────────────────────────────────
    ProductTypeSpec(
        product_type_id="wearables",
        parent_category="electronics",
        display_name="Smartwatches & Wearables",
        keywords=("smartwatch", "smart watch", "fitness band", "fitness tracker", "watch"),
        required_attributes=("brand or model",),
        optional_attributes=("features", "budget"),
        example_queries=("Apple Watch SE", "boAt Storm Call 2 smartwatch"),
        places_search_templates=(
            "{product} electronics store in {location}",
            "mobile accessories store in {location}",
        ),
        places_types=("electronics_store",),
        flash_compare_eligible=True,
        brand_hints=("boAt", "Noise"),
    ),
    # ── Electronics: Cameras & Printers ──────────────────────────────────
    ProductTypeSpec(
        product_type_id="cameras_printers",
        parent_category="electronics",
        display_name="Cameras & Printers",
        keywords=("camera", "dslr", "mirrorless", "printer", "scanner", "projector", "webcam", "canon", "nikon", "gopro"),
        required_attributes=("brand or model", "type"),
        optional_attributes=("budget",),
        example_queries=("Canon EOS R50 mirrorless camera", "HP LaserJet printer"),
        places_search_templates=(
            "{product} store in {location}",
            "camera store in {location}",
            "electronics store in {location}",
        ),
        places_types=("electronics_store",),
        flash_compare_eligible=True,
        brand_hints=("Canon", "HP"),
    ),
    # ── Electronics: Irons ───────────────────────────────────────────────
    ProductTypeSpec(
        product_type_id="irons",
        parent_category="electronics",
        display_name="Irons & Steamers",
        keywords=("iron", "steam iron", "dry iron", "garment steamer", "ironing"),
        required_attributes=("type", "brand or wattage"),
        optional_attributes=("budget",),
        example_queries=("Philips steam iron under 3000", "Bajaj dry iron 1000W"),
        places_search_templates=(
            "{product} home appliance store in {location}",
            "home appliance store in {location}",
            "electronics store in {location}",
        ),
        places_types=("home_goods_store", "electronics_store"),
        flash_compare_eligible=True,
        brand_hints=("Philips", "Bajaj"),
    ),
    # ── Electronics: Generic fallback ────────────────────────────────────
    ProductTypeSpec(
        product_type_id="electronics_generic",
        parent_category="electronics",
        display_name="Electronics (General)",
        keywords=(),  # Catch-all — matched only when nothing else matches
        required_attributes=("brand or model", "key specification"),
        optional_attributes=("budget",),
        example_queries=("iPhone 16 128GB", "boAt Airdopes 141"),
        places_search_templates=(
            "{product} store in {location}",
            "{product} dealer in {location}",
            "electronics store in {location}",
        ),
        places_types=("electronics_store",),
        flash_compare_eligible=True,
        brand_hints=("Samsung", "OnePlus"),
    ),

    # ── Medicine ─────────────────────────────────────────────────────────
    ProductTypeSpec(
        product_type_id="medicines",
        parent_category="medicine",
        display_name="Medicines",
        keywords=(
            "medicine", "tablet", "capsule", "syrup", "ointment", "inhaler",
            "drops", "injection", "cream", "gel", "powder", "suspension",
            "paracetamol", "dolo", "crocin", "calpol", "combiflam", "azithromycin",
            "amoxicillin", "cetirizine", "pantoprazole", "omeprazole",
            "pharmacy", "chemist", "drug",
        ),
        required_attributes=("medicine name or molecule", "strength or dosage"),
        optional_attributes=("quantity", "substitutes acceptable"),
        example_queries=("Dolo 650 tablets 1 strip", "Paracetamol 500mg 10 tablets", "Azithromycin 500mg"),
        places_search_templates=(
            "{product} pharmacy in {location}",
            "{product} medical store in {location}",
            "pharmacy in {location}",
            "chemist in {location}",
        ),
        places_types=("pharmacy", "drugstore"),
        flash_compare_eligible=False,
        brand_hints=("Dolo", "Crocin"),
    ),

    # ── Gold: Coins & Bars ───────────────────────────────────────────────
    ProductTypeSpec(
        product_type_id="gold_bullion",
        parent_category="gold",
        display_name="Gold Coins & Bars (Bullion)",
        keywords=("gold coin", "gold bar", "bullion", "gold biscuit", "gold slab"),
        required_attributes=("purity", "weight"),
        optional_attributes=("brand or jeweller",),
        example_queries=("24K 10g gold coin", "24K 50g gold bar", "1 oz gold bullion"),
        places_search_templates=(
            "gold bullion dealer in {location}",
            "gold coin dealer in {location}",
            "jewellery store in {location}",
        ),
        places_types=("jewelry_store",),
        flash_compare_eligible=False,
        brand_hints=("Tanishq", "Malabar Gold"),
    ),
    # ── Gold: Jewellery ──────────────────────────────────────────────────
    ProductTypeSpec(
        product_type_id="gold_jewellery",
        parent_category="gold",
        display_name="Gold Jewellery",
        keywords=(
            "ring", "chain", "necklace", "earring", "earrings", "bracelet",
            "bangle", "mangalsutra", "pendant", "anklet", "nose pin",
            "jewellery", "jewelry", "jeweller", "jeweler", "jewellers",
            "tanishq", "malabar", "kalyan", "joyalukkas", "pc jeweller",
        ),
        required_attributes=("product type", "purity or karat"),
        optional_attributes=("weight or budget", "preferred jeweller"),
        example_queries=("22K gold chain 20g", "18K diamond ring under 50k", "22K gold bangles pair"),
        places_search_templates=(
            "{product} jewellery store in {location}",
            "gold jewellery store in {location}",
            "jeweller in {location}",
        ),
        places_types=("jewelry_store",),
        flash_compare_eligible=False,
        brand_hints=("Tanishq", "Malabar Gold"),
    ),
    # ── Gold: Generic fallback ───────────────────────────────────────────
    ProductTypeSpec(
        product_type_id="gold_generic",
        parent_category="gold",
        display_name="Gold Products (General)",
        keywords=("gold",),  # Broad catch-all for gold category
        required_attributes=("product type", "purity or weight"),
        optional_attributes=("budget",),
        example_queries=("24K 10g gold coin", "22K gold chain"),
        places_search_templates=(
            "gold jewellery store in {location}",
            "jeweller in {location}",
            "bullion dealer in {location}",
        ),
        places_types=("jewelry_store",),
        flash_compare_eligible=False,
        brand_hints=("Tanishq", "Malabar Gold"),
    ),
]

# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

# Index by product_type_id for direct lookup.
_BY_ID: dict[str, ProductTypeSpec] = {spec.product_type_id: spec for spec in _REGISTRY}

# Category fallbacks (the generic entry for each parent category).
_CATEGORY_FALLBACKS: dict[str, ProductTypeSpec] = {
    spec.parent_category: spec
    for spec in _REGISTRY
    if spec.product_type_id.endswith("_generic")
}
# Medicine has no "_generic" suffix entry — use the single medicines spec as its fallback.
if "medicine" not in _CATEGORY_FALLBACKS:
    for spec in _REGISTRY:
        if spec.parent_category == "medicine":
            _CATEGORY_FALLBACKS["medicine"] = spec
            break


def get_by_id(product_type_id: str) -> ProductTypeSpec | None:
    return _BY_ID.get(product_type_id)


def classify_product_type(product: str, category: str) -> ProductTypeSpec:
    """Match a product string + category to the best ProductTypeSpec.

    Checks keywords from most specific to least specific within the given
    category, then falls back to the category generic entry.
    """
    normalized = product.lower()
    category_lower = category.lower()

    # Score each spec: count keyword hits, prefer longer (more specific) keyword matches.
    best_spec: ProductTypeSpec | None = None
    best_score: int = 0

    for spec in _REGISTRY:
        if spec.parent_category != category_lower:
            continue
        if not spec.keywords:
            continue  # Skip generic fallbacks in scoring pass
        score = 0
        for kw in spec.keywords:
            if kw in normalized:
                # Longer keyword = more specific = higher score
                score += len(kw)
        if score > best_score:
            best_score = score
            best_spec = spec

    if best_spec is not None:
        return best_spec

    # Fall back to category generic
    return _CATEGORY_FALLBACKS.get(category_lower, _CATEGORY_FALLBACKS.get("electronics", _REGISTRY[-1]))


def get_places_queries(spec: ProductTypeSpec, product: str, location: str) -> list[str]:
    """Build Google Places search queries from the spec templates."""
    loc = location if location and location != "unknown" else "Rajkot"
    return [
        template.format(product=product, location=loc)
        for template in spec.places_search_templates
    ]


def get_places_types(spec: ProductTypeSpec) -> list[str]:
    """Return the Google Places includedType values for this product type."""
    return list(spec.places_types)


def build_precision_prompt(spec: ProductTypeSpec) -> str:
    """Build a dynamic precision-check prompt section from the spec."""
    lines = [
        f"Category-specific guidance: {spec.display_name}.",
        "",
        "Required attributes (user MUST provide before search):",
    ]
    for attr in spec.required_attributes:
        lines.append(f"  - {attr}")
    if spec.optional_attributes:
        lines.append("")
        lines.append("Optional attributes (nice to have, do not block search):")
        for attr in spec.optional_attributes:
            lines.append(f"  - {attr}")
    if spec.example_queries:
        lines.append("")
        lines.append("Examples of precise-enough queries:")
        for ex in spec.example_queries:
            lines.append(f'  - "{ex}"')
    lines.append("")
    lines.append(
        "If the user has provided all required attributes (or explicitly declined preference "
        "for any of them), set precise_enough to true. Do not ask about optional attributes "
        "unless the required ones are all resolved."
    )
    return "\n".join(lines)


def is_product_precise(product: str, category: str, spec: ProductTypeSpec | None = None) -> bool:
    """Heuristic check: does the product string satisfy the spec's required attributes?

    This is the SINGLE source of truth for product precision, replacing the old
    scattered checks in chat_session._is_specific_product() and
    query_structurer._fallback_precision_assessment().
    """
    if spec is None:
        spec = classify_product_type(product, category)

    normalized = product.lower().strip()
    if not normalized:
        return False

    tokens = re.findall(r"[a-z0-9]+", normalized)
    has_digits = bool(re.search(r"\d", normalized))
    has_brand = _has_known_brand(normalized)

    pid = spec.product_type_id

    # ── Smartphones ──────────────────────────────────────────────────────
    if pid == "smartphones":
        has_storage = bool(re.search(r"\b\d+\s?(?:gb|tb)\b", normalized))
        has_model = bool(re.search(r"\b\d{1,2}\b", normalized))
        has_variant = any(v in normalized for v in ("pro", "plus", "max", "mini", "ultra", "air", "fe", "lite"))
        return has_brand and (has_storage or has_model or has_variant)

    # ── Laptops ──────────────────────────────────────────────────────────
    if pid == "laptops":
        has_spec = bool(re.search(r"\b(?:i[3579]|ryzen|m[1-4]|snapdragon|\d+\s?gb|\d+\s?tb|\d+\s?ssd)\b", normalized))
        return (has_brand or len(tokens) >= 3) and (has_spec or has_digits)

    # ── Tablets ──────────────────────────────────────────────────────────
    if pid == "tablets":
        has_storage = bool(re.search(r"\b\d+\s?(?:gb|tb)\b", normalized))
        return has_brand and (has_storage or has_digits)

    # ── TVs ──────────────────────────────────────────────────────────────
    if pid == "televisions":
        has_size = bool(re.search(r"\b\d{2,3}\s?(?:inch|in|\")\b", normalized))
        return has_brand and (has_size or has_digits)

    # ── Audio ────────────────────────────────────────────────────────────
    if pid == "audio":
        return has_brand or (len(tokens) >= 3 and has_digits)

    # ── Home Appliances ──────────────────────────────────────────────────
    if pid == "home_appliances":
        has_capacity = bool(re.search(r"\b\d+(?:\.\d+)?\s?(?:kg|l|litre|liter|ton|place|inch|in)\b", normalized))
        has_type = any(t in normalized for t in (
            "front load", "top load", "split", "window", "inverter",
            "freestanding", "built-in", "portable", "semi automatic", "fully automatic",
        ))
        has_budget = bool(re.search(r"\b(?:under|below|within|budget)?\s?\d+\s?(?:k|lakh|lac|rs|inr)\b", normalized))
        return (has_capacity or has_type or has_budget) and len(tokens) >= 2

    # ── Personal Care ────────────────────────────────────────────────────
    if pid == "personal_care_electronics":
        return has_brand or (len(tokens) >= 3)

    # ── Chargers & Accessories ───────────────────────────────────────────
    if pid == "chargers_accessories":
        has_spec = bool(re.search(r"\b\d+\s?(?:w|watt|mah|v)\b", normalized))
        return (has_brand or has_spec) and len(tokens) >= 2

    # ── Wearables ────────────────────────────────────────────────────────
    if pid == "wearables":
        return has_brand or (len(tokens) >= 3 and has_digits)

    # ── Cameras & Printers ───────────────────────────────────────────────
    if pid == "cameras_printers":
        return has_brand and len(tokens) >= 2

    # ── Irons ────────────────────────────────────────────────────────────
    if pid == "irons":
        has_type = any(t in normalized for t in ("steam", "dry", "garment steamer", "press"))
        return has_type or (has_brand and len(tokens) >= 2)

    # ── Medicines ────────────────────────────────────────────────────────
    if pid == "medicines":
        has_strength = bool(re.search(r"\b\d+\s?(?:mg|ml|mcg|iu)\b", normalized))
        has_form = any(f in normalized for f in ("tablet", "capsule", "syrup", "ointment", "inhaler", "drops", "cream", "gel"))
        return (has_brand or len(tokens) >= 2) and (has_strength or has_form)

    # ── Gold Bullion ─────────────────────────────────────────────────────
    if pid == "gold_bullion":
        has_purity = bool(re.search(r"\b(?:24|22|18)\s?k(?:t|arat)?\b", normalized)) or "999" in normalized or "916" in normalized
        has_weight = bool(re.search(r"\b\d+(?:\.\d+)?\s?(?:g|gm|gram|grams|kg|oz|tola)\b", normalized))
        has_type = any(t in normalized for t in ("coin", "bar", "biscuit", "bullion", "slab"))
        return has_purity or has_weight or has_type

    # ── Gold Jewellery ───────────────────────────────────────────────────
    if pid == "gold_jewellery":
        has_purity = bool(re.search(r"\b(?:24|22|18|14)\s?k(?:t|arat)?\b", normalized)) or "916" in normalized or "750" in normalized
        has_type = any(t in normalized for t in (
            "ring", "chain", "necklace", "earring", "earrings", "bracelet",
            "bangle", "mangalsutra", "pendant", "anklet", "nose pin",
        ))
        has_weight_or_budget = bool(re.search(r"\b\d+(?:\.\d+)?\s?(?:g|gm|gram|grams|lakh|lac|k|rs|inr)\b", normalized))
        return has_type and (has_purity or has_weight_or_budget)

    # ── Gold Generic ─────────────────────────────────────────────────────
    if pid == "gold_generic":
        has_purity = bool(re.search(r"\b(?:24|22|18)\s?k(?:t|arat)?\b", normalized)) or "916" in normalized
        has_type = any(t in normalized for t in (
            "coin", "bar", "ring", "chain", "earring", "earrings", "bracelet",
            "bangle", "mangalsutra", "necklace", "pendant", "bullion", "biscuit",
            "jewellery", "jewelry",
        ))
        has_weight_or_budget = bool(re.search(r"\b\d+(?:\.\d+)?\s?(?:g|gm|gram|grams|kg|lakh|lac|k|rs|inr)\b", normalized))
        return has_type and (has_purity or has_weight_or_budget)

    # ── Electronics Generic fallback ─────────────────────────────────────
    return len(tokens) >= 3 and (has_brand or has_digits)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_KNOWN_BRANDS: frozenset[str] = frozenset({
    "apple", "iphone", "ipad", "macbook", "samsung", "oneplus", "pixel", "redmi", "xiaomi", "vivo", "oppo",
    "realme", "poco", "nothing", "boat", "jbl", "sony", "philips", "bajaj",
    "havells", "usha", "lg", "whirlpool", "ifb", "bosch", "panasonic", "noise",
    "dolo", "crocin", "calpol", "supradyn", "revital", "anker", "spigen",
    "tanishq", "malabar", "kalyan", "joyalukkas", "pc jeweller",
    "hp", "lenovo", "dell", "asus", "acer", "msi",
    "canon", "nikon", "epson", "brother",
    "braun", "oral-b", "sonicare",
    "mi", "motorola", "iqoo", "tecno",
    "voltas", "daikin", "hitachi", "carrier", "bluestar", "godrej",
    "kent", "aquaguard", "pureit",
})


def _has_known_brand(normalized: str) -> bool:
    return any(brand in normalized for brand in _KNOWN_BRANDS)
