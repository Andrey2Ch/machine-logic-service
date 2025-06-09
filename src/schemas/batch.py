"""
@file: batch.py
@description: Pydantic schemas for batch operations
@dependencies: datetime, Optional, List, BaseModel, machine schemas
@created: 2024
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


# === BATCH CORE SCHEMAS ===

class BatchViewItem(BaseModel):
    id: int
    lot_id: int
    drawing_number: Optional[str]
    lot_number: Optional[str]
    current_quantity: int 
    current_location: str
    batch_time: Optional[datetime]
    warehouse_received_at: Optional[datetime]
    operator_name: Optional[str]

    class Config:
        from_attributes = True  # Pydantic v2
        populate_by_name = True  # Pydantic v2


# === BATCH INSPECTION SCHEMAS ===

class StartInspectionPayload(BaseModel):
    inspector_id: int


class InspectBatchPayload(BaseModel):
    inspector_id: int
    good_quantity: int
    rejected_quantity: int
    rework_quantity: int
    qc_comment: Optional[str] = None


# === BATCH OPERATIONS SCHEMAS ===

class BatchMergePayload(BaseModel):
    batch_ids: List[int]
    target_location: str


class BatchMovePayload(BaseModel):
    target_location: str
    inspector_id: Optional[int] = None  # ID пользователя, выполняющего перемещение


# === WAREHOUSE SCHEMAS ===

class WarehousePendingBatchItem(BaseModel):
    id: int
    lot_id: int
    drawing_number: Optional[str]
    lot_number: Optional[str]
    current_quantity: int 
    batch_time: Optional[datetime]
    operator_name: Optional[str]
    machine_name: Optional[str]  # Добавляем поле для названия станка
    card_number: Optional[int] = None  # Добавляем номер карточки

    class Config:
        from_attributes = True  # Pydantic v2
        populate_by_name = True  # Pydantic v2


class AcceptWarehousePayload(BaseModel):
    recounted_quantity: int
    warehouse_employee_id: int


# === BATCH CREATION SCHEMAS ===

class CreateBatchInput(BaseModel):
    lot_id: int
    operator_id: int
    machine_id: int
    drawing_number: str
    status: Optional[str] = 'sorting'


class CreateBatchResponse(BaseModel):
    batch_id: int
    lot_number: str
    drawing_number: str
    machine_name: str
    operator_id: int
    created_at: datetime
    shift: str 