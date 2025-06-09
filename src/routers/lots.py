/**
 * @file: lots.py
 * @description: Роутер для всех операций, связанных с лотами (Lots).
 * @dependencies: fastapi, sqlalchemy, src.database, src.models, src.schemas
 * @created: 2024-05-28
 */

import logging
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session, joinedload, selectinload, aliased
from sqlalchemy import func, case, and_, desc, or_
from sqlalchemy.exc import IntegrityError
import traceback
from src.database import get_db_session
from src.models.models import LotDB, BatchDB, PartDB, EmployeeDB, SetupDB, MachineDB, ReadingDB, CardDB
from src.schemas.lot import LotResponse, LotInfoItem, LotAnalyticsResponse, LotCreate, LotStatus, LotStatusUpdate, LotQuantityUpdate
from src.schemas.batch import BatchViewItem
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

lots_router = APIRouter(prefix="/lots", tags=["Lots"])

@lots_router.post("/", response_model=LotResponse, status_code=201)
async def create_lot(
    lot_data: LotCreate, 
    db: Session = Depends(get_db_session),
):
    """
    Создать новый лот.
    """
    try:
        logger.info(f"Запрос на создание лота: {lot_data.model_dump()}")

        part = db.query(PartDB).filter(PartDB.id == lot_data.part_id).first()
        if not part:
            logger.warning(f"Деталь с ID {lot_data.part_id} не найдена при создании лота.")
            raise HTTPException(status_code=404, detail=f"Part with id {lot_data.part_id} not found")

        existing_lot = db.query(LotDB).filter(LotDB.lot_number == lot_data.lot_number).first()
        if existing_lot:
            logger.warning(f"Лот с номером {lot_data.lot_number} уже существует (ID: {existing_lot.id}).")
            raise HTTPException(status_code=409, detail=f"Lot with lot_number '{lot_data.lot_number}' already exists.")

        db_lot_data = lot_data.model_dump(exclude_unset=True)
        
        if db_lot_data.get("order_manager_id") is not None:
            if db_lot_data.get("created_by_order_manager_at") is None:
                db_lot_data["created_by_order_manager_at"] = datetime.now(timezone.utc)
        
        db_lot = LotDB(**db_lot_data, status=LotStatus.NEW.value)

        db.add(db_lot)
        db.commit()
        db.refresh(db_lot)
        logger.info(f"Лот '{db_lot.lot_number}' успешно создан с ID {db_lot.id}.")
        
        return db_lot
    
    except HTTPException as http_e:
        db.rollback()
        raise http_e
    
    except IntegrityError as int_e:
        db.rollback()
        logger.error(f"IntegrityError при создании лота: {int_e}", exc_info=True)
        detailed_error = str(int_e.orig) if hasattr(int_e, 'orig') and int_e.orig else str(int_e)
        raise HTTPException(status_code=409, detail=f"Database integrity error: {detailed_error}")

    except Exception as e:
        db.rollback()
        logger.error(f"НЕПРЕДВИДЕННАЯ ОШИБКА СЕРВЕРА при создании лота: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Unexpected server error.")

@lots_router.get("/", response_model=List[LotResponse])
async def get_lots(
    response: Response, 
    search: Optional[str] = Query(None, description="Поисковый запрос для номера лота"),
    part_search: Optional[str] = Query(None, description="Поисковый запрос для номера детали"),
    status_filter: Optional[str] = Query(None, description="Фильтр по статусам (через запятую)"),
    skip: int = Query(0, ge=0, description="Количество записей для пропуска"),
    limit: int = Query(100, ge=1, le=500, description="Максимальное количество записей"),
    db: Session = Depends(get_db_session)
):
    """Получить список всех лотов с поиском и пагинацией"""
    query = db.query(LotDB).options(selectinload(LotDB.part))
    
    if search:
        search_term = f"%{search.lower()}%"
        query = query.filter(func.lower(LotDB.lot_number).like(search_term))
    
    if part_search:
        part_search_term = f"%{part_search.lower()}%"
        query = query.join(LotDB.part).filter(func.lower(PartDB.drawing_number).like(part_search_term))
    
    if status_filter:
        statuses = [status.strip() for status in status_filter.split(',') if status.strip()]
        if statuses:
            query = query.filter(LotDB.status.in_(statuses))
    
    total_count = query.count() 
    lots = query.order_by(LotDB.id.desc()).offset(skip).limit(limit).all()
    
    response.headers["X-Total-Count"] = str(total_count)
    return lots

@lots_router.get("/{lot_id}", response_model=LotResponse)
async def get_lot(lot_id: int, db: Session = Depends(get_db_session)):
    """
    Получить информацию о конкретном лоте.
    """
    lot = db.query(LotDB).options(selectinload(LotDB.part)).filter(LotDB.id == lot_id).first()
    if not lot:
        raise HTTPException(status_code=404, detail=f"Lot with id {lot_id} not found")
    
    return lot

