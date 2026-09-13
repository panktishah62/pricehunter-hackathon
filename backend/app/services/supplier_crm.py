from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import csv
import hashlib
import io
import json
import re
import uuid
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorCollection

from app.database import (
    call_attempts_collection,
    current_price_snapshots_collection,
    price_observations_collection,
    supplier_activity_collection,
    get_supplier_documents_bucket,
    supplier_documents_collection,
    supplier_loi_agreements_collection,
    supplier_vendors_collection,
    vendor_capabilities_collection,
    vendor_offerings_collection,
    zwig_vendor_category_links_collection,
    zwig_vendors_collection,
)
from app.services import supplier_pilot

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
LOI_VERSION = "supplier-loi-v1-2026-07-01"
EXTRACTION_VERSION = "supplier-document-extraction-v1"


@dataclass(frozen=True)
class SupplierRecord:
    source: str
    collection: AsyncIOMotorCollection
    document: dict[str, Any]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    return str(value)


def _first_present(*values: Any) -> str:
    for value in values:
        if isinstance(value, list):
            for item in value:
                cleaned = _clean(item)
                if cleaned:
                    return cleaned
            continue
        cleaned = _clean(value)
        if cleaned:
            return cleaned
    return ""


def _safe_regex(value: str) -> dict[str, str]:
    return {"$regex": re.escape(value), "$options": "i"}


def _object_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except Exception:
        return None


def _supplier_id(doc: dict[str, Any]) -> str:
    return _first_present(doc.get("vendor_id"), doc.get("supplier_id"), doc.get("_id"))


def _public_doc(doc: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in doc.items() if key not in {"_id", "gridfs_file_id"}}


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "supplier"


def _latest_source_profile(doc: dict[str, Any]) -> dict[str, Any]:
    profiles = doc.get("source_profiles") or []
    if not isinstance(profiles, list) or not profiles:
        return {}
    return profiles[-1] if isinstance(profiles[-1], dict) else {}


def _profile_sections(doc: dict[str, Any]) -> dict[str, Any]:
    profile = _latest_source_profile(doc)
    return {
        "identity": profile.get("vendor_identity") or {},
        "contact": profile.get("contact_details") or {},
        "verification": profile.get("verification") or {},
        "business": profile.get("business_details") or {},
        "catalog": profile.get("catalog_summary") or {},
    }


def _supplier_base(doc: dict[str, Any]) -> dict[str, Any]:
    sections = _profile_sections(doc)
    identity = sections["identity"]
    contact = sections["contact"]
    verification = sections["verification"]
    business = sections["business"]

    name = _first_present(doc.get("name"), doc.get("company_name"), identity.get("supplier_name"), identity.get("title"))
    phone = _first_present(doc.get("phone"), doc.get("phone_primary"), doc.get("phone_numbers"), contact.get("phone_primary"))
    city = _first_present(doc.get("city"), contact.get("city"))
    address = _first_present(doc.get("address"), contact.get("address"))
    website = _first_present(doc.get("website"), doc.get("supplier_url"), _latest_source_profile(doc).get("supplier_url"))

    return {
        "supplier_id": _supplier_id(doc),
        "name": name or "Supplier",
        "company_name": name or "Supplier",
        "contact_person": _first_present(doc.get("contact_person"), identity.get("contact_person"), business.get("company_ceo")),
        "phone_number": phone,
        "city": city,
        "address": address,
        "website": website,
        "gst": _first_present(doc.get("gst"), contact.get("gst"), verification.get("gst_masked")),
        "verification": {
            "trustseal": bool(verification.get("trustseal") or doc.get("trustseal")),
            "gst": _first_present(doc.get("gst"), contact.get("gst"), verification.get("gst_masked")),
            "rating": _first_present(doc.get("rating"), verification.get("rating")),
            "response_rate": _first_present(doc.get("response_rate"), verification.get("response_rate")),
        },
        "business_details": {
            "year_established": _first_present(business.get("year_established"), doc.get("year_established")),
            "turnover": _first_present(business.get("annual_turnover"), business.get("turnover"), doc.get("turnover")),
            "business_type": _first_present(business.get("business_type"), doc.get("business_type")),
        },
        "pilotStatus": doc.get("pilotStatus") or supplier_pilot.PILOT_STATUS_PENDING,
        "pilotAccepted": bool(doc.get("pilotAccepted")),
        "pilotAcceptedAt": doc.get("pilotAcceptedAt"),
        "pilotToken": _clean(doc.get("pilotToken")),
        "commercialDiscussionStarted": bool(doc.get("commercialDiscussionStarted")),
        "subscriptionStatus": _first_present(doc.get("subscriptionStatus")) or "Pending",
        "publicSlug": _clean(doc.get("publicSlug")),
        "source": _first_present(doc.get("source"), doc.get("source_platform")),
    }


