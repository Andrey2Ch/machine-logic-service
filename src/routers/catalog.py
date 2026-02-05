from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional, List
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime

from src.database import get_db_session
from src.models.models import AreaDB, MachineDB, SetupDB

router = APIRouter(prefix="/catalog", tags=["Catalog"])


# ======== Areas ========

class AreaCreate(BaseModel):
    name: str
    code: Optional[str] = None
    bot_row_size: int = 4  # Кол-во станков в ряду в TG-боте (2-6)


class AreaUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    is_active: Optional[bool] = None
    bot_row_size: Optional[int] = None


class AreaOut(BaseModel):
    id: int
    name: str
    code: Optional[str] = None
    is_active: bool
    bot_row_size: int = 4
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


@router.get("/areas", response_model=List[AreaOut])
async def list_areas(
    active: Optional[bool] = Query(None),
    q: Optional[str] = Query(None),
    db: Session = Depends(get_db_session),
):
    query = db.query(AreaDB)
    if active is not None:
        query = query.filter(AreaDB.is_active == active)
    if q:
        like = f"%{q}%"
        query = query.filter(func.lower(AreaDB.name).like(func.lower(like)))
    return query.order_by(AreaDB.name.asc()).all()


@router.post("/areas", response_model=AreaOut)
async def create_area(payload: AreaCreate, db: Session = Depends(get_db_session)):
    dup = db.query(AreaDB).filter(func.lower(AreaDB.name) == func.lower(payload.name)).first()
    if dup:
        raise HTTPException(status_code=409, detail="Area name must be unique")
    # Валидация bot_row_size (2-6)
    row_size = max(2, min(6, payload.bot_row_size))
    item = AreaDB(
        name=payload.name, 
        code=payload.code, 
        is_active=True, 
        bot_row_size=row_size,
        created_at=datetime.now()
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.patch("/areas/{area_id}", response_model=AreaOut)
async def update_area(area_id: int, payload: AreaUpdate, db: Session = Depends(get_db_session)):
    item = db.query(AreaDB).filter(AreaDB.id == area_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Area not found")
    if payload.name and payload.name != item.name:
        dup = db.query(AreaDB).filter(func.lower(AreaDB.name) == func.lower(payload.name)).first()
        if dup:
            raise HTTPException(status_code=409, detail="Area name must be unique")
        item.name = payload.name
    if payload.code is not None:
        item.code = payload.code
    if payload.is_active is not None:
        item.is_active = payload.is_active
    if payload.bot_row_size is not None:
        # Валидация bot_row_size (2-6)
        item.bot_row_size = max(2, min(6, payload.bot_row_size))
    db.commit()
    db.refresh(item)
    return item


@router.delete("/areas/{area_id}")
async def delete_area(area_id: int, db: Session = Depends(get_db_session)):
    item = db.query(AreaDB).filter(AreaDB.id == area_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Area not found")
    has_machines = db.query(MachineDB.id).filter(MachineDB.location_id == area_id).first()
    if has_machines:
        raise HTTPException(status_code=409, detail="Area contains machines. Move or deactivate them first.")
    db.delete(item)
    db.commit()
    return {"success": True}


class MoveMachinesPayload(BaseModel):
    to_area_id: int


@router.post("/areas/{area_id}/move-machines")
async def move_machines(area_id: int, payload: MoveMachinesPayload, db: Session = Depends(get_db_session)):
    src = db.query(AreaDB).filter(AreaDB.id == area_id).first()
    dst = db.query(AreaDB).filter(AreaDB.id == payload.to_area_id).first()
    if not src or not dst:
        raise HTTPException(status_code=404, detail="Area not found")
    db.query(MachineDB).filter(MachineDB.location_id == area_id).update({MachineDB.location_id: payload.to_area_id})
    db.commit()
    return {"success": True}


# ======== Machines ========

class MachineCreate(BaseModel):
    name: str
    type: str
    location_id: int
    serial_number: Optional[str] = None
    notes: Optional[str] = None


class MachineUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    location_id: Optional[int] = None
    serial_number: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None
    display_order: Optional[int] = None


class MachineOut(BaseModel):
    id: int
    name: str
    type: Optional[str] = None
    min_diameter: Optional[float] = None
    max_diameter: Optional[float] = None
    location_id: int
    serial_number: Optional[str] = None
    notes: Optional[str] = None
    is_active: bool
    display_order: Optional[int] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


@router.get("/machines", response_model=List[MachineOut])
async def list_machines(
    area_id: Optional[int] = Query(None),
    active: Optional[bool] = Query(None),
    q: Optional[str] = Query(None),
    db: Session = Depends(get_db_session),
):
    query = db.query(MachineDB)
    if area_id is not None:
        query = query.filter(MachineDB.location_id == area_id)
    if active is not None:
        query = query.filter(MachineDB.is_active == active)
    if q:
        like = f"%{q}%"
        query = query.filter(func.lower(MachineDB.name).like(func.lower(like)))
    return query.order_by(
        func.coalesce(MachineDB.display_order, 9999).asc(),
        MachineDB.name.asc(),
    ).all()


@router.post("/machines", response_model=MachineOut)
async def create_machine(payload: MachineCreate, db: Session = Depends(get_db_session)):
    # validate area
    area = db.query(AreaDB).filter(AreaDB.id == payload.location_id).first()
    if not area:
        raise HTTPException(status_code=404, detail="Area not found")
    # unique machine name (global)
    dup = db.query(MachineDB).filter(func.lower(MachineDB.name) == func.lower(payload.name)).first()
    if dup:
        raise HTTPException(status_code=409, detail="Machine name must be unique")
    # determine next display_order in area
    max_order = db.query(func.max(MachineDB.display_order)).filter(MachineDB.location_id == payload.location_id).scalar()
    next_order = (max_order + 1) if max_order is not None else 0
    item = MachineDB(
        name=payload.name,
        type=payload.type,
        location_id=payload.location_id,
        serial_number=payload.serial_number,
        notes=payload.notes,
        is_active=True,
        created_at=datetime.now(),
        display_order=next_order,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.patch("/machines/{machine_id}", response_model=MachineOut)
async def update_machine(machine_id: int, payload: MachineUpdate, db: Session = Depends(get_db_session)):
    item = db.query(MachineDB).filter(MachineDB.id == machine_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Machine not found")
    if payload.name and payload.name != item.name:
        dup = db.query(MachineDB).filter(func.lower(MachineDB.name) == func.lower(payload.name)).first()
        if dup:
            raise HTTPException(status_code=409, detail="Machine name must be unique")
        item.name = payload.name
    if payload.type is not None:
        item.type = payload.type
    if payload.location_id is not None and payload.location_id != item.location_id:
        area = db.query(AreaDB).filter(AreaDB.id == payload.location_id).first()
        if not area:
            raise HTTPException(status_code=404, detail="Area not found")
        # переносим машину в новую зону и ставим в конец порядка
        item.location_id = payload.location_id
        max_order_new_area = db.query(func.coalesce(func.max(MachineDB.display_order), -1)).\
            filter(MachineDB.location_id == payload.location_id).scalar()
        item.display_order = max_order_new_area + 1
    if payload.serial_number is not None:
        item.serial_number = payload.serial_number
    if payload.notes is not None:
        item.notes = payload.notes
    if payload.is_active is not None:
        item.is_active = payload.is_active
    # Игнорируем любые внешние попытки изменить display_order
    # (раскладка сетки отключена, порядок задаётся только при создании или переносе зоны)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/machines/{machine_id}")
async def delete_machine(machine_id: int, db: Session = Depends(get_db_session)):
    item = db.query(MachineDB).filter(MachineDB.id == machine_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Machine not found")
    # active setups guard
    active_setup = db.query(SetupDB.id).filter(
        SetupDB.machine_id == machine_id,
        SetupDB.status.in_(['created', 'pending_qc', 'allowed', 'started']),
        SetupDB.end_time.is_(None)
    ).first()
    if active_setup:
        raise HTTPException(status_code=409, detail="Machine has active or queued setups. Deactivate instead.")
    db.delete(item)
    db.commit()
    return {"success": True}


class ReorderMachinesPayload(BaseModel):
    machine_ids: List[int]


@router.post("/machines/reorder")
async def reorder_machines(
    area_id: int = Query(..., description="ID участка для перестановки станков"),
    payload: ReorderMachinesPayload = ...,
    db: Session = Depends(get_db_session)
):
    """
    Изменить порядок станков внутри участка.
    machine_ids - список ID станков в желаемом порядке.
    """
    # Проверяем, что участок существует
    area = db.query(AreaDB).filter(AreaDB.id == area_id).first()
    if not area:
        raise HTTPException(status_code=404, detail="Area not found")
    
    # Проверяем, что все станки принадлежат этому участку
    machines = db.query(MachineDB).filter(
        MachineDB.id.in_(payload.machine_ids),
        MachineDB.location_id == area_id
    ).all()
    
    if len(machines) != len(payload.machine_ids):
        raise HTTPException(
            status_code=400, 
            detail="Some machine IDs are invalid or don't belong to this area"
        )
    
    # Обновляем display_order для каждого станка (1-based индексы)
    machine_map = {m.id: m for m in machines}
    for order, machine_id in enumerate(payload.machine_ids, start=1):
        if machine_id in machine_map:
            machine_map[machine_id].display_order = order
    
    db.commit()
    return {"success": True, "updated": len(payload.machine_ids)}

