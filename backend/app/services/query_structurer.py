from __future__ import annotations

from dataclasses import dataclass
import logging
import re
import time

from openai import AsyncOpenAI

from app.categories.router import enrich_query_route
from app.config import settings
from app.models.schemas import (
    ProductPrecisionAssessment,
    StructuredQuery,
    UrgencyOption,
    WhatsAppIntakeRouterResult,
)
from app.services.product_registry import (
    ProductTypeSpec,
    build_precision_prompt,
    classify_product_type,
    is_product_precise as registry_is_product_precise,
)

logger = logging.getLogger(__name__)

# Shared OpenAI client — reuses TCP connections across LLM calls.
_openai_client: AsyncOpenAI | None = None


def _get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _openai_client

SUPPORTED_CATEGORIES = ("electronics", "medicine", "gold")
SUPPORTED_CATEGORY_MESSAGE = (
    "Right now we serve electronics, medicines, and gold products. "
    "Please search for an electronics, medicine, or gold product."
)

SYSTEM_PROMPT = """
You are a query structuring assistant. Convert the user's natural language shopping query into a structured JSON object.

Return ONLY valid JSON with these exact fields:
- "product": the GENERIC item they want WITHOUT manufacturer brand (string). "Tupperware bottles" -> "bottles" or "water bottles"; "probot water bottle" -> "water bottle".
- "brand": manufacturer/label brand or null (e.g. "Tupperware", "Probot", "Amul"). Not material/type words.
- "brand_preference": "specific" | "any" | "unknown" — use "specific" when brand is set.
- "category": MUST be exactly one of: "electronics", "medicine", "gold". Use "electronics" for phones, laptops, tablets, chargers, accessories, appliances, TVs, audio, cameras, printers, wearables, personal care electronics, and all tech products. Use "medicine" for medicines, tablets, capsules, syrups, pharmacy items. Use "gold" for gold, silver, jewellery, bullion, coins, bars, rings, chains. Keep the metal word in "product" (e.g. "silver 999", not "gold"). If the product does not fit any of these three, use the closest match.
- "location": the city or area mentioned, or "unknown" if not specified (string)
- "intent": one of "cheapest", "fastest", "best_value", "nearest" — infer from context (string)
- "urgency": one of "immediate", "1-2 days", "10 days", "no rush" — infer from context and default to "immediate" if missing
- "quantity": the quantity the user asked for INCLUDING its unit, e.g. "10 pieces", "1 litre", "2 dozen", "100 gram", "5 kg". Use null if no quantity was specified (string or null)
- "gold_form": for gold-category queries ONLY, classify the form: "bullion" (investment-grade bars/coins/biscuits, or a plain gold/silver rate ask), "jewellery" (rings, chains, necklaces, bangles, earrings, mangalsutra, pendants), "scrap" (old/scrap gold, casting, ash/bhasma, refining waste, dust, polish sweeps, filings), or "other" (any other gold-related ask). Use null for non-gold categories.
- "descriptors": style/quality adjectives that describe the product but do NOT define its type, e.g. "fancy", "premium", "designer", "stylish", "latest", "good quality". Keep them OUT of "product". Type-defining modifiers stay in "product": "ceiling fan", "dslr camera", "bluetooth speaker", "travel bags" are all product phrases, not descriptors. Use [] when none.
- "search_specs": variant specs like capacity/size/model — not brand, not quantity, not location. Use [] when none.
- "raw_query": the original query repeated verbatim (string)

Also normalize the product phrase to common catalog wording: use the standard commercial form of the noun ("traveling bags" -> "travel bags", "specs" -> "spectacles"). Keep brand spelling in the brand field.

Examples:
User: "I need cheap tomatoes in Rajkot"
Output: {"product": "tomatoes", "category": "electronics", "location": "Rajkot", "intent": "cheapest", "urgency": "immediate", "quantity": null, "gold_form": null, "descriptors": [], "raw_query": "I need cheap tomatoes in Rajkot"}

User: "fastest delivery for iPhone 15 pro max"
Output: {"product": "iPhone 15 Pro Max", "category": "electronics", "location": "unknown", "intent": "fastest", "urgency": "immediate", "quantity": null, "gold_form": null, "descriptors": [], "raw_query": "fastest delivery for iPhone 15 pro max"}

User: "iPhone C type charger"
Output: {"product": "iPhone USB-C charger", "category": "electronics", "location": "unknown", "intent": "best_value", "urgency": "immediate", "quantity": null, "gold_form": null, "descriptors": [], "raw_query": "iPhone C type charger"}

User: "paracetamol 500mg tablets"
Output: {"product": "Paracetamol 500mg tablets", "category": "medicine", "location": "unknown", "intent": "cheapest", "urgency": "immediate", "quantity": null, "gold_form": null, "descriptors": [], "raw_query": "paracetamol 500mg tablets"}

User: "10 tupperware 1 litre bottles bulk"
Output: {"product": "water bottles", "brand": "Tupperware", "brand_preference": "specific", "category": "electronics", "location": "unknown", "intent": "cheapest", "urgency": "immediate", "quantity": "10 pieces", "gold_form": null, "descriptors": [], "search_specs": ["1 litre"], "raw_query": "10 tupperware 1 litre bottles bulk"}

User: "fancy travel bags in Jaipur"
Output: {"product": "travel bags", "category": "electronics", "location": "Jaipur", "intent": "best_value", "urgency": "immediate", "quantity": null, "gold_form": null, "descriptors": ["fancy"], "raw_query": "fancy travel bags in Jaipur"}

User: "premium designer ceiling fan"
Output: {"product": "ceiling fan", "category": "electronics", "location": "unknown", "intent": "best_value", "urgency": "immediate", "quantity": null, "gold_form": null, "descriptors": ["premium", "designer"], "raw_query": "premium designer ceiling fan"}

User: "24K gold coin 10 grams"
Output: {"product": "24K gold coin 10g", "category": "gold", "location": "unknown", "intent": "cheapest", "urgency": "immediate", "quantity": "10 gram", "gold_form": "bullion", "descriptors": [], "raw_query": "24K gold coin 10 grams"}

User: "Silver 999 in Ahmedabad"
Output: {"product": "silver 999", "category": "gold", "location": "Ahmedabad", "intent": "cheapest", "urgency": "immediate", "quantity": null, "gold_form": "bullion", "descriptors": [], "raw_query": "Silver 999 in Ahmedabad"}

User: "gold rate today in Rajkot"
Output: {"product": "999 gold", "category": "gold", "location": "Rajkot", "intent": "cheapest", "urgency": "immediate", "quantity": null, "gold_form": "bullion", "descriptors": [], "raw_query": "gold rate today in Rajkot"}

User: "price for casting ash gold recovery"
Output: {"product": "gold casting ash", "category": "gold", "location": "unknown", "intent": "best_value", "urgency": "immediate", "quantity": null, "gold_form": "scrap", "descriptors": [], "raw_query": "price for casting ash gold recovery"}

User: "22k gold chain 15g"
Output: {"product": "22K gold chain 15g", "category": "gold", "location": "unknown", "intent": "best_value", "urgency": "immediate", "quantity": "15 gram", "gold_form": "jewellery", "descriptors": [], "raw_query": "22k gold chain 15g"}

Return ONLY the JSON object, no markdown, no explanation.
""".strip()

