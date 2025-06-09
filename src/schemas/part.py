from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class PartBase(BaseModel):
    drawing_number: str = Field(..., description="Номер чертежа детали, должен быть уникальным")
    material: Optional[str] = Field(None, description="Материал детали")

class PartCreate(PartBase):
    pass

class PartUpdate(PartBase):
    drawing_number: Optional[str] = None
    material: Optional[str] = None

class PartResponse(PartBase):
    id: int
    created_at: Optional[datetime]

    class Config:
        from_attributes = True 