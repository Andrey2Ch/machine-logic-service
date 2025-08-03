from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging

logger = logging.getLogger(__name__)

def get_active_lot_ids(db: Session, for_qc: bool = False) -> List[int]:
    """
    Возвращает список ID "активных" лотов.

    "Активный лот" — это лот, который еще не закрыт (статус не 'completed' или 'cancelled')
    и содержит либо незавершенные производственные наладки, либо партии, требующие внимания.

    :param db: Сессия SQLAlchemy.
    :param for_qc: Если True, логика будет строже и будет отфильтровывать лоты,
                   где все партии уже прошли контроль, но сам лот еще не закрыт.
                   Это специфично для страницы ОТК.
                   Если False, возвращает все в принципе незавершенные лоты.
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
        query = text(query_str)
        
        try:
            result = db.execute(query).fetchall()
            lot_ids = [row[0] for row in result]
            logger.info(f"Найдено {len(lot_ids)} лотов (включая завершенные). IDs: {lot_ids}")
            return lot_ids
        except Exception as e:
            logger.error(f"Ошибка при получении лотов: {e}", exc_info=True)
            return []

    # Для for_qc=True (строгая фильтрация для ОТК)
    # Базовые условия для активности:
    # 1. Лот не должен быть отменен (completed лоты теперь могут показываться)
    base_lot_filter = "status != 'cancelled'"

    # 2. У лота есть активные наладки
    active_setups_subquery = """
        SELECT DISTINCT lot_id FROM setup_jobs WHERE status IN ('created', 'pending_qc', 'allowed', 'started')
    """

    # 3. У лота есть партии, которые не в архиве
    # Для ОТК (for_qc=True) мы строже: ищем партии, которые не в финальных состояниях проверки.
    active_batches_condition = "current_location NOT IN ('good', 'defect', 'rework_repair', 'archived')"
    
    active_batches_subquery = f"""
        SELECT DISTINCT lot_id FROM batches WHERE {active_batches_condition}
    """
    
    # Объединяем условия:
    # Лот считается активным, если он соответствует базовому фильтру И (имеет активные наладки ИЛИ имеет активные партии)
    query_str = f"""
        SELECT id
        FROM lots
        WHERE
            {base_lot_filter}
            AND (
                id IN ({active_setups_subquery})
                OR
                id IN ({active_batches_subquery})
            )
    """

    query = text(query_str)
    
    try:
        result = db.execute(query).fetchall()
        lot_ids = [row[0] for row in result]
        logger.info(f"Найдено {len(lot_ids)} активных лотов. IDs: {lot_ids}")
        return lot_ids
    except Exception as e:
        logger.error(f"Ошибка при получении активных лотов: {e}", exc_info=True)
        return [] 