PRECISION_SYSTEM_PROMPT_BASE = """
You are a shopping intake assistant. Decide whether the user's current request is precise enough to safely start product search.

Return ONLY valid JSON with these exact fields:
- "refined_product": a cleaned canonical product name based on the user's latest context
- "precise_enough": boolean
- "missing_attributes": a short list of the important missing attributes still needed before search
- "follow_up_questions": 1 to 4 concise questions that would make the request search-ready

Rules:
- Be strict. If the request could match multiple materially different products, set "precise_enough" to false.
- Tailor the questions to the product. Do not ask irrelevant things like color for paracetamol.
- Ask at most 2 focused follow-up questions unless the request is extremely ambiguous.
- If the user says "any good brand" or is brand-flexible, do not keep asking for a brand. Suggest up to 2 strong brands for that category and continue gathering the remaining important attribute.
- Do not repeat already answered questions.
- If enough detail is already present to identify a real listing confidently, set "precise_enough" to true and return no follow-up questions.
- IMPORTANT: If the user explicitly states no preference for an attribute (e.g., "any brand is fine", "no specific brand", "doesn't matter", "flexible on color"), treat that attribute as RESOLVED, not missing. Do NOT include it in missing_attributes or ask about it in follow_up_questions.
- If a list of declined attributes is provided, those are attributes the user already said they have no preference for. Exclude them entirely from missing_attributes and follow_up_questions.
""".strip()

