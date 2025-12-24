import logging
from sqlalchemy.orm import Session, aliased
from .telegram_client import send_telegram_message
# Убираем RoleDB из импорта
from src.models.models import SetupDB, EmployeeDB, MachineDB, LotDB, PartDB 

logger = logging.getLogger(__name__)

# Предполагаемые ID ролей (!!! УТОЧНИТЬ РЕАЛЬНЫЕ ЗНАЧЕНИЯ !!!)
ADMIN_ROLE_ID = 3 # Was 4
OPERATOR_ROLE_ID = 1 # Correct
MACHINIST_ROLE_ID = 2 # Correct

async def send_setup_approval_notifications(db: Session, setup_id: int, notification_type: str = "approval"):
    """
    Отправляет уведомления о наладке разным ролям, используя SQLAlchemy.
    
    Args:
        db: Сессия базы данных
        setup_id: ID наладки
        notification_type: Тип уведомления ("approval" или "completion")
    """
    try:
        logger.info(f"Fetching data for {notification_type} notification (Setup ID: {setup_id}) using SQLAlchemy")

        Machinist = aliased(EmployeeDB)
        QAEmployee = aliased(EmployeeDB)

        setup = db.query(
                SetupDB,
                MachineDB,
                LotDB,
                PartDB,
                Machinist,
                QAEmployee
            )\
            .join(Machinist, SetupDB.employee_id == Machinist.id)\
            .join(MachineDB, SetupDB.machine_id == MachineDB.id)\
            .join(LotDB, SetupDB.lot_id == LotDB.id)\
            .join(PartDB, SetupDB.part_id == PartDB.id)\
            .outerjoin(QAEmployee, SetupDB.qa_id == QAEmployee.id)\
            .filter(SetupDB.id == setup_id)\
            .first()

        if not setup:
            logger.error(f"Setup {setup_id} not found for notification.")
            return False
        
        setup_obj, machine_obj, lot_obj, part_obj, machinist_obj, qa_obj = setup

        qa_name = qa_obj.full_name if qa_obj else 'Неизвестно'
        machinist_name = machinist_obj.full_name if machinist_obj else 'Неизвестно'
        machine_name = machine_obj.name if machine_obj else 'Неизвестно'
        drawing_number = part_obj.drawing_number if part_obj else 'Неизвестно'
        lot_number = lot_obj.lot_number if lot_obj else 'Неизвестно'
        qa_date_str = setup_obj.qa_date.strftime('%d.%m.%Y %H:%M') if setup_obj.qa_date else 'Нет даты'
        completion_time = setup_obj.end_time.strftime('%d.%m.%Y %H:%M') if setup_obj.end_time else 'Нет даты'

        if notification_type == "approval":
            base_message = (
                f"<b>✅ Наладка одобрена</b>\n\n"
                f"<b>Станок:</b> {machine_name}\n"
                f"<b>Чертёж:</b> {drawing_number}\n"
                f"<b>Партия:</b> {lot_number}\n"
                f"<b>Наладчик:</b> {machinist_name}\n"
                f"<b>ОТК:</b> {qa_name}\n"
                f"<b>Время:</b> {qa_date_str}"
            )
            machinist_message = base_message + "\n\n<i>Для начала работы установите время цикла ⏱</i>"
            admin_message = base_message + "\n\n<i>Требуется установка времени цикла ⏱</i>"
            operator_message = base_message + "\n\n<i>Готово к работе 🛠</i>"
        else:  # completion
            base_message = (
                f"<b>🏁 Наладка завершена</b>\n\n"
                f"<b>Станок:</b> {machine_name}\n"
                f"<b>Чертёж:</b> {drawing_number}\n"
                f"<b>Партия:</b> {lot_number}\n"
                f"<b>Наладчик:</b> {machinist_name}\n"
                f"<b>Время завершения:</b> {completion_time}\n"
                f"<b>Плановая партия:</b> {setup_obj.planned_quantity}\n"
                f"<b>Дополнительная партия:</b> {setup_obj.additional_quantity}"
            )
            machinist_message = base_message + "\n\n<i>Наладка успешно завершена ✅</i>"
            admin_message = base_message + "\n\n<i>Требуется проверка завершенной наладки 📋</i>"
            operator_message = base_message + "\n\n<i>Наладка завершена, можно приступать к следующей 🛠</i>"

        if machinist_obj and machinist_obj.telegram_id:
            await send_telegram_message(machinist_obj.telegram_id, machinist_message)

        await _notify_role_by_id_sqlalchemy(db, ADMIN_ROLE_ID, admin_message, exclude_id=machinist_obj.id if machinist_obj else None)
        await _notify_role_by_id_sqlalchemy(db, OPERATOR_ROLE_ID, operator_message, exclude_id=machinist_obj.id if machinist_obj else None)

        # 🔔 Если это завершение - уведомляем ВСЕХ наладчиков об освободившемся станке
        if notification_type == "completion":
            free_machine_message = (
                f"<b>🟢 Станок освободился!</b>\n\n"
                f"<b>Станок:</b> {machine_name}\n"
                f"<b>Чертёж:</b> {drawing_number}\n"
                f"<b>Партия:</b> {lot_number}\n"
                f"<b>Время:</b> {completion_time}\n\n"
                f"<i>Станок готов для новой наладки 🛠</i>"
            )
            await _notify_role_by_id_sqlalchemy(
                db, 
                MACHINIST_ROLE_ID, 
                free_machine_message, 
                exclude_id=machinist_obj.id if machinist_obj else None  # не шлём автору наладки
            )
            logger.info(f"Sent 'machine free' notification to all machinists for machine {machine_name}")

        logger.info(f"Successfully processed {notification_type} notifications for setup {setup_id}")
        return True

    except Exception as e:
        logger.error(f"Error sending {notification_type} notifications for setup {setup_id}: {e}", exc_info=True)
        return False

