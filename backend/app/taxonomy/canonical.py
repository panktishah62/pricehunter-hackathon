from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

TAXONOMY_VERSION = "canonical-taxonomy-v2-2026-08-14"

TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class TaxonomyNode:
    node_id: str
    display_name: str
    node_type: str
    parent_id: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaxonomyMatch:
    node_id: str
    taxonomy_path: tuple[str, ...]
    taxonomy_path_labels: tuple[str, ...]
    root_id: str
    canonical_category_id: str
    canonical_subcategory_id: str | None
    confidence: float
    reason: str
    needs_review: bool
    source: str = "rules"
    version: str = TAXONOMY_VERSION


NODES: tuple[TaxonomyNode, ...] = (
    TaxonomyNode("pharma", "Pharma", "root", aliases=("medicine", "medical", "drug", "pharmaceutical")),
    TaxonomyNode("pharma_tablets", "Tablets", "subcategory", "pharma", ("tablet", "tablets", "tab")),
    TaxonomyNode("pharma_capsules", "Capsules", "subcategory", "pharma", ("capsule", "capsules", "cap")),
    TaxonomyNode("pharma_syrups", "Syrups", "subcategory", "pharma", ("syrup", "suspension", "oral liquid")),
    TaxonomyNode("pharma_drops_sprays", "Drops & Sprays", "subcategory", "pharma", ("drop", "drops", "eye drop", "nasal spray")),
    TaxonomyNode("pharma_injections", "Injections", "subcategory", "pharma", ("injection", "injectable", "vial", "ampoule")),
    TaxonomyNode("pharma_creams_ointments", "Creams & Ointments", "subcategory", "pharma", ("cream", "ointment", "gel", "lotion")),
    TaxonomyNode("pharma_antibiotics", "Antibiotics", "product_group", "pharma", ("antibiotic", "antibacterial")),
    TaxonomyNode("pharma_anti_cancer", "Anti-Cancer Medicines", "product_group", "pharma", ("anti cancer", "oncology", "anticancer")),
    TaxonomyNode("pharma_api_raw_materials", "APIs & Pharma Raw Materials", "product_group", "pharma", ("api", "active pharmaceutical ingredient", "pharma raw material", "intermediate")),
    TaxonomyNode("pharma_ayurvedic_herbal", "Ayurvedic & Herbal Medicines", "product_group", "pharma", ("ayurvedic", "herbal medicine", "herbal product")),
    TaxonomyNode("pharma_anti_diabetic", "Anti-Diabetic Medicines", "product_group", "pharma", ("anti diabetic", "metformin", "glimepiride")),
    TaxonomyNode("pharma_erectile_dysfunction", "Erectile Dysfunction Medicines", "product_group", "pharma", ("erectile", "sildenafil", "tadalafil", "vardenafil")),
    TaxonomyNode("pharma_generic", "General Pharma Products", "subcategory", "pharma"),
    TaxonomyNode("medical_devices", "Medical Devices", "root", aliases=("medical device", "surgical equipment", "hospital equipment")),
    TaxonomyNode("medical_devices_diagnostic", "Diagnostic Equipment", "subcategory", "medical_devices", ("diagnostic", "monitor", "scanner")),
    TaxonomyNode("medical_devices_surgical", "Surgical Equipment", "subcategory", "medical_devices", ("surgical", "operation theatre", "ot light", "laryngoscope")),
    TaxonomyNode("medical_devices_hospital_furniture", "Hospital Furniture", "subcategory", "medical_devices", ("hospital furniture", "hospital bed", "fowler", "trolley", "stretcher")),
    TaxonomyNode("medical_devices_masks_consumables", "Masks & Medical Consumables", "subcategory", "medical_devices", ("mask", "glove", "nebulizer kit", "syringe")),
    TaxonomyNode("electronics", "Electronics", "root", aliases=("electronic", "electrical", "gadget")),
    TaxonomyNode("electronics_mobile_accessories", "Mobile Accessories", "subcategory", "electronics", ("mobile accessories", "phone case", "charger", "earphone")),
    TaxonomyNode("electronics_computer_accessories", "Computer Accessories", "subcategory", "electronics", ("computer accessories", "keyboard", "mouse", "ssd", "ram")),
    TaxonomyNode("electronics_mobile_devices", "Mobile Phones & Devices", "subcategory", "electronics", ("mobile phone", "smartphone", "feature phone")),
    TaxonomyNode("electronics_cctv_security", "CCTV & Security Systems", "subcategory", "electronics", ("cctv", "security camera", "dvr", "surveillance")),
    TaxonomyNode("electronics_access_control", "Access Control Systems", "subcategory", "electronics", ("access control", "biometric", "door phone")),
    TaxonomyNode("electronics_fire_safety_security", "Fire Safety & Security Systems", "subcategory", "electronics", ("fire alarm", "smoke detector", "security safe", "boom barrier")),
    TaxonomyNode("electronics_lighting", "Lighting", "subcategory", "electronics", ("led", "light", "lamp", "bulb", "chandelier")),
    TaxonomyNode("electronics_audio", "Audio & Speakers", "subcategory", "electronics", ("speaker", "audio", "headphone", "earbud", "microphone", "mic", "headset")),
    TaxonomyNode("electronics_av_display", "AV Display & Projectors", "subcategory", "electronics", ("projector", "interactive flat panel", "display panel")),
    TaxonomyNode("electronics_optics", "Optics & Binoculars", "subcategory", "electronics", ("binocular", "binoculars", "telescope", "optics")),
    TaxonomyNode("electronics_wearables", "Wearables", "subcategory", "electronics", ("smart watch", "smartwatch", "smart band", "fitness band")),
    TaxonomyNode("electronics_lighters", "Electronic Lighters", "subcategory", "electronics", ("electronic lighter", "usb lighter", "cigarette lighter", "arc lighter")),
    TaxonomyNode("electronics_computers", "Computers & PC Hardware", "subcategory", "electronics", ("desktop computer", "graphics card", "laptop")),
    TaxonomyNode("electronics_barcode_pos", "Barcode & POS Equipment", "subcategory", "electronics", ("barcode printer", "barcode scanner", "barcode label")),
    TaxonomyNode("electronics_networking", "Networking & Telecom", "subcategory", "electronics", ("network switch", "router", "antenna", "epabx")),
    TaxonomyNode("electronics_cables_connectors", "Cables & Connectors", "subcategory", "electronics", ("hdmi cable", "usb cable", "connector", "cable")),
    TaxonomyNode("electronics_appliances", "Home Appliances", "subcategory", "electronics", ("home appliance", "air conditioner", "air cooler", "cooler", "deep freezer", "ceiling fan")),
    TaxonomyNode("electronics_printing_consumables", "Printing Consumables", "subcategory", "electronics", ("toner cartridge", "printer cartridge", "ink cartridge")),
    TaxonomyNode("electronics_storage_peripherals", "Storage & USB Peripherals", "subcategory", "electronics", ("pen drive", "pendrive", "usb hub", "storage device")),
    TaxonomyNode("electronics_personal_care", "Personal Care Electronics", "subcategory", "electronics", ("body massager", "personal care", "trimmer")),
    TaxonomyNode("electronics_power", "Power & UPS", "subcategory", "electronics", ("ups", "inverter", "power supply", "battery")),
    TaxonomyNode("electronics_adapters_converters", "Adapters & Converters", "subcategory", "electronics", ("adapter", "converter", "smps")),
    TaxonomyNode("electronics_solar_energy", "Solar & Renewable Energy", "subcategory", "electronics", ("solar panel", "solar inverter", "solar power", "solar water heater", "solar electric", "solar energy")),
    TaxonomyNode("electronics_components", "Electronic Components", "subcategory", "electronics", ("ic", "microcontroller", "sensor", "module", "diode", "resistor", "proximity switch")),
    TaxonomyNode("electronics_generic", "General Electronics", "subcategory", "electronics"),
    TaxonomyNode("home_kitchen", "Home & Kitchen", "root", aliases=("home", "kitchen", "decor")),
    TaxonomyNode("home_kitchen_kitchenware", "Kitchenware", "subcategory", "home_kitchen", ("kitchen", "lunch box", "straw", "bottle")),
    TaxonomyNode("home_kitchen_lighting_decor", "Decorative Lighting", "subcategory", "home_kitchen", ("decorative lamp", "table lamp", "candle holder")),
    TaxonomyNode("home_kitchen_water_purifiers", "Water Purifiers", "subcategory", "home_kitchen", ("water purifier", "ro purifier")),
    TaxonomyNode("home_kitchen_home_decor", "Home Decor", "subcategory", "home_kitchen", ("home decor", "decorative item")),
    TaxonomyNode("home_kitchen_clocks", "Clocks", "subcategory", "home_kitchen", ("wall clock", "table clock", "clock")),
    TaxonomyNode("home_kitchen_bathroom_accessories", "Bathroom Accessories", "subcategory", "home_kitchen", ("bathroom accessories", "bath fitting")),
    TaxonomyNode("furniture", "Furniture", "root", aliases=("furniture", "sideboard", "console")),
    TaxonomyNode("furniture_tables_seating", "Tables & Seating", "subcategory", "furniture", ("table", "chair", "stool", "sofa")),
    TaxonomyNode("beauty_personal_care", "Beauty & Personal Care", "root", aliases=("beauty", "cosmetics", "skin care", "hair care", "personal care")),
    TaxonomyNode("beauty_personal_care_skin_hair", "Skin & Hair Care", "subcategory", "beauty_personal_care", ("skin care", "hair care", "facial", "soap")),
    TaxonomyNode("beauty_personal_care_cosmetics", "Cosmetics", "subcategory", "beauty_personal_care", ("cosmetic", "cosmetics", "makeup", "beauty product")),
    TaxonomyNode("beauty_personal_care_sexual_wellness", "Sexual Wellness", "subcategory", "beauty_personal_care", ("sexual wellness", "sexual oil", "delay spray")),
    TaxonomyNode("sports_fitness", "Sports & Fitness", "root", aliases=("fitness", "sports", "gym")),
    TaxonomyNode("pet_supplies", "Pet Supplies", "root", aliases=("pet bowl", "pet supplies")),
    TaxonomyNode("cleaning_supplies", "Cleaning Supplies", "root", aliases=("cleaning brush", "cleaning supplies")),
    TaxonomyNode("cleaning_supplies_machines", "Cleaning Machines", "subcategory", "cleaning_supplies", ("vacuum cleaner", "floor cleaning machine", "pressure washer")),
    TaxonomyNode("cleaning_supplies_products", "Cleaning Products", "subcategory", "cleaning_supplies", ("cleaning product", "cleaning products", "cleaner")),
    TaxonomyNode("food_beverages", "Food & Beverages", "root", aliases=("food", "snacks", "pickle", "banana chips")),
    TaxonomyNode("media_entertainment", "Media & Entertainment", "root", aliases=("video cd", "music cd", "cd")),
    TaxonomyNode("gifts", "Corporate Gifts", "root", aliases=("corporate gift", "promotional gift")),
    TaxonomyNode("toys", "Toys & Kids Products", "root", aliases=("toy", "toys", "kids toy")),
    TaxonomyNode("decor_collectibles", "Decor & Collectibles", "root", aliases=("antique", "collectible", "medieval")),
    TaxonomyNode("office_supplies", "Office & POS Supplies", "root", aliases=("office supplies", "thermal paper")),
    TaxonomyNode("office_supplies_stationery", "Office Stationery", "subcategory", "office_supplies", ("office stationery", "stationery")),
    TaxonomyNode("office_supplies_printers", "Printers & Office Machines", "subcategory", "office_supplies", ("printer", "multifunction printer", "laser printer", "id card printer")),
    TaxonomyNode("packaging", "Packaging Materials", "root", aliases=("wooden box", "packing box", "export packing")),
    TaxonomyNode("bags_luggage", "Bags & Luggage", "root", aliases=("bag", "bags", "luggage")),
    TaxonomyNode("bags_luggage_travel", "Travel Bags", "subcategory", "bags_luggage", ("duffle", "backpack", "travel bag", "sling bag")),
    TaxonomyNode("automotive", "Automotive", "root", aliases=("automotive", "vehicle", "two wheeler", "car", "bike")),
    TaxonomyNode("automotive_two_wheeler_parts", "Two Wheeler Parts", "subcategory", "automotive", ("two wheeler", "motorcycle", "bike", "scooty")),
    TaxonomyNode("automotive_brake_clutch_parts", "Brake & Clutch Parts", "subcategory", "automotive", ("brake shoe", "brake cable", "clutch cable", "throttle cable")),
    TaxonomyNode("automotive_accessories", "Automotive Accessories", "subcategory", "automotive", ("car accessories", "automotive accessories", "automotive gasket", "speedometer cable", "disk pad")),
    TaxonomyNode("textiles", "Textiles", "root", aliases=("textile", "fabric", "cloth", "garment")),
    TaxonomyNode("textiles_fabrics", "Fabrics", "subcategory", "textiles", ("fabric", "cotton", "polyester", "cloth")),
    TaxonomyNode("textiles_apparel", "Apparel & Fashion", "subcategory", "textiles", ("t shirt", "jacket", "shoes", "sunglasses")),
    TaxonomyNode("industrial", "Industrial Supplies", "root", aliases=("industrial", "hardware", "machinery")),
    TaxonomyNode("industrial_material_handling", "Material Handling Equipment", "subcategory", "industrial", ("material handling", "trolley", "hoist", "lift")),
    TaxonomyNode("industrial_food_processing_equipment", "Food Processing Equipment", "subcategory", "industrial", ("food processing machine", "bakery equipment", "momo machine")),
    TaxonomyNode("industrial_pipes_valves", "Pipes & Valves", "subcategory", "industrial", ("pipe", "valve", "fitting")),
    TaxonomyNode("industrial_lab_testing", "Lab & Testing Instruments", "subcategory", "industrial", ("laboratory equipment", "laboratory instrument", "lab equipment")),
    TaxonomyNode("industrial_tools", "Hand Tools", "subcategory", "industrial", ("hand tools", "tool kit", "bearing")),
    TaxonomyNode("industrial_measurement_control", "Measurement & Control Instruments", "subcategory", "industrial", ("digital multimeter", "clamp meter", "measuring instrument", "temperature controller", "control panel")),
    TaxonomyNode("industrial_hvac", "HVAC & Air Conditioning", "subcategory", "industrial", ("hvac", "cassette ac", "vrf", "fan coil", "air conditioning")),
    TaxonomyNode("industrial_generators", "Generators", "subcategory", "industrial", ("generator", "dg set", "genset")),
    TaxonomyNode("industrial_pumps_motors", "Pumps & Motors", "subcategory", "industrial", ("pump", "submersible pump", "diaphragm pump", "motor")),
    TaxonomyNode("industrial_automation", "Industrial Automation & PLC", "subcategory", "industrial", ("plc", "programmable logic controller", "automation", "contactor")),
    TaxonomyNode("industrial_electrical_components", "Industrial Electrical Components", "subcategory", "industrial", ("modular switch", "circuit breaker", "relay", "electrical accessory", "electric motor", "power cable", "extension board")),
    TaxonomyNode("industrial_heaters", "Industrial Heaters", "subcategory", "industrial", ("industrial heater", "glass tube heater", "cartridge heater")),
    TaxonomyNode("industrial_lubricants_chemicals", "Industrial Lubricants & Chemicals", "subcategory", "industrial", ("compressor oil", "lubricant", "industrial chemical")),
    TaxonomyNode("industrial_filters_filtration", "Filters & Filtration", "subcategory", "industrial", ("filter housing", "cartridge filter", "inline filter")),
    TaxonomyNode("industrial_metals_materials", "Industrial Metals & Raw Materials", "subcategory", "industrial", ("stainless steel", "round bar", "flange", "refractory brick")),
    TaxonomyNode("industrial_safety", "Industrial Safety Products", "subcategory", "industrial", ("safety shoes", "safety equipment", "ppe")),
    TaxonomyNode("industrial_wires_fencing", "Wires & Fencing Materials", "subcategory", "industrial", ("fencing wire", "wire mesh", "chain link fencing", "galvanized wire", "fence insulator")),
    TaxonomyNode(
        "industrial_architectural_hardware",
        "Architectural & Furniture Hardware",
        "subcategory",
        "industrial",
        (
            "door handle",
            "cabinet handle",
            "cabinet knob",
            "drawer pull",
            "tower bolt",
            "door stopper",
            "curtain bracket",
            "furniture hardware",
            "mortise handle",
            "mortice handle",
        ),
    ),
    TaxonomyNode("services_testing_analysis", "Testing & Analysis Services", "subcategory", "services", ("testing service", "analysis service", "calibration laboratory")),
    TaxonomyNode("agriculture", "Agriculture Supplies", "root", aliases=("agriculture", "farming", "solar fencing")),
    TaxonomyNode("agriculture_solar_fencing", "Solar Fencing & Farm Protection", "subcategory", "agriculture", ("solar fence", "solar fencing", "jhatka machine", "zatka machine", "fancing machine", "fence wire", "spray pump")),
    TaxonomyNode("agriculture_machinery", "Agriculture Machinery", "subcategory", "agriculture", ("harvester", "reaper", "farm machine")),
    TaxonomyNode("services", "Services", "root", aliases=("service", "services")),
    TaxonomyNode("services_digital_marketing", "Digital & Communication Services", "subcategory", "services", ("bulk sms", "sms service", "digital service")),
    TaxonomyNode("services_software_it", "Software & IT Services", "subcategory", "services", ("software", "application development", "billing software", "it service")),
    TaxonomyNode("services_repair_calibration", "Repair & Calibration Services", "subcategory", "services", ("repairing service", "calibration service")),
    TaxonomyNode("services_engineering_projects", "Engineering & Electrical Project Services", "subcategory", "services", ("electrical project", "design build service", "engineering service")),
    TaxonomyNode("gold", "Gold", "root", aliases=("gold", "bullion", "jewellery")),
    TaxonomyNode("gold_bullion", "Gold Bullion", "subcategory", "gold", ("bullion", "24k", "999", "995", "bis")),
    TaxonomyNode("taxonomy_review_hold", "Manual Taxonomy Review Hold", "root", aliases=("manual review", "taxonomy review")),
    TaxonomyNode("unknown", "Needs Classification", "root"),
)

