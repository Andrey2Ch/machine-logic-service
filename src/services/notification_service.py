import logging
import math
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session, aliased
from .telegram_client import send_telegram_message
from .whatsapp_client import send_whatsapp_to_role, send_whatsapp_to_role_personal, send_whatsapp_to_all_enabled_roles, WHATSAPP_ENABLED
# Убираем RoleDB из импорта
from src.models.models import SetupDB, EmployeeDB, MachineDB, LotDB, PartDB, LotMaterialDB
from src.routers.notification_settings import is_notification_enabled
from src.database import SessionLocal  # Для создания своей сессии в background tasks

logger = logging.getLogger(__name__)

# Предполагаемые ID ролей (!!! УТОЧНИТЬ РЕАЛЬНЫЕ ЗНАЧЕНИЯ !!!)
ADMIN_ROLE_ID = 3 # Was 4
OPERATOR_ROLE_ID = 1 # Correct
MACHINIST_ROLE_ID = 2 # Correct
QA_ROLE_ID = 5 # ОТК
VIEWER_ROLE_ID = 7 # Viewer (мониторинг)

MATERIAL_LOW_NOTIFICATION_TYPE = "material_low"
DEFAULT_BLADE_WIDTH_MM = 3.0
DEFAULT_FACING_ALLOWANCE_MM = 0.5
DEFAULT_MIN_REMAINDER_MM = 300.0


async def send_material_low_notification(
    db: Session,
    *,
    lot_material: LotMaterialDB,
    machine_name: str,
    lot_number: str,
    drawing_number: str,
    hours_remaining: float,
    net_issued_bars: int,
    bar_length_mm: float
):
    """
    Отправить уведомление о недостатке материала (<=12 часов).
    Отправляется админам и наладчикам при включенных настройках.
    """
    message = (
        f"<b>⚠️ Материал заканчивается</b>\n\n"
        f"<b>Станок:</b> {machine_name}\n"
        f"<b>Чертёж:</b> {drawing_number}\n"
        f"<b>Партия:</b> {lot_number}\n"
        f"<b>Осталось:</b> ~{hours_remaining} ч\n"
        f"<b>Выдано (нетто):</b> {net_issued_bars} прутков\n"
        f"<b>Длина прутка:</b> {bar_length_mm} мм"
    )

    telegram_enabled = await is_notification_enabled(db, MATERIAL_LOW_NOTIFICATION_TYPE, 'telegram')
    summary = {"telegram_sent": 0, "whatsapp_sent": 0, "telegram_targets": 0}

    if telegram_enabled and await is_notification_enabled(db, MATERIAL_LOW_NOTIFICATION_TYPE, 'machinists'):
        machinist_counts = await _notify_role_by_id_sqlalchemy(
            db, MACHINIST_ROLE_ID, message,
            notification_type=MATERIAL_LOW_NOTIFICATION_TYPE
        )
        summary["telegram_targets"] += machinist_counts["telegram_targets"]
        summary["telegram_sent"] += machinist_counts["telegram_sent"]
        summary["whatsapp_sent"] += machinist_counts["whatsapp_sent"]

    if telegram_enabled and await is_notification_enabled(db, MATERIAL_LOW_NOTIFICATION_TYPE, 'admin'):
        admin_counts = await _notify_role_by_id_sqlalchemy(
            db, ADMIN_ROLE_ID, message,
            notification_type=MATERIAL_LOW_NOTIFICATION_TYPE
        )
        summary["telegram_targets"] += admin_counts["telegram_targets"]
        summary["telegram_sent"] += admin_counts["telegram_sent"]
        summary["whatsapp_sent"] += admin_counts["whatsapp_sent"]

    logger.info(f"Material low notification sent for lot_material={lot_material.id}: {summary}")
    return summary


