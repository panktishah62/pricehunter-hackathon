from __future__ import annotations

import json
from datetime import datetime, timezone

from app.categories.gold.selection import plan_gold_bullion_query
from app.database import db
from app.models.schemas import StructuredQuery

vendor_searches_collection = db["vendor_searches"]


def _structured_query_from_search_doc(search_doc: dict) -> StructuredQuery:
    product = str(search_doc.get("product") or "")
    collected = search_doc.get("collected") or {}
    location = str(collected.get("location") or "unknown")
    return StructuredQuery(
        product=product,
        category=str(collected.get("category") or "gold"),
        location=location,
        intent=str(collected.get("intent") or "cheapest"),
        urgency=str(collected.get("urgency") or "immediate"),
        raw_query=str(collected.get("raw_query") or f"{product} in {location}"),
    )


def _parse_response_json(payload: dict) -> dict | None:
    interactive = payload.get("interactive") or {}
    reply = interactive.get("nfm_reply") or {}
    response_json = reply.get("response_json")
    if isinstance(response_json, str):
        try:
            return json.loads(response_json)
        except json.JSONDecodeError:
            return None
    if isinstance(response_json, dict):
        return response_json
    return None


def _format_more_rates(plan) -> str:
    if not plan.live_results:
        return "I couldn't find more live bullion rates right now."
    lines = [f"More live bullion rates for {plan.city or 'your city'}:"]
    for index, result in enumerate(plan.live_results[5:10], start=6):
        price_text = f"Rs {result.price:,.0f}" if result.price is not None else "Rate pending"
        dealer_name = result.name.split(" | ", 1)[0]
        lines.append(f"{index}. {dealer_name} | {price_text}")
    return "\n".join(lines)


def _format_whatsapp_ready_vendors(plan) -> str:
    whatsapp_vendors = [vendor for vendor in plan.discovered_vendors if vendor.whatsapp_available]
    if not whatsapp_vendors:
        return (
            "I don't have direct WhatsApp-enabled bullion dealers in this shortlist yet. "
            "I'm continuing with live rates and call-ready dealers."
        )
    lines = ["WhatsApp-ready bullion dealers from this shortlist:"]
    for vendor in whatsapp_vendors[:5]:
        phone = vendor.phone or "number pending"
        lines.append(f"- {vendor.name} | {phone}")
    return "\n".join(lines)


async def maybe_handle_flow_reply(
    *,
    payload: dict,
    to_phone: str,
    session_id: str,
) -> bool:
    from app.services.whatsapp import send_text_message

    response = _parse_response_json(payload)
    if not response:
        return False

    flow_key = str(response.get("flow_key") or "").strip()
    if flow_key != "gold_live_rates":
        return False

    search_id = str(response.get("search_id") or "").strip()
    next_action = str(response.get("next_action") or "").strip()
    if not search_id:
        await send_text_message(to_phone, "I couldn't identify that live-rates request. Please search again.", session_id=session_id)
        return True

    search_doc = await vendor_searches_collection.find_one({"search_id": search_id})
    if not search_doc:
        await send_text_message(to_phone, "That live-rates board has expired. Please search again for a fresh snapshot.", session_id=session_id)
        return True

    query = _structured_query_from_search_doc(search_doc)
    plan = await plan_gold_bullion_query(query)
    await vendor_searches_collection.update_one(
        {"search_id": search_id},
        {"$set": {"last_flow_reply_at": datetime.now(timezone.utc), "last_flow_action": next_action}},
    )

    if next_action == "call_top_dealers":
        message = (
            "I’m already calling the top bullion dealers from this shortlist. "
            "I’ll share confirmed quotes here as they come in."
        )
    elif next_action == "whatsapp_top_dealers":
        message = _format_whatsapp_ready_vendors(plan)
    elif next_action == "show_more_dealers":
        message = _format_more_rates(plan)
    else:
        message = "I’ve noted your live-rates selection."

    await send_text_message(to_phone, message, session_id=session_id, search_id=search_id)
    return True
