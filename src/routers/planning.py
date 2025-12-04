"""
Роутер для планирования и распределения работ по станкам
"""
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional, List
from pydantic import BaseModel
from src.database import get_db
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/planning", tags=["Planning"])


# ============ МОДЕЛИ ОТВЕТА ============

class MachineForecast(BaseModel):
    """Прогноз выполнения на станке"""
    can_make_by_deadline: int  # сколько успеем к сроку
    completion_rate: int       # процент выполнения к сроку
    days_for_full: float       # дней на полный заказ

class MachineRecommendation(BaseModel):
    """Рекомендация станка"""
    machine_id: int
    machine_name: str
    score: int                 # 0-100
    reasons: List[str]         # объяснения
    forecast: Optional[MachineForecast] = None
    current_diameter: Optional[float] = None  # текущий диаметр на станке
    queue_hours: float         # часов в очереди

class RecommendationsResponse(BaseModel):
    """Ответ с рекомендациями"""
    part_id: Optional[int]
    drawing_number: Optional[str]
    diameter: float
    quantity: int
    due_days: int
    recommendations: List[MachineRecommendation]


# ============ КОНСТАНТЫ ВЕСОВ ============

W_HISTORY = 30      # Бонус за историю (делали раньше)
W_SAME_DIAMETER = 25  # Бонус за тот же диаметр
W_FREE_QUEUE = 25    # Бонус за свободную очередь
W_CAPABILITIES = 20   # Бонус за специальные возможности (JBS, etc)


# ============ ENDPOINT ============

