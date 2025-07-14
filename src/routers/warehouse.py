"""
@file: machine-logic-service/src/routers/warehouse.py
@description: Роутер для обработки API-запросов, связанных со складскими операциями.
@dependencies: fastapi, sqlalchemy, pydantic
@created: 2024-07-29
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from src.database import get_db_session
from typing import List, Optional
from pydantic import BaseModel
from src.models.models import BatchDB, PartDB, LotDB # Импортируем модели SQLAlchemy
import datetime

router = APIRouter(
    prefix="/warehouse",
    tags=["Warehouse"],
    responses={404: {"description": "Not found"}},
)

# Pydantic модели для ответа
class PartOut(BaseModel):
    drawing_number: str

    class Config:
        from_attributes = True

class LotOut(BaseModel):
    lot_number: str
    part: PartOut

    class Config:
        from_attributes = True

class BatchOut(BaseModel):
    id: int
    initial_quantity: int
    operator_reported_quantity: Optional[int] = None
    recounted_quantity: Optional[int] = None
    current_quantity: int
    current_location: str
    created_at: Optional[datetime.datetime] = None
    warehouse_received_at: Optional[datetime.datetime] = None # Дата приемки на склад
    lot: LotOut

    class Config:
        from_attributes = True

class UpdateQuantityPayload(BaseModel):
    new_quantity: int

@router.get("/accepted-batches", response_model=List[BatchOut])
def get_accepted_batches(db: Session = Depends(get_db_session)):
    """
    Возвращает список всех партий, принятых на склад.
    Включает партии со статусами 'warehouse_counted' и 'sorting_warehouse'.
    """
    accepted_statuses = ['warehouse_counted', 'sorting_warehouse']
    
    batches = db.query(BatchDB)\
        .options(
            joinedload(BatchDB.lot).joinedload(LotDB.part)
        )\
        .filter(BatchDB.current_location.in_(accepted_statuses))\
        .order_by(BatchDB.created_at.desc())\
        .all()

    if not batches:
        raise HTTPException(status_code=404, detail="Accepted batches not found")
        
    return batches

@router.patch("/batches/{batch_id}/update-quantity", response_model=BatchOut)
def update_batch_quantity(batch_id: int, payload: UpdateQuantityPayload, db: Session = Depends(get_db_session)):
    """
    Обновляет фактическое количество (current_quantity) для указанной партии.
    """
    batch = db.query(BatchDB).filter(BatchDB.id == batch_id).first()

    if not batch:
        raise HTTPException(status_code=404, detail=f"Batch with id {batch_id} not found")

    batch.current_quantity = payload.new_quantity
    
    try:
        db.commit()
        db.refresh(batch)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update batch quantity: {str(e)}")

    return batch

# Эндпоинты будут добавлены на следующем шаге 