ELECTRONICS_PRECISION_PROMPT = """
Category-specific guidance: electronics.

Treat these as high-signal attributes in priority order:
1. Product type and compatibility
2. Brand or acceptable brands
3. Model / variant / storage / size / wattage, depending on the item
4. Budget only if the product is still broad

Examples:
- Phones: brand + model + storage/variant
- Chargers: brand or compatible device + charger type + wattage if relevant
- Earbuds/headphones: brand or acceptable brands + model or important feature (ANC, battery, fit)

If the user is flexible on brand, recommend 2 strong brands and ask only the next most important unresolved attribute.
Ignore cosmetic attributes like color unless they materially affect the listing.
""".strip()

MEDICINE_PRECISION_PROMPT = """
Category-specific guidance: medicines.

Treat these as high-signal attributes in priority order:
1. Medicine / molecule / brand
2. Strength (for example 500 mg, 650 mg)
3. Dosage form (tablet, capsule, syrup, ointment, inhaler)
4. Quantity and whether substitutes are acceptable

Do not ask irrelevant consumer-electronics style questions.
If the user is flexible on brand, suggest up to 2 common brands or say you can search generics, then ask only the next missing medical attribute.
If the request already has medicine name + strength or an exact well-known brand, do not over-question.
""".strip()

GOLD_PRECISION_PROMPT = """
Category-specific guidance: gold products.

Treat these as high-signal attributes in priority order:
1. Product type (coin, bar, ring, chain, earrings, bracelet, mangalsutra, necklace, bullion)
2. Purity / karat where relevant (24K for coins/bars, 22K/18K for jewellery)
3. Weight or budget range if available
4. Preferred jeweller only if the user has one

Do not ask electronics or medicine questions.
If the user says any good jeweller/brand is fine, suggest up to 2 trusted jewellers and continue.
If the request has product type + purity or product type + budget/weight, it is usually precise enough to search.
""".strip()


@dataclass(frozen=True)
class CategoryIntakePromptConfig:
    category: str
    prompt: str
    intro: str


CATEGORY_PROMPT_CONFIGS: dict[str, CategoryIntakePromptConfig] = {
    "electronics": CategoryIntakePromptConfig(
        category="electronics",
        prompt=ELECTRONICS_PRECISION_PROMPT,
        intro="Lock the exact electronics item before searching.",
    ),
    "medicine": CategoryIntakePromptConfig(
        category="medicine",
        prompt=MEDICINE_PRECISION_PROMPT,
        intro="Lock the exact medicine and dosage before searching.",
    ),
    "gold": CategoryIntakePromptConfig(
        category="gold",
        prompt=GOLD_PRECISION_PROMPT,
        intro="Lock the gold product type and purity before searching.",
    ),
}

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "groceries": (
        "tomato",
        "potato",
        "onion",
        "vegetable",
        "fruit",
        "milk",
        "rice",
        "atta",
        "paneer",
        "bread",
        "dal",
        "oil",
        "grocery",
    ),
    "electronics": (
        "iphone",
        "phone",
        "laptop",
        "tv",
        "tablet",
        "earbuds",
        "earphone",
        "headphone",
        "speaker",
        "camera",
        "charger",
        "monitor",
        "toothbrush",
        "trimmer",
        "mixer",
        "grinder",
        "geyser",
        "heater",
        "printer",
        "boat",
        "noise",
        "jbl",
    ),
    "clothing": (
        "shirt",
        "jeans",
        "dress",
        "shoe",
        "jacket",
        "kurta",
        "tshirt",
        "t-shirt",
        "saree",
        "hoodie",
        "trouser",
        "blazer",
    ),
    "medicine": (
        "medicine",
        "tablet",
        "capsule",
        "syrup",
        "pharmacy",
        "paracetamol",
        "chemist",
        "dolo",
        "crocin",
        "ointment",
        "inhaler",
    ),
    "gold": (
        "gold",
        "silver",
        "jewellery",
        "jewelry",
        "jeweller",
        "jeweler",
        "jewellers",
        "jewelers",
        "bullion",
        "coin",
        "bar",
        "ring",
        "chain",
        "earring",
        "bracelet",
        "necklace",
        "mangalsutra",
        "bangle",
        "tanishq",
        "malabar",
        "kalyan",
        "pc jeweller",
    ),
    "hardware": (
        "drill",
        "paint",
        "pipe",
        "screw",
        "hammer",
        "tool",
        "plywood",
        "cement",
        "wire",
        "switch",
        "nail",
        "tap",
    ),
    "services": (
        "repair",
        "service",
        "cleaning",
        "plumber",
        "electrician",
        "salon",
        "spa",
        "doctor",
        "dentist",
        "carpenter",
        "installation",
        "massage",
    ),
}