async def _categories_for_pricehunter(vendor_ids: list[str]) -> dict[str, list[str]]:
    if not vendor_ids:
        return {}
    pipeline = [
        {"$match": {"vendor_id": {"$in": vendor_ids}}},
        {
            "$group": {
                "_id": "$vendor_id",
                "categories": {"$addToSet": "$category_id"},
                "subcategories": {"$addToSet": "$subcategory_id"},
                "canonical_categories": {"$addToSet": "$canonical_category_id"},
                "canonical_subcategories": {"$addToSet": "$canonical_subcategory_id"},
                "taxonomy_labels": {"$addToSet": "$taxonomy_path_labels"},
                "offerings_count": {"$sum": 1},
            }
        },
    ]
    out: dict[str, list[str]] = {}
    async for row in vendor_offerings_collection.aggregate(pipeline):
        labels = []
        for path_labels in row.get("taxonomy_labels") or []:
            if isinstance(path_labels, list) and path_labels:
                cleaned = " > ".join(_clean(label) for label in path_labels if _clean(label))
                if cleaned and cleaned not in labels:
                    labels.append(cleaned)
        for value in [*(row.get("canonical_categories") or []), *(row.get("canonical_subcategories") or [])]:
            cleaned = _clean(value)
            if cleaned and cleaned not in labels:
                labels.append(cleaned)
        for value in [*(row.get("categories") or []), *(row.get("subcategories") or [])]:
            cleaned = _clean(value)
            if cleaned and cleaned not in labels:
                labels.append(cleaned)
        out[str(row["_id"])] = labels[:10]
    return out


async def _categories_from_capabilities(vendor_ids: list[str]) -> dict[str, list[str]]:
    if not vendor_ids:
        return {}
    rows = await vendor_capabilities_collection.find(
        {"vendor_id": {"$in": vendor_ids}},
        {
            "_id": 0,
            "vendor_id": 1,
            "taxonomy_path_labels": 1,
            "canonical_category_id": 1,
            "canonical_subcategory_id": 1,
            "taxonomy_node_id": 1,
            "capability_confidence": 1,
            "routing_confidence": 1,
            "quality_status": 1,
            "offering_count": 1,
        },
    ).to_list(length=max(len(vendor_ids) * 20, 2000))

    def _label(row: dict[str, Any]) -> str:
        path_labels = row.get("taxonomy_path_labels") or []
        if isinstance(path_labels, list) and path_labels:
            return " > ".join(_clean(label) for label in path_labels if _clean(label))
        return _first_present(row.get("canonical_subcategory_id"), row.get("taxonomy_node_id"), row.get("canonical_category_id"))

    rows.sort(
        key=lambda row: (
            0 if row.get("quality_status") == "review" else 1,
            len(row.get("taxonomy_path_labels") or []),
            float(row.get("routing_confidence") or row.get("capability_confidence") or 0),
            int(row.get("offering_count") or 0),
        ),
        reverse=True,
    )
    out: dict[str, list[str]] = {}
    for row in rows:
        vendor_id = _clean(row.get("vendor_id"))
        label = _label(row)
        if not vendor_id or not label:
            continue
        if row.get("quality_status") == "review":
            continue
        out.setdefault(vendor_id, [])
        if label not in out[vendor_id]:
            out[vendor_id].append(label)
    return {vendor_id: labels[:10] for vendor_id, labels in out.items()}


async def _offering_counts(vendor_ids: list[str]) -> dict[str, int]:
    if not vendor_ids:
        return {}
    pipeline = [
        {"$match": {"vendor_id": {"$in": vendor_ids}}},
        {"$group": {"_id": "$vendor_id", "count": {"$sum": 1}}},
    ]
    return {
        str(row["_id"]): int(row.get("count") or 0)
        async for row in vendor_offerings_collection.aggregate(pipeline)
    }


async def _document_counts(vendor_ids: list[str]) -> dict[str, int]:
    if not vendor_ids:
        return {}
    pipeline = [
        {"$match": {"supplier_id": {"$in": vendor_ids}}},
        {"$group": {"_id": "$supplier_id", "count": {"$sum": 1}}},
    ]
    return {
        str(row["_id"]): int(row.get("count") or 0)
        async for row in supplier_documents_collection.aggregate(pipeline)
    }


async def _gold_categories(vendor_ids: list[str]) -> dict[str, list[str]]:
    if not vendor_ids:
        return {}
    rows = await zwig_vendor_category_links_collection.find(
        {"vendor_id": {"$in": vendor_ids}},
        {"_id": 0, "vendor_id": 1, "category_id": 1},
    ).to_list(length=2000)
    out: dict[str, list[str]] = {}
    for row in rows:
        vendor_id = _clean(row.get("vendor_id"))
        category = _clean(row.get("category_id"))
        if not vendor_id or not category:
            continue
        out.setdefault(vendor_id, [])
        if category not in out[vendor_id]:
            out[vendor_id].append(category)
    return out


