from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class ReadingInput(BaseModel):
    machine_id: int
    operator_id: int
    value: int

class ReadingResponse(BaseModel):
    id: int
    machine_id: int
    employee_id: int
    reading: int
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True 