async def _notify_role_by_id_sqlalchemy(db: Session, role_id: int, message: str, exclude_id: int = None):
    """Отправляет сообщение всем сотрудникам с указанным role_id SQLAlchemy, кроме exclude_id."""
    try:
        query = db.query(EmployeeDB)\
            .filter(EmployeeDB.role_id == role_id) \
            .filter(EmployeeDB.is_active == True)\
            .filter(EmployeeDB.telegram_id != None)
        
        if exclude_id is not None:
            query = query.filter(EmployeeDB.id != exclude_id)
            
        employees = query.all()

        logger.debug(f"Found {len(employees)} active employees with role_id '{role_id}' and Telegram ID to notify.")

        for emp in employees:
            if emp.telegram_id:
                 logger.debug(f"Sending notification to role_id {role_id}: {emp.full_name} (ID: {emp.id}, TG_ID: {emp.telegram_id})")
                 await send_telegram_message(emp.telegram_id, message)

    except Exception as e:
        logger.error(f"Failed to notify role_id '{role_id}': {e}", exc_info=True)

async def send_batch_discrepancy_alert(db: Session, discrepancy_details: dict):
    """
    Отправляет уведомление администраторам о критическом расхождении при приемке батча.
    
    Args:
        db: Сессия базы данных.
        discrepancy_details: Словарь с деталями расхождения:
            {
                "batch_id": int,
                "drawing_number": str,
                "lot_number": str,
                "machine_name": str,       // Станок
                "operator_name": str,      // Оператор производства
                "warehouse_employee_name": str, // Кладовщик
                "original_qty": int,
                "recounted_qty": int,
                "discrepancy_abs": int,
                "discrepancy_perc": float
            }
    """
    try:
        logger.info(f"Sending discrepancy alert for Batch ID: {discrepancy_details.get('batch_id')}")

        message = (
            f"<b>🚨 Критическое расхождение при приемке батча!</b>\n\n"
            f"<b>Батч ID:</b> {discrepancy_details.get('batch_id', 'N/A')}\n"
            f"<b>Чертёж:</b> {discrepancy_details.get('drawing_number', 'N/A')}\n"
            f"<b>Партия:</b> {discrepancy_details.get('lot_number', 'N/A')}\n"
            f"<b>Станок:</b> {discrepancy_details.get('machine_name', 'N/A')}\n"
            f"------------------------------------\n"
            f"<b>Оператор производства:</b> {discrepancy_details.get('operator_name', 'N/A')}\n"
            f"<b>Кол-во от оператора:</b> {discrepancy_details.get('original_qty', 'N/A')}\n"
            f"------------------------------------\n"
            f"<b>Кладовщик:</b> {discrepancy_details.get('warehouse_employee_name', 'N/A')}\n"
            f"<b>Кол-во (склад):</b> {discrepancy_details.get('recounted_qty', 'N/A')}\n"
            f"------------------------------------\n"
            f"<b>Расхождение:</b> {discrepancy_details.get('discrepancy_abs', 'N/A')} шт. "
            f"({discrepancy_details.get('discrepancy_perc', 0.0):.2f}%)\n\n"
            f"<i>Требуется проверка и подтверждение администратора.</i>"
        )

        await _notify_role_by_id_sqlalchemy(db, ADMIN_ROLE_ID, message)
        logger.info(f"Successfully processed discrepancy alert for Batch ID: {discrepancy_details.get('batch_id')}")
        return True

    except Exception as e:
        logger.error(f"Error sending discrepancy alert for Batch ID {discrepancy_details.get('batch_id')}: {e}", exc_info=True)
        return False 