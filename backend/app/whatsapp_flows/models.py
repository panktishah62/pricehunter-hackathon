from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class WhatsAppFlowRateRow(BaseModel):
    row_id: str
    dealer_name: str
    rate_text: str
    meta_text: str = ""
    action_text: str = ""


class WhatsAppFlowAction(BaseModel):
    action_id: str
    label: str


class WhatsAppFlowDataExchangeRequest(BaseModel):
    action: str = "INIT"
    screen: Optional[str] = None
    flow_token: Optional[str] = None
    version: Optional[str] = None
    data: dict[str, Any] = Field(default_factory=dict)


class WhatsAppFlowScreenResponse(BaseModel):
    screen: str
    data: dict[str, Any] = Field(default_factory=dict)