@lots_router.get("/pending-qc", response_model=List[LotInfoItem])
async def get_lots_pending_qc(
    db: Session = Depends(get_db_session), 
    current_user_qa_id: Optional[int] = Query(None, alias="qaId"),
    hideCompleted: Optional[bool] = Query(False, description="Скрыть завершенные лоты и лоты со всеми проверенными батчами")
):
    """Получить лоты для ОТК с оптимизированной фильтрацией"""
    logger.info(f"Запрос /lots/pending-qc получен. qaId: {current_user_qa_id}, hideCompleted: {hideCompleted}")
    try:
        base_lot_ids_query = db.query(BatchDB.lot_id)\
            .filter(BatchDB.current_location != 'archived') \
            .distinct()

        if hideCompleted:
            base_lot_ids_query = base_lot_ids_query.join(LotDB, BatchDB.lot_id == LotDB.id)\
                .filter(LotDB.status != 'completed')
            
            unchecked_batch_exists = db.query(BatchDB.lot_id)\
                .filter(
                    or_(
                        BatchDB.current_location == 'qc_pending',
                        and_(
                            BatchDB.qc_inspector_id.is_(None),
                            BatchDB.current_location.notin_(['good', 'defect', 'archived'])
                        )
                    )
                )\
                .distinct()\
                .subquery()
            
            base_lot_ids_query = base_lot_ids_query.filter(BatchDB.lot_id.in_(unchecked_batch_exists))

        lot_ids_with_active_batches_tuples = base_lot_ids_query.all()
        lot_ids = [item[0] for item in lot_ids_with_active_batches_tuples]

        if not lot_ids:
            logger.info("Не найдено лотов с активными батчами (после фильтрации).")
            return []
        
        lots_query = db.query(LotDB, PartDB).select_from(LotDB)\
            .join(PartDB, LotDB.part_id == PartDB.id)\
            .filter(LotDB.id.in_(lot_ids))

        lots_query_result = lots_query.all()
        
        result = []
        for lot_obj, part_obj in lots_query_result:
            planned_quantity_val = None
            inspector_name_val = None
            machine_name_val = None
            
            latest_setup_details = db.query(
                    SetupDB.planned_quantity,
                    SetupDB.qa_id,
                    EmployeeDB.full_name.label("inspector_name_from_setup"),
                    MachineDB.name.label("machine_name_from_setup")
                )\
                .outerjoin(EmployeeDB, SetupDB.qa_id == EmployeeDB.id) \
                .outerjoin(MachineDB, SetupDB.machine_id == MachineDB.id) \
                .filter(SetupDB.lot_id == lot_obj.id)\
                .order_by(desc(SetupDB.created_at))\
                .first()

            passes_qa_filter = True

            if latest_setup_details:
                planned_quantity_val = latest_setup_details.planned_quantity
                machine_name_val = latest_setup_details.machine_name_from_setup

                if latest_setup_details.qa_id:
                    inspector_name_val = latest_setup_details.inspector_name_from_setup
                    if current_user_qa_id is not None and latest_setup_details.qa_id != current_user_qa_id:
                        passes_qa_filter = False
                elif current_user_qa_id is not None:
                    passes_qa_filter = False
            elif current_user_qa_id is not None:
                passes_qa_filter = False

            if not passes_qa_filter:
                continue

            item_data = {
                'id': lot_obj.id,
                'drawing_number': part_obj.drawing_number,
                'lot_number': lot_obj.lot_number,
                'inspector_name': inspector_name_val,
                'planned_quantity': planned_quantity_val,
                'machine_name': machine_name_val,
            }
            
            result.append(LotInfoItem.model_validate(item_data))

        logger.info(f"Сформировано {len(result)} элементов для ответа /lots/pending-qc.")
        return result

    except Exception as e:
        logger.error(f"Ошибка в /lots/pending-qc: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера при получении лотов для ОТК")

