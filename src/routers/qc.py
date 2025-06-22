import logging
from typing import List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text, desc

from src.database import get_db_session
from src.services.lot_service import get_active_lot_ids
from src.models.models import LotDB, PartDB, SetupDB, EmployeeDB, MachineDB
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Quality Control"])


class LotInfoItem(BaseModel):
    id: int
    drawing_number: Optional[str] = None
    lot_number: Optional[str] = None
    inspector_name: Optional[str] = None
    machinist_name: Optional[str] = None
    planned_quantity: Optional[int] = None
    machine_name: Optional[str] = None

    class Config:
        from_attributes = True
        populate_by_name = True


@router.get("/lots-pending-qc", response_model=List[LotInfoItem])
async def get_lots_pending_qc(
    db: Session = Depends(get_db_session),
    current_user_qa_id: Optional[int] = Query(None, alias="qaId"),
    hide_completed: bool = Query(True, description="Скрыть лоты, где все партии проверены"),
    date_filter: Optional[str] = Query("all", description="Фильтр по периоду: all, 1month, 2months, 6months")
):
    """
    Получить лоты, ожидающие контроля качества (ОТК).
    Использует централизованную логику для определения "активных" лотов.
    """
    logger.info(f"Запрос /qc/lots-pending. qaId: {current_user_qa_id}, hide_completed: {hide_completed}, date_filter: {date_filter}")
    try:
        # 1. Получаем ID активных лотов с помощью сервисной функции
        # Для ОТК всегда используем строгую проверку (for_qc=True)
        active_lot_ids = get_active_lot_ids(db, for_qc=hide_completed)

        if not active_lot_ids:
            logger.info("Активных лотов для ОТК не найдено.")
            return []

        # 2. Основной запрос для получения деталей по найденным лотам
        query = db.query(
            LotDB,
            PartDB.drawing_number,
            (SetupDB.planned_quantity + SetupDB.additional_quantity).label('total_planned_quantity'),
            MachineDB.name.label('machine_name'),
            EmployeeDB.full_name.label('machinist_name')
        ).select_from(LotDB)\
         .join(PartDB, LotDB.part_id == PartDB.id)\
         .outerjoin(SetupDB, LotDB.id == SetupDB.lot_id)\
         .outerjoin(MachineDB, SetupDB.machine_id == MachineDB.id)\
         .outerjoin(EmployeeDB, SetupDB.employee_id == EmployeeDB.id)\
         .filter(LotDB.id.in_(active_lot_ids))\
         .order_by(desc(LotDB.created_at))

        # Применяем фильтр по дате, если он есть
        params = {}
        if date_filter and date_filter != "all":
            from datetime import datetime, timedelta
            filter_date = None
            if date_filter == "1month": filter_date = datetime.now() - timedelta(days=30)
            elif date_filter == "2months": filter_date = datetime.now() - timedelta(days=60)
            elif date_filter == "6months": filter_date = datetime.now() - timedelta(days=180)
            
            if filter_date:
                query = query.filter(LotDB.created_at >= filter_date)

        # TODO: Добавить фильтрацию по current_user_qa_id, если потребуется

        results = query.all()
        
        # Собираем ответ
        response_items = []
        for lot, drawing_number, planned_quantity, machine_name, machinist_name in results:
            response_items.append(
                LotInfoItem(
                    id=lot.id,
                    drawing_number=drawing_number,
                    lot_number=lot.lot_number,
                    planned_quantity=planned_quantity,
                    machine_name=machine_name,
                    machinist_name=machinist_name,
                    inspector_name=None # TODO: Add inspector name logic if needed
                )
            )

        logger.info(f"Сформировано {len(response_items)} элементов для ответа /qc/lots-pending.")
        return response_items

    except Exception as e:
        logger.error(f"Ошибка в /qc/lots-pending: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера при получении лотов для ОТК") 