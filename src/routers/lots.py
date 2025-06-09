# -*- coding: utf-8 -*-

"""
@file: lots.py
@description: FastAPI router for lot management operations
@dependencies: FastAPI, SQLAlchemy, database models, lot schemas
@created: 2024
"""

import traceback
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..database import get_db_session
from ..models.models import LotDB, PartDB, BatchDB, SetupDB, EmployeeDB, MachineDB
from ..schemas.lot import (
    LotCreate, LotResponse, LotStatusUpdate, LotQuantityUpdate,
    LotInfoItem, LotAnalyticsResponse, LotSummaryReport, LotDetailReport,
    LotStatus
)

# Import logger
import logging
logger = logging.getLogger(__name__)

router = APIRouter()


# === LOT CRUD ENDPOINTS ===

@router.post("/lots/", response_model=LotResponse, status_code=201, tags=["Lots"])
async def create_lot(
    lot_data: LotCreate, 
    db: Session = Depends(get_db_session),
    # current_user: EmployeeDB = Depends(get_current_active_user) # Раскомментировать, когда аутентификация будет готова
):
    """Создать новый лот"""
    try:
        logger.info(f"Запрос на создание лота: {lot_data.model_dump()}")

        # Проверка существования детали
        part = db.query(PartDB).filter(PartDB.id == lot_data.part_id).first()
        if not part:
            logger.warning(f"Деталь с ID {lot_data.part_id} не найдена при создании лота.")
            raise HTTPException(status_code=404, detail=f"Part with id {lot_data.part_id} not found")

        # Проверка уникальности номера лота
        existing_lot = db.query(LotDB).filter(LotDB.lot_number == lot_data.lot_number).first()
        if existing_lot:
            logger.warning(f"Лот с номером {lot_data.lot_number} уже существует (ID: {existing_lot.id}).")
            raise HTTPException(status_code=409, detail=f"Lot with lot_number '{lot_data.lot_number}' already exists.")

        db_lot_data = lot_data.model_dump(exclude_unset=True)
        logger.debug(f"Данные для создания LotDB (после model_dump): {db_lot_data}")
        
        # Если order_manager_id передан, а created_by_order_manager_at нет, устанавливаем текущее время
        if db_lot_data.get("order_manager_id") is not None:
            if db_lot_data.get("created_by_order_manager_at") is None:
                db_lot_data["created_by_order_manager_at"] = datetime.now(timezone.utc)
        
        # Проверка ключевых полей перед созданием объекта
        if 'part_id' not in db_lot_data or db_lot_data['part_id'] is None:
            logger.error("Критическая ошибка: part_id отсутствует или None в db_lot_data перед созданием LotDB.")
            raise HTTPException(status_code=500, detail="Internal error: part_id is missing for LotDB creation.")
        
        if 'lot_number' not in db_lot_data or not db_lot_data['lot_number']:
            logger.error("Критическая ошибка: lot_number отсутствует или пуст в db_lot_data перед созданием LotDB.")
            raise HTTPException(status_code=500, detail="Internal error: lot_number is missing for LotDB creation.")

        logger.info(f"Попытка создать объект LotDB с данными: {db_lot_data} и status='{LotStatus.NEW.value}'")
        db_lot = LotDB(**db_lot_data, status=LotStatus.NEW.value)
        logger.info(f"Объект LotDB создан в памяти (ID пока нет).")

        db.add(db_lot)
        logger.info("Объект LotDB добавлен в сессию SQLAlchemy.")
        
        try:
            logger.info("Попытка выполнить db.flush().")
            db.flush()
            logger.info("db.flush() выполнен успешно.")
        except Exception as flush_exc:
            db.rollback()
            logger.error(f"Ошибка во время db.flush() при создании лота: {flush_exc}", exc_info=True)
            logger.error(f"Полный трейсбек ошибки flush: {traceback.format_exc()}")
            raise HTTPException(status_code=500, detail=f"Database flush error: {str(flush_exc)}")

        db.commit()
        logger.info("db.commit() выполнен успешно.")
        db.refresh(db_lot)
        logger.info(f"Лот '{db_lot.lot_number}' успешно создан с ID {db_lot.id}.")

        return db_lot
    
    except HTTPException as http_e:
        logger.error(f"HTTPException при создании лота: {http_e.status_code} - {http_e.detail}")
        raise http_e
    
    except IntegrityError as int_e:
        db.rollback()
        logger.error(f"IntegrityError при создании лота: {int_e}", exc_info=True)
        detailed_error = str(int_e.orig) if hasattr(int_e, 'orig') and int_e.orig else str(int_e)
        logger.error(f"Полный трейсбек IntegrityError: {traceback.format_exc()}")
        
        # Попытка определить, какое ограничение было нарушено
        if "uq_lot_number_global" in detailed_error.lower():
             raise HTTPException(status_code=409, detail=f"Lot with lot_number '{lot_data.lot_number}' already exists (race condition or concurrent request). Possible original error: {detailed_error}")
        elif "lots_part_id_fkey" in detailed_error.lower():
             raise HTTPException(status_code=404, detail=f"Part with id {lot_data.part_id} not found (race condition or concurrent request). Possible original error: {detailed_error}")
        else:
            raise HTTPException(status_code=500, detail=f"Database integrity error occurred. Possible original error: {detailed_error}")

    except Exception as e:
        db.rollback()
        logger.error(f"НЕПРЕДВИДЕННАЯ ОШИБКА СЕРВЕРА при создании лота: {e}", exc_info=True)
        detailed_traceback = traceback.format_exc()
        logger.error(f"ПОЛНЫЙ ТРЕЙСБЕК НЕПРЕДВИДЕННОЙ ОШИБКИ:\n{detailed_traceback}")
        raise HTTPException(status_code=500, detail=f"Unexpected server error. Traceback: {detailed_traceback}")


