from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import secrets
import uuid
from typing import Any, Literal

from motor.motor_asyncio import AsyncIOMotorCollection
from pymongo.errors import DuplicateKeyError

from app.database import (
    supplier_pilot_acceptances_collection,
    supplier_vendors_collection,
    vendor_offerings_collection,
    zwig_vendor_category_links_collection,
    zwig_vendors_collection,
)

AGREEMENT_VERSION = "supplier-pilot-v1-2026-07-01"
PILOT_STATUS_PENDING = "Pending"
PILOT_STATUS_ACTIVE = "Active"
SupplierCollectionName = Literal["pricehunter.vendors", "zwig_vendors.vendors"]


@dataclass(frozen=True)
class SupplierPilotRecord:
    collection_name: SupplierCollectionName
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
        cleaned = _clean(value)
        if cleaned:
            return cleaned
    return ""


def _latest_source_profile(doc: dict[str, Any]) -> dict[str, Any]:
    profiles = doc.get("source_profiles") or []
    if not isinstance(profiles, list) or not profiles:
        return {}
    return profiles[-1] if isinstance(profiles[-1], dict) else {}


def _source_profile_categories(profile: dict[str, Any]) -> list[str]:
    catalog = profile.get("catalog_summary") or {}
    categories = catalog.get("categories") or []
    names: list[str] = []
    for category in categories:
        if isinstance(category, dict):
            name = _clean(category.get("category_name"))
        else:
            name = _clean(category)
        if name and name not in names:
            names.append(name)
    return names[:8]


def _base_supplier_details(doc: dict[str, Any]) -> dict[str, Any]:
    profile = _latest_source_profile(doc)
    identity = profile.get("vendor_identity") or {}
    contact = profile.get("contact_details") or {}
    verification = profile.get("verification") or {}

    company_name = _first_present(
        doc.get("name"),
        doc.get("company_name"),
        identity.get("supplier_name"),
        identity.get("title"),
    )
    contact_person = _first_present(
        doc.get("contact_person"),
        identity.get("contact_person"),
        (profile.get("business_details") or {}).get("company_ceo"),
    )
    phone_number = _first_present(
        doc.get("phone"),
        doc.get("phone_primary"),
        doc.get("phone_number"),
        doc.get("phone_numbers"),
        contact.get("phone_primary"),
    )
    gst = _first_present(doc.get("gst"), contact.get("gst"), verification.get("gst_masked"))
    city = _first_present(doc.get("city"), contact.get("city"))
    address = _first_present(doc.get("address"), contact.get("address"))

    categories: list[str] = []
    for value in [
        doc.get("category"),
        doc.get("categories"),
        doc.get("category_ids"),
        doc.get("business_types"),
        identity.get("keywords"),
    ]:
        if isinstance(value, list):
            for item in value:
                cleaned = _clean(item)
                if cleaned and cleaned not in categories:
                    categories.append(cleaned)
        else:
            cleaned = _clean(value)
            if cleaned and cleaned not in categories:
                categories.append(cleaned)
    for name in _source_profile_categories(profile):
        if name not in categories:
            categories.append(name)

    return {
        "supplier_id": _first_present(doc.get("vendor_id"), doc.get("supplier_id"), doc.get("_id")),
        "company_name": company_name or "Supplier",
        "contact_person": contact_person,
        "phone_number": phone_number,
        "gst": gst,
        "city": city,
        "address": address,
        "categories": categories[:10],
        "website": _first_present(doc.get("website"), profile.get("supplier_url")),
    }


