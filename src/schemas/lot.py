from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum

class LotCreate(BaseModel):
    lot_number: str
    part_id: int
    initial_planned_quantity: Optional[int] = None
    due_date: Optional[datetime] = None
    order_manager_id: Optional[int] = None
    created_by_order_manager_at: Optional[datetime] = None

class LotStatusUpdate(BaseModel):
    status: "LotStatus"

class LotQuantityUpdate(BaseModel):
    additional_quantity: int = Field(..., ge=0, description="Дополнительное количество (неотрицательное число)")

# Модель для Детали (используется внутри LotResponse)
class PartResponseSimple(BaseModel):
    drawing_number: str
    material: Optional[str] = None
    id: int
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

# Основная модель для ответа по Лотам
class LotResponse(BaseModel):
    lot_number: str
    part_id: int
    initial_planned_quantity: Optional[int] = None
    due_date: Optional[datetime] = None
    id: int
    order_manager_id: Optional[int] = None
    created_by_order_manager_at: Optional[datetime] = None
    status: str
    created_at: Optional[datetime] = None
    total_planned_quantity: Optional[int] = None
    part: PartResponseSimple

    class Config:
        from_attributes = True

class LotStatus(str, Enum):
    NEW = "new"
    IN_PRODUCTION = "in_production"
    POST_PRODUCTION = "post_production"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ACTIVE = "active"  # Для совместимости

class LotAnalyticsResponse(BaseModel):
    accepted_by_warehouse_quantity: int = 0
    from_machine_quantity: int = 0

class LotInfoItem(BaseModel):
    id: int
    drawing_number: str
    lot_number: str
    inspector_name: Optional[str] = None
    planned_quantity: Optional[int] = None
    machine_name: Optional[str] = None

    class Config:
        from_attributes = True
        populate_by_name = True 