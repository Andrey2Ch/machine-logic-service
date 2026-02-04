"""
@file: machine-logic-service/src/routers/warehouse_materials.py
@description: API для партий материалов, адресов хранения и движений склада.
@dependencies: fastapi, sqlalchemy, pydantic
@created: 2026-02-03
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from src.database import get_db_session
from src.models.models import (
    MaterialBatchDB,
    MaterialGroupDB,
    MaterialSubgroupDB,
    StorageLocationDB,
    StorageLocationSegmentDB,
    InventoryPositionDB,
    WarehouseMovementDB,
)
from datetime import date, datetime
import os
import json
import httpx
import re

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_OCR_MODEL = os.getenv("ANTHROPIC_OCR_MODEL", "claude-sonnet-4-5-20250929")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

router = APIRouter(
    prefix="/warehouse-materials",
    tags=["WarehouseMaterials"],
    responses={404: {"description": "Not found"}},
)

# --- Pydantic models ---
class MaterialBatchIn(BaseModel):
    batch_id: str
    material_type: Optional[str] = None
    material_group_id: Optional[int] = None
    material_subgroup_id: Optional[int] = None
    diameter: Optional[float] = None
    bar_length: Optional[float] = None
    quantity_received: Optional[int] = None
    supplier: Optional[str] = None
    supplier_doc_number: Optional[str] = None
    date_received: Optional[date] = None
    cert_folder: Optional[str] = None
    allowed_drawings: Optional[List[str]] = None
    preferred_drawing: Optional[str] = None
    status: Optional[str] = "active"
    created_by: Optional[int] = None


class MaterialBatchUpdate(BaseModel):
    material_type: Optional[str] = None
    material_group_id: Optional[int] = None
    material_subgroup_id: Optional[int] = None
    diameter: Optional[float] = None
    bar_length: Optional[float] = None
    quantity_received: Optional[int] = None
    supplier: Optional[str] = None
    supplier_doc_number: Optional[str] = None
    date_received: Optional[date] = None
    cert_folder: Optional[str] = None
    allowed_drawings: Optional[List[str]] = None
    preferred_drawing: Optional[str] = None
    status: Optional[str] = None
    created_by: Optional[int] = None


class MaterialBatchOut(MaterialBatchIn):
    created_at: Optional[str] = None


class MaterialGroupIn(BaseModel):
    code: str
    name: str
    is_active: Optional[bool] = True


class MaterialGroupOut(MaterialGroupIn):
    id: int
    created_at: Optional[datetime] = None


class MaterialSubgroupIn(BaseModel):
    group_id: int
    code: str
    name: str
    is_active: Optional[bool] = True


class MaterialSubgroupOut(MaterialSubgroupIn):
    id: int
    created_at: Optional[datetime] = None


class StorageLocationIn(BaseModel):
    code: str
    name: str
    type: str
    capacity: Optional[int] = None
    status: Optional[str] = "active"


class StorageLocationOut(StorageLocationIn):
    created_at: Optional[str] = None


class StorageSegmentIn(BaseModel):
    segment_type: str
    code: str
    name: str
    sort_order: Optional[int] = 0
    is_active: Optional[bool] = True


class StorageSegmentOut(StorageSegmentIn):
    created_at: Optional[str] = None


class InventoryPositionIn(BaseModel):
    batch_id: str
    location_code: str
    quantity: int


class InventoryPositionOut(InventoryPositionIn):
    updated_at: Optional[str] = None


class WarehouseMovementIn(BaseModel):
    batch_id: str
    movement_type: str
    quantity: int
    from_location: Optional[str] = None
    to_location: Optional[str] = None
    related_lot_id: Optional[int] = None
    cut_factor: Optional[int] = None
    performed_by: Optional[int] = None
    notes: Optional[str] = None


class WarehouseMovementOut(WarehouseMovementIn):
    movement_id: int
    performed_at: Optional[str] = None


class OcrLabelIn(BaseModel):
    image_base64: str
    media_type: str = "image/jpeg"


class OcrLabelOut(BaseModel):
    batch_id: Optional[str] = None
    supplier: Optional[str] = None
    supplier_doc_number: Optional[str] = None
    date_received: Optional[str] = None
    material_type: Optional[str] = None
    diameter: Optional[float] = None
    bar_length: Optional[float] = None
    quantity_received: Optional[int] = None
    drawing_numbers: Optional[List[str]] = None
    preferred_drawing: Optional[str] = None
    raw_text: Optional[str] = None


# --- Batches ---
@router.post("/batches", response_model=MaterialBatchOut)
def create_batch(payload: MaterialBatchIn, db: Session = Depends(get_db_session)):
    existing = db.query(MaterialBatchDB).filter(MaterialBatchDB.batch_id == payload.batch_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="Batch already exists")

    payload_data = _normalize_material_catalog(payload.dict(), db)
    batch = MaterialBatchDB(**payload_data)
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch


@router.get("/batches", response_model=List[MaterialBatchOut])
def list_batches(
    search: Optional[str] = Query(None, description="batch_id or supplier_doc_number"),
    status: Optional[str] = Query(None, description="active/closed"),
    db: Session = Depends(get_db_session)
):
    query = db.query(MaterialBatchDB)
    if search:
        like = f"%{search}%"
        query = query.filter(
            (MaterialBatchDB.batch_id.ilike(like)) |
            (MaterialBatchDB.supplier_doc_number.ilike(like))
        )
    if status:
        query = query.filter(MaterialBatchDB.status == status)
    return query.order_by(MaterialBatchDB.created_at.desc()).limit(200).all()


@router.get("/batches/{batch_id}", response_model=MaterialBatchOut)
def get_batch(batch_id: str, db: Session = Depends(get_db_session)):
    batch = db.query(MaterialBatchDB).filter(MaterialBatchDB.batch_id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


@router.patch("/batches/{batch_id}", response_model=MaterialBatchOut)
def update_batch(batch_id: str, payload: MaterialBatchUpdate, db: Session = Depends(get_db_session)):
    batch = db.query(MaterialBatchDB).filter(MaterialBatchDB.batch_id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    payload_data = payload.dict(exclude_unset=True)
    payload_data = _normalize_material_catalog(payload_data, db, partial=True)
    for k, v in payload_data.items():
        setattr(batch, k, v)
    db.commit()
    db.refresh(batch)
    return batch


# --- Material catalogs ---
@router.get("/material-groups", response_model=List[MaterialGroupOut])
def list_material_groups(is_active: Optional[bool] = Query(None), db: Session = Depends(get_db_session)):
    query = db.query(MaterialGroupDB)
    if is_active is not None:
        query = query.filter(MaterialGroupDB.is_active == is_active)
    return query.order_by(MaterialGroupDB.name.asc()).all()


@router.post("/material-groups", response_model=MaterialGroupOut)
def create_material_group(payload: MaterialGroupIn, db: Session = Depends(get_db_session)):
    existing = db.query(MaterialGroupDB).filter(MaterialGroupDB.code == payload.code).first()
    if existing:
        raise HTTPException(status_code=409, detail="Material group already exists")
    group = MaterialGroupDB(**payload.dict())
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


@router.get("/material-subgroups", response_model=List[MaterialSubgroupOut])
def list_material_subgroups(
    group_id: Optional[int] = Query(None),
    is_active: Optional[bool] = Query(None),
    db: Session = Depends(get_db_session)
):
    query = db.query(MaterialSubgroupDB)
    if group_id is not None:
        query = query.filter(MaterialSubgroupDB.group_id == group_id)
    if is_active is not None:
        query = query.filter(MaterialSubgroupDB.is_active == is_active)
    return query.order_by(MaterialSubgroupDB.name.asc()).all()


@router.post("/material-subgroups", response_model=MaterialSubgroupOut)
def create_material_subgroup(payload: MaterialSubgroupIn, db: Session = Depends(get_db_session)):
    group = db.query(MaterialGroupDB).filter(MaterialGroupDB.id == payload.group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Material group not found")
    existing = db.query(MaterialSubgroupDB).filter(
        MaterialSubgroupDB.group_id == payload.group_id,
        MaterialSubgroupDB.code == payload.code
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Material subgroup already exists")
    subgroup = MaterialSubgroupDB(**payload.dict())
    db.add(subgroup)
    db.commit()
    db.refresh(subgroup)
    return subgroup


# --- Locations ---
@router.post("/locations", response_model=StorageLocationOut)
def upsert_location(payload: StorageLocationIn, db: Session = Depends(get_db_session)):
    location = db.query(StorageLocationDB).filter(StorageLocationDB.code == payload.code).first()
    if location:
        for k, v in payload.dict().items():
            setattr(location, k, v)
    else:
        location = StorageLocationDB(**payload.dict())
        db.add(location)
    db.commit()
    db.refresh(location)
    return location


@router.get("/locations", response_model=List[StorageLocationOut])
def list_locations(db: Session = Depends(get_db_session)):
    return db.query(StorageLocationDB).order_by(StorageLocationDB.code.asc()).all()


# --- Segments ---
@router.post("/segments", response_model=StorageSegmentOut)
def upsert_segment(payload: StorageSegmentIn, db: Session = Depends(get_db_session)):
    segment = db.query(StorageLocationSegmentDB).filter(
        StorageLocationSegmentDB.segment_type == payload.segment_type,
        StorageLocationSegmentDB.code == payload.code
    ).first()
    if segment:
        for k, v in payload.dict().items():
            setattr(segment, k, v)
    else:
        segment = StorageLocationSegmentDB(**payload.dict())
        db.add(segment)
    db.commit()
    db.refresh(segment)
    return segment


@router.get("/segments", response_model=List[StorageSegmentOut])
def list_segments(segment_type: Optional[str] = Query(None), db: Session = Depends(get_db_session)):
    query = db.query(StorageLocationSegmentDB)
    if segment_type:
        query = query.filter(StorageLocationSegmentDB.segment_type == segment_type)
    return query.order_by(StorageLocationSegmentDB.segment_type.asc(), StorageLocationSegmentDB.sort_order.asc()).all()


# --- Inventory positions ---
@router.post("/inventory", response_model=InventoryPositionOut)
def upsert_inventory(payload: InventoryPositionIn, db: Session = Depends(get_db_session)):
    pos = db.query(InventoryPositionDB).filter(
        InventoryPositionDB.batch_id == payload.batch_id,
        InventoryPositionDB.location_code == payload.location_code
    ).first()
    if pos:
        pos.quantity = payload.quantity
    else:
        pos = InventoryPositionDB(**payload.dict())
        db.add(pos)
    db.commit()
    db.refresh(pos)
    return pos


# --- Movements ---
@router.post("/movements", response_model=WarehouseMovementOut)
def create_movement(payload: WarehouseMovementIn, db: Session = Depends(get_db_session)):
    movement = WarehouseMovementDB(**payload.dict())
    db.add(movement)
    db.commit()
    db.refresh(movement)
    return movement


@router.get("/movements", response_model=List[WarehouseMovementOut])
def list_movements(
    batch_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db_session)
):
    query = db.query(WarehouseMovementDB)
    if batch_id:
        query = query.filter(WarehouseMovementDB.batch_id == batch_id)
    return query.order_by(WarehouseMovementDB.performed_at.desc()).limit(limit).all()


def _normalize_material_catalog(payload: dict, db: Session, partial: bool = False) -> dict:
    group_id = payload.get("material_group_id")
    subgroup_id = payload.get("material_subgroup_id")

    if subgroup_id:
        subgroup = db.query(MaterialSubgroupDB).filter(MaterialSubgroupDB.id == subgroup_id).first()
        if not subgroup:
            raise HTTPException(status_code=404, detail="Material subgroup not found")
        if group_id and subgroup.group_id != group_id:
            raise HTTPException(status_code=400, detail="Subgroup does not belong to group")
        payload["material_group_id"] = subgroup.group_id

    if group_id:
        group = db.query(MaterialGroupDB).filter(MaterialGroupDB.id == group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Material group not found")

    if not partial:
        payload.setdefault("material_group_id", group_id)
        payload.setdefault("material_subgroup_id", subgroup_id)

    return payload


# --- OCR ---
@router.post("/ocr-label", response_model=OcrLabelOut)
def ocr_label(payload: OcrLabelIn):
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not set")

    prompt = (
        "Extract fields from this Hebrew material label image and return JSON with keys: "
        "batch_id, supplier, supplier_doc_number, date_received, material_type, diameter, "
        "bar_length, quantity_received, drawing_numbers, preferred_drawing, diameter_fraction. "
        "Rules: batch_id must be the internal batch number (מס מנה) like 26000132-1. "
        "drawing_numbers should include part numbers (מס חלק) like 756-22 (can be multiple). "
        "diameter should be in millimeters if possible. If the diameter is shown as a fraction "
        '(e.g. עגול 3/4), put that in diameter_fraction as "3/4". '
        "bar_length is the bar length (אורך מוט). "
        "If a field is missing, return null. Use numeric types for diameter/bar_length/quantity. "
        "Return ONLY valid JSON."
    )

    body = {
        "model": ANTHROPIC_OCR_MODEL,
        "max_tokens": 1024,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": payload.media_type,
                            "data": payload.image_base64
                        }
                    },
                    {"type": "text", "text": prompt}
                ]
            }
        ]
    }

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(ANTHROPIC_API_URL, headers=headers, json=body)
        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        data = resp.json()

    text = data.get("content", [{}])[0].get("text", "")
    cleaned_text = text.strip()
    if cleaned_text.startswith("```"):
        cleaned_text = cleaned_text.strip("`")
        cleaned_text = cleaned_text.replace("json", "", 1).strip()
    try:
        parsed = json.loads(cleaned_text)
    except Exception:
        return OcrLabelOut(raw_text=text)

    parsed = _postprocess_ocr(parsed, text)
    return OcrLabelOut(**parsed)


def _postprocess_ocr(parsed: dict, raw_text: str) -> dict:
    # Normalize batch_id using Hebrew label field if model mixed it with part number
    batch_match = re.search(r"מס מנה[:\s]*([0-9]{5,}(?:-\d+)?)", raw_text)
    if batch_match:
        parsed["batch_id"] = batch_match.group(1)

    # Ensure drawing_numbers include part number(s)
    part_match = re.search(r"מס חלק[:\s]*([0-9]+(?:-\d+)?)", raw_text)
    if part_match:
        drawings = parsed.get("drawing_numbers") or []
        if part_match.group(1) not in drawings:
            drawings.append(part_match.group(1))
        parsed["drawing_numbers"] = drawings

    # Extract bar length if present in text
    length_match = re.search(r"אורך מוט[:\s]*([0-9]+(?:\.[0-9]+)?)", raw_text)
    if length_match and parsed.get("bar_length") is None:
        parsed["bar_length"] = float(length_match.group(1))

    # If diameter equals bar length, clear it (likely mis-mapped)
    if parsed.get("bar_length") is not None and parsed.get("diameter") == parsed.get("bar_length"):
        parsed["diameter"] = None

    # Normalize bar length to mm if value looks like meters
    if parsed.get("bar_length") is not None:
        parsed["bar_length"] = _normalize_bar_length_mm(parsed["bar_length"])

    # Parse diameter from fraction in inches
    fraction = parsed.get("diameter_fraction")
    if not fraction:
        fraction_match = re.search(r"עגול\s*([0-9]+)\s*/\s*([0-9]+)", raw_text)
        if fraction_match:
            fraction = f"{fraction_match.group(1)}/{fraction_match.group(2)}"

    if parsed.get("diameter") is None and fraction:
        mm = _fraction_to_mm(fraction)
        if mm:
            parsed["diameter"] = mm

    # Remove helper field if present
    if "diameter_fraction" in parsed:
        parsed.pop("diameter_fraction", None)

    return parsed


def _fraction_to_mm(value: str) -> Optional[float]:
    match = re.match(r"^\s*(\d+)\s*/\s*(\d+)\s*$", value)
    if not match:
        return None
    numerator = int(match.group(1))
    denominator = int(match.group(2))
    if denominator == 0:
        return None
    mm = (numerator / denominator) * 25.4
    return round(mm, 2)


def _normalize_bar_length_mm(value: float) -> float:
    # Heuristic: label shows meters (e.g. 3.66). Store in mm for consistency.
    if value <= 20:
        return round(value * 1000, 2)
    return round(value, 2)
