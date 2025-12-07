"""
Роутер для планирования и распределения работ по станкам
"""
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
from ..database import get_db_session
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/planning", tags=["Planning"])

# Константы для умных рекомендаций
SLACK_THRESHOLD_DAYS = 3  # Порог запаса: если slack > 3 дней, лот можно сдвинуть
MIN_QTY_FOR_TRANSFER = 100  # Минимальное количество для переноса на другой станок
SETUP_TIME_HOURS = 1.0  # Среднее время переналадки в часах


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
    db: Session = Depends(get_db_session)
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
    # ВАЖНО: используем COALESCE т.к. total_planned_quantity может быть NULL
    queue_query = text("""
        SELECT 
            l.assigned_machine_id as machine_id,
            SUM(
                CASE 
                    WHEN p.avg_cycle_time IS NOT NULL 
                         AND COALESCE(l.total_planned_quantity, l.initial_planned_quantity) IS NOT NULL
                    THEN (p.avg_cycle_time * COALESCE(l.total_planned_quantity, l.initial_planned_quantity)) / 3600.0
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


# ============ УМНЫЕ РЕКОМЕНДАЦИИ С РЕОРГАНИЗАЦИЕЙ ОЧЕРЕДИ ============

class QueueLot(BaseModel):
    """Лот в очереди станка"""
    lot_id: int
    lot_number: str
    position: int
    drawing_number: Optional[str]
    quantity: int
    cycle_time_sec: Optional[int]
    work_hours: float  # расчётное время работы
    due_date: Optional[datetime]
    eta: Optional[datetime]  # расчётное завершение
    slack_days: Optional[float]  # запас до дедлайна
    diameter: Optional[float]
    status: str

class QueueReorgAction(BaseModel):
    """Рекомендация по реорганизации очереди"""
    action: str  # "move_up", "move_down", "suggest_transfer"
    lot_id: int
    lot_number: str
    from_position: Optional[int]
    to_position: Optional[int]
    from_machine: Optional[str]
    to_machine: Optional[str]
    reason: str
    slack_before: Optional[float]
    slack_after: Optional[float]

class MachineQueueAnalysis(BaseModel):
    """Анализ очереди станка"""
    machine_id: int
    machine_name: str
    score: int
    current_diameter: Optional[float]
    queue_hours: float
    lots_in_queue: List[QueueLot]
    recommended_position: int  # куда вставить новый лот
    needs_setup_change: bool  # нужна переналадка
    reorg_actions: List[QueueReorgAction]  # рекомендации по реорганизации

class SmartRecommendationsResponse(BaseModel):
    """Ответ с умными рекомендациями"""
    new_lot: dict  # информация о новом лоте
    recommendations: List[MachineQueueAnalysis]
    warnings: List[str]  # предупреждения


@router.get("/recommend-with-queue", response_model=SmartRecommendationsResponse)
async def recommend_with_queue_analysis(
    diameter: float = Query(..., description="Диаметр материала (мм)"),
    quantity: int = Query(..., description="Количество деталей"),
    due_date: str = Query(..., description="Срок поставки (YYYY-MM-DD)"),
    cycle_time_sec: Optional[int] = Query(None, description="Время цикла (сек)"),
    part_id: Optional[int] = Query(None, description="ID детали"),
    drawing_number: Optional[str] = Query(None, description="Номер чертежа"),
    db: Session = Depends(get_db_session)
):
    """
    Умные рекомендации с анализом очередей и предложениями по реорганизации.
    
    Алгоритм:
    1. Для каждого подходящего станка анализирует очередь
    2. Рассчитывает ETA и slack для каждого лота
    3. Определяет оптимальную позицию для нового лота
    4. Генерирует рекомендации по перестановке если нужно
    """
    
    warnings = []
    
    # Парсим дату
    try:
        target_due_date = datetime.strptime(due_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверный формат даты. Используйте YYYY-MM-DD")
    
    # Расчёт времени работы нового лота
    new_lot_hours = 0
    if cycle_time_sec and cycle_time_sec > 0:
        new_lot_hours = (cycle_time_sec * quantity) / 3600.0
    
    now_utc = datetime.now(timezone.utc)
    due_days = (target_due_date - now_utc).days
    
    # 1. Получаем подходящие станки
    machines_query = text("""
        SELECT 
            m.id, m.name, m.min_diameter, m.max_diameter,
            m.max_part_length, m.is_jbs
        FROM machines m
        WHERE m.is_active = true
          AND (m.min_diameter IS NULL OR m.min_diameter <= :diameter)
          AND (m.max_diameter IS NULL OR m.max_diameter >= :diameter)
        ORDER BY m.name
    """)
    
    machines = db.execute(machines_query, {"diameter": diameter}).fetchall()
    
    if not machines:
        raise HTTPException(status_code=404, detail="Нет подходящих станков для данного диаметра")
    
    # 2. Получаем текущие диаметры на станках
    current_setup_query = text("""
        SELECT sj.machine_id, l.actual_diameter as current_diameter
        FROM setup_jobs sj
        JOIN lots l ON sj.lot_id = l.id
        WHERE sj.status IN ('started', 'completed') AND sj.end_time IS NULL
    """)
    current_setups = {row.machine_id: row.current_diameter 
                      for row in db.execute(current_setup_query).fetchall()}
    
    # 3. Получаем очереди всех станков с детальной информацией
    queue_query = text("""
        SELECT 
            l.id as lot_id,
            l.lot_number,
            l.assigned_machine_id as machine_id,
            l.assigned_order as position,
            l.due_date,
            l.status,
            l.actual_diameter,
            COALESCE(l.total_planned_quantity, l.initial_planned_quantity) as quantity,
            p.drawing_number,
            p.avg_cycle_time,
            p.recommended_diameter
        FROM lots l
        JOIN parts p ON l.part_id = p.id
        WHERE l.assigned_machine_id IS NOT NULL
          AND l.status IN ('assigned', 'in_production')
        ORDER BY l.assigned_machine_id, l.assigned_order
    """)
    
    queue_rows = db.execute(queue_query).fetchall()
    
    # Группируем лоты по станкам
    machine_queues = {}
    for row in queue_rows:
        mid = row.machine_id
        if mid not in machine_queues:
            machine_queues[mid] = []
        
        work_hours = 0
        if row.avg_cycle_time and row.quantity:
            work_hours = (row.avg_cycle_time * row.quantity) / 3600.0
        
        machine_queues[mid].append({
            "lot_id": row.lot_id,
            "lot_number": row.lot_number,
            "position": row.position or 999,
            "drawing_number": row.drawing_number,
            "quantity": row.quantity or 0,
            "cycle_time_sec": row.avg_cycle_time,
            "work_hours": work_hours,
            "due_date": row.due_date,
            "diameter": row.actual_diameter or row.recommended_diameter,
            "status": row.status
        })
    
    # 4. Анализируем каждый станок
    recommendations = []
    
    for m in machines:
        queue = machine_queues.get(m.id, [])
        queue.sort(key=lambda x: x["position"])
        
        current_d = current_setups.get(m.id)
        needs_setup = current_d is not None and abs(current_d - diameter) >= 0.5
        
        # Рассчитываем ETA и slack для каждого лота в очереди
        cumulative_hours = 0
        queue_lots = []
        
        for lot in queue:
            # ETA = сейчас + накопленные часы + время этого лота
            lot_eta = None
            lot_slack = None
            
            if lot["work_hours"] > 0:
                eta_datetime = now_utc + timedelta(hours=cumulative_hours + lot["work_hours"])
                lot_eta = eta_datetime
                
                if lot["due_date"]:
                    # Приводим due_date к UTC если он timezone-naive
                    lot_due = lot["due_date"]
                    if lot_due.tzinfo is None:
                        lot_due = lot_due.replace(tzinfo=timezone.utc)
                    lot_slack = (lot_due - eta_datetime).total_seconds() / 86400  # в днях
            
            cumulative_hours += lot["work_hours"]
            
            queue_lots.append(QueueLot(
                lot_id=lot["lot_id"],
                lot_number=lot["lot_number"],
                position=lot["position"],
                drawing_number=lot["drawing_number"],
                quantity=lot["quantity"],
                cycle_time_sec=lot["cycle_time_sec"],
                work_hours=round(lot["work_hours"], 1),
                due_date=lot["due_date"],
                eta=lot_eta,
                slack_days=round(lot_slack, 1) if lot_slack is not None else None,
                diameter=lot["diameter"],
                status=lot["status"]
            ))
        
        # Определяем оптимальную позицию для нового лота
        recommended_pos = len(queue_lots) + 1  # по умолчанию в конец
        new_lot_eta = now_utc + timedelta(hours=cumulative_hours + new_lot_hours)
        new_lot_slack = (target_due_date - new_lot_eta).total_seconds() / 86400
        
        reorg_actions = []
        
        # Если новый лот опаздывает (slack < 0), ищем куда его вставить
        if new_lot_slack < 0 and len(queue_lots) > 0:
            # Ищем позицию: вставляем перед первым лотом с slack > SLACK_THRESHOLD_DAYS
            for i, qlot in enumerate(queue_lots):
                if qlot.slack_days is not None and qlot.slack_days > SLACK_THRESHOLD_DAYS:
                    # Нельзя двигать лоты in_production
                    if qlot.status == "in_production":
                        continue
                    
                    # Проверяем совместимость диаметров - лишняя переналадка?
                    extra_setup_warning = None
                    if qlot.diameter and abs(qlot.diameter - diameter) >= 0.5:
                        # Вставка лота с другим диаметром может создать доп. переналадку
                        # Проверяем: если предыдущий лот (i-1) имеет тот же диаметр что сдвигаемый
                        if i > 0:
                            prev_lot = queue_lots[i-1]
                            if prev_lot.diameter and abs(prev_lot.diameter - qlot.diameter) < 0.5:
                                extra_setup_warning = f"⚠️ Вставка создаст дополнительную переналадку: {prev_lot.diameter}мм → {diameter}мм → {qlot.diameter}мм (+~{SETUP_TIME_HOURS}ч)"
                                warnings.append(extra_setup_warning)
                    
                    recommended_pos = qlot.position
                    
                    # Генерируем рекомендации по сдвигу
                    for j in range(i, len(queue_lots)):
                        moved_lot = queue_lots[j]
                        if moved_lot.status == "in_production":
                            continue
                        
                        # Рассчитываем новый slack после сдвига
                        shift_hours = new_lot_hours
                        new_slack = moved_lot.slack_days - (shift_hours / 24) if moved_lot.slack_days else None
                        
                        reorg_actions.append(QueueReorgAction(
                            action="move_down",
                            lot_id=moved_lot.lot_id,
                            lot_number=moved_lot.lot_number,
                            from_position=moved_lot.position,
                            to_position=moved_lot.position + 1,
                            from_machine=None,
                            to_machine=None,
                            reason=f"Сдвиг для срочного лота (запас {moved_lot.slack_days:.0f}д → {new_slack:.0f}д)" if new_slack else "Сдвиг для срочного лота",
                            slack_before=moved_lot.slack_days,
                            slack_after=round(new_slack, 1) if new_slack else None
                        ))
                        
                        # Проверяем цепную реакцию: если после сдвига slack < 0
                        if new_slack is not None and new_slack < 0:
                            warnings.append(f"⚠️ Лот {moved_lot.lot_number} после сдвига будет опаздывать на {abs(new_slack):.0f} дней!")
                    
                    break
        
        # Считаем score станка
        score = 50
        total_queue_hours = sum(l.work_hours for l in queue_lots)
        
        if total_queue_hours == 0:
            score += 25
        elif total_queue_hours < 24:
            score += 17
        elif total_queue_hours < 72:
            score += 8
        
        if not needs_setup:
            score += 25
        
        recommendations.append(MachineQueueAnalysis(
            machine_id=m.id,
            machine_name=m.name,
            score=min(score, 100),
            current_diameter=current_d,
            queue_hours=round(total_queue_hours, 1),
            lots_in_queue=queue_lots,
            recommended_position=recommended_pos,
            needs_setup_change=needs_setup,
            reorg_actions=reorg_actions
        ))
    
    # 5. Анализ переносов на другие станки (suggest_transfer)
    # Для станков с высокой загрузкой ищем лоты для переноса
    machine_name_map = {m.id: m.name for m in machines}
    
    for rec in recommendations:
        if rec.queue_hours < 48:  # Только для загруженных станков (>48ч)
            continue
        
        for lot in rec.lots_in_queue:
            # Пропускаем: in_production, маленькие партии, срочные
            if lot.status == "in_production":
                continue
            if lot.quantity < MIN_QTY_FOR_TRANSFER:
                continue
            if lot.slack_days is None or lot.slack_days <= SLACK_THRESHOLD_DAYS:
                continue
            
            # Ищем альтернативный станок
            best_alt = None
            best_alt_hours = float('inf')
            
            for alt_rec in recommendations:
                if alt_rec.machine_id == rec.machine_id:
                    continue
                if alt_rec.queue_hours >= rec.queue_hours:
                    continue  # Не переносим на более загруженный
                
                # Проверяем совместимость диаметра
                if lot.diameter:
                    # Станок должен поддерживать этот диаметр
                    alt_machine = next((m for m in machines if m.id == alt_rec.machine_id), None)
                    if alt_machine:
                        if alt_machine.min_diameter and lot.diameter < alt_machine.min_diameter:
                            continue
                        if alt_machine.max_diameter and lot.diameter > alt_machine.max_diameter:
                            continue
                
                if alt_rec.queue_hours < best_alt_hours:
                    best_alt = alt_rec
                    best_alt_hours = alt_rec.queue_hours
            
            if best_alt and (rec.queue_hours - best_alt_hours) > 8:  # Выигрыш >8ч
                rec.reorg_actions.append(QueueReorgAction(
                    action="suggest_transfer",
                    lot_id=lot.lot_id,
                    lot_number=lot.lot_number,
                    from_position=lot.position,
                    to_position=len(best_alt.lots_in_queue) + 1,
                    from_machine=rec.machine_name,
                    to_machine=best_alt.machine_name,
                    reason=f"Разгрузка очереди: запас {lot.slack_days:.0f}д, перенос на менее загруженный станок ({best_alt.queue_hours:.0f}ч vs {rec.queue_hours:.0f}ч)",
                    slack_before=lot.slack_days,
                    slack_after=lot.slack_days  # slack не изменится если очередь короче
                ))
    
    # Сортируем по score
    recommendations.sort(key=lambda x: x.score, reverse=True)
    
    return SmartRecommendationsResponse(
        new_lot={
            "diameter": diameter,
            "quantity": quantity,
            "due_date": due_date,
            "due_days": due_days,
            "cycle_time_sec": cycle_time_sec,
            "work_hours": round(new_lot_hours, 1),
            "part_id": part_id,
            "drawing_number": drawing_number
        },
        recommendations=recommendations[:5],
        warnings=warnings
    )

