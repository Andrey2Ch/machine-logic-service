import logging
from typing import List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text, desc

from src.database import get_db_session
from src.models.models import LotDB, PartDB, SetupDB, EmployeeDB, MachineDB
from pydantic import BaseModel
from src.services.telegram_client import send_telegram_message
from src.services.whatsapp_client import send_whatsapp_to_all_enabled_roles, WHATSAPP_ENABLED

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Quality Control"])


class LotInfoItem(BaseModel):
    id: int
    drawing_number: Optional[str] = None
    lot_number: Optional[str] = None
    inspector_name: Optional[str] = None
    machinist_name: Optional[str] = None
    planned_quantity: Optional[int] = None
    initial_planned_quantity: Optional[int] = None
    additional_quantity: Optional[int] = None
    machine_name: Optional[str] = None
    status: Optional[str] = None

    class Config:
        from_attributes = True
        populate_by_name = True


class NotifyRequest(BaseModel):
    setup_id: int

class DefectNotificationRequest(BaseModel):
    """Модель для уведомления о браке"""
    machine: str
    drawing_number: str
    lot_number: str
    defect_quantity: int
    total_defect_qty: int
    operator_name: str
    inspector_name: str
    defect_reason: Optional[str] = None
    timestamp: str
    operator_id: Optional[int] = None
    machinist_id: Optional[int] = None
    setup_job_id: Optional[int] = None


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
            LotDB.initial_planned_quantity.label('initial_planned_quantity'),
            SetupDB.additional_quantity.label('additional_quantity'),
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
        
        # Собираем ответ (показываем все лоты с оригинальными статусами)
        response_items = []
        for lot, drawing_number, planned_quantity, initial_planned_quantity, additional_quantity, machine_name, machinist_name, inspector_name in results:
            response_items.append(
                LotInfoItem(
                    id=lot.id,
                    drawing_number=drawing_number,
                    lot_number=lot.lot_number,
                    planned_quantity=planned_quantity,
                    initial_planned_quantity=initial_planned_quantity or 0,
                    additional_quantity=additional_quantity or 0,
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

        # 6. Отправить в WhatsApp группы
        if WHATSAPP_ENABLED:
            try:
                # Формируем сообщение для WhatsApp (без HTML)
                wa_message = (
                    f"✅ Наладка разрешена ОТК!\n\n"
                    f"🔧 Станок: {setup_info.machine_name}\n"
                    f"📝 Чертёж: {setup_info.drawing_number}\n"
                    f"👨‍🔧 Наладчик: {setup_info.machinist_name}\n"
                    f"✔️ ОТК: {setup_info.qa_name}\n\n"
                    f"Операторы могут начинать работу!"
                )
                
                # Отправляем всем включённым ролям
                wa_sent = await send_whatsapp_to_all_enabled_roles(db, wa_message, "setup_allowed")
                logger.info(f"WhatsApp уведомления о разрешении наладки {setup_id} отправлены ({wa_sent})")
            except Exception as wa_err:
                logger.warning(f"WhatsApp уведомление не отправлено (non-critical): {wa_err}")

        return {"success": True, "message": f"Уведомления отправлены {successful_sends} из {len(ids_to_notify)} получателей."}

    except HTTPException as e:
        # Перебрасываем HTTP исключения
        raise e
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления для наладки {setup_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера при отправке уведомления")

@router.post("/defect/notify", summary="Отправить уведомление о браке")
async def notify_defect_detected(
    request: DefectNotificationRequest,
    db: Session = Depends(get_db_session)
):
    """
    Отправляет уведомления о браке оператору, наладчику и админам.
    Вызывается из isramat-dashboard при создании батча defect.
    """
    logger.info(f"Получен запрос на уведомление о браке: {request.model_dump()}")
    logger.info(f"Defect notification request details: operator_id={request.operator_id}, machinist_id={request.machinist_id}, setup_job_id={request.setup_job_id}")
    
    try:
        # Формируем сообщение с визуальным выделением брака
        reason_text = f"\n📝 Причина: {request.defect_reason}" if request.defect_reason else ""
        # Используем красный цвет и жирный шрифт для количества брака
        message = (
            f"🚨 <b>ЗАФИКСИРОВАН БРАК!</b> 🚨\n\n"
            f"🔧 Станок: {request.machine}\n"
            f"📝 Чертёж: {request.drawing_number}\n"
            f"🔢 Партия: {request.lot_number}\n"
            f"<b>❌ БРАК: <u>{request.defect_quantity} шт.</u></b>\n"
            f"<b>📊 Общий брак по лоту: <u>{request.total_defect_qty} шт.</u></b>\n"
            f"👤 Оператор: {request.operator_name}\n"
            f"👤 Зафиксировал: {request.inspector_name}"
            f"{reason_text}\n"
            f"⏰ Время: {request.timestamp}"
        )
        
        recipients = []
        successful_sends = 0
        
        # 1. Оператор - только если брак по его станку
        if request.operator_id:
            try:
                operator = db.query(EmployeeDB.telegram_id, EmployeeDB.full_name).filter(
                    EmployeeDB.id == request.operator_id,
                    EmployeeDB.telegram_id.isnot(None),
                    EmployeeDB.telegram_id != -1,
                    EmployeeDB.is_active == True
                ).first()
                
                if operator:
                    recipients.append(('operator', operator.telegram_id, operator.full_name))
            except Exception as e:
                logger.error(f"Error finding operator for defect notification: {e}")
        
        # 2. Наладчик - если брак по его наладке
        # Сначала пробуем через machinist_id, если нет - через setup_job_id
        machinist_found = False
        if request.machinist_id:
            try:
                machinist = db.query(EmployeeDB.telegram_id, EmployeeDB.full_name).filter(
                    EmployeeDB.id == request.machinist_id,
                    EmployeeDB.telegram_id.isnot(None),
                    EmployeeDB.telegram_id != -1,
                    EmployeeDB.is_active == True
                ).first()
                
                if machinist:
                    recipients.append(('machinist', machinist.telegram_id, machinist.full_name))
                    machinist_found = True
                    logger.info(f"Found machinist via machinist_id: {request.machinist_id}")
            except Exception as e:
                logger.error(f"Error finding machinist for defect notification: {e}")
        
        # Если наладчик не найден через machinist_id, пробуем через setup_job_id
        if not machinist_found and request.setup_job_id:
            try:
                logger.info(f"Trying to find machinist via setup_job_id: {request.setup_job_id}")
                setup = db.query(SetupDB.employee_id).filter(SetupDB.id == request.setup_job_id).first()
                if setup:
                    logger.info(f"Setup found: employee_id={setup.employee_id}")
                    if setup.employee_id:
                        machinist = db.query(EmployeeDB.telegram_id, EmployeeDB.full_name).filter(
                            EmployeeDB.id == setup.employee_id,
                            EmployeeDB.telegram_id.isnot(None),
                            EmployeeDB.telegram_id != -1,
                            EmployeeDB.is_active == True
                        ).first()
                        
                        if machinist:
                            recipients.append(('machinist', machinist.telegram_id, machinist.full_name))
                            logger.info(f"Found machinist via setup_job_id: {request.setup_job_id}, name={machinist.full_name}, telegram_id={machinist.telegram_id}")
                        else:
                            logger.warning(f"Machinist with employee_id={setup.employee_id} not found or has no telegram_id")
                    else:
                        logger.warning(f"Setup {request.setup_job_id} has no employee_id")
                else:
                    logger.warning(f"Setup with id={request.setup_job_id} not found")
            except Exception as e:
                logger.error(f"Error finding machinist via setup_job_id: {e}", exc_info=True)
        
        # 3. Админы - всегда
        try:
            admins = db.query(EmployeeDB.telegram_id, EmployeeDB.full_name).filter(
                EmployeeDB.role_id == 3,  # Admin role
                EmployeeDB.telegram_id.isnot(None),
                EmployeeDB.telegram_id != -1,
                EmployeeDB.is_active == True
            ).all()
            
            for admin in admins:
                recipients.append(('admin', admin.telegram_id, admin.full_name))
        except Exception as e:
            logger.error(f"Error finding admins for defect notification: {e}")
        
        # Отправляем уведомления
        logger.info(f"Total recipients found: {len(recipients)}")
        if len(recipients) == 0:
            logger.warning("No recipients found for defect notification! Check operator_id, machinist_id, and admin role_id.")
        
        sent_recipients = []
        for role, telegram_id, name in recipients:
            try:
                logger.info(f"Attempting to send defect notification to {role} ({name}, telegram_id={telegram_id})")
                result = await send_telegram_message(
                    chat_id=telegram_id,
                    text=message
                )
                if result:
                    successful_sends += 1
                    sent_recipients.append(f"{role}:{name}")
                    logger.info(f"Defect notification sent successfully to {role} ({name}, {telegram_id})")
                else:
                    logger.error(f"send_telegram_message returned False for {role} ({name}, {telegram_id})")
            except Exception as e:
                logger.error(f"Failed to send defect notification to {telegram_id}: {e}", exc_info=True)
        
        logger.info(f"Defect notifications sent: {successful_sends}/{len(recipients)}")
        
        # 🔔 WhatsApp уведомления о браке - всем включённым ролям
        wa_sent = 0
        if WHATSAPP_ENABLED:
            try:
                wa_sent = await send_whatsapp_to_all_enabled_roles(db, message, "defect_detected")
                logger.info(f"WhatsApp defect notifications sent to {wa_sent} recipients/groups")
            except Exception as wa_err:
                logger.warning(f"WhatsApp defect notification failed (non-critical): {wa_err}")
        
        return {
            "success": True,
            "sent": successful_sends,
            "total_recipients": len(recipients),
            "recipients": sent_recipients,
            "whatsapp_sent": wa_sent
        }
        
    except Exception as e:
        logger.error(f"Error in defect notification: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при отправке уведомлений о браке: {str(e)}") 