NODE_BY_ID = {node.node_id: node for node in NODES}


def _tokens(value: str) -> set[str]:
    return set(TOKEN_RE.findall((value or "").lower()))


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_text(item) for item in value)
    return str(value)


GENERIC_SOURCE_CATEGORIES = {
    "",
    "home",
    "jsonld",
    "category",
    "products",
    "our products",
    "view all",
}

ARCHITECTURAL_HARDWARE_PHRASES = (
    "door handle",
    "door handles",
    "cabinet handle",
    "cabinet knob",
    "cabinet knobs",
    "drawer pull",
    "drawer knob",
    "pull handle",
    "tower bolt",
    "door stopper",
    "door knob",
    "door knobs",
    "curtain bracket",
    "curtain fittings",
    "curtain fitting",
    "curtain socket",
    "mortise handle",
    "mortice handle",
    "furniture hardware",
    "door kit",
    "door kits",
    "gate hook",
    "wall hook",
    "clothes hook",
    "flat hook",
    "concealed handle",
    "main door handle",
    "aluminum handle",
    "aluminium handle",
    "ss knob",
    "ss stopper",
    "d handle",
    "d handles",
    "sliding door roller",
    "aluminum section",
    "aluminium section",
    "aluminium profile",
    "aluminum profile",
    "glass profile",
    "g handle",
    "g profile",
    "gola profile",
    "shutter profile",
    "kitchen profile",
    "end cap",
    "glass railing",
    "cloth hanger",
    "clothes hanger",
    "curtain support",
    "door pull",
)


def _source_category_name(offering: dict[str, Any]) -> str:
    attrs = offering.get("attributes")
    if not isinstance(attrs, dict):
        return ""
    return _text(attrs.get("category_name")).strip()