VAGUE_PRODUCT_TERMS = {
    "iron",
    "phone",
    "mobile",
    "iphone",
    "laptop",
    "tv",
    "television",
    "tablet",
    "earphones",
    "earbuds",
    "headphones",
    "speaker",
    "charger",
    "monitor",
    "camera",
    "printer",
    "watch",
    "fridge",
    "refrigerator",
    "ac",
    "air conditioner",
    "washing machine",
    "medicine",
    "tablet",
    "capsule",
    "syrup",
    "paracetamol",
    "gold",
    "jewellery",
    "jewelry",
    "coin",
    "bar",
    "ring",
    "chain",
}

BRAND_HINTS: dict[str, dict[str, list[str]]] = {
    "electronics": {
        "earbuds": ["boAt", "Noise"],
        "earphones": ["boAt", "JBL"],
        "headphones": ["JBL", "Sony"],
        "charger": ["OnePlus", "Spigen"],
        "phone": ["OnePlus", "Samsung"],
        "mobile": ["OnePlus", "Samsung"],
        "laptop": ["HP", "Lenovo"],
        "tv": ["Samsung", "LG"],
        "_default": ["Samsung", "OnePlus"],
    },
    "medicine": {
        "paracetamol": ["Dolo", "Crocin"],
        "vitamin": ["Supradyn", "Revital"],
        "pain relief": ["Combiflam", "Crocin"],
        "cough": ["Benadryl", "Ascoril"],
        "_default": ["Dolo", "Calpol"],
    },
    "gold": {
        "coin": ["Tanishq", "Malabar Gold"],
        "bar": ["Malabar Gold", "Kalyan Jewellers"],
        "ring": ["Tanishq", "Kalyan Jewellers"],
        "chain": ["Malabar Gold", "Tanishq"],
        "jewellery": ["Tanishq", "Malabar Gold"],
        "jewelry": ["Tanishq", "Malabar Gold"],
        "_default": ["Tanishq", "Malabar Gold"],
    },
}

KNOWN_BRANDS = {
    "apple",
    "samsung",
    "oneplus",
    "pixel",
    "redmi",
    "xiaomi",
    "vivo",
    "oppo",
    "realme",
    "boat",
    "jbl",
    "sony",
    "philips",
    "bajaj",
    "havells",
    "usha",
    "dolo",
    "crocin",
    "calpol",
    "supradyn",
    "revital",
    "spigen",
    "anker",
    "tanishq",
    "malabar",
    "kalyan",
    "pc jeweller",
    "joyalukkas",
}


def is_supported_category(category: str | None) -> bool:
    return (category or "").strip().lower() in SUPPORTED_CATEGORIES


def normalize_category(category: str | None) -> str:
    """Map any LLM-returned category to one of the three supported categories."""
    raw = (category or "").strip().lower()
    if raw in SUPPORTED_CATEGORIES:
        return raw
    electronics_aliases = {
        "accessories", "mobile_accessories", "mobile accessories", "phone accessories",
        "appliances", "home_appliances", "home appliances", "gadgets", "tech",
        "computers", "computer", "audio", "wearables", "cameras", "printers",
        "personal care", "personal_care", "chargers", "power", "electrical",
        "hardware", "tools", "groceries", "clothing", "services",
    }
    if raw in electronics_aliases:
        return "electronics"
    medicine_aliases = {
        "medicines", "pharmacy", "medical", "health", "healthcare",
        "pharmaceutical", "drug", "drugs", "chemist",
    }
    if raw in medicine_aliases:
        return "medicine"
    gold_aliases = {
        "jewellery", "jewelry", "jewellers", "jewelers", "bullion",
        "ornaments", "precious metals",
    }
    if raw in gold_aliases:
        return "gold"
    return "electronics"


def unsupported_category_message() -> str:
    return SUPPORTED_CATEGORY_MESSAGE


def category_prompt_config(category: str | None) -> CategoryIntakePromptConfig | None:
    return CATEGORY_PROMPT_CONFIGS.get((category or "").strip().lower())


def category_prompt_for_product(product: str | None, category: str | None) -> str | None:
    """Build a dynamic precision prompt from the product registry."""
    if not category:
        return None
    spec = classify_product_type(product or "", category)
    return build_precision_prompt(spec)


