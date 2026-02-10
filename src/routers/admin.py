"""
Роутер для административных утилит и экстренных операций.
"""
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

# Относительные импорты для доступа к моделям и сессии БД
from ..database import get_db_session
from ..models.models import MachineDB, CardDB, SetupDB, LotDB, PartDB, EmployeeDB, BatchDB
from ..services.mtconnect_client import reset_counter_on_qa_approval
from ..services.telegram_client import send_telegram_message
from ..services.whatsapp_client import send_whatsapp_to_all_enabled_roles, WHATSAPP_ENABLED
from ..services.setup_program_handover import check_setup_program_handover_gate, ensure_setup_program_handover_row

# Настройка логгера
logger = logging.getLogger(__name__)

# Создание экземпляра роутера
router = APIRouter(
    prefix="/admin",
    tags=["Admin Tools"]
)

class ResetCardsPayload(BaseModel):
    machine_name: str

@router.post("/reset-cards-for-machine", summary="Экстренный сброс карточек станка")
async def reset_cards_for_machine(payload: ResetCardsPayload, db: Session = Depends(get_db_session)):
    """
    ЭКСТРЕННЫЙ ИНСТРУМЕНТ: Сбрасывает все 'in_use' карточки для указанного станка в статус 'free'.
    Использовать для исправления застрявших карточек после старой ошибки.
    """
    machine_name = payload.machine_name
    logger.warning(f"Starting emergency reset for machine '{machine_name}'...")

    try:
        machine = db.query(MachineDB).filter(func.lower(MachineDB.name) == func.lower(machine_name)).first()
        if not machine:
            raise HTTPException(status_code=404, detail=f"Станок с именем '{machine_name}' не найден.")

        cards_to_reset = db.query(CardDB).filter(
            CardDB.machine_id == machine.id,
            CardDB.status == 'in_use'
        ).all()

        if not cards_to_reset:
            return {"message": f"Для станка '{machine_name}' нет карточек в статусе 'in_use'. Ничего не сделано."}

        count = 0
        for card in cards_to_reset:
            card.status = 'free'
            card.batch_id = None
            card.last_event = datetime.now()
            count += 1
        
        db.commit()
        
        message = f"Успешно сброшено {count} карточек для станка '{machine_name}'."
        logger.warning(message)
        return {"message": message}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error during emergency reset for machine {machine_name}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера при сбросе карточек для станка {machine_name}") 


class CreateSetupPayload(BaseModel):
    employee_id: int
    machine_id: int
    drawing_number: str
    lot_number: str
    planned_quantity: float


class TransferSetupPayload(BaseModel):
    target_machine_id: int


