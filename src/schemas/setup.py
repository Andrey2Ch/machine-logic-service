from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from datetime import datetime
from src.models.setup import SetupStatus

# Pydantic модели для Наладок (Setups)
class SetupInput(BaseModel):
    machine_id: int
    operator_id: int
    part_id: int
    lot_id: Optional[int] = None
    planned_quantity: int

class SetupStatusUpdate(BaseModel):
    status: SetupStatus
    qc_operator_id: Optional[int] = None
    rejection_reason: Optional[str] = None
    details: Optional[Dict] = None

class SetupResponse(BaseModel):
    id: int
    machine_id: int
    operator_id: int
    part_id: int
    planned_quantity: int
    status: str
    created_at: datetime
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    part_drawing_number: str
    operator_full_name: str
    machine_name: str

    class Config:
        from_attributes = True

class SetupStatusInfo(BaseModel):
    machine_id: int
    status: str
    message: str

class ActiveSetupResponse(BaseModel):
    id: int
    machine_id: int
    status: str
    part_drawing_number: str
    
    class Config:
        from_attributes = True

class QaSetupViewItem(BaseModel):
    id: int
    machineName: Optional[str] = Field(None, alias='machine_name')
    drawingNumber: Optional[str] = Field(None, alias='drawing_number')
    lotNumber: Optional[str] = Field(None, alias='lot_number')
    machinistName: Optional[str] = Field(None, alias='machinist_name')
    startTime: Optional[datetime] = Field(None, alias='start_time')
    status: Optional[str]
    qaName: Optional[str] = Field(None, alias='qa_name')
    qaDate: Optional[datetime] = Field(None, alias='qa_date')

    class Config:
        from_attributes = True 
        populate_by_name = True 