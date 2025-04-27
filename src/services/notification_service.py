import logging
from sqlalchemy.orm import Session, aliased
from .telegram_client import send_telegram_message
# Убираем RoleDB из импорта
from src.models.models import SetupDB, EmployeeDB, MachineDB, LotDB, PartDB 

logger = logging.getLogger(__name__)

# Предполагаемые ID ролей (!!! УТОЧНИТЬ РЕАЛЬНЫЕ ЗНАЧЕНИЯ !!!)
ADMIN_ROLE_ID = 4 # Пример
OPERATOR_ROLE_ID = 1 # Пример
MACHINIST_ROLE_ID = 2 # Пример (на всякий случай)

async def send_setup_approval_notifications(db: Session, setup_id: int):
    """Отправляет уведомления об одобрении наладки разным ролям, используя SQLAlchemy."""
    try:
        logger.info(f"Fetching data for setup approval notification (Setup ID: {setup_id}) using SQLAlchemy")

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

        base_message = (
            f"<b>✅ Наладка одобрена</b>\n\n"
            f"<b>Станок:</b> {machine_name}\n"
            f"<b>Чертёж:</b> {drawing_number}\n"
            f"<b>Партия:</b> {lot_number}\n"
            f"<b>Наладчик:</b> {machinist_name}\n"
            f"<b>ОТК:</b> {qa_name}\n"
            f"<b>Время:</b> {qa_date_str}"
        )

        if machinist_obj and machinist_obj.telegram_id:
            machinist_message = base_message + "\n\n<i>Для начала работы установите время цикла ⏱</i>"
            await send_telegram_message(machinist_obj.telegram_id, machinist_message)

        await _notify_role_by_id_sqlalchemy(db, ADMIN_ROLE_ID, base_message + "\n\n<i>Требуется установка времени цикла ⏱</i>", exclude_id=machinist_obj.id if machinist_obj else None)
        await _notify_role_by_id_sqlalchemy(db, OPERATOR_ROLE_ID, base_message + "\n\n<i>Готово к работе 🛠</i>", exclude_id=machinist_obj.id if machinist_obj else None)

        logger.info(f"Successfully processed notifications for setup {setup_id}")
        return True

    except Exception as e:
        logger.error(f"Error sending setup approval notifications for setup {setup_id}: {e}", exc_info=True)
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