def _is_generic_source_category(name: str) -> bool:
    cleaned = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    return cleaned in GENERIC_SOURCE_CATEGORIES


def _architectural_hardware_match(
    text: str,
    reason: str,
    *,
    confidence: float,
    allow_generic_tokens: bool = False,
) -> TaxonomyMatch | None:
    if _contains(text, *ARCHITECTURAL_HARDWARE_PHRASES):
        return _match("industrial_architectural_hardware", confidence, reason)
    if allow_generic_tokens and _contains(
        text,
        "handle",
        "handles",
        "knob",
        "knobs",
        "hook",
        "hooks",
        "kadi",
        "stopper",
        "bolt",
        "bracket",
        "hinge",
        "hinges",
    ):
        return _match("industrial_architectural_hardware", confidence, reason)
    return None


def _contains(text: str, *phrases: str) -> bool:
    normalized = text.lower()
    token_set = _tokens(normalized)
    for phrase in phrases:
        cleaned = phrase.lower().strip()
        if not cleaned:
            continue
        if " " in cleaned:
            if cleaned in normalized:
                return True
        elif cleaned in token_set:
            return True
    return False


def _path_for(node_id: str) -> tuple[str, ...]:
    path: list[str] = []
    current = NODE_BY_ID.get(node_id)
    while current:
        path.append(current.node_id)
        current = NODE_BY_ID.get(current.parent_id or "")
    return tuple(reversed(path))


