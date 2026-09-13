from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile

from app.services import request_context, supplier_crm

router = APIRouter(prefix="/api/suppliers", tags=["suppliers-crm"])


def _base_url(request: Request) -> str:
    origin = request.headers.get("origin")
    if origin:
        return origin
    return str(request.base_url).rstrip("/")


@router.get("/crm")
async def list_supplier_crm(
    category: str = "",
    city: str = "",
    q: str = "",
    pilot_status: str = Query(default="", alias="pilotStatus"),
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    return await supplier_crm.list_suppliers(
        category=category,
        city=city,
        q=q,
        pilot_status=pilot_status,
        limit=limit,
        offset=offset,
    )


@router.get("/crm/{supplier_id}")
async def get_supplier_crm(supplier_id: str) -> dict[str, Any]:
    payload = await supplier_crm.supplier_detail(supplier_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Supplier not found.")
    return payload


@router.post("/crm/{supplier_id}/pilot-link")
async def generate_supplier_pilot_link(supplier_id: str, request: Request) -> dict[str, str]:
    link = await supplier_crm.ensure_pilot_link(supplier_id, _base_url(request))
    if link is None:
        raise HTTPException(status_code=404, detail="Supplier not found.")
    return link


@router.post("/crm/{supplier_id}/documents")
async def upload_supplier_documents(
    supplier_id: str,
    request: Request,
    document_type: str = Form(default="catalog"),
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    metadata = request_context.extract_request_metadata(request)
    try:
        documents = await supplier_crm.add_supplier_documents(
            supplier_id,
            files=files,
            document_type=document_type,
            uploaded_by=metadata.get("ip") or "internal",
        )
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    if not documents:
        raise HTTPException(status_code=404, detail="Supplier not found.")
    return {"documents": documents}


@router.post("/crm/{supplier_id}/documents/{document_id}/extract")
async def extract_supplier_document(supplier_id: str, document_id: str) -> dict[str, Any]:
    document = await supplier_crm.extract_supplier_document(document_id)
    if document is None or document.get("supplier_id") != supplier_id:
        raise HTTPException(status_code=404, detail="Supplier document not found.")
    return {"document": document}


@router.post("/crm/{supplier_id}/documents/{document_id}/apply")
async def apply_supplier_document_extraction(supplier_id: str, document_id: str) -> dict[str, Any]:
    payload = await supplier_crm.apply_document_extraction(document_id)
    if payload is None or payload.get("document", {}).get("supplier_id") != supplier_id:
        raise HTTPException(status_code=404, detail="Supplier document not found.")
    return payload


@router.post("/crm/{supplier_id}/loi")
async def generate_supplier_loi(supplier_id: str, request: Request) -> dict[str, Any]:
    metadata = request_context.extract_request_metadata(request)
    payload = await supplier_crm.generate_loi(
        supplier_id,
        base_url=_base_url(request),
        created_by=metadata.get("ip") or "internal",
    )
    if payload is None:
        raise HTTPException(status_code=404, detail="Supplier not found.")
    return payload


@router.get("/public/{slug}")
async def get_public_supplier(slug: str) -> dict[str, Any]:
    payload = await supplier_crm.public_supplier_profile(slug)
    if payload is None:
        raise HTTPException(status_code=404, detail="Supplier profile not found.")
    return payload


@router.get("/loi/{token}")
async def get_supplier_loi(token: str) -> dict[str, Any]:
    payload = await supplier_crm.get_loi_payload(token)
    if payload is None:
        raise HTTPException(status_code=404, detail="Supplier LOI not found.")
    return payload


@router.post("/loi/{token}/accept")
async def accept_supplier_loi(token: str, request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("agreed"):
        raise HTTPException(status_code=400, detail="Agreement confirmation is required.")
    signer_name = str(payload.get("signer_name") or "").strip()
    if not signer_name:
        raise HTTPException(status_code=400, detail="Signer name is required.")
    metadata = request_context.extract_request_metadata(request)
    accepted = await supplier_crm.accept_loi(
        token,
        signer_name=signer_name,
        ip=metadata.get("ip"),
        user_agent=metadata.get("user_agent"),
    )
    if accepted is None:
        raise HTTPException(status_code=404, detail="Supplier LOI not found.")
    return accepted
