from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging

logger = logging.getLogger(__name__)

def get_active_lot_ids(db: Session, for_qc: bool = False) -> List[int]:
    """
    Возвращает список ID "активных" лотов.

    :param db: Сессия SQLAlchemy.
    :param for_qc: Если True, исключаем лоты со статусом 'completed'.
                   Если False, возвращаем все лоты кроме отмененных.
    :return: Список ID лотов.
    """
    logger.info(f"Запрос активных лотов. Режим для ОТК: {for_qc}")

    # Если for_qc=False (пользователь хочет видеть завершенные), возвращаем все лоты кроме отмененных
    
    if not for_qc:
        query_str = """
            SELECT id
            FROM lots
            WHERE status != 'cancelled'
        """
    else:
        # Если for_qc=True (скрыть завершенные), исключаем completed лоты
        query_str = """
            SELECT id
            FROM lots
            WHERE status NOT IN ('cancelled', 'completed')
        """

    query = text(query_str)
    
    try:
        result = db.execute(query).fetchall()
        lot_ids = [row[0] for row in result]
        logger.info(f"Найдено {len(lot_ids)} лотов. IDs: {lot_ids}")
        return lot_ids
    except Exception as e:
        logger.error(f"Ошибка при получении лотов: {e}", exc_info=True)
        return [] 