"""
@file: machine-logic-service/src/routers/warehouse.py
@description: Роутер для обработки API-запросов, связанных со складскими операциями.
@dependencies: fastapi, sqlalchemy, pydantic
@created: 2024-07-29
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, and_
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

class PaginatedBatchesResponse(BaseModel):
    batches: List[BatchOut]
    total: int
    page: int
    per_page: int
    total_pages: int

@router.get("/accepted-batches", response_model=PaginatedBatchesResponse)
def get_accepted_batches(
    page: int = Query(1, ge=1, description="Номер страницы"),
    per_page: int = Query(50, ge=1, le=500, description="Количество элементов на странице"),
    date_from: Optional[datetime.date] = Query(None, description="Дата начала фильтрации (YYYY-MM-DD)"),
    date_to: Optional[datetime.date] = Query(None, description="Дата окончания фильтрации (YYYY-MM-DD)"),
    db: Session = Depends(get_db_session)
):
    """
    Возвращает список всех партий, принятых на склад с пагинацией и фильтрацией по датам.
    Включает партии со статусами 'warehouse_counted' и 'sorting_warehouse'.
    По умолчанию показывает данные за последнюю неделю.
    """
    accepted_statuses = ['warehouse_counted', 'sorting_warehouse']
    
    # Если даты не указаны, устанавливаем фильтр на последнюю неделю
    if date_from is None and date_to is None:
        date_to = datetime.date.today()
        date_from = date_to - datetime.timedelta(days=7)
    elif date_from is None:
        date_from = date_to - datetime.timedelta(days=7)
    elif date_to is None:
        date_to = datetime.date.today()
    
    # Базовый запрос
    query = db.query(BatchDB)\
        .options(
            joinedload(BatchDB.lot).joinedload(LotDB.part)
        )\
        .filter(BatchDB.current_location.in_(accepted_statuses))
    
    # Применяем фильтр по датам (используем warehouse_received_at если есть, иначе created_at)
    if date_from:
        date_from_datetime = datetime.datetime.combine(date_from, datetime.time.min)
        query = query.filter(
            or_(
                and_(BatchDB.warehouse_received_at.isnot(None), BatchDB.warehouse_received_at >= date_from_datetime),
                and_(BatchDB.warehouse_received_at.is_(None), BatchDB.created_at >= date_from_datetime)
            )
        )
    
    if date_to:
        date_to_datetime = datetime.datetime.combine(date_to, datetime.time.max)
        query = query.filter(
            or_(
                and_(BatchDB.warehouse_received_at.isnot(None), BatchDB.warehouse_received_at <= date_to_datetime),
                and_(BatchDB.warehouse_received_at.is_(None), BatchDB.created_at <= date_to_datetime)
            )
        )
    
    # Получаем общее количество записей
    total = query.count()
    
    # Применяем пагинацию и сортировку
    batches = query\
        .order_by(BatchDB.warehouse_received_at.desc().nullslast(), BatchDB.created_at.desc())\
        .offset((page - 1) * per_page)\
        .limit(per_page)\
        .all()
    
    total_pages = (total + per_page - 1) // per_page
    
    return PaginatedBatchesResponse(
        batches=batches,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages
    )

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