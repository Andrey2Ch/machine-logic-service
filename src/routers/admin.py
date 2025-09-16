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
    Создание наладки по предсозданному лоту (status='new').
    Используется дашбордом (аналог save_setup_job в боте для варианта с предсозданным лотом).
    """
    try:
        lot = (
            db.query(LotDB)
              .join(PartDB, PartDB.id == LotDB.part_id)
              .filter(LotDB.lot_number == payload.lot_number)
              .filter(PartDB.drawing_number == payload.drawing_number)
              .filter(LotDB.status == 'new')
              .first()
        )
        if not lot:
            raise HTTPException(status_code=404, detail="Лот не найден или не 'new'")

        setup = SetupDB(
            employee_id=payload.employee_id,
            machine_id=payload.machine_id,
            lot_id=lot.id,
            planned_quantity=payload.planned_quantity,
            status='created'
        )
        db.add(setup)

        # Перевод лота в производство
        if lot.status == 'new':
            lot.status = 'in_production'

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
        db.commit()
        return {"success": True, "status": setup.status}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка при отправке в ОТК: {e}")