@router.post("/setup/create", summary="Создать наладку по предсозданному лоту")
async def create_setup(payload: CreateSetupPayload, db: Session = Depends(get_db_session)):
    """
    Создание наладки по предсозданному лоту.
    ВАЖНО: Независимо от того, был ли лот assigned на другой станок, наладка создается на указанном станке,
    и все привязки лота обновляются на этот станок.
    Используется дашбордом (аналог save_setup_job в боте для варианта с предсозданным лотом).
    """
    try:
        lot = (
            db.query(LotDB)
              .join(PartDB, PartDB.id == LotDB.part_id)
              .filter(LotDB.lot_number == payload.lot_number)
              .filter(PartDB.drawing_number == payload.drawing_number)
              .first()
        )
        if not lot:
            raise HTTPException(status_code=404, detail="Лот не найден")

        # Проверяем есть ли активная наладка на станке
        active_setup = db.query(SetupDB).filter(
            SetupDB.machine_id == payload.machine_id,
            SetupDB.status.in_(['created', 'started', 'pending_qc', 'allowed']),
            SetupDB.end_time == None
        ).first()
        
        # Если есть активная наладка — новая идёт в очередь
        initial_status = 'queued' if active_setup else 'created'

        setup = SetupDB(
            employee_id=payload.employee_id,
            machine_id=payload.machine_id,
            lot_id=lot.id,
            part_id=lot.part_id,
            planned_quantity=payload.planned_quantity,
            status=initial_status
        )
        db.add(setup)
        db.flush()  # Flush to get setup.id

        # Создаём/фиксируем запись гейта (idempotent, safe)
        try:
            ensure_setup_program_handover_row(db, next_setup_id=setup.id, machine_id=payload.machine_id)
        except Exception as e:
            # fail-open: не ломаем создание наладки
            logger.warning("setup_program_handover init failed (non-critical): %s", e)

        # ОБНОВЛЕНИЕ ПРИВЯЗОК ЛОТА: независимо от предыдущего assigned, привязываем к станку, где создана наладка
        # Находим максимальный assigned_order для этого станка
        max_order = db.query(func.max(LotDB.assigned_order)).filter(
            LotDB.assigned_machine_id == payload.machine_id,
            LotDB.id != lot.id  # Исключаем текущий лот
        ).scalar() or 0
        
        # Обновляем привязки лота к станку, где создана наладка
        lot.assigned_machine_id = payload.machine_id
        lot.assigned_order = max_order + 1

        # Перевод лота в производство ТОЛЬКО если наладка не в очереди
        # queued = станок занят другой наладкой → лот остается assigned
        if initial_status == 'created':
            if lot.status in ('new', 'assigned'):
                lot.status = 'in_production'
        # Если initial_status == 'queued', лот остается assigned
        
        # УБРАНО: start_time НЕ должен устанавливаться при создании наладки!
        # start_time = начало РАБОТЫ (когда станок получает показания и статус -> 'started')
        # Для отображения чертежа на витрине нужно использовать created_at, а не start_time

        db.commit()
        db.refresh(setup)
        return {"setup_id": setup.id, "status": setup.status}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка создания наладки: {e}")


