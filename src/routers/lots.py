"""
@file: routers/lots.py
@description: Роутер для расширенного управления лотами (редактирование, удаление)
@dependencies: FastAPI, SQLAlchemy, Pydantic models
@created: 2024-12-19
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import and_, or_
from typing import List, Optional
from datetime import datetime
import logging

from ..database import get_db_session

# Импортируем модели из models.py
from ..models.models import LotDB, SetupDB, BatchDB

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lots", tags=["Lots Management"])

# Pydantic модели для редактирования лотов
from pydantic import BaseModel, Field

class LotUpdate(BaseModel):
    """Модель для полного обновления лота"""
    lot_number: Optional[str] = Field(None, description="Номер лота")
    part_id: Optional[int] = Field(None, description="ID детали")
    initial_planned_quantity: Optional[int] = Field(None, ge=1, description="Планируемое количество")
    due_date: Optional[datetime] = Field(None, description="Срок производства")

class LotDeleteResponse(BaseModel):
    """Ответ при удалении лота"""
    success: bool
    message: str
    deleted_lot_id: int

def can_modify_lot(lot: LotDB) -> tuple[bool, str]:
    """
    Проверяет можно ли редактировать или удалять лот.
    Возвращает (можно ли модифицировать, причина запрета)
    """
    # Лоты можно модифицировать только в статусах 'new' и 'in_production'
    if lot.status not in ['new', 'in_production']:
        return False, f"Лот в статусе '{lot.status}' нельзя редактировать"
    
    return True, ""

def has_production_activity(db: Session, lot_id: int) -> tuple[bool, str]:
    """
    Проверяет есть ли производственная активность по лоту.
    Возвращает (есть ли активность, описание активности)
    """
    # Проверяем наладки
    setups = db.query(SetupDB).filter(SetupDB.lot_id == lot_id).all()
    if setups:
        return True, f"По лоту есть {len(setups)} наладок"
    
    # Проверяем батчи
    batches = db.query(BatchDB).filter(BatchDB.lot_id == lot_id).all()
    if batches:
        return True, f"По лоту есть {len(batches)} батчей"
    
    return False, "Нет производственной активности"

@router.put("/{lot_id:int}")
async def update_lot(
    lot_id: int,
    lot_update: LotUpdate,
    db: Session = Depends(get_db_session)
):
    """
    Обновить лот (доступно только для статусов 'new' и 'in_production')
    """
    try:
        # Найти лот
        lot = db.query(LotDB).filter(LotDB.id == lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail=f"Лот с ID {lot_id} не найден")
        
        # Проверить можно ли модифицировать
        can_modify, reason = can_modify_lot(lot)
        if not can_modify:
            raise HTTPException(status_code=400, detail=reason)
        
        # Обновить поля если они переданы
        if lot_update.lot_number is not None:
            # Проверить уникальность номера лота
            existing_lot = db.query(LotDB).filter(
                and_(LotDB.lot_number == lot_update.lot_number, LotDB.id != lot_id)
            ).first()
            if existing_lot:
                raise HTTPException(status_code=400, detail=f"Лот с номером '{lot_update.lot_number}' уже существует")
            lot.lot_number = lot_update.lot_number
        
        if lot_update.part_id is not None:
            lot.part_id = lot_update.part_id
        
        if lot_update.initial_planned_quantity is not None:
            lot.initial_planned_quantity = lot_update.initial_planned_quantity
            # Пересчитать total_planned_quantity
            additional = (lot.total_planned_quantity or lot.initial_planned_quantity) - (lot.initial_planned_quantity or 0)
            lot.total_planned_quantity = lot_update.initial_planned_quantity + max(0, additional)
        
        if lot_update.due_date is not None:
            lot.due_date = lot_update.due_date
        
        db.commit()
        db.refresh(lot)
        
        logger.info(f"Лот {lot_id} успешно обновлен")
        
        # Возвращаем обновленный лот с информацией о детали
        return db.query(LotDB).options(selectinload(LotDB.part)).filter(LotDB.id == lot_id).first()
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка при обновлении лота {lot_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {str(e)}")

@router.delete("/{lot_id:int}", response_model=LotDeleteResponse)
async def delete_lot(
    lot_id: int,
    force: bool = Query(False, description="Принудительное удаление (игнорировать производственную активность)"),
    db: Session = Depends(get_db_session)
):
    """
    Удалить лот. По умолчанию можно удалять только лоты без производственной активности.
    С параметром force=true можно удалить лот даже если по нему была активность.
    """
    try:
        # Найти лот
        lot = db.query(LotDB).filter(LotDB.id == lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail=f"Лот с ID {lot_id} не найден")
        
        # Проверить можно ли модифицировать
        can_modify, reason = can_modify_lot(lot)
        if not can_modify:
            raise HTTPException(status_code=400, detail=f"Нельзя удалить лот: {reason}")
        
        # Проверить производственную активность
        has_activity, activity_desc = has_production_activity(db, lot_id)
        if has_activity and not force:
            raise HTTPException(
                status_code=400, 
                detail=f"Нельзя удалить лот: {activity_desc}. Используйте параметр force=true для принудительного удаления"
            )
        
        lot_number = lot.lot_number
        
        # Если force=true, удаляем связанные записи
        if force and has_activity:
            # Удалить связанные батчи
            db.query(BatchDB).filter(BatchDB.lot_id == lot_id).delete()
            # Удалить связанные наладки
            db.query(SetupDB).filter(SetupDB.lot_id == lot_id).delete()
            logger.warning(f"Принудительно удалена производственная активность для лота {lot_id}")
        
        # Удалить лот
        db.delete(lot)
        db.commit()
        
        message = f"Лот '{lot_number}' (ID: {lot_id}) успешно удален"
        if force and has_activity:
            message += f" вместе с производственной активностью ({activity_desc})"
        
        logger.info(message)
        
        return LotDeleteResponse(
            success=True,
            message=message,
            deleted_lot_id=lot_id
        )
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка при удалении лота {lot_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {str(e)}")

@router.get("/{lot_id:int}/can-modify")
async def check_lot_modifiable(
    lot_id: int,
    db: Session = Depends(get_db_session)
):
    """
    Проверить можно ли редактировать/удалять лот
    """
    try:
        lot = db.query(LotDB).filter(LotDB.id == lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail=f"Лот с ID {lot_id} не найден")
        
        can_modify, reason = can_modify_lot(lot)
        has_activity, activity_desc = has_production_activity(db, lot_id)
        
        return {
            "lot_id": lot_id,
            "can_edit": can_modify,
            "can_delete": can_modify and not has_activity,
            "can_force_delete": can_modify,
            "edit_reason": reason if not can_modify else "Лот можно редактировать",
            "delete_reason": activity_desc if has_activity else "Лот можно удалить",
            "has_production_activity": has_activity,
            "activity_description": activity_desc,
            "status": lot.status
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при проверке лота {lot_id}: {e}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера") 