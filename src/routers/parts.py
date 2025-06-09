from fastapi import APIRouter, HTTPException, Query, Response, Depends
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
import logging

from src.database import get_db_session
from src.models.models import PartDB
from src.schemas.part import PartCreate, PartResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/parts", tags=["Parts"])

@router.post("/", response_model=PartResponse, status_code=201)
async def create_part(part_in: PartCreate, db: Session = Depends(get_db_session)):
    """
    Создать новую деталь.
    - **drawing_number**: Номер чертежа (уникальный)
    - **material**: Материал (опционально)
    """
    logger.info(f"Запрос на создание детали: {part_in.model_dump()}")
    existing_part = db.query(PartDB).filter(PartDB.drawing_number == part_in.drawing_number).first()
    if existing_part:
        logger.warning(f"Деталь с номером чертежа {part_in.drawing_number} уже существует (ID: {existing_part.id})")
        raise HTTPException(status_code=409, detail=f"Деталь с номером чертежа '{part_in.drawing_number}' уже существует.")
    
    new_part = PartDB(
        drawing_number=part_in.drawing_number,
        material=part_in.material
        # created_at будет установлен по умолчанию
    )
    db.add(new_part)
    try:
        db.commit()
        db.refresh(new_part)
        logger.info(f"Деталь '{new_part.drawing_number}' успешно создана с ID {new_part.id}")
        return new_part
    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка при сохранении детали {part_in.drawing_number}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера при сохранении детали: {str(e)}")

@router.get("/", response_model=List[PartResponse])
async def get_parts(
    response: Response, 
    search: Optional[str] = Query(None, description="Поисковый запрос для номера чертежа или материала"),
    skip: int = Query(0, ge=0, description="Количество записей для пропуска (пагинация)"),
    limit: int = Query(100, ge=1, le=500, description="Максимальное количество записей для возврата (пагинация)"),
    db: Session = Depends(get_db_session)
):
    """
    Получить список всех деталей.
    Поддерживает поиск по `drawing_number` и `material` (частичное совпадение без учета регистра).
    Поддерживает пагинацию через `skip` и `limit`.
    """
    try:
        query = db.query(PartDB)
        
        if search:
            search_term = f"%{search.lower()}%"
            query = query.filter(
                (func.lower(PartDB.drawing_number).like(search_term)) |
                (func.lower(func.coalesce(PartDB.material, '')).like(search_term))
            )

        total_count = query.count()

        parts = query.order_by(PartDB.drawing_number).offset(skip).limit(limit).all()
        logger.info(f"Запрос списка деталей: search='{search}', skip={skip}, limit={limit}. Возвращено {len(parts)} из {total_count} деталей.")
        
        response.headers["X-Total-Count"] = str(total_count)
            
        return parts
    except Exception as e:
        logger.error(f"Ошибка при получении списка деталей (search='{search}', skip={skip}, limit={limit}): {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера при получении списка деталей: {str(e)}") 