@router.get("/lots/", response_model=List[LotResponse], tags=["Lots"])
async def get_lots(
    response: Response, 
    search: Optional[str] = Query(None, description="Поисковый запрос для номера лота"),
    part_search: Optional[str] = Query(None, description="Поисковый запрос для номера детали"),
    status_filter: Optional[str] = Query(None, description="Фильтр по статусам (через запятую, например: new,in_production)"),
    skip: int = Query(0, ge=0, description="Количество записей для пропуска (пагинация)"),
    limit: int = Query(100, ge=1, le=500, description="Максимальное количество записей для возврата (пагинация)"),
    db: Session = Depends(get_db_session)
):
    """
    Получить список лотов с возможностью поиска и фильтрации.
    Поддерживает пагинацию и поиск по номеру лота и номеру детали.
    """
    try:
        # Базовый запрос без selectinload для избежания зависания
        query = db.query(LotDB)
        
        # Поиск по номеру лота
        if search:
            search_term = f"%{search.lower()}%"
            query = query.filter(func.lower(LotDB.lot_number).like(search_term))
        
        # Поиск по номеру детали - через подзапрос
        if part_search:
            part_search_term = f"%{part_search.lower()}%"
            part_ids = db.query(PartDB.id).filter(func.lower(PartDB.drawing_number).like(part_search_term)).subquery()
            query = query.filter(LotDB.part_id.in_(part_ids))
        
        # Фильтрация по статусам
        if status_filter:
            statuses = [status.strip() for status in status_filter.split(',') if status.strip()]
            if statuses:
                query = query.filter(LotDB.status.in_(statuses))
        
        # Получаем лоты с пагинацией
        lots = query.order_by(LotDB.id.desc()).offset(skip).limit(limit).all()
        
        # Простое количество для заголовка
        total_count = skip + len(lots) + (1 if len(lots) == limit else 0)
        
        logger.info(f"Запрос списка лотов: search='{search}', part_search='{part_search}', skip={skip}, limit={limit}. Возвращено {len(lots)} лотов.")
        response.headers["X-Total-Count"] = str(total_count)
        
        return lots
    except Exception as e:
        logger.error(f"Ошибка в get_lots: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/lots/{lot_id}", response_model=LotResponse, tags=["Lots"])
async def get_lot(lot_id: int, db: Session = Depends(get_db_session)):
    """Получить лот по ID"""
    lot = db.query(LotDB).filter(LotDB.id == lot_id).first()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    return lot


