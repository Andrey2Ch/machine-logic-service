"""Stoppage reason codes API (רשימת תקלות)."""

from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db_session
from ..models.models import StoppageReasonDB

router = APIRouter(prefix="/stoppage-reasons", tags=["stoppage-reasons"])


class StoppageReasonOut(BaseModel):
    code: int
    category: str
    name_he: str
    name_ru: str
    name_en: str
    is_active: bool

    class Config:
        from_attributes = True


@router.get("", response_model=List[StoppageReasonOut])
def list_stoppage_reasons(
    category: Optional[str] = Query(None, description="machine, part, or work_and_material"),
    active_only: bool = Query(True),
    db: Session = Depends(get_db_session),
):
    q = db.query(StoppageReasonDB)
    if active_only:
        q = q.filter(StoppageReasonDB.is_active.is_(True))
    if category:
        q = q.filter(StoppageReasonDB.category == category)
    return q.order_by(StoppageReasonDB.code).all()


@router.get("/{code}", response_model=StoppageReasonOut)
def get_stoppage_reason(code: int, db: Session = Depends(get_db_session)):
    reason = db.query(StoppageReasonDB).filter(StoppageReasonDB.code == code).first()
    if not reason:
        from fastapi import HTTPException
        raise HTTPException(404, f"Stoppage reason code {code} not found")
    return reason