def infer_category(raw_query: str) -> str:
    lowered = raw_query.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return category
    return "groceries" if any(token in lowered for token in ("kg", "fresh", "near me")) else "electronics"


def infer_intent(raw_query: str) -> str:
    lowered = raw_query.lower()
    if any(word in lowered for word in ("cheap", "cheapest", "lowest", "budget")):
        return "cheapest"
    if any(word in lowered for word in ("fast", "fastest", "urgent", "quick")):
        return "fastest"
    if any(word in lowered for word in ("near", "nearby", "closest", "nearest")):
        return "nearest"
    return "best_value"


def infer_location(raw_query: str) -> str:
    if "near me" in raw_query.lower():
        return "near me"
    for trigger in ("in", "near", "at"):
        if f" {trigger} " in raw_query.lower():
            return raw_query.lower().split(f" {trigger} ", 1)[1].strip().title() or "unknown"
    return "unknown"


def infer_product(raw_query: str) -> str:
    location_stripped = re.split(r"\b(?:near me|near|in|at)\b", raw_query, maxsplit=1, flags=re.IGNORECASE)[0]
    cleaned = re.sub(
        (
            r"\b("
            r"cheapest|cheap|fastest|best|best value|near|near me|in|find|get|need|buy|delivery|for|"
            r"want|looking|searching|purchase|order"
            r")\b"
        ),
        "",
        location_stripped,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\b(i|me|myself|to|a|an)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,")
    return cleaned or raw_query.strip()


def infer_urgency(raw_query: str) -> UrgencyOption:
    lowered = raw_query.lower()
    if any(token in lowered for token in ("immediate", "right now", "asap", "today", "urgent")):
        return "immediate"
    if any(token in lowered for token in ("1-2 days", "1 / 2 days", "1 or 2 days", "tomorrow", "day after")):
        return "1-2 days"
    if any(token in lowered for token in ("10 days", "ten days", "next week", "week or so")):
        return "10 days"
    if any(token in lowered for token in ("no rush", "flexible", "anytime", "whenever")):
        return "no rush"
    return "immediate"


# Units we recognize in free-form buyer text. Order matters only for the
# alternation; the regex matches the unit token that follows a number.
_QUANTITY_UNIT_PATTERN = (
    r"(?:kg|kilo(?:gram)?s?|grams?|gm|"
    r"litres?|liters?|ltr|ml|"
    r"dozens?|"
    r"pieces?|pcs?|pc|units?|nos?|"
    r"pack(?:s|ets)?|boxe?s?|sets?|pairs?|"
    r"tablets?|tabs?|capsules?|caps?|strips?|bottles?|tubes?)"
)
_QUANTITY_RE = re.compile(
    rf"\b(\d+(?:\.\d+)?)\s*({_QUANTITY_UNIT_PATTERN})\b",
    flags=re.IGNORECASE,
)


def infer_quantity(raw_query: str) -> str | None:
    """Best-effort extraction of a 'number + unit' quantity from free text.

    Returns a normalized phrase like '10 pieces' or '1 litre', or None when no
    explicit quantity is present. Used as a fallback when the LLM structurer is
    unavailable so the voice agent still knows how much the buyer wants.
    """
    match = _QUANTITY_RE.search(raw_query or "")
    if not match:
        return None
    number = match.group(1)
    unit = match.group(2).lower()
    return f"{number} {unit}".strip()


def product_mentions_brand(product: str | None) -> bool:
    normalized = (product or "").lower()
    return any(brand in normalized for brand in KNOWN_BRANDS)


def suggest_top_brands(product: str | None, category: str | None) -> list[str]:
    normalized = (product or "").lower()
    category_hints = BRAND_HINTS.get((category or "").strip().lower())
    if not category_hints:
        return []
    for token, brands in category_hints.items():
        if token == "_default":
            continue
        if token in normalized:
            return brands[:2]
    return category_hints.get("_default", [])[:2]


def _normalize_product_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" ,")


