from fastapi import APIRouter, Depends, HTTPException
from typing import List
from sqlalchemy.orm import Session
from datetime import datetime
import logging

from src.database import get_db_session
from src.models.models import ReadingDB, MachineDB, EmployeeDB
from src.schemas.reading import ReadingInput, ReadingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/readings", tags=["Readings"])

@router.post("/", response_model=dict)
async def save_reading(reading_input: ReadingInput, db: Session = Depends(get_db_session)):
    """
    Сохранить показание станка
    """
    logger.info(f"--- Запрос POST /readings получен ---")
    logger.info(f"Данные: machine_id={reading_input.machine_id}, operator_id={reading_input.operator_id}, value={reading_input.value}")
    
    # Начинаем транзакцию
    trans = db.begin()
    try:
        # Создаем новое показание
        new_reading = ReadingDB(
            machine_id=reading_input.machine_id,
            employee_id=reading_input.operator_id,
            reading=reading_input.value,
            created_at=datetime.now()
        )
        
        db.add(new_reading)
        trans.commit()
        db.refresh(new_reading)
        
        logger.info(f"Показание ID {new_reading.id} успешно сохранено")
        return {
            "success": True,
            "reading_id": new_reading.id,
            "message": "Показание сохранено успешно"
        }
        
    except Exception as e:
        trans.rollback()
        logger.error(f"Error in save_reading: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while saving reading")

@router.get("/", response_model=dict)
async def get_readings(db: Session = Depends(get_db_session)):
    """
    Получить последние показания
    """
    logger.info("--- Запрос GET /readings получен ---")
    try:
        logger.info("Выполняется запрос к ReadingDB...")
        readings = db.query(ReadingDB).order_by(ReadingDB.created_at.desc()).limit(100).all()
        logger.info(f"Запрос к ReadingDB выполнен, получено {len(readings)} записей.")
        
        # Формируем ответ
        response_data = {
            "readings": [
                {
                    "id": r.id,
                    "machine_id": r.machine_id,
                    "employee_id": r.employee_id,
                    "reading": r.reading,
                    # Преобразуем дату в строку ISO, чтобы избежать проблем сериализации
                    "created_at": r.created_at.isoformat() if r.created_at else None 
                } for r in readings
            ]
        }
        logger.info("--- Ответ для GET /readings сформирован успешно --- ")
        return response_data # Возвращаем словарь, FastAPI сам сделает JSON
        
    except Exception as e:
        logger.error(f"!!! Ошибка в GET /readings: {e}", exc_info=True) # Логируем ошибку
        # Поднимаем HTTPException, чтобы FastAPI вернул корректный JSON ошибки 500
        raise HTTPException(status_code=500, detail="Internal server error processing readings")

@router.get("/{machine_id}", response_model=dict)
async def get_machine_readings(machine_id: int, db: Session = Depends(get_db_session)):
    """
    Получить показания для конкретного станка
    """
    readings = db.query(ReadingDB).filter(
        ReadingDB.machine_id == machine_id
    ).order_by(ReadingDB.created_at.desc()).limit(100).all()
    
    return {
        "machine_id": machine_id,
        "readings": [
            {
                "id": r.id,
                "employee_id": r.employee_id,
                "reading": r.reading,
                "created_at": r.created_at
            } for r in readings
        ]
    } 