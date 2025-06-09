from fastapi import APIRouter, Depends, HTTPException
from typing import List
from sqlalchemy.orm import Session
import logging

from src.database import get_db_session
from src.models.models import EmployeeDB
from src.schemas.employee import EmployeeItem

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/employees", tags=["Employees"])

@router.get("/", response_model=List[EmployeeItem])
async def get_employees(db: Session = Depends(get_db_session)):
    """
    Получить список всех сотрудников.
    """
    try:
        employees = db.query(EmployeeDB).all()
        logger.info(f"Запрос списка сотрудников. Возвращено {len(employees)} сотрудников.")
        return employees
    except Exception as e:
        logger.error(f"Ошибка при получении списка сотрудников: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера при получении списка сотрудников: {str(e)}") 