@router.get("/recommend-machines", response_model=RecommendationsResponse)
async def recommend_machines(
    diameter: float = Query(..., description="Диаметр материала (мм)"),
    quantity: int = Query(..., description="Количество деталей"),
    due_days: int = Query(..., description="Дней до срока поставки"),
    cycle_time_sec: Optional[int] = Query(None, description="Время цикла (сек), если известно"),
    part_length: Optional[float] = Query(None, description="Длина детали (мм)"),
    part_id: Optional[int] = Query(None, description="ID детали"),
    drawing_number: Optional[str] = Query(None, description="Номер чертежа"),
    db: Session = Depends(get_db)
):
    """
    Рекомендует лучшие станки для детали с объяснением.
    
    Алгоритм:
    1. Фильтрует станки по жёстким ограничениям (диаметр, длина)
    2. Добавляет бонус за историю (где раньше делали эту деталь)
    3. Добавляет бонус за тот же диаметр (без переналадки)
    4. Учитывает текущую загрузку станка
    5. Возвращает топ рекомендации с прогнозом
    """
    
    # 1. Получаем все активные станки с их параметрами
    machines_query = text("""
        SELECT 
            m.id,
            m.name,
            m.min_diameter,
            m.max_diameter,
            m.max_bar_length,
            m.max_part_length,
            m.is_jbs,
            m.supports_no_guidebush
        FROM machines m
        WHERE m.is_active = true
        ORDER BY m.name
    """)
    
    machines_result = db.execute(machines_query).fetchall()
    
    if not machines_result:
        raise HTTPException(status_code=404, detail="Нет активных станков")
    
    # 2. Получаем текущий диаметр на каждом станке (из активных наладок)
    current_setup_query = text("""
        SELECT 
            sj.machine_id,
            l.actual_diameter as current_diameter
        FROM setup_jobs sj
        JOIN lots l ON sj.lot_id = l.id
        WHERE sj.status IN ('started', 'completed')
          AND sj.end_time IS NULL
    """)
    
    current_setups = {row.machine_id: row.current_diameter 
                      for row in db.execute(current_setup_query).fetchall()}
    
    # 3. Получаем загрузку станков (часы в очереди)
    queue_query = text("""
        SELECT 
            l.assigned_machine_id as machine_id,
            SUM(
                CASE 
                    WHEN p.avg_cycle_time_sec IS NOT NULL AND l.total_planned_quantity IS NOT NULL
                    THEN (p.avg_cycle_time_sec * l.total_planned_quantity) / 3600.0
                    ELSE 0
                END
            ) as queue_hours
        FROM lots l
        JOIN parts p ON l.part_id = p.id
        WHERE l.assigned_machine_id IS NOT NULL
          AND l.status IN ('assigned', 'in_production')
        GROUP BY l.assigned_machine_id
    """)
    
    queue_hours = {row.machine_id: float(row.queue_hours or 0) 
                   for row in db.execute(queue_query).fetchall()}
    
    # 4. Получаем историю: на каких станках делали эту деталь
    history = {}
    if part_id or drawing_number:
        history_query = text("""
            SELECT 
                sj.machine_id,
                COUNT(*) as times_made
            FROM setup_jobs sj
            JOIN lots l ON sj.lot_id = l.id
            JOIN parts p ON l.part_id = p.id
            WHERE sj.status = 'completed'
              AND (
                  (:part_id IS NOT NULL AND p.id = :part_id)
                  OR (:drawing_number IS NOT NULL AND p.drawing_number = :drawing_number)
              )
            GROUP BY sj.machine_id
        """)
        
        history_result = db.execute(history_query, {
            "part_id": part_id,
            "drawing_number": drawing_number
        }).fetchall()
        
        history = {row.machine_id: row.times_made for row in history_result}
    
    # 5. Оцениваем каждый станок
    recommendations = []
    
    for m in machines_result:
        reasons = []
        score = 50  # базовый score
        
        # --- ЖЁСТКИЕ ОГРАНИЧЕНИЯ ---
        
        # Проверка диаметра
        if m.min_diameter and diameter < m.min_diameter:
            continue  # станок не подходит
        if m.max_diameter and diameter > m.max_diameter:
            continue  # станок не подходит
        
        reasons.append(f"✅ Диаметр {diameter}мм подходит ({m.min_diameter or '?'}-{m.max_diameter or '?'})")
        
        # Проверка длины детали
        if part_length and m.max_part_length:
            if part_length > m.max_part_length:
                continue  # деталь слишком длинная
            reasons.append(f"✅ Длина детали {part_length}мм ≤ {m.max_part_length}мм")
        elif part_length and not m.max_part_length:
            reasons.append(f"✅ Без ограничения длины детали")
        
        # --- МЯГКИЕ КРИТЕРИИ (score) ---
        
        # История
        if m.id in history:
            times = history[m.id]
            bonus = min(W_HISTORY, times * 10)  # макс 30 баллов
            score += bonus
            reasons.append(f"✅ Делали раньше ({times} раз)")
        else:
            reasons.append("🆕 Раньше не делали")
        
        # Тот же диаметр (без переналадки)
        current_d = current_setups.get(m.id)
        if current_d:
            if abs(current_d - diameter) < 0.5:  # тот же диаметр (±0.5мм)
                score += W_SAME_DIAMETER
                reasons.append(f"✅ Без переналадки (сейчас {current_d}мм)")
            else:
                reasons.append(f"⚠️ Переналадка {current_d}мм → {diameter}мм")
        
        # Загрузка очереди
        hours = queue_hours.get(m.id, 0)
        if hours == 0:
            score += W_FREE_QUEUE
            reasons.append("✅ Свободен")
        elif hours < 24:
            score += int(W_FREE_QUEUE * 0.7)
            reasons.append(f"⚡ Очередь: {hours:.0f}ч")
        elif hours < 72:
            score += int(W_FREE_QUEUE * 0.3)
            reasons.append(f"⏳ Очередь: {hours:.0f}ч")
        else:
            reasons.append(f"⚠️ Большая очередь: {hours:.0f}ч")
        
        # Специальные возможности (JBS)
        if m.is_jbs:
            score += 5
            reasons.append("🔧 JBS (неидеальный диаметр)")
        
        # --- ПРОГНОЗ ---
        forecast = None
        if cycle_time_sec and cycle_time_sec > 0:
            available_seconds = due_days * 24 * 3600
            can_make = available_seconds // cycle_time_sec
            completion_rate = min(int(can_make * 100 / quantity), 100)
            
            total_seconds = quantity * cycle_time_sec
            days_for_full = total_seconds / (24 * 3600)
            
            forecast = MachineForecast(
                can_make_by_deadline=min(can_make, quantity),
                completion_rate=completion_rate,
                days_for_full=round(days_for_full, 1)
            )
            
            if completion_rate < 100:
                reasons.append(f"⚠️ Частичная поставка: {completion_rate}% к сроку")
        
        recommendations.append(MachineRecommendation(
            machine_id=m.id,
            machine_name=m.name,
            score=min(score, 100),
            reasons=reasons,
            forecast=forecast,
            current_diameter=current_d,
            queue_hours=hours
        ))
    
    # Сортируем по score (лучшие первые)
    recommendations.sort(key=lambda x: x.score, reverse=True)
    
    # Возвращаем топ-5
    return RecommendationsResponse(
        part_id=part_id,
        drawing_number=drawing_number,
        diameter=diameter,
        quantity=quantity,
        due_days=due_days,
        recommendations=recommendations[:5]
    )