async def _category_labels_for_supplier(record: SupplierPilotRecord) -> list[str]:
    vendor_id = record.document.get("vendor_id")
    if not vendor_id:
        return []

    if record.collection_name == "pricehunter.vendors":
        pipeline = [
            {"$match": {"vendor_id": vendor_id}},
            {"$group": {"_id": "$category_id", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 8},
        ]
        return [
            _clean(row.get("_id"))
            for row in await vendor_offerings_collection.aggregate(pipeline).to_list(length=8)
            if _clean(row.get("_id"))
        ]

    rows = await zwig_vendor_category_links_collection.find(
        {"vendor_id": vendor_id},
        {"_id": 0, "category_id": 1},
    ).to_list(length=8)
    return [_clean(row.get("category_id")) for row in rows if _clean(row.get("category_id"))]


async def supplier_details(record: SupplierPilotRecord) -> dict[str, Any]:
    details = _base_supplier_details(record.document)
    category_labels = await _category_labels_for_supplier(record)
    for label in category_labels:
        if label not in details["categories"]:
            details["categories"].append(label)
    details["categories"] = details["categories"][:10]
    return details


async def find_supplier_by_pilot_token(token: str) -> SupplierPilotRecord | None:
    cleaned_token = _clean(token)
    if not cleaned_token:
        return None

    doc = await supplier_vendors_collection.find_one({"pilotToken": cleaned_token})
    if doc:
        return SupplierPilotRecord("pricehunter.vendors", supplier_vendors_collection, doc)

    doc = await zwig_vendors_collection.find_one({"pilotToken": cleaned_token})
    if doc:
        return SupplierPilotRecord("zwig_vendors.vendors", zwig_vendors_collection, doc)

    return None


def public_pilot_state(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "pilotAccepted": bool(doc.get("pilotAccepted")),
        "pilotAcceptedAt": doc.get("pilotAcceptedAt"),
        "pilotAgreementVersion": doc.get("pilotAgreementVersion") or AGREEMENT_VERSION,
        "pilotStatus": doc.get("pilotStatus") or PILOT_STATUS_PENDING,
    }


async def get_pilot_payload(token: str) -> dict[str, Any] | None:
    record = await find_supplier_by_pilot_token(token)
    if record is None:
        return None
    return {
        "supplier": await supplier_details(record),
        **public_pilot_state(record.document),
        "agreementVersion": record.document.get("pilotAgreementVersion") or AGREEMENT_VERSION,
    }


async def accept_pilot(token: str, *, ip: str | None, user_agent: str | None) -> dict[str, Any] | None:
    record = await find_supplier_by_pilot_token(token)
    if record is None:
        return None

    existing_status = public_pilot_state(record.document)
    supplier_id = _first_present(record.document.get("vendor_id"), record.document.get("supplier_id"), record.document.get("_id"))
    accepted_at = _now()
    agreement_version = AGREEMENT_VERSION

    if not existing_status["pilotAccepted"]:
        acceptance_doc = {
            "acceptance_id": f"pilot_acceptance:{uuid.uuid4().hex}",
            "supplier_id": supplier_id,
            "supplier_collection": record.collection_name,
            "pilot_token": token,
            "accepted_at": accepted_at,
            "agreement_version": agreement_version,
            "ip_address": ip,
            "user_agent": user_agent,
        }
        await supplier_pilot_acceptances_collection.insert_one(acceptance_doc)
        await record.collection.update_one(
            {"_id": record.document["_id"]},
            {
                "$set": {
                    "pilotAccepted": True,
                    "pilotAcceptedAt": accepted_at,
                    "pilotAgreementVersion": agreement_version,
                    "pilotAcceptedIP": ip,
                    "pilotAcceptedUserAgent": user_agent,
                    "pilotStatus": PILOT_STATUS_ACTIVE,
                    "updated_at": accepted_at,
                }
            },
        )
        record.document.update(
            {
                "pilotAccepted": True,
                "pilotAcceptedAt": accepted_at,
                "pilotAgreementVersion": agreement_version,
                "pilotAcceptedIP": ip,
                "pilotAcceptedUserAgent": user_agent,
                "pilotStatus": PILOT_STATUS_ACTIVE,
            }
        )

    return {
        "supplier": await supplier_details(record),
        **public_pilot_state(record.document),
        "agreementVersion": agreement_version,
    }


async def generate_unique_pilot_token(collection: AsyncIOMotorCollection) -> str:
    for _ in range(8):
        token = secrets.token_urlsafe(32)
        if not await supplier_vendors_collection.find_one({"pilotToken": token}, {"_id": 1}) and not await zwig_vendors_collection.find_one({"pilotToken": token}, {"_id": 1}):
            return token
    raise RuntimeError("Could not generate a unique supplier pilot token")


async def ensure_pilot_token(collection: AsyncIOMotorCollection, supplier_filter: dict[str, Any]) -> str | None:
    doc = await collection.find_one(supplier_filter, {"pilotToken": 1})
    if not doc:
        return None
    if _clean(doc.get("pilotToken")):
        return _clean(doc.get("pilotToken"))

    for _ in range(8):
        token = await generate_unique_pilot_token(collection)
        try:
            result = await collection.update_one(
                {**supplier_filter, "$or": [{"pilotToken": {"$exists": False}}, {"pilotToken": ""}, {"pilotToken": None}]},
                {"$set": {"pilotToken": token, "pilotStatus": PILOT_STATUS_PENDING, "updated_at": _now()}},
            )
            if result.modified_count:
                return token
        except DuplicateKeyError:
            continue

    refreshed = await collection.find_one(supplier_filter, {"pilotToken": 1})
    return _clean((refreshed or {}).get("pilotToken")) or None
