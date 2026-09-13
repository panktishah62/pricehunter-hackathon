from __future__ import annotations

from typing import Protocol

from app.whatsapp_flows.models import WhatsAppFlowDataExchangeRequest, WhatsAppFlowScreenResponse


class WhatsAppFlowProvider(Protocol):
    flow_key: str
    flow_id_setting_name: str

    async def build_response(self, request: WhatsAppFlowDataExchangeRequest) -> WhatsAppFlowScreenResponse:
        ...