@lots_router.patch("/{lot_id}/status", response_model=LotResponse)
async def update_lot_status(
    lot_id: int,
    status_update: LotStatusUpdate,
    db: Session = Depends(get_db_session)
):
    """
    Обновить статус лота.
    """
    try:
        lot = db.query(LotDB).options(selectinload(LotDB.part)).filter(LotDB.id == lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail=f"Lot with id {lot_id} not found")
        
        old_status = lot.status
        lot.status = status_update.status.value
        
        db.commit()
        db.refresh(lot)
        
        logger.info(f"Статус лота {lot_id} обновлен с '{old_status}' на '{status_update.status.value}'")
        
        return lot
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка при обновлении статуса лота {lot_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while updating lot status")

@lots_router.patch("/{lot_id}/quantity", response_model=LotResponse)
async def update_lot_quantity(
    lot_id: int, 
    quantity_update: LotQuantityUpdate, 
    db: Session = Depends(get_db_session)
):
    """
    Обновить дополнительное количество для лота.
    """
    try:
        lot = db.query(LotDB).options(selectinload(LotDB.part)).filter(LotDB.id == lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail=f"Лот с ID {lot_id} не найден")
        
        allowed_statuses = [LotStatus.NEW.value, LotStatus.IN_PRODUCTION.value]
        if lot.status not in allowed_statuses:
            raise HTTPException(
                status_code=400, 
                detail=f"Изменение количества доступно только для статусов: {allowed_statuses}. Текущий статус: '{lot.status}'"
            )
        
        initial_quantity = lot.initial_planned_quantity or 0
        additional_quantity = lot.additional_quantity or 0
        new_additional_quantity = additional_quantity + quantity_update.additional_quantity
        
        lot.additional_quantity = new_additional_quantity
        lot.total_planned_quantity = initial_quantity + new_additional_quantity
        
        db.commit()
        db.refresh(lot)
        
        logger.info(f"Количество лота {lot_id} обновлено: initial={initial_quantity}, new_additional={new_additional_quantity}, total={lot.total_planned_quantity}")
        
        return lot
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при обновлении количества лота {lot_id}: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера при обновлении количества: {str(e)}")

@lots_router.patch("/{lot_id}/close", response_model=LotResponse)
async def close_lot(lot_id: int, db: Session = Depends(get_db_session)):
    """
    Закрыть лот (перевести в статус 'completed').
    """
    try:
        lot = db.query(LotDB).options(selectinload(LotDB.part)).filter(LotDB.id == lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail=f"Лот с ID {lot_id} не найден")
        
        if lot.status != LotStatus.POST_PRODUCTION.value:
            raise HTTPException(
                status_code=400, 
                detail=f"Нельзя закрыть лот в статусе '{lot.status}'. Ожидался статус '{LotStatus.POST_PRODUCTION.value}'"
            )
        
        lot.status = LotStatus.COMPLETED.value
        db.commit()
        db.refresh(lot)
        
        logger.info(f"Successfully closed lot {lot_id}")
        
        return lot
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error closing lot {lot_id}: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера при закрытии лота: {str(e)}")

@lots_router.get("/{lot_id}/batches", response_model=List[BatchViewItem])
async def get_batches_for_lot(lot_id: int, db: Session = Depends(get_db_session)):
    """Вернуть все неархивные батчи для указанного лота."""
    try:
        batches = db.query(BatchDB, PartDB, LotDB, EmployeeDB).select_from(BatchDB) \
            .join(LotDB, BatchDB.lot_id == LotDB.id) \
            .join(PartDB, LotDB.part_id == PartDB.id) \
            .outerjoin(EmployeeDB, BatchDB.operator_id == EmployeeDB.id) \
            .filter(BatchDB.lot_id == lot_id) \
            .filter(BatchDB.current_location != 'archived') \
            .all()

        result = []
        for row in batches:
            batch_obj, part_obj, lot_obj, emp_obj = row
            result.append({
                'id': batch_obj.id,
                'lot_id': lot_id,
                'drawing_number': part_obj.drawing_number if part_obj else None,
                'lot_number': lot_obj.lot_number if lot_obj else None,
                'current_quantity': batch_obj.current_quantity,
                'current_location': batch_obj.current_location,
                'batch_time': batch_obj.batch_time,
                'warehouse_received_at': batch_obj.warehouse_received_at, 
                'operator_name': emp_obj.full_name if emp_obj else None, 
            })
        return result
    except Exception as e:
        logger.error(f"Error fetching batches for lot {lot_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while fetching batches")

@lots_router.get("/{lot_id}/analytics", response_model=LotAnalyticsResponse)
async def get_lot_analytics(lot_id: int, db: Session = Depends(get_db_session)):
    """Получить сводную аналитику по указанному лоту."""
    lot = db.query(LotDB).filter(LotDB.id == lot_id).first()
    if not lot:
        raise HTTPException(status_code=404, detail=f"Lot with id {lot_id} not found")

    accepted_warehouse_query = db.query(func.sum(BatchDB.recounted_quantity))\
        .filter(BatchDB.lot_id == lot_id)\
        .filter(BatchDB.recounted_quantity.isnot(None))
    
    accepted_by_warehouse_result = accepted_warehouse_query.scalar() or 0

    from_machine_result = 0
    latest_setup_for_lot = db.query(SetupDB.machine_id)\
        .filter(SetupDB.lot_id == lot_id)\
        .order_by(SetupDB.created_at.desc())\
        .first()
    
    if latest_setup_for_lot and latest_setup_for_lot.machine_id:
        machine_id_for_lot = latest_setup_for_lot.machine_id
        
        # Найти последнюю карточку для этой наладки
        latest_card_for_setup = db.query(CardDB)\
            .filter(CardDB.setup_id == latest_setup_for_lot.id)\
            .order_by(CardDB.created_at.desc())\
            .first()

        if latest_card_for_setup:
            # Найти последнее показание для этой карточки
            latest_reading_for_card = db.query(func.max(ReadingDB.reading))\
                .filter(ReadingDB.card_id == latest_card_for_setup.id)\
                .scalar()
            
            if latest_reading_for_card is not None:
                from_machine_result = latest_reading_for_card

    return LotAnalyticsResponse(
        accepted_by_warehouse_quantity=accepted_by_warehouse_result,
        from_machine_quantity=from_machine_result
    )