async def check_low_materials_and_notify():
    """
    Периодическая проверка: если материала осталось <=12 часов — отправляем уведомления.
    Пропускаем если нет длины прутка или нет времени цикла.
    """
    own_db = SessionLocal()
    try:
        items = (
            own_db.query(LotMaterialDB, LotDB, MachineDB, PartDB)
            .join(LotDB, LotMaterialDB.lot_id == LotDB.id)
            .join(MachineDB, LotMaterialDB.machine_id == MachineDB.id)
            .outerjoin(PartDB, LotDB.part_id == PartDB.id)
            .filter(LotMaterialDB.closed_at == None)
            .filter(LotMaterialDB.bar_length_mm != None)
            .all()
        )

        for lot_material, lot, machine, part in items:
            net_issued = (lot_material.issued_bars or 0) - (lot_material.returned_bars or 0) - (lot_material.defect_bars or 0)
            if net_issued <= 0:
                continue

            if lot_material.material_low_notified_at and lot_material.material_low_notified_at > datetime.now(timezone.utc) - timedelta(hours=12):
                continue

            # cycle_time: active setup -> part avg
            cycle_time = None
            setup = own_db.query(SetupDB.cycle_time).filter(
                SetupDB.lot_id == lot.id,
                SetupDB.machine_id == machine.id,
                SetupDB.end_time == None
            ).order_by(SetupDB.id.desc()).first()
            if setup and setup[0]:
                cycle_time = setup[0]
            elif part and part.avg_cycle_time:
                cycle_time = part.avg_cycle_time
            if not cycle_time or not part or not part.part_length:
                continue

            bar_length_mm = lot_material.bar_length_mm
            blade_width_mm = lot_material.blade_width_mm or machine.material_blade_width_mm or DEFAULT_BLADE_WIDTH_MM
            facing_allowance_mm = lot_material.facing_allowance_mm or machine.material_facing_allowance_mm or DEFAULT_FACING_ALLOWANCE_MM
            min_remainder_mm = lot_material.min_remainder_mm or machine.material_min_remainder_mm or DEFAULT_MIN_REMAINDER_MM

            usable_length = bar_length_mm - min_remainder_mm
            length_per_part = part.part_length + facing_allowance_mm + blade_width_mm
            if usable_length <= 0 or length_per_part <= 0:
                continue
            parts_per_bar = math.floor(usable_length / length_per_part)
            if parts_per_bar <= 0:
                continue
            remaining_parts_by_material = net_issued * parts_per_bar
            hours = round((remaining_parts_by_material * cycle_time) / 3600.0, 2)

            if hours <= 12:
                await send_material_low_notification(
                    own_db,
                    lot_material=lot_material,
                    machine_name=machine.name,
                    lot_number=lot.lot_number,
                    drawing_number=part.drawing_number if part else "—",
                    hours_remaining=hours,
                    net_issued_bars=net_issued,
                    bar_length_mm=bar_length_mm
                )
                lot_material.material_low_notified_at = datetime.now(timezone.utc)
                own_db.commit()

    except Exception as e:
        logger.error(f"Material low check failed: {e}", exc_info=True)
    finally:
        own_db.close()