def _fallback_follow_up_questions(product: str, category: str | None) -> list[str]:
    normalized = product.lower()

    if "iron" in normalized:
        return [
            "Do you need a dry iron, steam iron, garment steamer, or ironing press?",
            "Any preferred brand, like Philips, Bajaj, Havells, or Usha?",
            "What budget range should I stay within?",
            "Any must-have spec like wattage, soleplate type, or travel use?",
        ]

    if category == "medicine" or any(term in normalized for term in ("paracetamol", "tablet", "capsule", "syrup")):
        return [
            "Which medicine or brand do you need exactly?",
            "What strength or dosage should I look for, like 500 mg or 650 mg?",
            "How much quantity do you need?",
            "Are substitutes okay, or do you need one exact brand?",
        ]

    if category == "gold" or any(term in normalized for term in ("gold", "jewellery", "jewelry", "bullion")):
        return [
            "What type of gold product should I search for, like coin, bar, ring, chain, or earrings?",
            "What purity should I look for, like 24K, 22K, or 18K?",
            "What weight or budget range should I stay within?",
            "Any preferred jeweller, or should I check trusted nearby jewellers?",
        ]

    if any(term in normalized for term in ("iphone", "phone", "mobile", "smartphone")):
        return [
            "Which exact brand and model do you want?",
            "What storage or RAM variant should I search for?",
            "Any preferred color?",
            "What budget range should I stay within?",
        ]

    return [
        "Which exact brand or model should I search for?",
        "What key specification or variant matters most for this item?",
        "What budget range should I stay within?",
    ]


def _fallback_precision_assessment(
    raw_query: str,
    category: str | None = None,
    current_product: str | None = None,
    declined_attributes: list[str] | None = None,
) -> ProductPrecisionAssessment:
    product = _normalize_product_text(current_product or infer_product(raw_query))
    normalized = product.lower()
    tokens = re.findall(r"[a-z0-9]+", normalized)
    has_numbers = bool(re.search(r"\d", normalized))
    has_variant = bool(
        re.search(
            r"\b(pro|max|plus|mini|ultra|air|steam|dry|press|automatic|semi|500mg|650mg|128gb|256gb|512gb|1tb)\b",
            normalized,
        )
    )
    has_brand = product_mentions_brand(product)

    declined_lower = {a.lower() for a in (declined_attributes or [])}
    precise_enough = True
    missing_attributes: list[str] = []
    suggested_brands = suggest_top_brands(product, category)
    brand_flexible = any(
        phrase in raw_query.lower()
        for phrase in ("any good brand", "any brand", "good brand", "brand flexible", "open brand")
    )

    if not normalized or normalized in VAGUE_PRODUCT_TERMS or len(tokens) <= 1:
        precise_enough = False

    if category == "electronics":
        if "iron" in normalized and not any(term in normalized for term in ("steam", "dry", "press", "steamer")):
            precise_enough = False
            if "product type" not in declined_lower:
                missing_attributes.append("product type")
        if not (has_brand or has_numbers or has_variant) and len(tokens) <= 2:
            precise_enough = False
        if not has_brand and not brand_flexible:
            if "brand" not in declined_lower:
                missing_attributes.append("brand")
        if not (has_numbers or has_variant) and "specification or variant" not in declined_lower:
            missing_attributes.append("specification or variant")
    elif category == "medicine":
        if not has_numbers:
            precise_enough = False
            if "strength" not in declined_lower:
                missing_attributes.append("strength")
        if len(tokens) <= 2:
            precise_enough = False
        if "quantity" not in declined_lower:
            missing_attributes.append("quantity")
        has_dosage_form = any(term in normalized for term in ("tablet", "capsule", "syrup", "ointment", "inhaler"))
        if not has_brand and not has_dosage_form and "dosage form" not in declined_lower:
            precise_enough = False
            missing_attributes.append("dosage form")
    elif category == "gold":
        has_gold_type = any(
            term in normalized
            for term in (
                "coin",
                "bar",
                "ring",
                "chain",
                "earring",
                "earrings",
                "bracelet",
                "necklace",
                "mangalsutra",
                "bangle",
                "jewellery",
                "jewelry",
                "bullion",
            )
        )
        has_purity = bool(re.search(r"\b(?:24|22|18)\s?k\b", normalized)) or "916" in normalized
        has_weight_or_budget = bool(re.search(r"\b\d+(?:\.\d+)?\s?(?:g|gm|gram|grams|kg|lakh|lac|k|rs|inr)\b", normalized))
        if not has_gold_type:
            precise_enough = False
            if "product type" not in declined_lower:
                missing_attributes.append("product type")
        if not (has_purity or has_weight_or_budget):
            precise_enough = False
            if "purity or weight" not in declined_lower:
                missing_attributes.append("purity or weight")
    else:
        if len(tokens) <= 1:
            precise_enough = False
            if "exact product name" not in declined_lower:
                missing_attributes.append("exact product name")

    missing_attributes = list(dict.fromkeys(attr for attr in missing_attributes if attr))
    # If all would-be-missing attributes were declined, treat as precise enough
    if not missing_attributes and not precise_enough:
        precise_enough = True
    follow_up_questions = [] if precise_enough else _fallback_follow_up_questions(product or raw_query, category)
    if brand_flexible and suggested_brands:
        if category == "electronics":
            follow_up_questions = [
                f"If you're open on brand, the strongest options here are {suggested_brands[0]} and {suggested_brands[1]}. Which specification or variant should I lock in next?",
                *follow_up_questions,
            ]
        elif category == "medicine":
            follow_up_questions = [
                f"If you're open on brand, common options here are {suggested_brands[0]} and {suggested_brands[1]}. What strength should I look for?",
                *follow_up_questions,
            ]
        elif category == "gold":
            follow_up_questions = [
                f"If you're open on jeweller, trusted options here are {suggested_brands[0]} and {suggested_brands[1]}. What purity or weight should I lock?",
                *follow_up_questions,
            ]
    if declined_lower and follow_up_questions:
        follow_up_questions = [
            q for q in follow_up_questions
            if not any(d in q.lower() for d in declined_lower)
        ]
    follow_up_questions = follow_up_questions[:2]

    return ProductPrecisionAssessment(
        refined_product=product or raw_query.strip(),
        precise_enough=precise_enough,
        missing_attributes=missing_attributes,
        follow_up_questions=follow_up_questions,
    )