@router.post("/setup/{setup_id}/transfer", summary="Перенести активную наладку на другой станок (админ)")
async def transfer_setup(setup_id: int, payload: TransferSetupPayload, db: Session = Depends(get_db_session)):
    """
    Перенос (transfer) активной наладки:
    - завершает текущую наладку (source) на исходном станке,
    - создаёт новую наладку (target) на выбранном станке с остатком по плану.

    ВАЖНО:
    - перенос работает только для активных статусов (created/started/allowed),
      чтобы не обходить процесс ОТК (pending_qc).
    - перенос НЕ требует миграций: используется существующая модель SetupDB.
    """
    try:
        # Блокируем строку наладки на время переноса (минимизируем гонки)
        setup = (
            db.query(SetupDB)
              .filter(SetupDB.id == setup_id)
              .with_for_update()
              .first()
        )
        if not setup:
            raise HTTPException(status_code=404, detail="Наладка не найдена")
        if setup.end_time is not None:
            raise HTTPException(status_code=400, detail="Наладка уже завершена")

        allowed_statuses = ("created", "started", "allowed")
        if setup.status not in allowed_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Перенос доступен только для статусов {allowed_statuses}. Текущий: '{setup.status}'",
            )

        if payload.target_machine_id == setup.machine_id:
            raise HTTPException(status_code=400, detail="Станок назначения совпадает с исходным")

        target_machine = db.query(MachineDB).filter(MachineDB.id == payload.target_machine_id).first()
        if not target_machine:
            raise HTTPException(status_code=404, detail="Станок назначения не найден")

        produced = (
            db.query(func.coalesce(func.sum(BatchDB.current_quantity), 0))
              .filter(BatchDB.setup_job_id == setup.id)
              .scalar()
        ) or 0

        total_planned = int(setup.planned_quantity or 0) + int(setup.additional_quantity or 0)
        remaining = max(total_planned - int(produced), 0)
        if remaining <= 0:
            raise HTTPException(status_code=400, detail="Остаток = 0. Нечего переносить.")

        # Завершаем исходную наладку
        setup.status = "completed"
        setup.end_time = datetime.now()

        # Проверяем занятость станка назначения → queued/created
        active_on_target = db.query(SetupDB).filter(
            SetupDB.machine_id == payload.target_machine_id,
            SetupDB.status.in_(["created", "started", "pending_qc", "allowed"]),
            SetupDB.end_time == None
        ).first()
        target_status = "queued" if active_on_target else "created"

        target_setup = SetupDB(
            employee_id=setup.employee_id,
            machine_id=payload.target_machine_id,
            lot_id=setup.lot_id,
            part_id=setup.part_id,
            planned_quantity=remaining,
            additional_quantity=0,
            status=target_status,
            cycle_time=setup.cycle_time,
        )
        db.add(target_setup)
        db.flush()

        # Создаём/фиксируем запись гейта для новой наладки (idempotent, safe)
        try:
            ensure_setup_program_handover_row(db, next_setup_id=target_setup.id, machine_id=payload.target_machine_id)
        except Exception as e:
            logger.warning("setup_program_handover init failed (non-critical): %s", e)

        # Обновляем привязки лота к станку назначения (как в create_setup)
        lot = db.query(LotDB).filter(LotDB.id == setup.lot_id).first()
        if lot:
            max_order = db.query(func.max(LotDB.assigned_order)).filter(
                LotDB.assigned_machine_id == payload.target_machine_id,
                LotDB.id != lot.id
            ).scalar() or 0
            lot.assigned_machine_id = payload.target_machine_id
            lot.assigned_order = max_order + 1

            # Перевод лота в производство только если наладка не queued
            if target_status == "created":
                if lot.status in ("new", "assigned"):
                    lot.status = "in_production"

        db.commit()
        db.refresh(target_setup)
        return {
            "source_setup_id": setup_id,
            "target_setup_id": target_setup.id,
            "target_status": target_setup.status,
            "remaining": remaining,
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка переноса наладки: {e}")


@router.post("/setup/{setup_id}/send-to-qc", summary="Отправить наладку в ОТК (pending_qc)")
async def send_setup_to_qc(setup_id: int, db: Session = Depends(get_db_session)):
    try:
        setup = db.query(SetupDB).filter(SetupDB.id == setup_id).first()
        if not setup:
            raise HTTPException(status_code=404, detail="Наладка не найдена")
        if setup.status != 'created':
            raise HTTPException(status_code=400, detail=f"Ожидался статус 'created', текущий: '{setup.status}'")

        # === Gate: программа предыдущей наладки должна быть зафиксирована (или skip) ===
        ok, row = check_setup_program_handover_gate(db, next_setup_id=setup.id, machine_id=setup.machine_id)
        if not ok:
            # 409 = конфликт состояния процесса
            raise HTTPException(
                status_code=409,
                detail=(
                    "Перед отправкой в ОТК нужно зафиксировать программу предыдущей наладки "
                    "в базе (в дашборде) или пропустить с причиной. "
                    f"(handover_status={row.get('status')}, prev_setup_id={row.get('prev_setup_id')})"
                ),
            )

        setup.status = 'pending_qc'
        # фиксируем момент передачи в ОТК
        setup.pending_qc_date = datetime.now()
        db.commit()
        
        # 🔔 Отправляем уведомления
        try:
            # Получаем данные для уведомления
            machine = db.query(MachineDB).filter(MachineDB.id == setup.machine_id).first()
            lot = db.query(LotDB).filter(LotDB.id == setup.lot_id).first()
            part = db.query(PartDB).filter(PartDB.id == setup.part_id).first()
            machinist = db.query(EmployeeDB).filter(EmployeeDB.id == setup.employee_id).first()
            
            machine_name = machine.name if machine else 'Неизвестно'
            lot_number = lot.lot_number if lot else 'Неизвестно'
            drawing_number = part.drawing_number if part else 'Неизвестно'
            machinist_name = machinist.full_name if machinist else 'Неизвестно'
            planned_qty = setup.planned_quantity or 0
            
            message = (
                f"<b>🔄 Наладка отправлена в ОТК</b>\n\n"
                f"<b>Станок:</b> {machine_name}\n"
                f"<b>Чертёж:</b> {drawing_number}\n"
                f"<b>Партия:</b> {lot_number}\n"
                f"<b>План:</b> {planned_qty}\n"
                f"<b>Наладчик:</b> {machinist_name}\n\n"
                f"<i>Ожидает проверки ОТК ✅</i>"
            )
            
            # Отправляем в Telegram (QA role_id=5, Admin role_id=3)
            qa_employees = db.query(EmployeeDB).filter(
                EmployeeDB.role_id == 5,
                EmployeeDB.is_active == True,
                EmployeeDB.telegram_id != None
            ).all()
            
            for emp in qa_employees:
                if emp.telegram_id:
                    await send_telegram_message(emp.telegram_id, message)
            
            admin_employees = db.query(EmployeeDB).filter(
                EmployeeDB.role_id == 3,
                EmployeeDB.is_active == True,
                EmployeeDB.telegram_id != None
            ).all()
            
            for emp in admin_employees:
                if emp.telegram_id:
                    await send_telegram_message(emp.telegram_id, message)
            
            # Отправляем в WhatsApp всем включённым ролям
            if WHATSAPP_ENABLED:
                await send_whatsapp_to_all_enabled_roles(db, message, "setup_pending_qc")
            
            logger.info(f"Sent pending_qc notifications for setup {setup_id}")
            
        except Exception as notif_err:
            logger.warning(f"Failed to send pending_qc notifications (non-critical): {notif_err}")
        
        return {"success": True, "status": setup.status}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка при отправке в ОТК: {e}")


class ApproveSetupPayload(BaseModel):
    qa_id: int


@router.post("/setup/{setup_id}/approve", summary="Разрешить наладку (allowed) и зафиксировать qa_date, qa_id")
async def approve_setup(setup_id: int, payload: ApproveSetupPayload, db: Session = Depends(get_db_session)):
    try:
        setup = db.query(SetupDB).filter(SetupDB.id == setup_id).first()
        if not setup:
            raise HTTPException(status_code=404, detail="Наладка не найдена")
        if setup.status not in ("pending_qc", "created"):
            raise HTTPException(status_code=400, detail=f"Ожидался статус 'pending_qc' или 'created', текущий: '{setup.status}'")
        # Гейт блокируем ТОЛЬКО на этапе send-to-qc.
        # Если наладка уже в pending_qc, блокировать approve — поздно и приводит к дедлокам.

        setup.status = 'allowed'
        setup.qa_id = payload.qa_id
        setup.qa_date = datetime.now()
        db.commit()

        # Получаем имя станка для сброса счётчика MTConnect
        machine = db.query(MachineDB.name).filter(MachineDB.id == setup.machine_id).scalar()
        
        # Сбрасываем счётчик MTConnect на 0 при разрешении ОТК
        if machine:
            try:
                await reset_counter_on_qa_approval(machine)
            except Exception as mtc_error:
                logger.warning(f"MTConnect counter reset failed (non-critical): {mtc_error}")

        # Отправляем уведомление через встроенный QC роутер (локально внутри сервиса)
        try:
            # Импортируем здесь, чтобы избежать циклических импортов на уровне модуля
            from .qc import notify_setup_allowed, NotifyRequest
            # Вызовем хендлер напрямую, передав сессию
            req = NotifyRequest(setup_id=setup.id)
            # notify_setup_allowed — async; вызовем и проигнорируем ошибки, чтобы не ломать approve
            await notify_setup_allowed(req, db)
        except Exception as notify_err:
            logger.warning(f"Approve ok, но уведомление не отправлено: {notify_err}")

        return {"success": True, "status": setup.status}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка при разрешении наладки: {e}")