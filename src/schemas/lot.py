"""
@file: lot.py
@description: Pydantic schemas for lot operations
@dependencies: datetime, Optional, List, Dict, BaseModel, Field, Enum, PartResponse
@created: 2024
"""

from datetime import datetime
from typing import Optional, List, Dict
from enum import Enum
from pydantic import BaseModel, Field

from .part import PartResponse


# === LOT STATUS ENUM ===

class LotStatus(str, Enum):
    """Статусы лотов для синхронизации между Telegram-ботом и FastAPI"""
    NEW = "new"                    # Новый лот от Order Manager
    IN_PRODUCTION = "in_production"  # Лот в производстве (после начала наладки)
    POST_PRODUCTION = "post_production"  # Лот после производства (все наладки завершены)
    COMPLETED = "completed"        # Завершенный лот
    CANCELLED = "cancelled"        # Отмененный лот
    ACTIVE = "active"             # Устаревший статус (для совместимости)


# === CORE LOT SCHEMAS ===

class LotBase(BaseModel):
    lot_number: str
    part_id: int
    initial_planned_quantity: Optional[int] = None  # Сделано опциональным
    due_date: Optional[datetime] = None
    # Статус будет устанавливаться по умолчанию на бэкенде


class LotCreate(LotBase):
    # order_manager_id и created_by_order_manager_at будут добавлены на бэкенде
    # Обновляем для временного решения: клиент может передавать эти поля
    order_manager_id: Optional[int] = None
    created_by_order_manager_at: Optional[datetime] = None


class LotResponse(LotBase):
    id: int
    order_manager_id: Optional[int] = None
    created_by_order_manager_at: Optional[datetime] = None
    status: LotStatus
    created_at: Optional[datetime] = None  # Сделано опциональным
    total_planned_quantity: Optional[int] = None  # Общее количество (плановое + дополнительное)
    part: Optional[PartResponse] = None  # Для возврата информации о детали вместе с лотом

    class Config:
        from_attributes = True  # Исправлено с orm_mode
        populate_by_name = True


# === LOT UPDATE SCHEMAS ===

class LotStatusUpdate(BaseModel):
    status: LotStatus


class LotQuantityUpdate(BaseModel):
    additional_quantity: int = Field(..., ge=0, description="Дополнительное количество (неотрицательное число)")


# === LOT INFO SCHEMAS ===

class LotInfoItem(BaseModel):
    id: int
    drawing_number: str
    lot_number: str
    inspector_name: Optional[str] = None
    planned_quantity: Optional[int] = None
    machine_name: Optional[str] = None

    class Config:
        from_attributes = True  # Pydantic v2
        populate_by_name = True  # Pydantic v2


class LotAnalyticsResponse(BaseModel):
    accepted_by_warehouse_quantity: int = 0  # "Принято" (на складе)
    from_machine_quantity: int = 0           # "Со станка" (сырые)


# === LOT REPORT SCHEMAS ===

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