/**
 * @file: batches.py
 * @description: Роутер для операций, связанных со складом (Warehouse) и батчами (Batches).
 * @dependencies: fastapi, sqlalchemy, src.database, src.models, src.schemas
 * @created: 2024-05-28
 */

import logging
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from src.database import get_db_session
from src.models.models import BatchDB
from src.schemas.batch import WarehousePendingBatchItem

logger = logging.getLogger(__name__)

# Создаем роутер для склада и батчей
batches_router = APIRouter(prefix="/warehouse", tags=["Warehouse"])


# Эндпоинты для склада
@batches_router.get("/batches-pending", response_model=List[WarehousePendingBatchItem])
async def get_warehouse_pending_batches(db: Session = Depends(get_db_session)):
    """
    Получить все батчи, ожидающие приемки на складе (статус 'pending_warehouse').
    """
    try:
        pending_batches = db.query(BatchDB)\
            .filter(BatchDB.current_location == 'pending_warehouse')\
            .order_by(BatchDB.id.desc())\
            .all()
        
        # Преобразуем объекты SQLAlchemy в Pydantic модели
        result = [WarehousePendingBatchItem.from_orm(batch) for batch in pending_batches]
        return result

    except Exception as e:
        logger.error(f"Ошибка при получении батчей для склада: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера при получении данных для склада")

# В будущем здесь могут быть другие эндпоинты, связанные с батчами, 
# например, POST /batches/{batch_id}/receive 