async def analyze_product_precision(
    raw_query: str,
    category: str | None = None,
    current_product: str | None = None,
    declined_attributes: list[str] | None = None,
) -> ProductPrecisionAssessment:
    if not settings.openai_api_key:
        return _fallback_precision_assessment(raw_query, category, current_product, declined_attributes)

    client = _get_openai_client()
    declined_note = ""
    if declined_attributes:
        declined_note = f"\nUser has explicitly declined preference for: {', '.join(declined_attributes)}. Treat these as resolved."
    context = (
        f"Raw user context: {raw_query}\n"
        f"Current category: {category or 'unknown'}\n"
        f"Current product candidate: {current_product or 'unknown'}"
        f"{declined_note}"
    )
    # Use dynamic registry-driven prompt when available, fall back to static config.
    dynamic_prompt = category_prompt_for_product(current_product, category)
    category_config = category_prompt_config(category)
    system_prompt = PRECISION_SYSTEM_PROMPT_BASE
    if dynamic_prompt:
        system_prompt = f"{PRECISION_SYSTEM_PROMPT_BASE}\n\n{dynamic_prompt}"
    elif category_config:
        system_prompt = f"{PRECISION_SYSTEM_PROMPT_BASE}\n\n{category_config.prompt}"

    try:
        t0 = time.monotonic()
        response = await client.responses.parse(
            model=settings.openai_model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context},
            ],
            text_format=ProductPrecisionAssessment,
            temperature=0,
        )
        elapsed = time.monotonic() - t0
        assessment = response.output_parsed
        if assessment is None:
            raise ValueError("OpenAI returned no parsed ProductPrecisionAssessment.")
        logger.info("Product precision analysis completed in %.2fs precise=%s", elapsed, assessment.precise_enough)
        assessment.refined_product = _normalize_product_text(assessment.refined_product or current_product or "")
        if not assessment.refined_product:
            assessment.refined_product = _normalize_product_text(current_product or infer_product(raw_query))
        return assessment
    except Exception as exc:  # pragma: no cover - external API
        logger.warning("Product precision analysis failed, using fallback: %s", exc)
        return _fallback_precision_assessment(raw_query, category, current_product, declined_attributes)


def _best_effort_structure(raw_query: str) -> StructuredQuery:
    return StructuredQuery(
        product=infer_product(raw_query),
        category=normalize_category(infer_category(raw_query)),
        location=infer_location(raw_query),
        intent=infer_intent(raw_query),
        urgency=infer_urgency(raw_query),
        quantity=infer_quantity(raw_query),
        raw_query=raw_query,
    )


