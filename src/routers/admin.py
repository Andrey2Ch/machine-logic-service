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
from ..models.models import MachineDB, CardDB, SetupDB, LotDB, PartDB
from ..services.mtconnect_client import reset_counter_on_qa_approval

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

        setup = SetupDB(
            employee_id=payload.employee_id,
            machine_id=payload.machine_id,
            lot_id=lot.id,
            part_id=lot.part_id,
            planned_quantity=payload.planned_quantity,
            status='created'
        )
        db.add(setup)
        db.flush()  # Flush to get setup.id

        # ОБНОВЛЕНИЕ ПРИВЯЗОК ЛОТА: независимо от предыдущего assigned, привязываем к станку, где создана наладка
        # Находим максимальный assigned_order для этого станка
        max_order = db.query(func.max(LotDB.assigned_order)).filter(
            LotDB.assigned_machine_id == payload.machine_id,
            LotDB.id != lot.id  # Исключаем текущий лот
        ).scalar() or 0
        
        # Обновляем привязки лота к станку, где создана наладка
        lot.assigned_machine_id = payload.machine_id
        lot.assigned_order = max_order + 1

        # Перевод лота в производство + фиксация момента создания наладки
        if lot.status == 'new':
            lot.status = 'in_production'
        elif lot.status == 'assigned':
            # Если лот был assigned, переводим в in_production, так как наладка создана
            lot.status = 'in_production'
        
        # Устанавливаем start_time для статуса 'created' как момент регистрации наладки
        # (это нужно, чтобы витрина и бот видели чертеж сразу после регистрации)
        if setup.status == 'created' and getattr(setup, 'start_time', None) is None:
            setup.start_time = datetime.utcnow()

        db.commit()
        db.refresh(setup)
        return {"setup_id": setup.id, "status": setup.status}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка создания наладки: {e}")


@router.post("/setup/{setup_id}/send-to-qc", summary="Отправить наладку в ОТК (pending_qc)")
async def send_setup_to_qc(setup_id: int, db: Session = Depends(get_db_session)):
    try:
        setup = db.query(SetupDB).filter(SetupDB.id == setup_id).first()
        if not setup:
            raise HTTPException(status_code=404, detail="Наладка не найдена")
        if setup.status != 'created':
            raise HTTPException(status_code=400, detail=f"Ожидался статус 'created', текущий: '{setup.status}'")

        setup.status = 'pending_qc'
        # фиксируем момент передачи в ОТК
        setup.pending_qc_date = datetime.now()
        db.commit()
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