async def _metrics_for_supplier(supplier_id: str) -> dict[str, Any]:
    call_query = {
        "$or": [
            {"vendor.vendor_id": supplier_id},
            {"vendor.id": supplier_id},
            {"vendor.vendor_key": supplier_id},
            {"supplier_id": supplier_id},
        ]
    }
    ai_calls = await call_attempts_collection.count_documents(call_query)
    quotes = await price_observations_collection.count_documents({"vendor_id": supplier_id})
    return {
        "aiCallsReceived": ai_calls,
        "quotesSubmitted": quotes,
        "ordersWon": 0,
    }


async def _summarize_records(records: list[SupplierRecord]) -> list[dict[str, Any]]:
    pricehunter_records = [record for record in records if record.source == "pricehunter"]
    gold_records = [record for record in records if record.source == "gold"]
    pricehunter_ids = [_supplier_id(record.document) for record in pricehunter_records if _supplier_id(record.document)]
    gold_ids = [_supplier_id(record.document) for record in gold_records if _supplier_id(record.document)]

    capability_categories = await _categories_from_capabilities([*pricehunter_ids, *gold_ids])
    pricehunter_categories = await _categories_for_pricehunter(pricehunter_ids)
    pricehunter_counts = await _offering_counts(pricehunter_ids)
    gold_categories = await _gold_categories(gold_ids)
    doc_counts = await _document_counts([*pricehunter_ids, *gold_ids])

    summaries: list[dict[str, Any]] = []
    for record in records:
        base = _supplier_base(record.document)
        supplier_id = base["supplier_id"]
        fallback_categories = pricehunter_categories.get(supplier_id) if record.source == "pricehunter" else gold_categories.get(supplier_id)
        categories = capability_categories.get(supplier_id) or fallback_categories
        summaries.append(
            {
                **base,
                "source_collection": record.source,
                "categories": categories or [],
                "offerings_count": pricehunter_counts.get(supplier_id, 0) if record.source == "pricehunter" else 0,
                "documents_count": doc_counts.get(supplier_id, 0),
                "metrics": await _metrics_for_supplier(supplier_id),
            }
        )
    return summaries


async def find_supplier(supplier_id: str) -> SupplierRecord | None:
    cleaned = _clean(supplier_id)
    if not cleaned:
        return None

    object_id = _object_id(cleaned)
    query: dict[str, Any] = {"$or": [{"vendor_id": cleaned}, {"supplier_id": cleaned}, {"publicSlug": cleaned}]}
    if object_id:
        query["$or"].append({"_id": object_id})

    doc = await supplier_vendors_collection.find_one(query)
    if doc:
        return SupplierRecord("pricehunter", supplier_vendors_collection, doc)

    doc = await zwig_vendors_collection.find_one(query)
    if doc:
        return SupplierRecord("gold", zwig_vendors_collection, doc)

    return None


async def list_suppliers(
    *,
    category: str = "",
    city: str = "",
    q: str = "",
    pilot_status: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)

    pricehunter_filter: dict[str, Any] = {}
    gold_filter: dict[str, Any] = {}
    if city:
        city_regex = _safe_regex(city)
        pricehunter_filter["$or"] = [{"city": city_regex}, {"source_profiles.contact_details.city": city_regex}]
        gold_filter["city"] = city_regex
    if q:
        q_regex = _safe_regex(q)
        q_or = [{"name": q_regex}, {"company_name": q_regex}, {"phone": q_regex}, {"website": q_regex}]
        pricehunter_filter.setdefault("$and", []).append({"$or": q_or})
        gold_filter.setdefault("$and", []).append({"$or": [{"name": q_regex}, {"phone_primary": q_regex}, {"website": q_regex}]})
    if pilot_status:
        pricehunter_filter["pilotStatus"] = pilot_status
        gold_filter["pilotStatus"] = pilot_status
    if category:
        category_regex = _safe_regex(category)
        vendor_ids = await vendor_capabilities_collection.distinct(
            "vendor_id",
            {
                "$or": [
                    {"canonical_category_id": category_regex},
                    {"canonical_subcategory_id": category_regex},
                    {"taxonomy_node_id": category_regex},
                    {"taxonomy_path": category_regex},
                    {"taxonomy_path_labels": category_regex},
                    {"sample_products": category_regex},
                ]
            },
        )
        pricehunter_filter.setdefault("$and", []).append({"vendor_id": {"$in": vendor_ids or ["__none__"]}})
        gold_filter.setdefault("$and", []).append({"vendor_id": {"$in": vendor_ids or ["__none__"]}})

    projection = {
        "_id": 1,
        "vendor_id": 1,
        "supplier_id": 1,
        "name": 1,
        "company_name": 1,
        "phone": 1,
        "phone_primary": 1,
        "phone_numbers": 1,
        "city": 1,
        "address": 1,
        "website": 1,
        "gst": 1,
        "source_profiles": {"$slice": -1},
        "pilotToken": 1,
        "pilotStatus": 1,
        "pilotAccepted": 1,
        "pilotAcceptedAt": 1,
        "commercialDiscussionStarted": 1,
        "subscriptionStatus": 1,
        "publicSlug": 1,
    }
    pricehunter_total = await supplier_vendors_collection.count_documents(pricehunter_filter)
    gold_total = await zwig_vendors_collection.count_documents(gold_filter)

    pricehunter_offset = min(offset, pricehunter_total)
    pricehunter_limit = min(limit, max(0, pricehunter_total - offset))
    price_docs = []
    if pricehunter_limit:
        price_docs = await supplier_vendors_collection.find(pricehunter_filter, projection).sort("updated_at", -1).skip(pricehunter_offset).limit(pricehunter_limit).to_list(length=pricehunter_limit)

    remaining = max(0, limit - len(price_docs))
    gold_offset = max(0, offset - pricehunter_total)
    gold_docs = []
    if remaining:
        gold_docs = await zwig_vendors_collection.find(gold_filter, projection).sort("name", 1).skip(gold_offset).limit(remaining).to_list(length=remaining)

    records = [SupplierRecord("pricehunter", supplier_vendors_collection, doc) for doc in price_docs]
    records.extend(SupplierRecord("gold", zwig_vendors_collection, doc) for doc in gold_docs)
    return {
        "suppliers": await _summarize_records(records),
        "total": pricehunter_total + gold_total,
        "limit": limit,
        "offset": offset,
    }