async def structure_query(raw_query: str) -> StructuredQuery:
    from app.services import brand_search

    logger.info("Structuring query: %s", raw_query)
    if not settings.openai_api_key:
        logger.info("OpenAI API key missing; using heuristic query structurer.")
        structured = brand_search.ensure_brand_on_query(_best_effort_structure(raw_query))
        return await enrich_query_route(structured)

    client = _get_openai_client()

    try:
        t0 = time.monotonic()
        response = await client.responses.parse(
            model=settings.openai_model,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": raw_query},
            ],
            text_format=StructuredQuery,
            temperature=0,
        )
        elapsed = time.monotonic() - t0
        structured = response.output_parsed
        if structured is None:
            raise ValueError("OpenAI returned no parsed StructuredQuery.")
        structured.raw_query = raw_query
        structured.category = normalize_category(structured.category)
        if not structured.quantity:
            structured.quantity = infer_quantity(raw_query)
        structured = brand_search.ensure_brand_on_query(structured)
        structured = await enrich_query_route(structured)
        logger.info("Structured query generated successfully in %.2fs", elapsed)
        return structured
    except Exception as exc:  # pragma: no cover - depends on external API
        logger.warning("Query structurer failed, falling back to heuristic: %s", exc)
        structured = brand_search.ensure_brand_on_query(_best_effort_structure(raw_query))
        return await enrich_query_route(structured)


WHATSAPP_INTAKE_ROUTER_SYSTEM_PROMPT = """
You classify the latest WhatsApp message for a shopping assistant (India, electronics, medicines, and gold products).

Return JSON matching the schema. Rules:
- Set is_conversational_chaff=true when the latest message does not add or change product, city, pincode, or search constraints
  (e.g. thanks, ok, bye, yes/no alone, generic chit-chat, polite brush-offs with no new shopping detail).
- When is_conversational_chaff=true, effective_search_text MUST be empty.
- When is_conversational_chaff=false, effective_search_text MUST be one line the backend should run query structuring on.
- If the latest message clearly starts a NEW search (new product), use that line alone—do not keep unrelated product/location
  from older context (e.g. drop stale iPhone/Rajkot when the user asks for a Samsung TV).
- If the latest message only adds city, area, or 6-digit pincode and prior context had a product,
  merge into one line (e.g. "Samsung 32 inch LED TV in Mumbai" or "Dolo 650 Ahmedabad 380001").
- Use English or mixed Hinglish as the user wrote; keep it compact.
""".strip()


def _truncate_router_context(text: str, max_len: int = 600) -> str:
    t = text.strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 3] + "..."


async def route_whatsapp_intake(
    *,
    latest_message: str,
    previous_raw_query: str,
    previous_product: str | None,
    previous_location: str | None = None,
) -> WhatsAppIntakeRouterResult | None:
    """Route WhatsApp bubbles; returns None to let callers use heuristic combine logic."""

    if not settings.openai_api_key or not settings.whatsapp_intake_router_enabled:
        return None

    lm = latest_message.strip()
    if not lm:
        return None

    model = (settings.openai_whatsapp_router_model or settings.openai_model).strip()

    user_block = "\n".join(
        [
            f"Latest message: {_truncate_router_context(lm, 800)}",
            f"Previous raw_query (truncated): {_truncate_router_context(previous_raw_query)}",
            f"Previous product (if any): {previous_product or 'none'}",
            f"Previous location (if any): {previous_location or 'unknown'}",
        ]
    )

    client = _get_openai_client()
    try:
        t0 = time.monotonic()
        response = await client.responses.parse(
            model=model,
            input=[
                {"role": "system", "content": WHATSAPP_INTAKE_ROUTER_SYSTEM_PROMPT},
                {"role": "user", "content": user_block},
            ],
            text_format=WhatsAppIntakeRouterResult,
            temperature=0,
        )
        elapsed = time.monotonic() - t0
        result = response.output_parsed
        if result is None:
            raise ValueError("OpenAI returned no parsed WhatsAppIntakeRouterResult.")
        if result.is_conversational_chaff:
            result = result.model_copy(update={"effective_search_text": ""})
        else:
            text = (result.effective_search_text or "").strip()
            if not text:
                result = result.model_copy(update={"effective_search_text": lm})
        logger.info(
            "WhatsApp intake router: chaff=%s effective=%r elapsed=%.2fs",
            result.is_conversational_chaff,
            (result.effective_search_text[:120] + "…") if len(result.effective_search_text) > 120 else result.effective_search_text,
            elapsed,
        )
        return result
    except Exception as exc:  # pragma: no cover - external API
        logger.warning("WhatsApp intake router failed: %s", exc)
        return None
