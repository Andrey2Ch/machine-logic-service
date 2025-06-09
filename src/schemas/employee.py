from pydantic import BaseModel
from typing import Optional

class EmployeeItem(BaseModel):
    id: int
    full_name: Optional[str] = None
    role_id: Optional[int] = None

    class Config:
        from_attributes = True 