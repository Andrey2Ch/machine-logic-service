from pydantic import BaseModel
from typing import Optional, Dict, List
from datetime import datetime

class LotSummaryReport(BaseModel):
    """Сводный отчет по лотам"""
    total_lots: int
    lots_by_status: Dict[str, int]
    total_planned_quantity: int
    total_produced_quantity: int
    average_completion_time_hours: Optional[float] = None
    on_time_delivery_rate: float  # Процент лотов, выполненных в срок

class LotDetailReport(BaseModel):
    """Детальный отчет по конкретному лоту"""
    lot_id: int
    lot_number: str
    drawing_number: str
    material: Optional[str]
    status: str
    initial_planned_quantity: Optional[int]
    total_produced_quantity: int
    total_good_quantity: int
    total_defect_quantity: int
    total_rework_quantity: int
    created_at: Optional[datetime]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    due_date: Optional[datetime]
    is_overdue: bool
    completion_time_hours: Optional[float]
    setups_count: int
    batches_count: int
    machines_used: List[str]
    operators_involved: List[str]

class ProductionPerformanceReport(BaseModel):
    """Отчет по производительности"""
    period_start: datetime
    period_end: datetime
    total_setups: int
    total_batches: int
    total_produced_quantity: int
    average_cycle_time_seconds: Optional[float]
    machine_utilization: Dict[str, float]  # Процент использования по станкам
    operator_productivity: Dict[str, int]  # Количество деталей по операторам

class QualityReport(BaseModel):
    """Отчет по качеству"""
    period_start: datetime
    period_end: datetime
    total_inspected_quantity: int
    good_quantity: int
    defect_quantity: int
    rework_quantity: int
    defect_rate: float  # Процент брака
    rework_rate: float  # Процент переборки
    quality_by_drawing: Dict[str, Dict[str, int]]  # Качество по чертежам 