@router.patch("/lots/{lot_id}/status", response_model=LotResponse, tags=["Lots"])
async def update_lot_status(
    lot_id: int,
    status_update: LotStatusUpdate,
    db: Session = Depends(get_db_session)
):
    """
    Обновить статус лота.
    Используется для синхронизации статусов между Telegram-ботом и FastAPI.
    """
    try:
        # Найти лот
        lot = db.query(LotDB).filter(LotDB.id == lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail="Lot not found")

        # Обновить статус
        old_status = lot.status
        lot.status = status_update.status.value
        db.commit()
        db.refresh(lot)

        logger.info(f"Статус лота {lot_id} обновлен с '{old_status}' на '{lot.status}'")
        return lot

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка при обновлении статуса лота {lot_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/lots/{lot_id}/quantity", response_model=LotResponse, tags=["Lots"])
async def update_lot_quantity(
    lot_id: int, 
    quantity_update: LotQuantityUpdate, 
    db: Session = Depends(get_db_session)
):
    """
    Обновить дополнительное количество для лота.
    Увеличивает общее плановое количество лота.
    """
    try:
        # Найти лот
        lot = db.query(LotDB).filter(LotDB.id == lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail="Lot not found")

        # Обновить дополнительное количество
        if lot.additional_quantity is None:
            lot.additional_quantity = 0
        lot.additional_quantity += quantity_update.additional_quantity
        
        db.commit()
        db.refresh(lot)

        logger.info(f"Дополнительное количество для лота {lot_id} увеличено на {quantity_update.additional_quantity}")
        return lot

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка при обновлении количества лота {lot_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/lots/{lot_id}/close", response_model=LotResponse, tags=["Lots"])
async def close_lot(lot_id: int, db: Session = Depends(get_db_session)):
    """
    Закрыть лот (установить статус 'completed').
    Используется для окончательного завершения работы с лотом.
    """
    try:
        # Найти лот
        lot = db.query(LotDB).filter(LotDB.id == lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail="Lot not found")

        # Проверить, можно ли закрыть лот
        if lot.status == LotStatus.COMPLETED.value:
            logger.warning(f"Лот {lot_id} уже завершен")
            return lot  # Возвращаем лот без изменений

        # Обновить статус на завершенный
        old_status = lot.status
        lot.status = LotStatus.COMPLETED.value
        
        db.commit()
        db.refresh(lot)

        logger.info(f"Лот {lot_id} закрыт. Статус изменен с '{old_status}' на '{lot.status}'")
        return lot

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка при закрытии лота {lot_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


# === LOT INFO ENDPOINTS ===

@router.get("/lots/pending-qc", response_model=List[LotInfoItem])
async def get_lots_pending_qc(
    db: Session = Depends(get_db_session), 
    current_user_qa_id: Optional[int] = Query(None, alias="qaId"),
    hideCompleted: Optional[bool] = Query(False, description="Скрыть завершенные лоты и лоты со всеми проверенными батчами")
):
    """
    Получить список лотов, ожидающих контроля качества.
    Используется для интерфейса ОТК.
    """
    try:
        # Базовый запрос для получения лотов с информацией о деталях
        query = db.query(LotDB, PartDB) \
                  .join(PartDB, LotDB.part_id == PartDB.id)

        # Если нужно скрыть завершенные лоты
        if hideCompleted:
            # Исключаем лоты со статусом 'completed'
            query = query.filter(LotDB.status != 'completed')

        lots_data = query.all()
        
        result = []
        for lot, part in lots_data:
            # Получаем информацию об инспекторе (если есть активная наладка)
            inspector_name = None
            planned_quantity = lot.initial_planned_quantity
            machine_name = None
            
            # Ищем активную наладку для этого лота
            active_setup = db.query(SetupDB, EmployeeDB) \
                            .outerjoin(EmployeeDB, SetupDB.employee_id == EmployeeDB.id) \
                            .filter(SetupDB.lot_id == lot.id) \
                            .filter(SetupDB.status.in_(['created', 'started', 'pending_qc', 'allowed'])) \
                            .first()
            
            if active_setup:
                setup, employee = active_setup
                if employee:
                    inspector_name = employee.full_name
                if setup.planned_quantity:
                    planned_quantity = setup.planned_quantity

            result.append(LotInfoItem(
                id=lot.id,
                drawing_number=part.drawing_number,
                lot_number=lot.lot_number,
                inspector_name=inspector_name,
                planned_quantity=planned_quantity,
                machine_name=machine_name
            ))

        logger.info(f"Возвращено {len(result)} лотов для QC (hideCompleted={hideCompleted})")
        return result

    except Exception as e:
        logger.error(f"Ошибка при получении лотов для QC: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/lots/{lot_id}/analytics", response_model=LotAnalyticsResponse)
async def get_lot_analytics(lot_id: int, db: Session = Depends(get_db_session)):
    """
    Получить аналитическую информацию по лоту.
    Возвращает количество принятых на склад и произведенных деталей.
    """
    try:
        # Проверяем существование лота
        lot = db.query(LotDB).filter(LotDB.id == lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail="Lot not found")

        # Получаем количество деталей, принятых на склад
        accepted_quantity = db.query(func.coalesce(func.sum(BatchDB.recounted_quantity), 0)) \
                             .filter(BatchDB.lot_id == lot_id) \
                             .filter(BatchDB.current_location == 'warehouse_counted') \
                             .scalar() or 0

        # Получаем количество деталей "со станка" (в производстве)
        from_machine_quantity = db.query(func.coalesce(func.sum(BatchDB.current_quantity), 0)) \
                                 .filter(BatchDB.lot_id == lot_id) \
                                 .filter(BatchDB.current_location.in_(['production', 'sorting'])) \
                                 .scalar() or 0

        return LotAnalyticsResponse(
            accepted_by_warehouse_quantity=int(accepted_quantity),
            from_machine_quantity=int(from_machine_quantity)
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при получении аналитики для лота {lot_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")