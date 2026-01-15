"""
Dashboard Data Collector Service

Фоновый сервис для сбора данных дашборда.
Собирает данные один раз в N секунд и хранит в памяти.
Все клиенты получают одни и те же данные без нагрузки на БД.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from zoneinfo import ZoneInfo
import os

from sqlalchemy.orm import Session
from sqlalchemy import text

logger = logging.getLogger(__name__)

# ========== КОНФИГУРАЦИЯ ==========

COLLECT_INTERVAL_SEC = 5  # Как часто собирать данные
TIMEZONE_NAME = os.getenv("TIMEZONE") or os.getenv("BOT_TIMEZONE") or "Asia/Jerusalem"

try:
    TZ = ZoneInfo(TIMEZONE_NAME)
except Exception:
    TZ = ZoneInfo("UTC")


# ========== SHARED STATE ==========

class DashboardState:
    """Хранилище последних данных дашборда"""
    
    def __init__(self):
        self.data: Dict[str, Any] = {}
        self.last_update: float = 0
        self.is_collecting: bool = False
        self.error: Optional[str] = None
    
    def get_data(self) -> Dict[str, Any]:
        return self.data
    
    def set_data(self, data: Dict[str, Any]):
        import time
        self.data = data
        self.last_update = time.time()
        self.error = None
    
    def set_error(self, error: str):
        self.error = error


# Глобальный экземпляр состояния
_state = DashboardState()


def get_dashboard_state() -> DashboardState:
    """Получить текущее состояние дашборда"""
    return _state


# ========== SHIFT CALCULATION ==========

def get_current_shift_bounds() -> tuple[datetime, datetime]:
    """
    Вычисляет границы текущей смены.
    Дневная: 06:00-18:00, Ночная: 18:00-06:00
    """
    now = datetime.now(TZ)
    hour = now.hour
    
    if 6 <= hour < 18:
        # Дневная смена
        shift_start = now.replace(hour=6, minute=0, second=0, microsecond=0)
        shift_end = now.replace(hour=18, minute=0, second=0, microsecond=0)
    elif hour >= 18:
        # Ночная смена (начало сегодня)
        shift_start = now.replace(hour=18, minute=0, second=0, microsecond=0)
        shift_end = (now + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
    else:
        # Ночная смена (начало вчера)
        shift_start = (now - timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
        shift_end = now.replace(hour=6, minute=0, second=0, microsecond=0)
    
    return shift_start, shift_end


def get_previous_shift_bounds(current_start: datetime) -> tuple[datetime, datetime]:
    """Границы предыдущей смены"""
    if current_start.hour == 6:
        # Текущая дневная → предыдущая ночная
        prev_end = current_start
        prev_start = prev_end - timedelta(hours=12)
    else:
        # Текущая ночная → предыдущая дневная
        prev_end = current_start
        prev_start = prev_end - timedelta(hours=12)
    
    return prev_start, prev_end


# ========== DATA COLLECTION ==========

async def collect_machines_data(db: Session) -> List[Dict[str, Any]]:
    """Собирает данные о станках"""
    from src.models.models import MachineDB, AreaDB
    
    machines = db.query(MachineDB).filter(MachineDB.is_active == True).all()
    
    result = []
    for m in machines:
        area_name = None
        if m.location_id:
            area = db.query(AreaDB).filter(AreaDB.id == m.location_id).first()
            area_name = area.name if area else None
        
        result.append({
            "id": m.id,
            "name": m.name,
            "type": m.type or "CNC",
            "status": getattr(m, 'status', 'unknown'),
            "area_id": m.location_id,
            "area_name": area_name,
            "display_order": m.display_order,
            "is_active": m.is_active,
        })
    
    return result


async def collect_shift_setup_times(db: Session, shift_start: datetime, shift_end: datetime) -> Dict[str, float]:
    """
    Собирает время наладок за смену для ВСЕХ станков одним запросом.
    Возвращает dict: {machine_name: setup_seconds}
    """
    from src.models.models import SetupDB, MachineDB
    
    # Один запрос для всех станков
    query = text("""
        SELECT m.name as machine_name, 
               COALESCE(SUM(
                   EXTRACT(EPOCH FROM (
                       LEAST(s.first_part_at, :shift_end) - GREATEST(s.created_at, :shift_start)
                   ))
               ), 0) as setup_sec
        FROM setup_jobs s
        JOIN machines m ON s.machine_id = m.id
        WHERE s.created_at < :shift_end 
          AND (s.first_part_at IS NULL OR s.first_part_at > :shift_start)
          AND s.first_part_at IS NOT NULL
        GROUP BY m.name
    """)
    
    try:
        rows = db.execute(query, {
            "shift_start": shift_start,
            "shift_end": shift_end
        }).fetchall()
        
        return {row.machine_name: max(0, row.setup_sec) for row in rows}
    except Exception as e:
        logger.error(f"Error collecting setup times: {e}")
        return {}


async def collect_hourly_data(db: Session, shift_start: datetime, shift_end: datetime) -> Dict[str, List[Dict]]:
    """
    Собирает почасовые данные за смену для ВСЕХ станков.
    Возвращает dict: {machine_name: [{hour, setup_sec}, ...]}
    """
    # Упрощённая версия - можно расширить позже
    # Пока возвращаем пустой dict, данные добавим при необходимости
    return {}


async def collect_all_dashboard_data(db: Session) -> Dict[str, Any]:
    """
    Главная функция сбора ВСЕХ данных для дашборда.
    Вызывается один раз в N секунд.
    """
    import time
    start_time = time.time()
    
    shift_start, shift_end = get_current_shift_bounds()
    prev_start, prev_end = get_previous_shift_bounds(shift_start)
    
    # Собираем всё параллельно где возможно
    machines = await collect_machines_data(db)
    setup_times = await collect_shift_setup_times(db, shift_start, shift_end)
    prev_setup_times = await collect_shift_setup_times(db, prev_start, prev_end)
    
    elapsed = time.time() - start_time
    
    return {
        "timestamp": time.time(),
        "collected_at": datetime.now(TZ).isoformat(),
        "collection_time_ms": round(elapsed * 1000, 2),
        "shift": {
            "start": shift_start.isoformat(),
            "end": shift_end.isoformat(),
            "type": "day" if shift_start.hour == 6 else "night"
        },
        "machines": machines,
        "setup_times": setup_times,
        "prev_setup_times": prev_setup_times,
        # Можно добавить больше данных:
        # "utilization": {...},
        # "hourly": {...},
    }


# ========== BACKGROUND TASK ==========

async def dashboard_collector_task(get_db_func):
    """
    Фоновая задача сбора данных.
    Запускается при старте приложения.
    
    Args:
        get_db_func: Функция для получения DB сессии
    """
    state = get_dashboard_state()
    state.is_collecting = True
    
    logger.info(f"📊 Dashboard collector started (interval: {COLLECT_INTERVAL_SEC}s)")
    
    while state.is_collecting:
        try:
            # Получаем сессию БД
            db_gen = get_db_func()
            db = next(db_gen)
            
            try:
                # Собираем данные
                data = await collect_all_dashboard_data(db)
                state.set_data(data)
                
                machine_count = len(data.get("machines", []))
                logger.debug(f"📊 Dashboard data collected: {machine_count} machines, {data['collection_time_ms']}ms")
                
            finally:
                # Закрываем сессию
                try:
                    next(db_gen)
                except StopIteration:
                    pass
                    
        except Exception as e:
            logger.error(f"❌ Dashboard collector error: {e}", exc_info=True)
            state.set_error(str(e))
        
        await asyncio.sleep(COLLECT_INTERVAL_SEC)
    
    logger.info("📊 Dashboard collector stopped")


def stop_collector():
    """Остановить сборщик данных"""
    state = get_dashboard_state()
    state.is_collecting = False