def _labels_for(path: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(NODE_BY_ID[node_id].display_name for node_id in path if node_id in NODE_BY_ID)


def taxonomy_nodes() -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for node in NODES:
        path = _path_for(node.node_id)
        docs.append(
            {
                "node_id": node.node_id,
                "parent_id": node.parent_id,
                "root_id": path[0] if path else node.node_id,
                "level": len(path) - 1,
                "node_type": node.node_type,
                "display_name": node.display_name,
                "aliases": list(node.aliases),
                "path": list(path),
                "path_labels": list(_labels_for(path)),
                "is_active": True,
                "taxonomy_version": TAXONOMY_VERSION,
            }
        )
    return docs


def _match(node_id: str, confidence: float, reason: str, *, needs_review: bool = False, source: str = "rules") -> TaxonomyMatch:
    path = _path_for(node_id)
    root_id = path[0] if path else node_id
    return TaxonomyMatch(
        node_id=node_id,
        taxonomy_path=path,
        taxonomy_path_labels=_labels_for(path),
        root_id=root_id,
        canonical_category_id=root_id,
        canonical_subcategory_id=node_id if node_id != root_id else None,
        confidence=max(0.0, min(confidence, 1.0)),
        reason=reason,
        needs_review=needs_review,
        source=source,
    )


def classify_offering(offering: dict[str, Any]) -> TaxonomyMatch:
    """Classify a vendor offering into the canonical taxonomy.

    This is deliberately deterministic and conservative. It should be cheap
    enough for ingestion/backfills. Ambiguous rows are routed to broader nodes
    with `needs_review=True`; later we can plug in embeddings/LLM for those.
    """
    raw_category = _text(offering.get("category_id"))
    raw_subcategory = _text(offering.get("subcategory_id"))
    product = _text(offering.get("product_name_raw") or offering.get("normalized_product_name"))
    attrs = _text(offering.get("attributes") or {})
    combined = " ".join([raw_category, raw_subcategory, product, attrs]).lower()
    combined = combined.replace("_", " ").replace("-", " ")
    product_context = " ".join([product, attrs]).lower().replace("_", " ").replace("-", " ")
    raw_context = " ".join([raw_category, raw_subcategory]).lower().replace("_", " ").replace("-", " ")

    if (
        not _contains(combined, "gold-plated", "gold plated", "plated")
        and not _contains(combined, "barcode", "printer", "ethernet", "switch", "lamp", "capsule", "tablet", "eye patch", "face mask", "chair", "chairs", "personal care", "moisturizing", "dark circle", "acne")
        and (
            _contains(combined, "gold bullion", "bullion", "gold bar", "gold coin", "gold biscuit")
            or (_contains(combined, "gold") and _contains(combined, "24k", "999", "995", "bis") and _contains(combined, "gram", "grams", "gm", "kg", "bar", "coin", "biscuit", "rtgs"))
        )
    ):
        return _match("gold_bullion", 0.92, "gold/bullion keyword match")

    source_category = _source_category_name(offering)
    source_category_text = source_category.lower().replace("_", " ").replace("-", " ")
    if source_category and not _is_generic_source_category(source_category):
        if _contains(source_category_text, "bathroom"):
            return _match(
                "home_kitchen_bathroom_accessories",
                0.88,
                "indiamart category bathroom match",
            )
        if _contains(source_category_text, "sofa", "furniture"):
            return _match("furniture", 0.86, "indiamart category furniture match")
        hardware = _architectural_hardware_match(
            source_category_text,
            "indiamart category hardware match",
            confidence=0.9,
            allow_generic_tokens=True,
        )
        if hardware:
            return hardware

    hardware = _architectural_hardware_match(
        product_context,
        "product-name hardware keyword match",
        confidence=0.84,
        allow_generic_tokens=_is_generic_source_category(source_category),
    )
    if hardware:
        return hardware

    if _contains(product_context, "injection", "injectable", "vial", "ampoule"):
        return _match("pharma_injections", 0.94, "product-name injection keyword match")
    if _contains(product_context, "tablet", "tablets", "tab"):
        return _match("pharma_tablets", 0.94, "product-name tablet keyword match")
    if _contains(product_context, "capsule", "capsules"):
        return _match("pharma_capsules", 0.94, "product-name capsule keyword match")
    if _contains(product_context, "syrup", "suspension"):
        return _match("pharma_syrups", 0.92, "product-name syrup keyword match")
    if _contains(product_context, "eye drop", "nasal spray", "ear drop", "drops", "ophthalmic"):
        return _match("pharma_drops_sprays", 0.9, "product-name drops/spray keyword match")

    if _contains(combined, "tablet", "tablets", "tab"):
        return _match("pharma_tablets", 0.94, "tablet keyword match")
    if _contains(combined, "capsule", "capsules"):
        return _match("pharma_capsules", 0.94, "capsule keyword match")
    if _contains(combined, "syrup", "suspension"):
        return _match("pharma_syrups", 0.92, "syrup keyword match")
    if _contains(combined, "eye drop", "nasal spray", "ear drop", "drops", "ophthalmic"):
        return _match("pharma_drops_sprays", 0.9, "drops/spray keyword match")
    if _contains(combined, "injection", "injectable", "vial", "ampoule"):
        return _match("pharma_injections", 0.92, "injection keyword match")
    if _contains(combined, "cream", "ointment", "gel", "lotion"):
        return _match("pharma_creams_ointments", 0.88, "topical medicine keyword match")
    if _contains(combined, "erectile", "sildenafil", "tadalafil", "vardenafil"):
        return _match("pharma_erectile_dysfunction", 0.95, "ed medicine keyword match")
    if _contains(combined, "anti cancer", "anticancer", "oncology"):
        return _match("pharma_anti_cancer", 0.9, "anti-cancer keyword match")
    if _contains(combined, "anti diabetic", "diabetic care", "diaba cure", "metformin", "glimepiride"):
        return _match("pharma_anti_diabetic", 0.9, "anti-diabetic medicine keyword match")
    if _contains(combined, "api", "active pharmaceutical ingredient", "active pharma ingredient", "pharma raw material", "pharmaceutical raw material", "intermediate", "drug intermediate", "pharmaceutical chemical"):
        return _match("pharma_api_raw_materials", 0.86, "api/raw material keyword match")
    if _contains(combined, "ayurvedic", "herbal medicine", "herbal product", "herbal ayurvedic"):
        return _match("pharma_ayurvedic_herbal", 0.84, "ayurvedic/herbal keyword match")

    if _contains(combined, "hospital furniture", "hospital bed", "fowler", "stretcher", "trolley", "examination table"):
        return _match("medical_devices_hospital_furniture", 0.9, "hospital furniture keyword match")
    if _contains(combined, "surgical", "operation theatre", "ot light", "laryngoscope", "catheter", "anesthesia workstation", "instrument tray", "needle cutter", "destroyer"):
        return _match("medical_devices_surgical", 0.88, "surgical equipment keyword match")
    if _contains(combined, "pneumatic tourniquet", "rotary microtome", "xenon pole"):
        return _match("medical_devices_surgical", 0.84, "medical/surgical instrument keyword match", needs_review=True)
    if _contains(combined, "mask", "glove", "syringe", "nebulizer", "hand sanitizer", "sanitizer", "nasal cannula", "ecg paper", "adult bains circuit", "bains circuit", "bain circuit", "stomach tube", "face shield"):
        return _match("medical_devices_masks_consumables", 0.82, "medical consumable keyword match", needs_review=True)
    if _contains(combined, "breast pump"):
        return _match("medical_devices_masks_consumables", 0.82, "breast pump/medical device keyword match", needs_review=True)
    if _contains(
        combined,
        "medical device",
        "diagnostic",
        "patient monitor",
        "medical equipment",
        "medical examination",
        "hemoglobin meter",
        "anatomical model",
        "anatomical models",
        "human anatomical",
        "human joint",
        "medical microscope",
        "blood pressure monitor",
        "bp monitor",
        "physiotherapy equipment",
        "digital thermometer",
        "ecg cable",
        "wheelchair",
        "hearing aid",
        "hearing aids",
        "visual field screener",
        "pharmacy instruments",
        "hospital equipment",
        "derma roller",
        "ear cleaner",
        "support belt",
        "lumbar support",
        "lumbo sacral",
        "chest belt",
        "chest binder",
        "neck shoulder relaxer",
        "heel pads",
        "compression gloves",
        "orthopedic belt",
        "walking stick",
        "nellcor",
        "ift tens",
        "tens ms",
        "blood cell counter",
        "nursing training manikin",
        "medical models",
        "rna model",
        "urine analyzer",
        "biochemistry analyzer",
        "chemistry analyzer",
        "coagulation analyzer",
        "hormone immunoassay",
        "skin temperature probe",
        "blood pressure cuffs",
        "sphygmomanometer",
        "suction machine",
        "vacuum therapy",
    ):
        return _match("medical_devices_diagnostic", 0.8, "medical device keyword match", needs_review=True)
    if _contains(combined, "dental instrument", "dental instruments", "dental accessories", "urology instruments", "laparoscopic", "laparoscopic scissors", "surgical scissors", "orthopedic instruments", "forceps", "gigli saw", "ot table", "operation table", "labour table", "dental luxators"):
        return _match("medical_devices_surgical", 0.84, "surgical/dental instrument keyword match", needs_review=True)

    if _contains(combined, "fire fighting", "fire fight", "fire extinguisher", "fire equipments", "fire equipment", "fire pump", "fire pumps", "fire pod"):
        return _match("industrial_safety", 0.84, "fire fighting/safety keyword match", needs_review=True)

    if _contains(combined, "access control", "biometric", "video door phone", "door phone", "fingerprint scanner", "fingerprint device", "fingerprint reader", "digital persona", "iris scanner", "mantra mis100", "smart card reader", "contactless card", "id card accessories", "smart card", "smart cards", "rfid", "rfid tag", "rfid card", "rfid wristband", "time attendance", "digital door lock", "smart door lock", "door lock", "morpho", "identi", "flap barrier", "parking barrier gate", "turnstile", "tripod turnstile", "eas systems", "library security gates", "security tag", "eas rf label", "am deactivator"):
        return _match("electronics_access_control", 0.9, "access control keyword match")
    if _contains(combined, "motorola mt2070"):
        return _match("electronics_access_control", 0.84, "scanner/access control keyword match")
    if _contains(combined, "fire alarm", "smoke detector", "security safe", "safety locker", "locker", "metal detector", "x ray baggage scanner", "baggage scanner", "boom barrier"):
        return _match("electronics_fire_safety_security", 0.88, "fire/security systems keyword match")
    if _contains(
        combined,
        "cctv",
        "surveillance",
        "security camera",
        "dvr",
        "hdcvi",
        "bullet camera",
        "dome camera",
        "ir bullet",
        "motion detector",
        "security system",
        "wireless security",
        "office security",
        "zoom camera",
        "ip camera",
        "wifi camera",
        "network video recorder",
        "nvr",
        "camera accessories",
        "thermal camera",
        "ptz camera",
        "camera lens",
        "web camera",
        "web cameras",
        "video camera",
        "360 video camera",
        "live streaming camera",
        "action camera",
        "nikon keymission",
        "orbbec",
        "webcam",
        "wifi blub camera",
        "machine vision camera",
        "gige interface",
        "10 gige",
        "basler",
        "eng cameras",
        "canon cameras",
        "mini elegant camera",
        "video balun",
    ):
        return _match("electronics_cctv_security", 0.9, "security electronics keyword match")
    if _contains(combined, "meeting owl"):
        return _match("electronics_cctv_security", 0.82, "camera/conferencing device keyword match", needs_review=True)
    if _contains(combined, "lidar", "depth camera", "digital camera", "camera boxes", "rear view camera", "dslr camera", "sony camera", "camera 5000mp", "time of flight tof camera", "360fly hd camera", "autofocus digital selfie camera"):
        return _match("electronics_cctv_security", 0.8, "camera/security keyword match", needs_review=True)
    if _contains(combined, "digital video recorder", "digital video recorders"):
        return _match("electronics_cctv_security", 0.9, "dvr/security recorder keyword match")
    if _contains(combined, "mobile accessories", "phone case", "iphone 13 case", "slide camera cover", "charger", "earphone", "mobile cover", "mobile tempered glass", "tempered glass", "selfie stick", "selfhi stick", "bluetooth selfhi", "phone tripod", "mobile tripod", "mobile repairing tools", "opening tool", "ipad holder", "tablet holder", "mobile spare part", "mobile spare parts", "cc mobile spare", "v8 cc", "oca lamination machine", "bga reballing stencil", "reballing stencil"):
        return _match("electronics_mobile_accessories", 0.88, "mobile accessories keyword match")
    if _contains(combined, "oculus", "meta quest", "vision pro"):
        return _match("electronics_mobile_accessories", 0.78, "consumer device accessory keyword match", needs_review=True)
    if _contains(combined, "mobile holder", "mobile stand", "phone holder", "phone stand"):
        return _match("electronics_mobile_accessories", 0.86, "mobile holder/accessory keyword match")
    if _contains(combined, "mobile phone", "smartphone", "feature phone", "windows phone"):
        return _match("electronics_mobile_devices", 0.88, "mobile device keyword match")
    if _contains(combined, "keyboard", "mouse", "ssd", "ram", "computer accessories"):
        return _match("electronics_computer_accessories", 0.88, "computer accessories keyword match")
    if _contains(combined, "speaker", "speakers", "loudspeaker", "headphone", "headphones", "headset", "headsets", "earbud", "earbuds", "earphones", "earphone", "airpods", "subwoofer", "soundbar", "audio", "microphone", "microphones", "mic", "karaoke", "collar mic", "collor mic", "music system", "power amplifier", "power amplifiers", "amplifier", "amplifiers", "public address system", "pa system", "paging system", "industrial pa", "car tweeters", "av receiver", "studio monitor", "receiver", "dj mixer", "scratch mixer", "dj equipment", "dj setup services", "home theater", "home theatre", "rcf st"):
        return _match("electronics_audio", 0.88, "audio electronics keyword match")
    if _contains(combined, "projector", "projectors", "projection screen", "interactive flat panel", "display panel", "video switcher", "video capture card", "capture card", "hdmi splitter", "digital signage", "digital standee", "digital podium", "display board", "lcd display", "lcd panel", "interactive board", "interactive smart board", "smart class services", "video conferencing system", "live video streaming", "set top box", "tv booster", "computer monitor", "led monitor", "lcd monitor", "monitor", "display unit", "hologram fan", "open box monitor", "android tv box", "blackmagic design", "ursa mini", "camera tripod", "travel tripod", "desktop camera tripod", "tripod stand", "gimbal stabilizer", "studio accessories", "photo reflector"):
        return _match("electronics_av_display", 0.86, "av/display keyword match")
    if _contains(combined, "smart watch", "smartwatch", "smart band", "fitness band"):
        return _match("electronics_wearables", 0.88, "wearable electronics keyword match")
    if _contains(combined, "cigarette lighter", "electronic lighter", "usb lighter", "arc lighter", "bbq lighter"):
        return _match("electronics_lighters", 0.86, "electronic lighter keyword match")
    if _contains(combined, "desktop computer", "refurbished desktop", "graphics card", "graphic card", "laptop", "laptops", "netbook", "notebook", "server", "rack server", "motherboard", "computer processor", "touch pad panel", "computer parts", "computer cabinet", "computer cabinets", "gaming cpu", "gaming desktop", "gaming cabinet", "msi mag", "msi pro", "b650", "b550m", "hp desktop"):
        return _match("electronics_computers", 0.88, "computer hardware keyword match")
    if _contains(combined, "hard disk", "hard drive", "hdd", "ssd"):
        return _match("electronics_computers", 0.84, "computer storage keyword match")
    if _contains(combined, "barcode printer", "barcode scanner", "barcode readers", "barcode reader", "barcode label", "handheld laser scanner", "wired scanner", "laser scanner", "vision system", "kiosk", "touch screen kiosk", "interactive kiosk", "self terminals"):
        return _match("electronics_barcode_pos", 0.88, "barcode equipment keyword match")
    if _contains(combined, "micro atm", "mini atm", "aadhaar enabled mini atm", "aadhaar based micro atm", "micro atm pos"):
        return _match("electronics_barcode_pos", 0.84, "pos/atm equipment keyword match")
    if _contains(combined, "network switch", "poe switch", "router", "routerboard", "mikrotik", "ubiquiti", "powerbeam", "pbe-5ac", "antenna", "epabx", "wireless access point", "access point", "landline phone", "ip phone", "onu", "optical network unit", "nas", "network attached storage", "firewall", "sonicwall", "communication equipments", "inmarsat", "fiber optic cable", "fibre optic cable", "optical fiber cable", "fiber optic pigtail", "fibre optic pigtail", "fiber patch panel", "liu fiber", "lan cable", "networking cable", "networking devices", "networking equipment", "networking solution", "networking products", "networking rack", "patch cord", "rj45", "utp cable", "d-link cat 6", "ku band lnb", "dish tv lnb", "faceplate"):
        return _match("electronics_networking", 0.86, "networking/telecom keyword match")
    if _contains(combined, "cable entry", "cable entry frames", "cable entry plates", "cable holder", "cable organizer", "pd cable", "mantra cable", "dc cable", "apple dc cable", "lenovo dc cable", "havells cable", "electrical insulated wires", "insulated wires", "electrical wiring accessories", "pvc sheathed flexible cables"):
        return _match("electronics_cables_connectors", 0.84, "cable/connector keyword match")
    if _contains(combined, "cables", "cable assembly", "seatel cable", "sma cable", "rf cable assemblies", "50 meters wires", "veto wire", "flexible cable"):
        return _match("electronics_cables_connectors", 0.82, "cable/wire keyword match", needs_review=True)
    if _contains(combined, "hdmi cable", "hdmi extender", "usb cable", "connector", "connectors", "data cable", "charging cable", "electric cable", "electric cables", "electrical cable", "electrical wire", "house wire", "multi strand wire", "armoured cable", "coaxial cable", "power cord", "screen cable", "cat6 cable", "cat 6 cable", "wires and cables", "computer cable", "av cable", "vga cable", "vga y cable", "cable clips", "cable accessories", "rubber cable", "instrumentation cables", "copper flexible cable", "submersible flat cable"):
        return _match("electronics_cables_connectors", 0.84, "cable/connector keyword match")
    if _contains(combined, "home appliance", "air conditioner", "air cooler", "air purifier", "air touch", "cooler", "room heater", "electric heater", "water heater", "immersion heater", "immersion rod", "geyser", "electric geyser", "electric iron", "dry iron", "deep freezer", "freezer", "ceiling fan", "exhaust fan", "table fan", "pedestal fan", "commercial fan", "air circulator fan", "mixer grinder", "washing machine", "washing maching", "refrigerator", "fridge", "water dispenser", "electric kettle", "coffee maker", "coffee machine", "microwave oven", "bakery oven", "pizza oven", "hand dryer", "egg boiler", "electric stove", "infrared cooker", "electric chulha", "ac remote", "remote control", "ice cube machine"):
        return _match("electronics_appliances", 0.84, "appliance keyword match")
    if _contains(combined, "heat bag electric", "electric heat bag", "hand mixer", "air fryer", "digital air fryer"):
        return _match("electronics_appliances", 0.82, "consumer appliance keyword match", needs_review=True)
    if _contains(combined, "multifunction printer", "laser printer", "canon printer", "epson printer", "id card printer", "printer", "printers", "document scanner", "desktop scanner", "sheet feed scanner", "sheet-feed scanner", "omr sheet scanner", "scanjet", "imagecenter", "ads-2800w"):
        return _match("office_supplies_printers", 0.86, "printer/office machine keyword match")
    if _contains(combined, "sheetfed scanner", "canon scanner", "fujitsu scanner", "scanner spare parts", "cano scan", "epson workforce"):
        return _match("office_supplies_printers", 0.86, "scanner/office machine keyword match")
    if _contains(combined, "toner cartridge", "printer cartridge", "ink cartridge", "fuser assembly", "fuser heater", "laserjet fuser"):
        return _match("electronics_printing_consumables", 0.86, "printing consumable keyword match")
    if _contains(combined, "print head"):
        return _match("electronics_printing_consumables", 0.84, "printer spare keyword match")
    if _contains(combined, "pen drive", "pendrive", "usb hub", "memory card", "memory cards", "compact flash card", "data cartridge", "solid state drive"):
        return _match("electronics_storage_peripherals", 0.86, "storage/usb peripheral keyword match")
    if _contains(combined, "body massager", "massager", "massage stick", "personal care", "trimmer", "hair dryer"):
        return _match("electronics_personal_care", 0.82, "personal care electronics keyword match")
    if _contains(combined, "solar panel", "solar inverter", "solar power", "solar water heater", "solar electric", "solar energy"):
        return _match("electronics_solar_energy", 0.84, "solar energy keyword match")
    if _contains(combined, "solar tree"):
        return _match("electronics_solar_energy", 0.82, "solar product keyword match", needs_review=True)
    if _contains(combined, "ups", "inverter", "power supply", "battery", "power bank", "voltage stabilizer", "servo stabilizer", "high current universal power"):
        return _match("electronics_power", 0.84, "power electronics keyword match")
    if _contains(combined, "adapter", "converter", "smps", "ac dc", "dc ac", "ac to dc", "dc to ac"):
        return _match("electronics_adapters_converters", 0.84, "adapter/converter keyword match")
    if _contains(combined, "ic", "integrated circuit", "integrated circuits", "microcontroller", "sensor", "sensors", "module", "modem board", "radio modem", "bridge rectifier", "rotary encoder", "encoder", "membrane keypad", "development board", "development boards", "arduino", "raspberry pi", "robotic kit", "robotic kits", "robotics accessories", "trainer kit", "demodulation", "bo motor", "electronic component", "electronic components", "diode", "diodes", "rectifiers", "resistor", "capacitor", "capacitors", "smd capacitor", "proximity switch", "zero speed switch", "zero speed swich", "mosfet", "transistor", "semiconductor module", "tcon board", "lcd controller", "dwin display", "cpu unit", "siemens simatic", "simatic s5", "simatic", "keyence drive", "pcb", "pc board", "printed circuit board", "circuit board", "terminal", "molex terminal", "wiring harness", "pci card", "pci cards"):
        return _match("electronics_components", 0.84, "electronic component keyword match")
    if _contains(combined, "drone flight controller", "pixhawk", "drone kits", "drone parts", "flight controller", "stepper motor", "flysky"):
        return _match("electronics_components", 0.82, "drone/electronics component keyword match", needs_review=True)
    if _contains(combined, "bnc connector", "epabx", "gps"):
        return _match("electronics_components", 0.76, "electronics component/telecom keyword match", needs_review=True)
    if _contains(combined, "led", "light", "lights", "lighting", "loghts", "tube light", "tube lights", "fluorescent tube", "light fitting", "emergency tube", "gate lite", "gate light", "cfl", "cfl lamp", "ultraviolet tube", "lamp", "bulb", "chandelier", "osram halogen", "halogen", "cool white", "artificial daylight"):
        return _match("electronics_lighting", 0.78, "lighting keyword match", needs_review=_contains(combined, "decorative", "candle"))
    if _contains(combined, "brake shoe", "brake cable", "clutch cable", "throttle cable", "clutch plate", "clutch assembly"):
        return _match("automotive_brake_clutch_parts", 0.9, "automotive brake/clutch keyword match")
    if _contains(combined, "two wheeler", "motorcycle", "scooty", "bike spare", "bike speedometer"):
        return _match("automotive_two_wheeler_parts", 0.86, "two wheeler parts keyword match")
    if _contains(combined, "car accessories", "car interior accessories", "car care accessories", "car freshener", "car stereo", "car mp3", "car parts", "car spare parts", "automotive body parts", "bumper", "gear knob", "automotive accessories", "automotive component", "automotive components", "automotive spare", "automotive spare parts", "automotive gasket", "speedometer cable", "accelerator cable", "gear cable", "throttle electric bike", "car grill", "car spoiler", "body kit", "side skirts", "roof spoiler", "diffuser", "drl", "android car", "automotive horn", "single horn", "pressure horn", "flasher", "car door frame guard", "exterior kit", "scorpio classic", "jaguar fpace", "oil filter", "air filter", "jcb pin", "jcb bucket pin", "jcb bush", "loader arm bush", "dipper rod bush", "disk pad", "okinawa", "electric scooter cable", "scooter spare", "car center locking", "car seat cover", "engine oil", "liqui moly"):
        return _match("automotive_accessories", 0.84, "automotive accessories keyword match")
    if _contains(combined, "central locking system", "steering wheel", "foot step", "electric side foot step", "electric bike parts", "stainless steel electric bike parts"):
        return _match("automotive_accessories", 0.82, "automotive accessory keyword match", needs_review=True)
    if _contains(combined, "duffle", "duffel", "backpack", "school bag", "travel bag", "sling bag", "messenger bag", "satchel bag", "bucket bag", "wash bag", "document organizer bag", "conference folder", "shoe wash bag", "luggage", "card holder"):
        return _match("bags_luggage_travel", 0.9, "bag/luggage keyword match")
    if _contains(combined, "t shirt", "t-shirt", "mens t shirt", "mens half jacket", "women jacket", "womens jacket", "ladies jacket", "designer jacket", "hiking jacket", "athletic jacket", "windbreaker", "waist tightener", "buttons pins", "loose jeans", "shoes", "sneaker", "sunglasses", "sunglass", "wrist watch", "mens watches", "hair accessories", "headbands", "head wrap", "leather belt", "leather accessories", "necklace", "pendant"):
        return _match("textiles_apparel", 0.78, "apparel/fashion keyword match", needs_review=True)
    if _contains(combined, "thermal vest", "sleeveless fleece", "spiderman costume", "kids wear", "kids wool", "boys shorts", "girls top", "crop top", "sandals", "mens sandals", "ladies watch", "fossil", "audemars", "chanel"):
        return _match("textiles_apparel", 0.76, "apparel/fashion keyword match", needs_review=True)
    if _contains(combined, "fabric", "cotton", "polyester", "cloth", "garment", "cushion cover"):
        return _match("textiles_fabrics", 0.86, "textile keyword match")
    if _contains(combined, "laboratory equipment", "laboratory instrument", "lab equipment"):
        return _match("industrial_lab_testing", 0.86, "lab/testing instruments keyword match")
    if _contains(combined, "laboratory products", "laboratory ware", "laboratory glassware", "laboratory plasticware", "laboratory crucible", "laboratory crucibles", "crucible", "filtration flask", "physics lab", "physics equipment", "physics equipments", "physics instrument", "physics instruments", "laboratory apparatus", "scientific instrument", "science models", "biological models", "analytical instrument", "analytical instruments", "soil testing equipment", "testing equipment", "testing instruments", "testing machine", "test chamber", "stability test chamber", "hot air oven", "chemical reaction engineering lab", "mass transfer lab", "centrifuge machine", "recycle bed reactor", "microscope", "ph meter", "orp meter", "micro pippette", "pipette", "lab instruments", "ultrasonic bath", "sonicator", "heating mantle", "glass stirrer", "reaction flask", "ice flaking machine", "fluid mechanics lab", "bernoullis theorem", "slinky"):
        return _match("industrial_lab_testing", 0.84, "lab/scientific instrument keyword match")
    if _contains(combined, "nessler cylinder", "le chatelier flask", "davies condenser", "rotary microtome"):
        return _match("industrial_lab_testing", 0.82, "lab glassware/instrument keyword match", needs_review=True)
    if _contains(combined, "pressure transmitter", "pressure gauge", "pressure switch", "digital multimeter", "multimeter", "digital panel meter", "panel meter", "electrical meter", "lcr meter", "sound level meter", "moisture meter", "clamp meter", "ammeter", "analog ammeter", "data logger", "transducer", "transducers", "temperature scanner", "infrared thermometer", "thermometer", "dual k type thermometer", "flow meter", "measuring instrument", "measurement instrument", "measuring tools", "feeler gauge", "energy meter", "temperature controller", "control panel", "multifunction meter", "multi function meter", "water activity meter", "insulation tester", "resistance tester", "survey instrument", "surveying instruments", "prism pole", "total station", "vibration meter", "digital tachometer", "lux meter", "weighing scale", "weighing balances", "tabletop balance", "gas detector", "anemometer", "3d scanner", "leica gst", "wooden tripod", "drawing instruments", "parallel ruler"):
        return _match("industrial_measurement_control", 0.86, "measurement/control keyword match")
    if _contains(combined, "vision measuring machine", "video measuring machine", "dial gauge", "load cell", "weighing system", "rice lake", "slope indicator", "temperature control apparatus", "process control engineering", "hydrocyclone", "controller tuning"):
        return _match("industrial_measurement_control", 0.84, "measurement/process control keyword match", needs_review=True)
    if _contains(combined, "cassette ac", "split ac", "ac indoor unit", "vrf", "vrv", "fan coil", "fcu", "chilled water fcu", "aircon", "air conditioning", "hvac", "air curtain", "laminar airflow", "laminar air flow", "clean room equipment", "centrifugal blower", "blower", "centrifugal fan", "force draft ventilation", "wall fan", "electric fans", "fan", "fans", "axial flow fan", "bifurcated axial", "straight tube fan", "industrial roof extractor", "extractor roof unit", "frp extractor", "tunnel jet fan", "tunnel fans", "frp axial fan", "wind turbine ventilator", "air ventilator", "roof extractor fan", "water chiller", "industrial chillers", "flameproof and weatherproof fans", "weatherproof fans", "wall mounting fan", "flameproof fan"):
        return _match("industrial_hvac", 0.86, "hvac/air conditioning keyword match")
    if _contains(combined, "dg set", "generator", "generators", "genset"):
        return _match("industrial_generators", 0.86, "generator keyword match")
    if _contains(combined, "plc", "programmable logic controller", "siemens plc", "schneider electric", "allen bradley", "servo drive", "ac drive", "vfd", "schneider vfd", "speed controller", "servo motor", "gearbox", "gear box", "hmi", "hmi touch panel", "touch panel", "limit switch", "contactor", "automation", "marine equipments"):
        return _match("industrial_automation", 0.86, "industrial automation/plc keyword match")
    if _contains(combined, "abb rdcu", "acs800", "phoenix contact", "industrial ethernet switch", "ring main unit", "abb ring main unit", "vcb panel", "automatic voltage regulator", "avr card"):
        return _match("industrial_automation", 0.84, "industrial automation/electrical control keyword match", needs_review=True)
    if _contains(combined, "modular switch", "modular plate", "modular plates", "switches", "electrical switch", "rocker switch", "rotary switch", "toggle switch", "electric socket", "socket", "sockets", "gang box", "fan regulator", "circuit breaker", "relay", "relays", "electrical accessory", "electrical accessories", "electrical products", "electrical product", "distribution box", "distribution board", "mcb box", "electrical plug", "plug top", "uk plug top", "pop up box", "electrical fuse", "fuse holder", "hrc fuse", "main fuse", "flame proof electrical fitting", "flameproof cable gland", "cable gland", "thermocouple head", "electric motor", "dc motor", "power cable", "extension board", "power strip", "current protector", "current transformer", "rectifier", "junction box", "push button", "push buttons", "push button station", "start stop push button", "flameproof button", "weatherproof button", "automatic transfer switch", "flameproof emergency stop switch", "carbon brush", "cooling fan", "wire clip", "cable ties", "cable tie", "timer", "timers", "annuciation system", "annunciation system", "alarm annunciator", "electronic hooter", "electronic ballast", "power transformer", "energy management system"):
        return _match("industrial_electrical_components", 0.86, "industrial electrical component keyword match")
    if _contains(combined, "power window switch", "reed switch", "pencil reed switch", "elevator header parts", "elevator accessories", "solenoid"):
        return _match("industrial_electrical_components", 0.82, "electromechanical component keyword match", needs_review=True)
    if _contains(combined, "industrial heater", "glass tube heater", "cartridge heater", "tubular heater", "tubular heaters", "band heater", "cold room door heater", "incubator heater element", "halogen lamps", "heating element", "heating elements"):
        return _match("industrial_heaters", 0.86, "industrial heater keyword match")
    if _contains(combined, "drill machine", "drill bits", "miter saw", "mitre saw", "flap disc", "diamond polishing pad", "diamond polishing pads", "hex key set", "l-wrench", "torx l-wrench", "ball end l-wrench", "tamper resistant torx", "industrial hacksaw", "heat gun", "angle grinder", "carpenter hammer", "plunge cut saw"):
        return _match("industrial_tools", 0.82, "industrial tools keyword match", needs_review=True)
    if _contains(combined, "powder coating machine", "industrial sewing machine", "laser marking machine", "fiber laser marker", "laser machine", "laser focus lens", "f theta", "beam expander"):
        return _match("industrial_tools", 0.8, "industrial machine/tool keyword match", needs_review=True)
    if _contains(combined, "air compressor", "compressor"):
        return _match("industrial_tools", 0.82, "air compressor/tool keyword match")
    if _contains(combined, "agriculture spray pump", "agricultural spray pump", "farm spray pump"):
        return _match("agriculture_machinery", 0.86, "agriculture spray pump keyword match")
    if _contains(combined, "pump", "pumps", "submersible pump", "diaphragm pump", "water pump", "dc motor", "servo motor"):
        return _match("industrial_pumps_motors", 0.84, "pump/motor keyword match", needs_review=True)
    if _contains(combined, "glucose d", "glucose powder", "fruit powder"):
        return _match("food_beverages", 0.82, "food/beverage powder keyword match", needs_review=True)
    if _contains(combined, "compressor oil", "air lube compressor oil", "industrial chemical", "food grade chemical", "laboratory chemicals", "speciality chemicals", "dense soda ash", "potassium hydroxide", "acetone", "colloidal", "sodium starch glycolate", "naphthalene", "naphthalne", "octadecane", "sodium thiosulfate", "magnesium sulphate", "barium carbonate", "sodium carboxy methyl cellulose", "carboxy methyl cellulose", "sodium cmc", "boric acid"):
        return _match("industrial_lubricants_chemicals", 0.82, "industrial lubricant/chemical keyword match", needs_review=True)
    if _contains(combined, "chloro acetyl chloride", "hydrochloric acid", "sulphuric acid", "hydrofluoric acid", "ferrous sulphate", "calcium sulphate", "silver nitrate", "potassium permanganate", "diphenylamine", "antimony potassium tartrate", "charcoal powder", "silicon monoxide", "borax powder", "hydrogen peroxide", "formic acid", "polyvinyl alcohol", "epichlorohydrin"):
        return _match("industrial_lubricants_chemicals", 0.82, "industrial/lab chemical keyword match", needs_review=True)
    if _contains(combined, "filter housing", "stainless steel filter housing", "cartridge filter", "inline filter", "sediment filter", "high flow cartridge filter"):
        return _match("industrial_filters_filtration", 0.84, "filter/filtration keyword match", needs_review=True)
    if _contains(combined, "eaton replacement filter", "replacement filter", "alkaline housing", "dust collection system", "fume extraction", "frp scrubber", "industrial scrubber", "smoke exhaust system", "air pollution control"):
        return _match("industrial_filters_filtration", 0.82, "filter/pollution control keyword match", needs_review=True)
    if _contains(combined, "stainless steel flanges", "duplex steel", "duplex ss flange", "slip on flanges", "mild steel flanges", "stainless steel rod", "stainless steel sheets", "stainless steel sheet", "ss bright round bar", "ss 304 shim sheet", "round bar", "titanium grade", "inconel", "refractory brick", "refractory bricks", "magnesia carbon brick", "fire bricks"):
        return _match("industrial_metals_materials", 0.82, "industrial metals/materials keyword match", needs_review=True)
    if _contains(combined, "safety shoes", "safety equipment", "industrial safety products", "safety goggles", "road safety products", "road safety", "convex mirror", "rotating beacon", "life boat release cable", "emergency exit sign", "exit sign", "sign board", "signage", "pvc strip curtain", "magnetic pvc strip curtain", "safety belt", "safety belts", "fall protection", "fall arrest", "body protection", "ppe", "personal protective equipment", "hand gloves", "safety gloves", "leather gloves"):
        return _match("industrial_safety", 0.82, "industrial safety keyword match", needs_review=True)
    if _contains(combined, "safety signages", "plastic safety barrier", "lockout tagout", "loto", "cable lockout", "scaffolding tag"):
        return _match("industrial_safety", 0.82, "industrial safety accessory keyword match", needs_review=True)
    if _contains(combined, "hand tools", "hand tool", "tool kit", "power tools", "power tool", "wire cutter", "wire stripper", "impact wrench", "bearing", "pneumatic tools", "air grinder", "soldering iron", "soldering station", "smd rework station", "rework station", "welding machine", "swaging tool", "ratchet screwdriver", "screwdriver set", "virax", "road cutting machine", "road cutting machines", "bar cutting machine", "steel bending machine", "bar bending machine", "cnc spare parts", "mpg handwheel"):
        return _match("industrial_tools", 0.8, "industrial tools keyword match", needs_review=True)
    if _contains(combined, "soldering wire", "soldering tip", "solder paste", "soldering wick", "liquid flux", "flux bond"):
        return _match("industrial_tools", 0.8, "soldering/tool keyword match", needs_review=True)
    if _contains(combined, "earth rammer", "power trowel", "concrete measurement box", "sand screening machine", "road texturing brush", "demolition hammer", "cone pulley lathe", "lathe machine", "wire straightening", "cut off machine"):
        return _match("industrial_tools", 0.8, "construction/industrial tool keyword match", needs_review=True)
    if _contains(combined, "material handling", "material handling equipment", "forklift", "pallet truck", "hoist", "lifting equipment", "lifting ring"):
        return _match("industrial_material_handling", 0.84, "material handling keyword match", needs_review=True)
    if _contains(combined, "lifting pole", "transmission spacers", "hydraulic actuators", "oil shaft"):
        return _match("industrial_material_handling", 0.78, "industrial handling/mechanical keyword match", needs_review=True)
    if _contains(combined, "food processing machine", "bakery equipment", "momo wrapper", "momo wrapper making machine", "wrapper making machine"):
        return _match("industrial_food_processing_equipment", 0.84, "food processing equipment keyword match", needs_review=True)
    if _contains(combined, "fencing wire", "wire mesh", "chain link fencing", "galvanized wire", "clutch wire", "reel insulator", "fence insulator"):
        return _match("industrial_wires_fencing", 0.82, "wire/fencing material keyword match", needs_review=True)
    if _contains(combined, "pipe", "valve", "fitting", "storage tank", "air storage tank", "compressed air storage tank"):
        return _match("industrial_pipes_valves", 0.82, "industrial pipe/valve keyword match")
    if _contains(combined, "steam boiler", "boiler parts", "industrial boilers", "coil type steam boiler", "boiler pressure part"):
        return _match("industrial_pipes_valves", 0.78, "boiler/pressure equipment keyword match", needs_review=True)
    if _contains(combined, "solar fence", "solar fencing", "jhatka machine", "zatka machine", "fancing machine", "fence wire", "spray pump"):
        return _match("agriculture_solar_fencing", 0.86, "agriculture solar fencing keyword match")
    if _contains(combined, "harvester", "reaper", "farm machine", "agricultural equipment", "tissue culture hood", "garden tools", "water spray nozzle"):
        return _match("agriculture_machinery", 0.84, "agriculture machinery keyword match")
    if _contains(combined, "electrical project", "design build service", "engineering service", "automation instrumentation electronics services", "fire fighting designing", "fire fighting design", "electrical contractor", "ofc fiber cable splicing", "network structured cabling"):
        return _match("services_engineering_projects", 0.82, "engineering/electrical service keyword match")
    if _contains(combined, "event management service", "event management services", "event organizer", "event organizers", "dj services", "stage lighting services", "cultural event services", "printing services", "corporate advertising"):
        return _match("services_engineering_projects", 0.76, "event/service keyword match", needs_review=True)
    if _contains(combined, "destination wedding", "wedding service", "wedding package", "tour package", "tour packages"):
        return _match("services_engineering_projects", 0.74, "travel/event service keyword match", needs_review=True)
    if _contains(combined, "testing service", "analysis service", "analytical analysis", "calibration laboratory", "certification services", "welder certification"):
        return _match("services_testing_analysis", 0.82, "testing/analysis service keyword match")
    if _contains(combined, "repairing service", "calibration service", "mobile repairing course", "iphone training course"):
        return _match("services_repair_calibration", 0.86, "repair/calibration service keyword match")
    if _contains(combined, "consultancy service", "consultancy services", "designing service", "designing services", "maintenance service", "products services trader", "share trading"):
        return _match("services", 0.72, "general service keyword match", needs_review=True)
    if _contains(combined, "bpo service", "bpo services", "bpo projects", "non voice", "voice bpo", "survey data entry", "pan card service", "aeps", "bbps service", "delivery boy placement"):
        return _match("services", 0.72, "business service keyword match", needs_review=True)
    if _contains(combined, "bulk sms", "sms service"):
        return _match("services_digital_marketing", 0.86, "digital service keyword match")
    if _contains(combined, "water purifier", "ro water purifier", "ro purifier", "ro spare parts", "ro membrane", "filter cartridge", "ro plant", "industrial ro plant", "water treatment plant", "water softener", "ro cabinet", "ro cabinets"):
        return _match("home_kitchen_water_purifiers", 0.86, "water purifier keyword match")
    if _contains(combined, "pet bowl", "pet bowls", "dog bowl", "cat bowl"):
        return _match("pet_supplies", 0.82, "pet supplies keyword match", needs_review=True)
    if _contains(combined, "stainless steel bowl", "steel bowl", "non tip bowl"):
        return _match("home_kitchen_kitchenware", 0.82, "kitchenware bowl keyword match")
    if _contains(combined, "wash basin", "one piece toilet", "toilet", "door handle", "bathroom"):
        return _match("home_kitchen_bathroom_accessories", 0.8, "bathroom/home fitting keyword match", needs_review=True)
    if _contains(combined, "furniture", "sideboard", "console", "teak wood", "timber console", "display counter", "strong room door"):
        return _match("furniture", 0.82, "furniture keyword match", needs_review=True)
    if _contains(combined, "vacuum cleaner", "high pressure washer", "floor cleaning machine", "cleaning equipment", "dust collector", "ultrasonic cleaner", "scrubbing machine", "scrubber dryer", "sweeping machine", "fly catcher machine", "insect killer machine", "insect catcher"):
        return _match("cleaning_supplies_machines", 0.86, "cleaning machine keyword match")
    if _contains(combined, "cleaning products", "cleaning product", "cleaning brush", "cleaning tools", "floor cleaner", "push broom", "tyre sponge brush", "cleaning chemicals", "lime scale remover", "dustbin", "plastic dustbin", "pedal bin"):
        return _match("cleaning_supplies", 0.82, "cleaning supplies keyword match", needs_review=True)
    if _contains(combined, "foam cleaner spray", "multipurpose foam cleaner", "brake parts cleaner spray", "rust prevention", "anti track compound spray"):
        return _match("cleaning_supplies_products", 0.8, "cleaning spray keyword match", needs_review=True)
    if _contains(combined, "banana chips", "pickle", "masala chips", "herbal powder", "strawberry fruit powder"):
        return _match("food_beverages", 0.82, "food/beverage keyword match", needs_review=True)
    if _contains(combined, "green tea", "himalayan berry", "herbs"):
        return _match("food_beverages", 0.8, "food/beverage keyword match", needs_review=True)
    if _contains(combined, "video cd", "video cds", "music cd", "movie cd", "bulbul movie", "musical instrument", "musical instruments", "musical guitar", "acoustic guitar", "tarot card"):
        return _match("media_entertainment", 0.78, "media/entertainment keyword match", needs_review=True)
    if _contains(combined, "mp3 cd", "mp3 cds", "blank optical media", "verbatim", "dvd-r", "dvd+rw", "cd-r", "gujarati movie", "telefilm", "lokvarta"):
        return _match("media_entertainment", 0.78, "media/optical disc keyword match", needs_review=True)
    if _contains(combined, "serving tray", "wooden tray", "cake stand", "bar accessories", "beer tower", "tableware", "ice bucket", "bowl", "bowls", "dinner set", "dinner box", "double dinner", "lunch box", "coffee mug", "vacuum flask", "metal dry fruit box", "dry fruit box", "filter coffee maker", "cooking equipment", "catering equipment", "electric shawarma", "gas stove", "stainless steel stove", "copper mug", "copper mugs", "drinkware", "wine glasses"):
        return _match("home_kitchen_kitchenware", 0.84, "home/kitchen serving keyword match")
    if _contains(combined, "metal trays", "designer tray", "aluminium enameled oval trays", "tea cups", "tea sets", "cup n saucer", "metal jugs", "water jug", "custom jug", "copper finish jug", "stainless steel cup", "cocktail glasses"):
        return _match("home_kitchen_kitchenware", 0.84, "home/kitchen tableware keyword match")
    if _contains(combined, "home decor", "decorative item", "decorative mirror", "wall mirror", "photo frame", "flower vase", "flower pot", "planter", "planters", "metal planter", "metal planters", "metal urli", "brass urli", "brass diya", "metal wall art", "brass handicrafts", "handicraft items", "wooden handicraft", "wooden antique box", "wall hanging", "wall decor", "wall hook", "wall hooks", "napkin ring", "napkin rings", "candle stand", "candle lantern", "hanging lantern", "lanterns", "metal lantern", "nautical compass", "brass compass", "compass", "brass bell", "cremation urn", "brass cremation urn", "jewelry box", "jewellery box", "magnifying glass", "brass telescope", "tissue box", "wedding decoration", "wedding decoration items", "wedding sofa", "decorative tray", "christmas ornament", "christmas items", "christmas tree", "merry christmas", "ornament", "garden torch", "watering can"):
        return _match("home_kitchen_home_decor", 0.8, "home decor keyword match", needs_review=True)
    if _contains(combined, "curtain tiebacks", "curtain holder", "candelabra", "wedding candelabra", "moroccan lantern", "independence day decoration", "suncatcher charm"):
        return _match("home_kitchen_home_decor", 0.8, "home decor accessory keyword match", needs_review=True)
    if _contains(combined, "wall clock", "table clock", "clock"):
        return _match("home_kitchen_clocks", 0.82, "clock keyword match")
    if _contains(combined, "bathroom accessories", "bath fitting"):
        return _match("home_kitchen_bathroom_accessories", 0.8, "bathroom accessories keyword match", needs_review=True)
    if _contains(combined, "corporate gift", "promotional gift", "promotional items", "diwali gifts", "gift set", "gift sets", "gift hamper", "gift hampers", "hamper basket", "key chain", "key rings", "wooden key ring", "wooden key rings", "crystal cube", "award trophy", "visiting card holder"):
        return _match("gifts", 0.78, "gift keyword match", needs_review=True)
    if _contains(combined, "office stationery", "stationery", "stationary products", "ball point pen", "calculator", "notepad"):
        return _match("office_supplies_stationery", 0.86, "office stationery keyword match")
    if _contains(combined, "thermal paper", "thermal paper roll", "pos thermal paper", "paper roll", "thermal jumbo paper", "tissue paper"):
        return _match("office_supplies", 0.82, "office/pos supplies keyword match")
    if _contains(combined, "paper bag", "craft paper bag", "wooden box", "plywood box", "export packing", "packing box", "plastic container", "printed labels", "pre printed labels", "printed stickers", "warranty void sticker", "pvc card", "printed cards", "void tape", "tape", "tapes", "tamper proof tape", "tamper evident tape", "deodorant stick tube", "refillable packaging"):
        return _match("packaging", 0.82, "packaging material keyword match", needs_review=True)
    if _contains(combined, "pvc soft profiles", "soft profiles"):
        return _match("packaging", 0.72, "pvc profile material keyword match", needs_review=True)
    if _contains(combined, "medieval", "helmet", "sword", "brass compass", "antique pocket watch", "artist divider"):
        return _match("decor_collectibles", 0.78, "decor/collectible keyword match", needs_review=True)
    if _contains(combined, "kids toy", "kids toys", "toy", "toys"):
        return _match("toys", 0.82, "toy keyword match", needs_review=True)
    if _contains(combined, "walkie talkies for kids", "inclusive games", "braille tactile", "braille dominoes", "ludo"):
        return _match("toys", 0.78, "kids/game keyword match", needs_review=True)
    if _contains(combined, "game controller", "game pad", "books", "book", "psychology of money"):
        return _match("media_entertainment", 0.72, "media/books/game keyword match", needs_review=True)
    if _contains(combined, "software", "application software", "billing software", "management software", "software development", "software development services", "customized software", "application software package"):
        return _match("services_software_it", 0.84, "software/it service keyword match", needs_review=True)
    if _contains(product_context, "pharma", "pharmaceutical", "medicine", "drug", "antibiotic", "ayurvedic", "pcd pharma", "pain relief") or _contains(
        raw_context,
        "pcd pharma franchise",
        "pharmaceutical medicines",
        "pharmaceutical syrup",
        "pharmaceutical syrups",
        "pain relief drug",
    ):
        return _match("pharma_generic", 0.78, "general pharma keyword match", needs_review=True)
    if _contains(combined, "constipation", "joint care", "vati", "herbal supplement", "hair growth supplement", "supplement"):
        return _match("pharma_ayurvedic_herbal", 0.82, "wellness/herbal supplement keyword match")
    if _contains(combined, "sexual oil", "delay spray", "sexual wellness"):
        return _match("beauty_personal_care_sexual_wellness", 0.86, "sexual wellness keyword match")
    if _contains(combined, "skin care", "skin whitening", "facial", "facial scrub", "soap", "toothbrush", "hair care products", "hair care", "hair growth", "beauty products", "manicure", "pedicure"):
        return _match("beauty_personal_care_skin_hair", 0.84, "skin/hair care keyword match")
    if _contains(combined, "beauty tweezer", "eyebrow tweezer", "hair straightener", "hair styler", "electric head lice comb", "hair extensions", "hair wig", "lace wig", "virgin hairs"):
        return _match("beauty_personal_care_skin_hair", 0.84, "beauty/hair accessory keyword match")
    if _contains(combined, "cosmetic", "cosmetics", "makeup", "beauty product"):
        return _match("beauty_personal_care_cosmetics", 0.84, "cosmetics keyword match")
    if _contains(combined, "bees wax", "beeswax", "wax"):
        return _match("industrial_lubricants_chemicals", 0.76, "wax/raw material keyword match", needs_review=True)
    if _contains(combined, "plantation work", "plantation", "agriculture service"):
        return _match("agriculture", 0.76, "agriculture/plantation keyword match", needs_review=True)
    if _contains(combined, "ac installation", "installation service", "rental service", "rental services", "turnkey projects"):
        return _match("services_engineering_projects", 0.76, "installation/rental service keyword match", needs_review=True)
    if _contains(combined, "gym accessories", "exercise resistance band", "resistance bands", "motion sickness belt"):
        return _match("sports_fitness", 0.76, "sports/fitness keyword match", needs_review=True)
    if _contains(combined, "revolving stool", "stool", "chair", "chairs", "dermatology chairs", "side table", "sofa side table", "drawing table"):
        return _match("furniture_tables_seating", 0.82, "furniture seating/table keyword match", needs_review=True)
    if _contains(combined, "stainless steel table", "ss dining table", "centre table", "coffee glass table", "portable cabins", "portable cabin", "office cabins", "container office cabins", "porta cabins", "portable bunkhouse"):
        return _match("furniture_tables_seating", 0.78, "furniture/prefab seating keyword match", needs_review=True)
    if _contains(combined, "leather bag", "leather bags", "combo bag", "shoulder bag", "ladies hand bag", "ladies handbag", "handbag", "jute bag", "chest bag", "wallet", "wallets", "leather wallet", "men leather wallet", "cheque book wallet"):
        return _match("bags_luggage_travel", 0.82, "bag/luggage keyword match")
    if _contains(combined, "umbrella"):
        return _match("bags_luggage", 0.72, "umbrella/travel accessory keyword match", needs_review=True)
    if _contains(combined, "kitchen", "lunch box", "straw", "bottle"):
        return _match("home_kitchen_kitchenware", 0.8, "kitchenware keyword match", needs_review=True)
    if _contains(combined, "candle holder", "decorative lamp", "table lamp"):
        return _match("home_kitchen_lighting_decor", 0.8, "home decor keyword match", needs_review=True)

    # Recapture high-confidence long-tail rows that came from noisy IndiaMART
    # electronics buckets but clearly belong elsewhere.
    if _contains(combined, "binocular", "binoculars", "nikon prostaff", "nikon monarch", "nikon aculon", "telescope imported", "reflected telescope"):
        return _match("electronics_optics", 0.84, "optics/binocular keyword match", needs_review=True)
    if _contains(combined, "medical lamp", "medical lamps", "xenon lamp", "ophthalmoscope lamp", "otoscope lamp", "surgical light"):
        return _match("medical_devices_surgical", 0.82, "medical lamp keyword match", needs_review=True)
    if _contains(combined, "ventilator", "hematology analyzer", "haematology analyzer", "esr analyzer", "nephelometric analyzer", "breath analyser", "breath analyzer", "eye testing drum", "laser therapy", "nursing simulator", "nursing manikin", "human torso model", "knee joint model", "foot joint section model"):
        return _match("medical_devices_diagnostic", 0.82, "medical diagnostic equipment keyword match", needs_review=True)
    if _contains(combined, "dental products", "dental alginate", "cheek retractor", "dental rubber dam", "intradental brush", "rvg sleeves", "dental impression trays"):
        return _match("medical_devices_surgical", 0.82, "dental product keyword match", needs_review=True)
    if _contains(combined, "spectrometer", "gcms", "icp mass", "sieve shaker", "test sieves", "chemical reaction engineering", "isothermal batch reactor", "combined flow reactor", "semi batch reactor", "liquid phase chemical reactor", "test rig", "overhead stirrer", "laboratory stirrer", "burette", "burettes", "measuring cylinder", "friedrichs condenser", "overflow cup"):
        return _match("industrial_lab_testing", 0.82, "lab/scientific long-tail keyword match", needs_review=True)
    if _contains(combined, "sodium hypochlorite", "liquid chlorine", "potassium nitrate", "malic acid", "iso propyl alcohol", "isopropyl alcohol", "acetic acid glacial", "caustic potash", "n butanol", "dicyandiamide", "trimethylolpropane", "engineered fluid", "fluorinated fluid", "heat transfer fluids"):
        return _match("industrial_lubricants_chemicals", 0.82, "industrial/lab chemical long-tail keyword match", needs_review=True)
    if _contains(combined, "semiconductor fuse", "specialty fuse", "bussmann", "eaton fuse", "wickmann", "ferraz", "jean muller"):
        return _match("industrial_electrical_components", 0.84, "industrial fuse keyword match", needs_review=True)
    if _contains(combined, "siemens cpu", "siemens products", "6es7", "6es5", "6fc5", "6gk7", "kv-c", "op-30589", "simatic"):
        return _match("industrial_automation", 0.84, "industrial automation controller keyword match", needs_review=True)
    if _contains(combined, "l-wrench", "l wrench", "can wrench", "combination wrench", "nut spinner", "link extractor", "ratchet ring spanner", "podger ratchet", "bench vice", "spring dividers", "outside spring calipers", "thread repair kit", "micrometer", "mig weld hammer", "tool balancer", "key duplicator", "replacement blade"):
        return _match("industrial_tools", 0.82, "industrial tool long-tail keyword match", needs_review=True)
    if _contains(combined, "gumboot", "gum boot", "arc flash", "flash suit", "electrical insulating gloves", "safety switch", "dome mirror", "wheel lock", "plastic safety device"):
        return _match("industrial_safety", 0.8, "industrial safety long-tail keyword match", needs_review=True)
    if _contains(combined, "mobile flip cover", "magnetic mobile flip cover", "lcd screen separator", "touch separator", "lcd separator"):
        return _match("electronics_mobile_accessories", 0.82, "mobile accessory long-tail keyword match", needs_review=True)
    if _contains(combined, "cisco switch", "cisco catalyst", "aruba", "wi-fi 6e ap"):
        return _match("electronics_networking", 0.84, "networking equipment long-tail keyword match", needs_review=True)
    if _contains(combined, "door bell", "ding dong bell", "musical door bell", "electronic door bell"):
        return _match("electronics_access_control", 0.78, "door bell/access keyword match", needs_review=True)
    if _contains(combined, "room freshener", "air freshener", "air revitaliser", "liquid room freshener"):
        return _match("cleaning_supplies_products", 0.78, "air freshener keyword match", needs_review=True)
    if _contains(combined, "anchor bolt", "flush anchor", "undercut anchor", "transit frame", "chip board screws", "round pin", "glass bracket", "jumbo clamp", "hand gluer"):
        return _match("industrial_tools", 0.76, "hardware/tool long-tail keyword match", needs_review=True)
    if _contains(combined, "paperboard", "paper board", "kappa board", "duplex paper board", "handmade paper"):
        return _match("packaging", 0.78, "paperboard/packaging keyword match", needs_review=True)
    if _contains(combined, "hotel booking", "hotel booking services", "valet cleaning service", "car hire service", "double cottage"):
        return _match("services", 0.72, "hospitality/service keyword match", needs_review=True)
    if _contains(combined, "boxing bag", "boxing ring", "teeth guard", "upper cut", "punching bag", "puching beg"):
        return _match("sports_fitness", 0.78, "boxing/sports equipment keyword match", needs_review=True)
    if _contains(combined, "yantra", "durga yantra", "kuber yantra", "shree yantra", "akhand copper meru"):
        return _match("decor_collectibles", 0.76, "yantra/decor keyword match", needs_review=True)
    if _contains(combined, "garden genie gloves", "bird feeder", "garden stick", "water cans"):
        return _match("agriculture_machinery", 0.72, "garden/agriculture accessory keyword match", needs_review=True)

    if raw_category.startswith("electronics"):
        return _match("electronics_generic", 0.62, "legacy electronics category fallback", needs_review=True)

    return _match("unknown", 0.2, "no deterministic taxonomy rule matched", needs_review=True)
