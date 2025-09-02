from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional

from src.database import get_db_session
from src.models.models import EmployeeDB, AreaDB, EmployeeAreaRoleDB

router = APIRouter(prefix="/employees", tags=["Employees"])


class DefaultAreaPayload(BaseModel):
    area_id: int


class AreaRolePayload(BaseModel):
    area_id: int
    role: str


class AreaRoleOut(BaseModel):
    area_id: int
    area_name: str
    role: str


@router.get("/{telegram_id}/default-area")
async def get_default_area(telegram_id: int, db: Session = Depends(get_db_session)):
    emp = db.query(EmployeeDB).filter(EmployeeDB.telegram_id == telegram_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    return {"default_area_id": emp.default_area_id}


@router.post("/{telegram_id}/default-area")
async def set_default_area(telegram_id: int, payload: DefaultAreaPayload, db: Session = Depends(get_db_session)):
    emp = db.query(EmployeeDB).filter(EmployeeDB.telegram_id == telegram_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    area = db.query(AreaDB).filter(AreaDB.id == payload.area_id).first()
    if not area:
        raise HTTPException(status_code=404, detail="Area not found")
    emp.default_area_id = payload.area_id
    db.commit()
    return {"success": True}


@router.get("/{telegram_id}/areas", response_model=List[AreaRoleOut])
async def list_area_roles(telegram_id: int, db: Session = Depends(get_db_session)):
    emp = db.query(EmployeeDB).filter(EmployeeDB.telegram_id == telegram_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    rows = db.query(EmployeeAreaRoleDB.area_id, AreaDB.name, EmployeeAreaRoleDB.role).\
        join(AreaDB, AreaDB.id == EmployeeAreaRoleDB.area_id).\
        filter(EmployeeAreaRoleDB.employee_id == emp.id).all()
    return [AreaRoleOut(area_id=r[0], area_name=r[1], role=r[2]) for r in rows]


@router.post("/{telegram_id}/areas")
async def grant_area_role(telegram_id: int, payload: AreaRolePayload, db: Session = Depends(get_db_session)):
    emp = db.query(EmployeeDB).filter(EmployeeDB.telegram_id == telegram_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    area = db.query(AreaDB).filter(AreaDB.id == payload.area_id).first()
    if not area:
        raise HTTPException(status_code=404, detail="Area not found")
    exists = db.query(EmployeeAreaRoleDB).filter(
        EmployeeAreaRoleDB.employee_id == emp.id,
        EmployeeAreaRoleDB.area_id == payload.area_id,
        func.lower(EmployeeAreaRoleDB.role) == func.lower(payload.role),
    ).first()
    if exists:
        return {"success": True}
    db.add(EmployeeAreaRoleDB(employee_id=emp.id, area_id=payload.area_id, role=payload.role))
    db.commit()
    return {"success": True}


@router.delete("/{telegram_id}/areas")
async def revoke_area_role(telegram_id: int, payload: AreaRolePayload, db: Session = Depends(get_db_session)):
    emp = db.query(EmployeeDB).filter(EmployeeDB.telegram_id == telegram_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    deleted = db.query(EmployeeAreaRoleDB).filter(
        EmployeeAreaRoleDB.employee_id == emp.id,
        EmployeeAreaRoleDB.area_id == payload.area_id,
        func.lower(EmployeeAreaRoleDB.role) == func.lower(payload.role),
    ).delete()
    db.commit()
    return {"success": True, "deleted": deleted}