async def ensure_public_slug(record: SupplierRecord) -> str:
    existing = _clean(record.document.get("publicSlug"))
    if existing:
        return existing
    supplier_id = _supplier_id(record.document)
    base = _slugify(_supplier_base(record.document)["company_name"])
    suffix = _slugify(supplier_id)[-10:] if supplier_id else uuid.uuid4().hex[:8]
    slug = f"{base}-{suffix}".strip("-")
    await record.collection.update_one({"_id": record.document["_id"]}, {"$set": {"publicSlug": slug, "updated_at": _now()}})
    record.document["publicSlug"] = slug
    return slug


async def ensure_pilot_link(supplier_id: str, base_url: str) -> dict[str, str] | None:
    record = await find_supplier(supplier_id)
    if record is None:
        return None
    token = await supplier_pilot.ensure_pilot_token(record.collection, {"_id": record.document["_id"]})
    if not token:
        return None
    slug = await ensure_public_slug(record)
    return {
        "token": token,
        "pilot_url": f"{base_url.rstrip('/')}/supplier/pilot/{token}",
        "public_url": f"{base_url.rstrip('/')}/suppliers/{slug}",
    }


async def list_documents(supplier_id: str) -> list[dict[str, Any]]:
    rows = await supplier_documents_collection.find(
        {"supplier_id": supplier_id},
        {"_id": 0, "gridfs_file_id": 0},
    ).sort("uploaded_at", -1).to_list(length=200)
    return rows


async def add_supplier_documents(
    supplier_id: str,
    *,
    files: list[Any],
    document_type: str,
    uploaded_by: str = "",
) -> list[dict[str, Any]]:
    record = await find_supplier(supplier_id)
    if record is None:
        return []

    saved: list[dict[str, Any]] = []
    now = _now()
    for file in files:
        content = await file.read()
        if len(content) > MAX_DOCUMENT_BYTES:
            raise ValueError(f"{file.filename or 'file'} exceeds the 20MB upload limit.")
        document_id = f"supplier_doc:{uuid.uuid4().hex}"
        gridfs_id = await get_supplier_documents_bucket().upload_from_stream(
            file.filename or document_id,
            content,
            metadata={
                "document_id": document_id,
                "supplier_id": supplier_id,
                "content_type": file.content_type,
                "document_type": document_type,
            },
        )
        doc = {
            "document_id": document_id,
            "supplier_id": supplier_id,
            "supplier_collection": record.source,
            "document_type": document_type,
            "original_filename": file.filename or "uploaded-file",
            "content_type": file.content_type or "application/octet-stream",
            "size_bytes": len(content),
            "storage": "gridfs",
            "gridfs_file_id": gridfs_id,
            "uploaded_at": now,
            "uploaded_by": uploaded_by,
            "extraction_status": "queued",
            "review_status": "pending",
        }
        await supplier_documents_collection.insert_one(doc)
        await supplier_activity_collection.insert_one(
            {
                "activity_id": f"supplier_activity:{uuid.uuid4().hex}",
                "supplier_id": supplier_id,
                "activity_type": "document_uploaded",
                "occurred_at": now,
                "metadata": {
                    "document_id": document_id,
                    "document_type": document_type,
                    "filename": file.filename,
                },
            }
        )
        saved.append(_public_doc(doc))
    return saved


async def _read_document_bytes(doc: dict[str, Any]) -> bytes:
    gridfs_file_id = doc.get("gridfs_file_id")
    if gridfs_file_id is None:
        return b""
    stream = await get_supplier_documents_bucket().open_download_stream(gridfs_file_id)
    return await stream.read()


