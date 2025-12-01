"""
@file: routers/lots.py
@description: Роутер для расширенного управления лотами (редактирование, удаление)
@dependencies: FastAPI, SQLAlchemy, Pydantic models
@created: 2024-12-19
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import and_, or_, func
from typing import List, Optional
from datetime import datetime
import logging

from ..database import get_db_session

# Импортируем модели из models.py
from ..models.models import LotDB, SetupDB, BatchDB

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lots-management", tags=["Lots Management"])

# Pydantic модели для редактирования лотов
from pydantic import BaseModel, Field

class LotUpdate(BaseModel):
    """Модель для полного обновления лота"""
    lot_number: Optional[str] = Field(None, description="Номер лота")
    part_id: Optional[int] = Field(None, description="ID детали")
    initial_planned_quantity: Optional[int] = Field(None, ge=1, description="Планируемое количество")
    due_date: Optional[datetime] = Field(None, description="Срок производства")

class LotQuantityUpdate(BaseModel):
    """Модель для обновления дополнительного количества"""
    additional_quantity: int = Field(description="Дополнительное количество")

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
            new_initial = lot_update.initial_planned_quantity
            lot.initial_planned_quantity = new_initial
            
            # Синхронизируем с активным сетапом
            setup = db.query(SetupDB).filter(
                SetupDB.lot_id == lot_id,
                SetupDB.end_time == None,
                SetupDB.status.in_(['created', 'started', 'pending_qc', 'allowed'])
            ).first()
            
            if setup:
                # Обновляем planned_quantity в сетапе
                setup.planned_quantity = new_initial
                # Пересчитываем total из сетапа (истина в сетапах!)
                lot.total_planned_quantity = setup.planned_quantity + (setup.additional_quantity or 0)
            else:
                # Нет сетапа - total = initial
                lot.total_planned_quantity = new_initial
        
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

@router.get("/search-new")
async def search_new_lots(
    drawing_number: str = Query(..., description="Номер чертежа"),
    limit: int = Query(5, ge=1, le=20, description="Максимум лотов в ответе"),
    include_production: bool = Query(False, description="Включить лоты в производстве"),
    db: Session = Depends(get_db_session)
):
    """
    Поиск лотов для чертежа.
    По умолчанию: status='new' или 'assigned'.
    С include_production=true: также включает 'in_production'.
    Возвращает: id, lot_number, initial_planned_quantity, due_date, order_manager_name, status
    """
    try:
        from ..models.models import PartDB, EmployeeDB
        
        # Определяем допустимые статусы
        allowed_statuses = ['new', 'assigned']
        if include_production:
            allowed_statuses.append('in_production')
        
        rows = (
            db.query(
                LotDB.id,
                LotDB.lot_number,
                LotDB.initial_planned_quantity,
                LotDB.due_date,
                LotDB.status,
                EmployeeDB.full_name.label("order_manager_name")
            )
            .join(PartDB, PartDB.id == LotDB.part_id)
            .outerjoin(EmployeeDB, EmployeeDB.id == LotDB.order_manager_id)
            .filter(PartDB.drawing_number == drawing_number)
            .filter(LotDB.status.in_(allowed_statuses))
            .order_by(LotDB.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "lot_number": r.lot_number,
                "initial_planned_quantity": r.initial_planned_quantity,
                "due_date": r.due_date.isoformat() if r.due_date else None,
                "order_manager_name": r.order_manager_name,
                "status": r.status,
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"Ошибка поиска лотов для {drawing_number}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка поиска лотов")

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

@router.patch("/{lot_id}/quantity")
async def update_lot_quantity(
    lot_id: int,
    quantity_update: LotQuantityUpdate,
    db: Session = Depends(get_db_session)
):
    """
    Обновляет дополнительное количество для лота.
    ИСТИНА В СЕТАПАХ: меняется setup_jobs.additional_quantity, lot.total пересчитывается.
    """
    try:
        # Найти лот
        lot = db.query(LotDB).filter(LotDB.id == lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail="Лот не найден")
        
        # Найти АКТИВНЫЙ setup_job для этого лота
        setup_job = db.query(SetupDB).filter(
            SetupDB.lot_id == lot_id,
            SetupDB.end_time == None,
            SetupDB.status.in_(['created', 'started', 'pending_qc', 'allowed'])
        ).first()
        
        if not setup_job:
            raise HTTPException(status_code=404, detail="Активная наладка для лота не найдена")
        
        # Обновить additional_quantity в setup_jobs (ИСТОЧНИК ИСТИНЫ)
        setup_job.additional_quantity = quantity_update.additional_quantity
        
        # Пересчитать total_planned_quantity в lots ИЗ СЕТАПА
        lot.total_planned_quantity = setup_job.planned_quantity + setup_job.additional_quantity
        
        # Сохранить изменения
        db.commit()
        
        logger.info(f"Обновлено количество для лота {lot_id}: setup.additional={quantity_update.additional_quantity}, lot.total={lot.total_planned_quantity}")
        
        return {
            "success": True,
            "message": "Количество успешно обновлено",
            "lot_id": lot_id,
            "initial_planned_quantity": lot.initial_planned_quantity,
            "additional_quantity": quantity_update.additional_quantity,
            "total_planned_quantity": lot.total_planned_quantity
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка при обновлении количества лота {lot_id}: {e}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера") 

@router.post("/backfill-total-planned", summary="Backfill lots.total_planned_quantity from setups")
async def backfill_total_planned(db: Session = Depends(get_db_session)):
    """
    One-off helper: total_planned_quantity = COALESCE(MAX(setup.planned + additional), initial_planned_quantity)
    Safe to run multiple times.
    """
    try:
        subq = (
            db.query(
                SetupDB.lot_id.label("lot_id"),
                func.max((SetupDB.planned_quantity + func.coalesce(SetupDB.additional_quantity, 0))).label("setup_total")
            )
            .group_by(SetupDB.lot_id)
            .subquery()
        )

        BATCH_SIZE = 500
        offset = 0
        updated = 0
        while True:
            rows = (
                db.query(LotDB, subq.c.setup_total)
                .outerjoin(subq, LotDB.id == subq.c.lot_id)
                .order_by(LotDB.id)
                .offset(offset)
                .limit(BATCH_SIZE)
                .all()
            )
            if not rows:
                break
            for lot, setup_total in rows:
                initial = lot.initial_planned_quantity or 0
                resolved = setup_total if setup_total is not None else initial
                if lot.total_planned_quantity != resolved:
                    lot.total_planned_quantity = resolved
                    updated += 1
            db.commit()
            offset += BATCH_SIZE

        return {"updated": updated}
    except Exception as e:
        db.rollback()
        logger.error(f"Backfill total_planned_quantity error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Backfill error")


@router.post("/{lot_id:int}/sync-total-planned", summary="Sync lot.total_planned_quantity from its setups")
async def sync_lot_total_planned(lot_id: int, db: Session = Depends(get_db_session)):
    """
    Пересчитывает total_planned_quantity лота на основе его сетапов:
    total_planned_quantity = COALESCE(MAX(planned_quantity + COALESCE(additional_quantity,0)), initial_planned_quantity)
    """
    try:
        lot = db.query(LotDB).filter(LotDB.id == lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail="Лот не найден")

        setup_total = (
            db.query(func.max(SetupDB.planned_quantity + func.coalesce(SetupDB.additional_quantity, 0)))
              .filter(SetupDB.lot_id == lot_id)
              .scalar()
        )
        initial = lot.initial_planned_quantity or 0
        resolved = setup_total if setup_total is not None else initial
        lot.total_planned_quantity = resolved
        db.commit()
        return {
            "lot_id": lot_id,
            "initial_planned_quantity": initial,
            "setup_total": setup_total,
            "total_planned_quantity": lot.total_planned_quantity
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Sync total_planned_quantity error for lot {lot_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Sync error")