from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from datetime import datetime

class MachineItem(BaseModel):
    id: int
    name: Optional[str] = None

    class Config:
        from_attributes = True

class OperatorMachineViewItem(BaseModel):
    id: int 
    name: Optional[str] = None
    reading: Optional[str] = '' 
    lastReading: Optional[int] = Field(None, alias='last_reading')
    lastReadingTime: Optional[datetime] = Field(None, alias='last_reading_time')
    setupId: Optional[int] = Field(None, alias='setup_id')
    drawingNumber: Optional[str] = Field(None, alias='drawing_number')
    plannedQuantity: Optional[int] = Field(None, alias='planned_quantity')
    additionalQuantity: Optional[int] = Field(None, alias='additional_quantity')
    status: Optional[str] = None

    class Config: 
        from_attributes = True
        populate_by_name = True

class BatchLabelInfo(BaseModel):
    id: int  # ID самого батча
    lot_id: int 
    drawing_number: str
    lot_number: str
    machine_name: str
    operator_name: str
    operator_id: int
    batch_time: datetime
    shift: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    initial_quantity: int
    current_quantity: int
    batch_quantity: int
    # Новые поля для складской информации
    warehouse_received_at: Optional[datetime] = None
    warehouse_employee_name: Optional[str] = None
    recounted_quantity: Optional[int] = None

class BatchAvailabilityInfo(BaseModel):
    """Информация о доступности печати этикеток для станка"""
    machine_id: int
    machine_name: str
    has_active_batch: bool  # Есть ли активный батч в production
    has_any_batch: bool     # Есть ли любой батч (для переборки)
    last_batch_data: Optional[BatchLabelInfo] = None  # Данные последнего батча

    class Config:
        from_attributes = True 