def _decode_text(content: bytes, filename: str, content_type: str) -> tuple[str, bool]:
    lowered_name = filename.lower()
    lowered_type = content_type.lower()
    text_like = (
        lowered_type.startswith("text/")
        or "csv" in lowered_type
        or "json" in lowered_type
        or "html" in lowered_type
        or lowered_name.endswith((".csv", ".txt", ".json", ".html", ".htm", ".md", ".tsv"))
    )
    if not text_like:
        return "", False
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return content.decode(encoding), True
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="ignore"), True


def _pick(row: dict[str, Any], *keys: str) -> str:
    normalized = {str(key).strip().lower().replace(" ", "_"): value for key, value in row.items()}
    for key in keys:
        value = normalized.get(key)
        cleaned = _clean(value)
        if cleaned:
            return cleaned
    return ""


def _parse_price(value: str) -> float | None:
    cleaned = re.sub(r"[^0-9.]", "", value or "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _candidate_from_row(row: dict[str, Any]) -> dict[str, Any] | None:
    product_name = _pick(
        row,
        "product_name",
        "product_name_raw",
        "product",
        "item",
        "item_name",
        "title",
        "name",
        "description",
    )
    if not product_name or len(product_name) < 3:
        return None
    price_raw = _pick(row, "price", "rate", "amount", "mrp", "selling_price", "latest_price")
    unit = _pick(row, "unit", "uom", "pack_size", "packaging", "quantity_unit")
    category = _pick(row, "category", "category_id", "product_category")
    subcategory = _pick(row, "subcategory", "subcategory_id", "sub_category")
    moq = _pick(row, "moq", "minimum_order_quantity", "min_order")
    image_url = _pick(row, "product_image", "image_url", "image", "thumbnail", "photo")
    return {
        "product_name": product_name[:240],
        "category": category,
        "subcategory": subcategory,
        "price": _parse_price(price_raw),
        "price_raw": price_raw,
        "currency": "INR" if price_raw and ("₹" in price_raw or "rs" in price_raw.lower() or "inr" in price_raw.lower()) else "INR",
        "unit": unit,
        "moq": moq,
        "image_url": image_url,
        "attributes": {key: _clean(value) for key, value in row.items() if _clean(value)} if len(row) <= 40 else {},
    }


def _extract_from_csv_text(text: str) -> list[dict[str, Any]]:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    candidates: list[dict[str, Any]] = []
    for row in list(reader)[:200]:
        candidate = _candidate_from_row(row)
        if candidate:
            candidates.append(candidate)
    return candidates


def _extract_from_json_text(text: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    rows = parsed if isinstance(parsed, list) else parsed.get("products") if isinstance(parsed, dict) else []
    if not isinstance(rows, list):
        return []
    candidates: list[dict[str, Any]] = []
    for row in rows[:200]:
        if not isinstance(row, dict):
            continue
        candidate = _candidate_from_row(row)
        if candidate:
            candidates.append(candidate)
    return candidates


def _extract_from_loose_text(text: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    price_pattern = re.compile(r"(?P<name>[A-Za-z0-9][A-Za-z0-9 ./()&,+-]{4,120})\s+(?:₹|rs\.?|inr)?\s*(?P<price>\d{1,7}(?:\.\d{1,2})?)", re.I)
    for line in text.splitlines()[:500]:
        cleaned = _clean(line)
        if not cleaned:
            continue
        match = price_pattern.search(cleaned)
        if match:
            candidates.append(
                {
                    "product_name": _clean(match.group("name"))[:240],
                    "category": "",
                    "subcategory": "",
                    "price": _parse_price(match.group("price")),
                    "price_raw": match.group("price"),
                    "currency": "INR",
                    "unit": "",
                    "moq": "",
                    "attributes": {},
                }
            )
    return candidates[:80]


def _infer_categories(candidates: list[dict[str, Any]], fallback: list[str]) -> list[str]:
    labels = []
    for value in fallback:
        cleaned = _clean(value)
        if cleaned and cleaned not in labels:
            labels.append(cleaned)
    text = " ".join(_clean(candidate.get("product_name")).lower() for candidate in candidates[:100])
    keyword_map = {
        "pharmaceuticals": ["tablet", "capsule", "syrup", "injection", "mg", "medicine", "pharma"],
        "electronics": ["cable", "adapter", "charger", "ic", "sensor", "module", "speaker", "headphone"],
        "gold": ["gold", "bullion", "999", "995", "bis"],
        "industrial": ["bearing", "pump", "valve", "motor", "steel", "machine"],
    }
    for label, keywords in keyword_map.items():
        if any(keyword in text for keyword in keywords) and label not in labels:
            labels.append(label)
    return labels[:8]


async def extract_supplier_document(document_id: str) -> dict[str, Any] | None:
    doc = await supplier_documents_collection.find_one({"document_id": document_id})
    if not doc:
        return None

    content = await _read_document_bytes(doc)
    text, is_text_like = _decode_text(content, _clean(doc.get("original_filename")), _clean(doc.get("content_type")))
    supplier_id = _clean(doc.get("supplier_id"))
    supplier_detail_payload = await supplier_detail(supplier_id)
    supplier_categories = (supplier_detail_payload or {}).get("supplier", {}).get("categories", [])

    warnings: list[str] = []
    candidates: list[dict[str, Any]] = []
    if is_text_like and text.strip():
        if doc.get("original_filename", "").lower().endswith(".json") or "json" in _clean(doc.get("content_type")).lower():
            candidates = _extract_from_json_text(text)
        if not candidates:
            candidates = _extract_from_csv_text(text)
        if not candidates:
            candidates = _extract_from_loose_text(text)
        if not candidates:
            warnings.append("No product rows were confidently detected. Review the file manually.")
    else:
        warnings.append("This file type is stored, but automatic text extraction is not enabled yet.")

    categories = _infer_categories(candidates, supplier_categories)
    extraction = {
        "extraction_id": f"supplier_extraction:{uuid.uuid4().hex}",
        "parser_version": EXTRACTION_VERSION,
        "extracted_at": _now(),
        "status": "review_pending" if candidates else "needs_manual_review",
        "confidence": 0.72 if candidates else 0.18,
        "summary": {
            "products_detected": len(candidates),
            "categories_detected": categories,
            "has_prices": any(candidate.get("price") is not None for candidate in candidates),
        },
        "supplier_updates": {
            "categories": categories,
        },
        "product_candidates": candidates[:100],
        "warnings": warnings,
    }
    await supplier_documents_collection.update_one(
        {"document_id": document_id},
        {
            "$set": {
                "extraction": extraction,
                "extraction_status": extraction["status"],
                "review_status": "pending",
                "updated_at": _now(),
            }
        },
    )
    await supplier_activity_collection.insert_one(
        {
            "activity_id": f"supplier_activity:{uuid.uuid4().hex}",
            "supplier_id": supplier_id,
            "activity_type": "document_extracted",
            "occurred_at": _now(),
            "metadata": {
                "document_id": document_id,
                "products_detected": len(candidates),
                "status": extraction["status"],
            },
        }
    )
    updated = await supplier_documents_collection.find_one({"document_id": document_id}) or doc
    return _public_doc(updated)


async def apply_document_extraction(document_id: str) -> dict[str, Any] | None:
    doc = await supplier_documents_collection.find_one({"document_id": document_id})
    if not doc:
        return None
    extraction = doc.get("extraction") or {}
    supplier_id = _clean(doc.get("supplier_id"))
    candidates = extraction.get("product_candidates") or []
    if not supplier_id or not candidates:
        return {"applied": 0, "document": _public_doc(doc)}

    now = _now()
    applied = 0
    for candidate in candidates:
        product_name = _clean(candidate.get("product_name"))
        if not product_name:
            continue
        normalized_name = product_name.lower()
        offering_key = hashlib.sha1(f"{supplier_id}|{normalized_name}".encode("utf-8")).hexdigest()[:24]
        offering_id = f"offering:{offering_key}"
        category = _clean(candidate.get("category")) or (extraction.get("summary", {}).get("categories_detected") or [""])[0]
        await vendor_offerings_collection.update_one(
            {"offering_id": offering_id},
            {
                "$set": {
                    "offering_id": offering_id,
                    "vendor_id": supplier_id,
                    "category_id": category,
                    "subcategory_id": _clean(candidate.get("subcategory")),
                    "product_id": None,
                    "product_name_raw": product_name,
                    "normalized_product_name": normalized_name,
                    "attributes": candidate.get("attributes") or {},
                    "image_url": _clean(candidate.get("image_url")),
                    "images": [_clean(candidate.get("image_url"))] if _clean(candidate.get("image_url")) else [],
                    "source": "supplier_document",
                    "source_url": "",
                    "source_document_id": document_id,
                    "evidence_score": extraction.get("confidence") or 0.5,
                    "supply_confidence": extraction.get("confidence") or 0.5,
                    "moq": candidate.get("moq"),
                    "last_seen_at": now,
                    "is_active": True,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        if candidate.get("price") is not None:
            observation_id = f"price_observation:{uuid.uuid4().hex}"
            price_doc = {
                "observation_id": observation_id,
                "vendor_id": supplier_id,
                "offering_id": offering_id,
                "product_id": None,
                "category_id": category,
                "price": candidate.get("price"),
                "currency": candidate.get("currency") or "INR",
                "unit": _clean(candidate.get("unit")),
                "availability": "unknown",
                "source_type": "supplier_document",
                "source_document_id": document_id,
                "observed_at": now,
                "expires_at": None,
            }
            await price_observations_collection.insert_one(price_doc)
            await current_price_snapshots_collection.update_one(
                {"vendor_id": supplier_id, "offering_id": offering_id},
                {
                    "$set": {
                        "vendor_id": supplier_id,
                        "offering_id": offering_id,
                        "latest_price": candidate.get("price"),
                        "best_recent_price": candidate.get("price"),
                        "currency": candidate.get("currency") or "INR",
                        "unit": _clean(candidate.get("unit")),
                        "availability": "unknown",
                        "confidence": extraction.get("confidence") or 0.5,
                        "last_observed_at": now,
                        "source_type": "supplier_document",
                        "source_document_id": document_id,
                    }
                },
                upsert=True,
            )
        applied += 1

    await supplier_documents_collection.update_one(
        {"document_id": document_id},
        {"$set": {"review_status": "applied", "applied_at": now, "updated_at": now}},
    )
    await supplier_activity_collection.insert_one(
        {
            "activity_id": f"supplier_activity:{uuid.uuid4().hex}",
            "supplier_id": supplier_id,
            "activity_type": "document_extraction_applied",
            "occurred_at": now,
            "metadata": {"document_id": document_id, "offerings_applied": applied},
        }
    )
    updated = await supplier_documents_collection.find_one({"document_id": document_id}) or doc
    return {"applied": applied, "document": _public_doc(updated)}


def _agreement_text(supplier: dict[str, Any]) -> str:
    company = supplier.get("company_name") or "the supplier"
    categories = ", ".join(supplier.get("categories") or []) or "the products listed in the supplier profile"
    return (
        f"{company} agrees to participate in the ZWIG Supplier Pilot for {categories}. "
        "Participation in the pilot is free, has no upfront charges, and is intended to help the supplier receive "
        "verified procurement enquiries from ZWIG partner buyers. The supplier may choose which enquiries to respond to. "
        "If ZWIG consistently generates meaningful commercial opportunities, the supplier agrees to discuss a future "
        "commercial partnership with ZWIG. ZWIG is operated by INCREDIBLE LIFESTYLE SOLUTION PRIVATE LIMITED."
    )


async def generate_loi(supplier_id: str, *, base_url: str, created_by: str = "") -> dict[str, Any] | None:
    detail = await supplier_detail(supplier_id)
    if detail is None:
        return None
    supplier = detail["supplier"]
    token = uuid.uuid4().hex + uuid.uuid4().hex
    created_at = _now()
    agreement_text = _agreement_text(supplier)
    agreement_hash = hashlib.sha256(agreement_text.encode("utf-8")).hexdigest()
    agreement_doc = {
        "agreement_id": f"supplier_loi:{uuid.uuid4().hex}",
        "supplier_id": supplier["supplier_id"],
        "supplier_snapshot": supplier,
        "loiToken": token,
        "agreement_version": LOI_VERSION,
        "agreement_text": agreement_text,
        "agreement_hash": agreement_hash,
        "status": "Pending Signature",
        "created_at": created_at,
        "created_by": created_by,
        "signed_at": None,
        "signed_by": "",
        "signed_ip": "",
        "signed_user_agent": "",
    }
    await supplier_loi_agreements_collection.insert_one(agreement_doc)
    await supplier_activity_collection.insert_one(
        {
            "activity_id": f"supplier_activity:{uuid.uuid4().hex}",
            "supplier_id": supplier["supplier_id"],
            "activity_type": "loi_generated",
            "occurred_at": created_at,
            "metadata": {"agreement_id": agreement_doc["agreement_id"]},
        }
    )
    public_doc = {key: value for key, value in agreement_doc.items() if key != "_id"}
    return {**public_doc, "loi_url": f"{base_url.rstrip('/')}/supplier/loi/{token}"}


async def list_lois(supplier_id: str) -> list[dict[str, Any]]:
    return await supplier_loi_agreements_collection.find(
        {"supplier_id": supplier_id},
        {"_id": 0, "loiToken": 0},
    ).sort("created_at", -1).to_list(length=50)


async def get_loi_payload(token: str) -> dict[str, Any] | None:
    doc = await supplier_loi_agreements_collection.find_one({"loiToken": _clean(token)}, {"_id": 0})
    if not doc:
        return None
    return {
        "agreement": {
            key: doc.get(key)
            for key in [
                "agreement_id",
                "supplier_id",
                "supplier_snapshot",
                "agreement_version",
                "agreement_text",
                "agreement_hash",
                "status",
                "created_at",
                "signed_at",
                "signed_by",
            ]
        }
    }


async def accept_loi(token: str, *, signer_name: str, ip: str | None, user_agent: str | None) -> dict[str, Any] | None:
    doc = await supplier_loi_agreements_collection.find_one({"loiToken": _clean(token)})
    if not doc:
        return None
    signed_at = _now()
    await supplier_loi_agreements_collection.update_one(
        {"_id": doc["_id"]},
        {
            "$set": {
                "status": "Signed",
                "signed_at": signed_at,
                "signed_by": _clean(signer_name),
                "signed_ip": ip or "",
                "signed_user_agent": user_agent or "",
            }
        },
    )
    supplier_id = _clean(doc.get("supplier_id"))
    record = await find_supplier(supplier_id)
    if record:
        await record.collection.update_one(
            {"_id": record.document["_id"]},
            {
                "$set": {
                    "pilotStatus": "Verified Pilot",
                    "loiSigned": True,
                    "loiSignedAt": signed_at,
                    "updated_at": signed_at,
                }
            },
        )
    await supplier_activity_collection.insert_one(
        {
            "activity_id": f"supplier_activity:{uuid.uuid4().hex}",
            "supplier_id": supplier_id,
            "activity_type": "loi_signed",
            "occurred_at": signed_at,
            "metadata": {"agreement_id": doc.get("agreement_id"), "signed_by": _clean(signer_name)},
        }
    )
    return await get_loi_payload(token)


async def _offerings_for_supplier(supplier_id: str) -> list[dict[str, Any]]:
    rows = await vendor_offerings_collection.find(
        {"vendor_id": supplier_id, "is_active": {"$ne": False}},
        {
            "_id": 0,
            "offering_id": 1,
            "product_id": 1,
            "image_url": 1,
            "images": 1,
            "source_image_url": 1,
            "category_id": 1,
            "subcategory_id": 1,
            "canonical_category_id": 1,
            "canonical_subcategory_id": 1,
            "taxonomy_node_id": 1,
            "taxonomy_path_labels": 1,
            "product_name_raw": 1,
            "normalized_product_name": 1,
            "attributes": 1,
            "source_url": 1,
            "moq": 1,
            "last_seen_at": 1,
        },
    ).sort("last_seen_at", -1).limit(80).to_list(length=80)
    offering_ids = [_clean(row.get("offering_id")) for row in rows if _clean(row.get("offering_id"))]
    snapshots = {
        _clean(row.get("offering_id")): row
        for row in await current_price_snapshots_collection.find(
            {"offering_id": {"$in": offering_ids}},
            {"_id": 0, "offering_id": 1, "latest_price": 1, "currency": 1, "unit": 1, "availability": 1, "last_observed_at": 1},
        ).to_list(length=len(offering_ids) or 1)
    }
    products: list[dict[str, Any]] = []
    for row in rows:
        snapshot = snapshots.get(_clean(row.get("offering_id")), {})
        taxonomy_labels = row.get("taxonomy_path_labels") or []
        display_category = _clean(taxonomy_labels[0]) if isinstance(taxonomy_labels, list) and taxonomy_labels else _clean(row.get("canonical_category_id"))
        display_subcategory = (
            _clean(taxonomy_labels[-1])
            if isinstance(taxonomy_labels, list) and len(taxonomy_labels) > 1
            else _clean(row.get("canonical_subcategory_id") or row.get("taxonomy_node_id"))
        )
        if display_subcategory == display_category:
            display_subcategory = ""
        products.append(
            {
                "offering_id": _clean(row.get("offering_id")),
                "product_name": _first_present(row.get("normalized_product_name"), row.get("product_name_raw"), row.get("product_id")),
                "raw_name": _clean(row.get("product_name_raw")),
                "category": display_category or _clean(row.get("category_id")),
                "subcategory": display_subcategory,
                "raw_category": _clean(row.get("category_id")),
                "raw_subcategory": _clean(row.get("subcategory_id")),
                "taxonomy_node_id": _clean(row.get("taxonomy_node_id")),
                "attributes": row.get("attributes") or {},
                "image_url": _first_present(
                    row.get("image_url"),
                    row.get("images"),
                    (row.get("attributes") or {}).get("product_image"),
                    (row.get("attributes") or {}).get("image_url"),
                ),
                "images": [image for image in (row.get("images") or []) if _clean(image)],
                "source_image_url": _first_present(
                    row.get("source_image_url"),
                    (row.get("attributes") or {}).get("source_product_image"),
                    (row.get("attributes") or {}).get("product_image"),
                ),
                "source_url": _clean(row.get("source_url")),
                "moq": row.get("moq"),
                "price": snapshot.get("latest_price"),
                "currency": snapshot.get("currency") or "INR",
                "unit": snapshot.get("unit"),
                "availability": snapshot.get("availability"),
                "last_observed_at": snapshot.get("last_observed_at"),
            }
        )
    return products


async def supplier_detail(supplier_id: str) -> dict[str, Any] | None:
    record = await find_supplier(supplier_id)
    if record is None:
        return None
    summary = (await _summarize_records([record]))[0]
    slug = await ensure_public_slug(record)
    supplier_id_value = summary["supplier_id"]
    return {
        "supplier": {**summary, "publicSlug": slug},
        "products": await _offerings_for_supplier(supplier_id_value) if record.source == "pricehunter" else [],
        "documents": await list_documents(supplier_id_value),
        "lois": await list_lois(supplier_id_value),
        "metrics": await _metrics_for_supplier(supplier_id_value),
    }


async def public_supplier_profile(slug: str) -> dict[str, Any] | None:
    record = await find_supplier(slug)
    if record is None:
        return None
    detail = await supplier_detail(_supplier_id(record.document))
    if detail is None:
        return None
    supplier = detail["supplier"]
    return {
        "supplier": {
            key: supplier.get(key)
            for key in [
                "supplier_id",
                "name",
                "company_name",
                "city",
                "address",
                "website",
                "verification",
                "business_details",
                "pilotStatus",
                "pilotAccepted",
                "publicSlug",
                "categories",
            ]
        },
        "products": detail["products"],
    }
