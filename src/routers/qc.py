import logging
from typing import List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text, desc

from src.database import get_db_session
from src.models.models import LotDB, PartDB, SetupDB, EmployeeDB, MachineDB
from pydantic import BaseModel
from src.services.telegram_client import send_telegram_message

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
    status: Optional[str] = None

    class Config:
        from_attributes = True
        populate_by_name = True


class NotifyRequest(BaseModel):
    setup_id: int


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
    if current_user_qa_id is not None:
        logger.info(f"Применяется фильтрация по QA ID: {current_user_qa_id}")
    try:
        # Основной запрос для получения лотов для ОТК
        # Создаем алиасы для разных ролей сотрудников
        machinist_alias = db.query(EmployeeDB).subquery().alias('machinist')
        inspector_alias = db.query(EmployeeDB).subquery().alias('inspector')
        
        query = db.query(
            LotDB,
            PartDB.drawing_number,
            (SetupDB.planned_quantity + SetupDB.additional_quantity).label('total_planned_quantity'),
            MachineDB.name.label('machine_name'),
            machinist_alias.c.full_name.label('machinist_name'),
            inspector_alias.c.full_name.label('inspector_name')
        ).select_from(LotDB)\
         .join(PartDB, LotDB.part_id == PartDB.id)\
         .outerjoin(SetupDB, LotDB.id == SetupDB.lot_id)\
         .outerjoin(MachineDB, SetupDB.machine_id == MachineDB.id)\
         .outerjoin(machinist_alias, SetupDB.employee_id == machinist_alias.c.id)\
         .outerjoin(inspector_alias, SetupDB.qa_id == inspector_alias.c.id)\
         .filter(LotDB.status.notin_(['new', 'cancelled']))\
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

        # Фильтрация по QA ID убрана - теперь фильтрация происходит на фронтенде
        # if current_user_qa_id is not None:
        #     query = query.filter(SetupDB.qa_id == current_user_qa_id)

        results = query.all()
        
        # Собираем ответ
        response_items = []
        for lot, drawing_number, planned_quantity, machine_name, machinist_name, inspector_name in results:
            response_items.append(
                LotInfoItem(
                    id=lot.id,
                    drawing_number=drawing_number,
                    lot_number=lot.lot_number,
                    planned_quantity=planned_quantity,
                    machine_name=machine_name,
                    machinist_name=machinist_name,
                    inspector_name=inspector_name,
                    status=lot.status
                )
            )

        logger.info(f"Сформировано {len(response_items)} элементов для ответа /qc/lots-pending.")
        
        # Логируем статусы для отладки
        status_counts = {}
        for item in response_items:
            status = item.status or 'null'
            status_counts[status] = status_counts.get(status, 0) + 1
        logger.info(f"Статистика статусов: {status_counts}")
        
        return response_items

    except Exception as e:
        logger.error(f"Ошибка в /qc/lots-pending: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера при получении лотов для ОТК")


@router.post("/setups/notify-allowed", summary="Отправить уведомление о разрешении наладки")
async def notify_setup_allowed(
    request: NotifyRequest,
    db: Session = Depends(get_db_session)
):
    """
    Принимает ID наладки и отправляет уведомление наладчику о том,
    что его наладка разрешена ОТК и можно начинать работу.
    """
    setup_id = request.setup_id
    logger.info(f"Получен запрос на уведомление о разрешении для наладки ID: {setup_id}")
    
    try:
        # 1. Найти наладку и связанную информацию
        from sqlalchemy.orm import aliased
        QaEmployee = aliased(EmployeeDB)
        
        setup_info = db.query(
            SetupDB.id,
            SetupDB.status,
            EmployeeDB.telegram_id,
            EmployeeDB.full_name.label("machinist_name"),
            MachineDB.name.label("machine_name"),
            PartDB.drawing_number,
            QaEmployee.full_name.label("qa_name")
        ).select_from(SetupDB)\
         .join(EmployeeDB, SetupDB.employee_id == EmployeeDB.id)\
         .join(MachineDB, SetupDB.machine_id == MachineDB.id)\
         .join(LotDB, SetupDB.lot_id == LotDB.id)\
         .join(PartDB, LotDB.part_id == PartDB.id)\
         .join(QaEmployee, SetupDB.qa_id == QaEmployee.id)\
         .filter(SetupDB.id == setup_id)\
         .first()

        if not setup_info:
            logger.error(f"Наладка с ID {setup_id} не найдена для отправки уведомления.")
            raise HTTPException(status_code=404, detail="Наладка не найдена")

        # 2. Проверить статус
        if setup_info.status != 'allowed':
            logger.warning(f"Попытка уведомить о наладке в статусе '{setup_info.status}', а не 'allowed'. Уведомление не отправлено.")
            return {"message": "Уведомление не отправлено, так как статус наладки не 'allowed'."}

        # 3. Получить всех получателей (наладчик, админы, операторы)
        other_recipients = db.query(EmployeeDB.telegram_id).filter(
            EmployeeDB.role_id.in_([1, 3]),  # 1=Оператор, 3=Админ
            EmployeeDB.is_active == True,
            EmployeeDB.telegram_id.isnot(None)
        ).all()
        
        ids_to_notify = {recipient.telegram_id for recipient in other_recipients}
        machinist_telegram_id = setup_info.telegram_id
        if machinist_telegram_id:
            ids_to_notify.add(machinist_telegram_id)

        logger.info(f"Всего получателей уведомления: {len(ids_to_notify)}")

        # 4. Сформировать сообщения
        machinist_message = (
            f"✅ **Ваша** наладка на станке <b>{setup_info.machine_name}</b> для детали <b>{setup_info.drawing_number}</b> одобрена ОТК ({setup_info.qa_name}).\n\n"
            f"Можно начинать работу!"
        )
        general_message = (
            f"ℹ️ Наладка на станке <b>{setup_info.machine_name}</b> для детали <b>{setup_info.drawing_number}</b> (наладчик: {setup_info.machinist_name}) одобрена ОТК ({setup_info.qa_name}).\n\n"
            f"Операторы могут начинать работу."
        )

        # 5. Отправить уведомления
        successful_sends = 0
        for user_id in ids_to_notify:
            try:
                message_to_send = machinist_message if user_id == machinist_telegram_id else general_message
                await send_telegram_message(
                    chat_id=user_id,
                    text=message_to_send
                )
                logger.info(f"Уведомление успешно отправлено пользователю ID: {user_id}")
                successful_sends += 1
            except Exception as send_error:
                logger.error(f"Не удалось отправить уведомление пользователю {user_id}: {send_error}")

        return {"success": True, "message": f"Уведомления отправлены {successful_sends} из {len(ids_to_notify)} получателей."}

    except HTTPException as e:
        # Перебрасываем HTTP исключения
        raise e
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления для наладки {setup_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера при отправке уведомления") 