async def send_setup_approval_notifications(db: Session, setup_id: int, notification_type: str = "approval"):
    """
    Отправляет уведомления о наладке разным ролям, используя SQLAlchemy.
    
    ВАЖНО: Эта функция часто вызывается через asyncio.create_task() как фоновая задача.
    Переданная сессия db может быть уже закрыта к моменту выполнения.
    Поэтому функция создаёт СВОЮ сессию БД для надёжности.
    
    Args:
        db: Сессия базы данных (игнорируется, используется своя)
        setup_id: ID наладки
        notification_type: Тип уведомления ("approval" или "completion")
    """
    # Создаём свою сессию, т.к. переданная может быть закрыта (asyncio.create_task)
    own_db = SessionLocal()
    try:
        logger.info(f"Fetching data for {notification_type} notification (Setup ID: {setup_id}) using own DB session")
        summary = {
            "telegram_sent": 0,
            "whatsapp_sent": 0,
            "telegram_targets": 0
        }

        Machinist = aliased(EmployeeDB)
        QAEmployee = aliased(EmployeeDB)

        setup = own_db.query(
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
            return summary
        
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
            summary["telegram_targets"] += 1
            if await send_telegram_message(machinist_obj.telegram_id, machinist_message):
                summary["telegram_sent"] += 1

        # Используем существующие типы из БД
        notif_type = "setup_allowed" if notification_type == "approval" else "setup_completed"
        
        admin_counts = await _notify_role_by_id_sqlalchemy(
            own_db, ADMIN_ROLE_ID, admin_message, 
            exclude_id=machinist_obj.id if machinist_obj else None,
            notification_type=notif_type
        )
        operator_counts = await _notify_role_by_id_sqlalchemy(
            own_db, OPERATOR_ROLE_ID, operator_message, 
            exclude_id=machinist_obj.id if machinist_obj else None,
            notification_type=notif_type
        )
        summary["telegram_targets"] += admin_counts["telegram_targets"] + operator_counts["telegram_targets"]
        summary["telegram_sent"] += admin_counts["telegram_sent"] + operator_counts["telegram_sent"]
        summary["whatsapp_sent"] += admin_counts["whatsapp_sent"] + operator_counts["whatsapp_sent"]

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
            machinist_counts = await _notify_role_by_id_sqlalchemy(
                own_db, 
                MACHINIST_ROLE_ID, 
                free_machine_message, 
                exclude_id=machinist_obj.id if machinist_obj else None,
                notification_type="machine_free"
            )
            # Операторам тоже
            operator_free_counts = await _notify_role_by_id_sqlalchemy(
                own_db, 
                OPERATOR_ROLE_ID, 
                free_machine_message, 
                notification_type="machine_free"
            )
            # Viewer - личные (TG + WhatsApp)
            viewer_free_counts = await _notify_viewer_personal(own_db, free_machine_message, notification_type="machine_free")
            summary["telegram_targets"] += (
                machinist_counts["telegram_targets"]
                + operator_free_counts["telegram_targets"]
                + viewer_free_counts["telegram_targets"]
            )
            summary["telegram_sent"] += (
                machinist_counts["telegram_sent"]
                + operator_free_counts["telegram_sent"]
                + viewer_free_counts["telegram_sent"]
            )
            summary["whatsapp_sent"] += (
                machinist_counts["whatsapp_sent"]
                + operator_free_counts["whatsapp_sent"]
                + viewer_free_counts["whatsapp_sent"]
            )
            logger.info(f"Sent 'machine free' notification to machinists + operators + viewers for machine {machine_name}")

        # 🔔 Уведомляем Viewer'ов (личные TG + личные WhatsApp)
        viewer_counts = await _notify_viewer_personal(own_db, base_message, notification_type=notif_type)
        summary["telegram_targets"] += viewer_counts["telegram_targets"]
        summary["telegram_sent"] += viewer_counts["telegram_sent"]
        summary["whatsapp_sent"] += viewer_counts["whatsapp_sent"]
        
        logger.info(f"Successfully processed {notification_type} notifications for setup {setup_id}: {summary}")
        return summary

    except Exception as e:
        logger.error(f"Error sending {notification_type} notifications for setup {setup_id}: {e}", exc_info=True)
        return {
            "telegram_sent": 0,
            "whatsapp_sent": 0,
            "telegram_targets": 0
        }
    finally:
        own_db.close()
        logger.debug(f"Closed own DB session for {notification_type} notification")

async def _notify_role_by_id_sqlalchemy(
    db: Session, 
    role_id: int, 
    message: str, 
    exclude_id: int = None,
    notification_type: str = None
):
    """Отправляет сообщение всем сотрудникам с указанным role_id SQLAlchemy, кроме exclude_id."""
    try:
        query = db.query(EmployeeDB)\
            .filter(EmployeeDB.role_id == role_id) \
            .filter(EmployeeDB.is_active == True)\
            .filter(EmployeeDB.telegram_id != None)
        
        if exclude_id is not None:
            query = query.filter(EmployeeDB.id != exclude_id)
            
        employees = query.all()
        tg_sent = 0
        wa_sent = 0

        logger.debug(f"Found {len(employees)} active employees with role_id '{role_id}' and Telegram ID to notify.")

        for emp in employees:
            if emp.telegram_id:
                 logger.debug(f"Sending notification to role_id {role_id}: {emp.full_name} (ID: {emp.id}, TG_ID: {emp.telegram_id})")
                 if await send_telegram_message(emp.telegram_id, message):
                     tg_sent += 1

        # 🔔 Дублируем в WhatsApp группу (с переводом если настроено)
        if WHATSAPP_ENABLED:
            try:
                wa_sent = await send_whatsapp_to_role(
                    db, role_id, message, exclude_id, 
                    notification_type=notification_type
                )
                logger.info(f"WhatsApp sent to {wa_sent} group(s) for role_id {role_id}")
            except Exception as wa_err:
                logger.warning(f"WhatsApp send failed (non-critical): {wa_err}")

        return {
            "telegram_sent": tg_sent,
            "whatsapp_sent": wa_sent,
            "telegram_targets": len(employees)
        }

    except Exception as e:
        logger.error(f"Failed to notify role_id '{role_id}': {e}", exc_info=True)
        return {
            "telegram_sent": 0,
            "whatsapp_sent": 0,
            "telegram_targets": 0
        }


async def _notify_viewer_personal(
    db: Session,
    message: str,
    notification_type: str = None
):
    """
    Уведомляет Viewer'ов (role_id=7) через личные сообщения:
    - Telegram личка
    - WhatsApp личка (не группа!)
    """
    try:
        logger.info(f"=== Notifying Viewers for '{notification_type}' ===")
        
        # Telegram личка
        employees = db.query(EmployeeDB).filter(
            EmployeeDB.role_id == VIEWER_ROLE_ID,
            EmployeeDB.is_active == True,
            EmployeeDB.telegram_id != None
        ).all()
        
        tg_sent = 0
        for emp in employees:
            if emp.telegram_id:
                try:
                    if await send_telegram_message(emp.telegram_id, message):
                        tg_sent += 1
                except Exception as tg_err:
                    logger.warning(f"Failed to send TG to viewer {emp.full_name}: {tg_err}")
        
        logger.info(f"Telegram sent to {tg_sent}/{len(employees)} viewers")
        
        # WhatsApp личка (с переводом)
        wa_sent = 0
        if WHATSAPP_ENABLED:
            wa_sent = await send_whatsapp_to_role_personal(
                db, VIEWER_ROLE_ID, message,
                notification_type=notification_type
            )
            logger.info(f"WhatsApp personal sent to {wa_sent} viewers for '{notification_type}'")
        else:
            logger.debug("WhatsApp disabled, skipping viewer personal messages")

        return {
            "telegram_sent": tg_sent,
            "whatsapp_sent": wa_sent,
            "telegram_targets": len(employees)
        }
            
    except Exception as e:
        logger.error(f"Failed to notify viewers: {e}", exc_info=True)
        return {
            "telegram_sent": 0,
            "whatsapp_sent": 0,
            "telegram_targets": 0
        }


async def _notify_whatsapp_only(
    db: Session, 
    role_id: int, 
    message: str,
    notification_type: str = None
):
    """
    Отправляет сообщение только в WhatsApp группу роли (без личных Telegram).
    Используется для Viewer и других ролей где нужен только групповой канал.
    """
    if not WHATSAPP_ENABLED:
        return
    
    try:
        wa_sent = await send_whatsapp_to_role(
            db, role_id, message, 
            exclude_id=None,
            notification_type=notification_type
        )
        logger.info(f"WhatsApp-only sent to {wa_sent} group(s) for role_id {role_id}")
    except Exception as wa_err:
        logger.warning(f"WhatsApp-only send failed (non-critical): {wa_err}")


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

        await _notify_role_by_id_sqlalchemy(
            db, ADMIN_ROLE_ID, message,
            notification_type="defect_detected"
        )
        logger.info(f"Successfully processed discrepancy alert for Batch ID: {discrepancy_details.get('batch_id')}")
        return True

    except Exception as e:
        logger.error(f"Error sending discrepancy alert for Batch ID {discrepancy_details.get('batch_id')}: {e}", exc_info=True)
        return False 