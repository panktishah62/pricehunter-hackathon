from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.services import request_context, supplier_pilot

router = APIRouter(prefix="/api/supplier/pilot", tags=["supplier-pilot"])


class SupplierPilotDetails(BaseModel):
    supplier_id: str
    company_name: str
    contact_person: str = ""
    phone_number: str = ""
    gst: str = ""
    city: str = ""
    address: str = ""
    categories: list[str] = Field(default_factory=list)
    website: str = ""


class SupplierPilotResponse(BaseModel):
    supplier: SupplierPilotDetails
    pilotAccepted: bool = False
    pilotAcceptedAt: datetime | None = None
    pilotAgreementVersion: str
    pilotStatus: str
    agreementVersion: str


class SupplierPilotAcceptRequest(BaseModel):
    agreed: bool


@router.get("/{token}", response_model=SupplierPilotResponse)
async def get_supplier_pilot(token: str) -> dict[str, Any]:
    payload = await supplier_pilot.get_pilot_payload(token)
    if payload is None:
        raise HTTPException(status_code=404, detail="Supplier pilot link not found.")
    return payload


@router.post("/{token}/accept", response_model=SupplierPilotResponse)
async def accept_supplier_pilot(
    token: str,
    request: Request,
    payload: SupplierPilotAcceptRequest,
) -> dict[str, Any]:
    if not payload.agreed:
        raise HTTPException(status_code=400, detail="Agreement confirmation is required.")

    metadata = request_context.extract_request_metadata(request)
    accepted = await supplier_pilot.accept_pilot(
        token,
        ip=metadata.get("ip"),
        user_agent=metadata.get("user_agent"),
    )
    if accepted is None:
        raise HTTPException(status_code=404, detail="Supplier pilot link not found.")
    return accepted
