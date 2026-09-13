from __future__ import annotations

import logging
from uuid import uuid4

from app.config import settings
from app.whatsapp_flows.gold_live_rates import GoldLiveRatesFlowProvider
from app.whatsapp_flows.models import WhatsAppFlowDataExchangeRequest, WhatsAppFlowScreenResponse

logger = logging.getLogger(__name__)

_PROVIDERS = {
    GoldLiveRatesFlowProvider.flow_key: GoldLiveRatesFlowProvider(),
}


def _provider_for_key(flow_key: str | None):
    return _PROVIDERS.get((flow_key or "").strip())


def _normalize_request(request: WhatsAppFlowDataExchangeRequest) -> WhatsAppFlowDataExchangeRequest:
    data = dict(request.data or {})
    if not data.get("flow_key"):
        data["flow_key"] = "gold_live_rates"
    if not data.get("search_id") and request.flow_token:
        token = request.flow_token.strip()
        if ":" in token:
            parts = token.split(":")
            if len(parts) >= 2 and parts[1].strip():
                data["search_id"] = parts[1].strip()
        elif token:
            data["search_id"] = token
    return request.model_copy(update={"data": data})


async def build_flow_data_exchange_response(payload: dict) -> dict:
    request = _normalize_request(WhatsAppFlowDataExchangeRequest.model_validate(payload))
    if request.action == "ping":
        return {"data": {"status": "active"}}
    if (request.data or {}).get("error"):
        return {"data": {"acknowledged": True}}

    flow_key = str(request.data.get("flow_key") or "").strip()
    provider = _provider_for_key(flow_key)
    if provider is None:
        response = WhatsAppFlowScreenResponse(
            screen=request.screen or "UNSUPPORTED_FLOW",
            data={
                "title": "Unsupported flow",
                "summary": "No flow provider is registered for this request.",
                "rows": [],
                "actions": [],
            },
        )
        return response.model_dump(mode="json")

    response = await provider.build_response(request)
    return response.model_dump(mode="json")


async def send_flow_message_for_category(
    *,
    to: str,
    flow_key: str,
    search_id: str,
    body: str,
    session_id: str | None = None,
) -> bool:
    provider = _provider_for_key(flow_key)
    if provider is None:
        logger.warning("No WhatsApp flow provider found for flow_key=%s", flow_key)
        return False

    flow_id = getattr(settings, provider.flow_id_setting_name, "")
    if not settings.whatsapp_flows_enabled or not flow_id:
        return False

    from app.services.whatsapp import send_flow_message

    await send_flow_message(
        to,
        body=body,
        flow_id=flow_id,
        flow_cta=settings.whatsapp_gold_live_rates_flow_cta,
        flow_token=f"{flow_key}:{search_id}:{uuid4()}",
        flow_action_payload={
            "data": {
                "flow_key": flow_key,
                "search_id": search_id,
            }
        },
        session_id=session_id,
        search_id=search_id,
    )
    return True
