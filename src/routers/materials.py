"""
@file: machine-logic-service/src/routers/materials.py
@description: Роутер для обработки API-запросов, связанных с управлением материалами (сырьём).
@dependencies: fastapi, sqlalchemy, pydantic
@created: 2025-11-30
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import and_
from src.database import get_db_session
from typing import List, Optional
from pydantic import BaseModel
from src.models.models import (
    MaterialTypeDB, 
    LotMaterialDB, 
    LotDB, 
    MachineDB, 
    EmployeeDB,
    PartDB
)
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/materials",
    tags=["Materials"],
    responses={404: {"description": "Not found"}},
)

# ========== Pydantic схемы ==========

class MaterialTypeOut(BaseModel):
    id: int
    material_name: str
    density_kg_per_m3: float
    description: Optional[str] = None

    class Config:
        from_attributes = True

class IssueToMachineRequest(BaseModel):
    machine_id: Optional[int] = None
    lot_id: int
    drawing_number: Optional[str] = None  # מס' שרטוט
    material_type: str  # סוג חומר
    diameter: float  # диаметр
    quantity_bars: int  # כמות במוטות
    material_receipt_id: Optional[int] = None
    notes: Optional[str] = None

class LotMaterialOut(BaseModel):
    id: int
    lot_id: int
    machine_id: Optional[int] = None
    material_type: Optional[str] = None
    diameter: Optional[float] = None
    issued_bars: int
    issued_at: Optional[datetime] = None
    status: str
    notes: Optional[str] = None

    class Config:
        from_attributes = True

# ========== Endpoints ==========

@router.get("/types", response_model=List[MaterialTypeOut])
def get_material_types(db: Session = Depends(get_db_session)):
    """Получить справочник материалов с плотностью"""
    try:
        types = db.query(MaterialTypeDB).order_by(MaterialTypeDB.material_name).all()
        return types
    except Exception as e:
        # Логируем ошибку и возвращаем пустой список вместо падения
        logger.error(f"Error fetching material types: {e}", exc_info=True)
        # Возвращаем пустой список, чтобы не ломать фронтенд
        return []

@router.post("/issue-to-machine", response_model=LotMaterialOut)
def issue_material_to_machine(
    request: IssueToMachineRequest,
    db: Session = Depends(get_db_session)
):
    """
    Выдать материал на станок (по записке-требованию)
    
    Сценарий: Кладовщик получает записку, находит материал на складе,
    переносит к станку и фиксирует в системе.
    """
    try:
        # Проверяем существование лота
        lot = db.query(LotDB).filter(LotDB.id == request.lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail=f"Лот {request.lot_id} не найден")
        
        # Проверяем станок (если указан)
        if request.machine_id:
            machine = db.query(MachineDB).filter(MachineDB.id == request.machine_id).first()
            if not machine:
                raise HTTPException(status_code=404, detail=f"Станок {request.machine_id} не найден")
        
        # Проверяем drawing_number (если указан)
        if request.drawing_number:
            part = db.query(PartDB).filter(PartDB.drawing_number == request.drawing_number).first()
            if not part:
                raise HTTPException(status_code=404, detail=f"Деталь с номером {request.drawing_number} не найдена")
        
        # Создаём запись о выдаче материала
        lot_material = LotMaterialDB(
            lot_id=request.lot_id,
            machine_id=request.machine_id,
            material_type=request.material_type,
            diameter=request.diameter,
            issued_bars=request.quantity_bars,
            issued_at=datetime.now(timezone.utc),
            status="issued",
            notes=request.notes
        )
        
        db.add(lot_material)
        
        # Обновляем статус материала в лоте
        lot.material_status = "issued"
        
        db.commit()
        db.refresh(lot_material)
        
        return lot_material
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error issuing material to machine: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка при выдаче материала: {str(e)}")

@router.get("/lot-materials", response_model=List[LotMaterialOut])
def get_lot_materials(
    lot_id: Optional[int] = Query(None, description="ID лота"),
    machine_id: Optional[int] = Query(None, description="ID станка"),
    status: Optional[str] = Query(None, description="Статус (pending/issued/completed)"),
    db: Session = Depends(get_db_session)
):
    """Получить материалы по лоту, станку или статусу"""
    try:
        query = db.query(LotMaterialDB)
        
        if lot_id:
            query = query.filter(LotMaterialDB.lot_id == lot_id)
        if machine_id:
            query = query.filter(LotMaterialDB.machine_id == machine_id)
        if status:
            query = query.filter(LotMaterialDB.status == status)
        
        materials = query.order_by(LotMaterialDB.created_at.desc()).all()
        return materials
    except Exception as e:
        logger.error(f"Error fetching lot materials: {e}", exc_info=True)
        # Возвращаем пустой список вместо падения
        return []

