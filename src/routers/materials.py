"""
@file: machine-logic-service/src/routers/materials.py
@description: Роутер для обработки API-запросов, связанных с управлением материалами (сырьём).
@dependencies: fastapi, sqlalchemy, pydantic
@created: 2025-11-30
@updated: 2025-12-01 - Добавлены endpoints для управления материалом (add-bars, return, history)
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func
from src.database import get_db_session
from typing import List, Optional
from pydantic import BaseModel
from src.models.models import (
    MaterialTypeDB, 
    LotMaterialDB, 
    LotDB, 
    MachineDB, 
    EmployeeDB,
    PartDB,
    MaterialOperationDB,
    SetupDB
)
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/materials",
    tags=["Materials"],
    responses={404: {"description": "Not found"}},
)

# ========== Pydantic схемы ==========

class MaterialTypeOut(BaseModel):
    id: int
    material_name: str
    density_kg_per_m3: float
    description: Optional[str] = None

    class Config:
        from_attributes = True

class IssueToMachineRequest(BaseModel):
    machine_id: int  # Теперь обязательный!
    lot_id: int
    drawing_number: Optional[str] = None  # מס' שרטוט
    material_type: Optional[str] = None  # סוג חומר - теперь необязательный
    diameter: float  # диаметр
    quantity_bars: int  # כמות במוטות
    material_receipt_id: Optional[int] = None
    notes: Optional[str] = None

class AddBarsRequest(BaseModel):
    quantity_bars: int
    performed_by: Optional[int] = None
    notes: Optional[str] = None

class ReturnBarsRequest(BaseModel):
    quantity_bars: int
    performed_by: Optional[int] = None
    notes: Optional[str] = None

class CloseMaterialRequest(BaseModel):
    defect_bars: int = 0
    notes: Optional[str] = None
    closed_by: Optional[int] = None

class MaterialOperationOut(BaseModel):
    id: int
    lot_material_id: int
    operation_type: str
    quantity_bars: int
    diameter: Optional[float] = None
    performed_by: Optional[int] = None
    performer_name: Optional[str] = None
    performed_at: Optional[datetime] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class LotMaterialOut(BaseModel):
    id: int
    lot_id: int
    lot_number: Optional[str] = None
    machine_id: Optional[int] = None
    machine_name: Optional[str] = None
    drawing_number: Optional[str] = None
    material_type: Optional[str] = None
    diameter: Optional[float] = None
    issued_bars: int
    returned_bars: int
    defect_bars: int = 0
    used_bars: int  # issued - returned - defect
    issued_at: Optional[datetime] = None
    status: str
    notes: Optional[str] = None
    closed_at: Optional[datetime] = None
    closed_by: Optional[int] = None
    created_at: Optional[datetime] = None
    # Дополнительная информация для определения состояния
    lot_status: Optional[str] = None
    setup_status: Optional[str] = None

    class Config:
        from_attributes = True

class LotMaterialDetailOut(LotMaterialOut):
    operations: List[MaterialOperationOut] = []

# ========== Endpoints ==========

@router.get("/types", response_model=List[MaterialTypeOut])
def get_material_types(db: Session = Depends(get_db_session)):
    """Получить справочник материалов с плотностью"""
    try:
        types = db.query(MaterialTypeDB).order_by(MaterialTypeDB.material_name).all()
        result = []
        for mt in types:
            result.append({
                "id": mt.id,
                "material_name": mt.material_name,
                "density_kg_per_m3": mt.density_kg_per_m3,
                "description": mt.description
            })
        return result
    except Exception as e:
        logger.error(f"Error fetching material types: {e}", exc_info=True)
        return []


@router.post("/issue-to-machine", response_model=LotMaterialOut)
def issue_material_to_machine(
    request: IssueToMachineRequest,
    db: Session = Depends(get_db_session)
):
    """
    Выдать материал на станок (по записке-требованию)
    
    Логика:
    1. Проверяем существование лота и станка
    2. Ищем существующую запись lot_materials с таким же lot_id + machine_id + diameter
    3. Если найдена — добавляем к issued_bars
    4. Если нет — создаём новую запись
    5. Записываем операцию в material_operations
    """
    try:
        # Проверяем существование лота
        lot = db.query(LotDB).filter(LotDB.id == request.lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail=f"Лот {request.lot_id} не найден")
        
        # Проверяем станок
        machine = db.query(MachineDB).filter(MachineDB.id == request.machine_id).first()
        if not machine:
            raise HTTPException(status_code=404, detail=f"Станок {request.machine_id} не найден")
        
        # Получаем drawing_number из лота если не передан
        drawing_number = request.drawing_number
        if not drawing_number and lot.part_id:
            part = db.query(PartDB).filter(PartDB.id == lot.part_id).first()
            if part:
                drawing_number = part.drawing_number
        
        # Ищем существующую запись с таким же lot_id + machine_id + diameter
        existing = db.query(LotMaterialDB).filter(
            and_(
                LotMaterialDB.lot_id == request.lot_id,
                LotMaterialDB.machine_id == request.machine_id,
                LotMaterialDB.diameter == request.diameter
            )
        ).first()
        
        if existing:
            # Добавляем к существующей записи (used_bars вычисляется автоматически в PostgreSQL)
            existing.issued_bars = (existing.issued_bars or 0) + request.quantity_bars
            if request.notes:
                existing.notes = f"{existing.notes or ''}\n{request.notes}".strip()
            
            lot_material = existing
            operation_type = "add"
        else:
            # Создаём новую запись (НЕ включаем used_bars - это generated column в PostgreSQL!)
            lot_material = LotMaterialDB(
                lot_id=request.lot_id,
                machine_id=request.machine_id,
                material_type=request.material_type,
                diameter=request.diameter,
                issued_bars=request.quantity_bars,
                returned_bars=0,
                issued_at=datetime.now(timezone.utc),
                status="issued",
                notes=request.notes
            )
            db.add(lot_material)
            operation_type = "issue"
        
        # Обновляем статус материала в лоте
        lot.material_status = "issued"
        
        # 🎯 ВАЖНО: Записываем фактический диаметр из материала в лот!
        # Кладовщик измеряет реальный диаметр при выдаче - это приоритетнее теоретического
        # Обновляем ВСЕГДА (даже если был заполнен при создании лота)
        if request.diameter:
            lot.actual_diameter = request.diameter
            logger.info(f"Updated lot {lot.id} actual_diameter to {request.diameter} from warehouse issue")
        
        db.flush()  # Получаем ID для lot_material
        
        # Записываем операцию в историю
        operation = MaterialOperationDB(
            lot_material_id=lot_material.id,
            operation_type=operation_type,
            quantity_bars=request.quantity_bars,
            diameter=request.diameter,
            notes=request.notes,
            performed_at=datetime.now(timezone.utc)
        )
        db.add(operation)
        
        db.commit()
        db.refresh(lot_material)
        
        return {
            "id": lot_material.id,
            "lot_id": lot_material.lot_id,
            "lot_number": lot.lot_number,
            "machine_id": lot_material.machine_id,
            "machine_name": machine.name,
            "drawing_number": drawing_number,
            "material_type": lot_material.material_type,
            "diameter": lot_material.diameter,
            "issued_bars": lot_material.issued_bars or 0,
            "returned_bars": lot_material.returned_bars or 0,
            "defect_bars": lot_material.defect_bars or 0,
            "used_bars": (lot_material.issued_bars or 0) - (lot_material.returned_bars or 0) - (lot_material.defect_bars or 0),
            "issued_at": lot_material.issued_at,
            "status": lot_material.status,
            "notes": lot_material.notes,
            "closed_at": lot_material.closed_at,
            "closed_by": lot_material.closed_by,
            "created_at": lot_material.created_at,
            "lot_status": lot.status,
            "setup_status": None  # Для нового материала еще нет setup
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error issuing material to machine: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка при выдаче материала: {str(e)}")


@router.patch("/lot-materials/{id}/add-bars", response_model=LotMaterialOut)
def add_bars_to_material(
    id: int,
    request: AddBarsRequest,
    db: Session = Depends(get_db_session)
):
    """
    Добавить прутки к существующей выдаче материала
    """
    try:
        lot_material = db.query(LotMaterialDB).filter(LotMaterialDB.id == id).first()
        if not lot_material:
            raise HTTPException(status_code=404, detail=f"Запись материала {id} не найдена")
        
        if request.quantity_bars <= 0:
            raise HTTPException(status_code=400, detail="Количество прутков должно быть положительным")
        
        # Обновляем количество (used_bars вычисляется автоматически в PostgreSQL)
        lot_material.issued_bars = (lot_material.issued_bars or 0) + request.quantity_bars
        
        # Записываем операцию
        operation = MaterialOperationDB(
            lot_material_id=id,
            operation_type="add",
            quantity_bars=request.quantity_bars,
            diameter=lot_material.diameter,
            performed_by=request.performed_by,
            notes=request.notes,
            performed_at=datetime.now(timezone.utc)
        )
        db.add(operation)
        
        db.commit()
        db.refresh(lot_material)
        
        # Получаем связанные данные
        lot = db.query(LotDB).filter(LotDB.id == lot_material.lot_id).first()
        machine = db.query(MachineDB).filter(MachineDB.id == lot_material.machine_id).first() if lot_material.machine_id else None
        
        return {
            "id": lot_material.id,
            "lot_id": lot_material.lot_id,
            "lot_number": lot.lot_number if lot else None,
            "machine_id": lot_material.machine_id,
            "machine_name": machine.name if machine else None,
            "material_type": lot_material.material_type,
            "diameter": lot_material.diameter,
            "issued_bars": lot_material.issued_bars or 0,
            "returned_bars": lot_material.returned_bars or 0,
            "used_bars": (lot_material.issued_bars or 0) - (lot_material.returned_bars or 0),
            "issued_at": lot_material.issued_at,
            "status": lot_material.status,
            "notes": lot_material.notes,
            "created_at": lot_material.created_at
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding bars: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка при добавлении прутков: {str(e)}")


@router.post("/lot-materials/{id}/return", response_model=LotMaterialOut)
def return_bars(
    id: int,
    request: ReturnBarsRequest,
    db: Session = Depends(get_db_session)
):
    """
    Вернуть прутки на склад
    """
    try:
        lot_material = db.query(LotMaterialDB).filter(LotMaterialDB.id == id).first()
        if not lot_material:
            raise HTTPException(status_code=404, detail=f"Запись материала {id} не найдена")
        
        if request.quantity_bars <= 0:
            raise HTTPException(status_code=400, detail="Количество прутков должно быть положительным")
        
        # Проверяем, что не возвращаем больше чем есть
        max_returnable = (lot_material.issued_bars or 0) - (lot_material.returned_bars or 0)
        if request.quantity_bars > max_returnable:
            raise HTTPException(
                status_code=400, 
                detail=f"Нельзя вернуть {request.quantity_bars} прутков. Максимум: {max_returnable}"
            )
        
        # Обновляем количество (used_bars вычисляется автоматически в PostgreSQL)
        lot_material.returned_bars = (lot_material.returned_bars or 0) + request.quantity_bars
        lot_material.returned_at = datetime.now(timezone.utc)
        lot_material.returned_by = request.performed_by
        
        # Обновляем статус (вычисляем used_bars для проверки)
        calculated_used = (lot_material.issued_bars or 0) - (lot_material.returned_bars or 0)
        if calculated_used == 0:
            lot_material.status = "returned"
        elif lot_material.returned_bars > 0:
            lot_material.status = "partially_returned"
        
        # Записываем операцию (отрицательное количество для возврата)
        operation = MaterialOperationDB(
            lot_material_id=id,
            operation_type="return",
            quantity_bars=-request.quantity_bars,  # Отрицательное для возврата
            diameter=lot_material.diameter,
            performed_by=request.performed_by,
            notes=request.notes,
            performed_at=datetime.now(timezone.utc)
        )
        db.add(operation)
        
        db.commit()
        db.refresh(lot_material)
        
        # Получаем связанные данные
        lot = db.query(LotDB).filter(LotDB.id == lot_material.lot_id).first()
        machine = db.query(MachineDB).filter(MachineDB.id == lot_material.machine_id).first() if lot_material.machine_id else None
        
        return {
            "id": lot_material.id,
            "lot_id": lot_material.lot_id,
            "lot_number": lot.lot_number if lot else None,
            "machine_id": lot_material.machine_id,
            "machine_name": machine.name if machine else None,
            "material_type": lot_material.material_type,
            "diameter": lot_material.diameter,
            "issued_bars": lot_material.issued_bars or 0,
            "returned_bars": lot_material.returned_bars or 0,
            "used_bars": (lot_material.issued_bars or 0) - (lot_material.returned_bars or 0),
            "issued_at": lot_material.issued_at,
            "status": lot_material.status,
            "notes": lot_material.notes,
            "created_at": lot_material.created_at
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error returning bars: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка при возврате прутков: {str(e)}")


@router.patch("/lot-materials/{id}/close", response_model=LotMaterialOut)
def close_material(
    id: int,
    request: CloseMaterialRequest,
    db: Session = Depends(get_db_session)
):
    """
    Закрыть выдачу материала после проверки кладовщиком
    
    Используется когда наладка завершена и кладовщик проверил:
    - Весь материал использован
    - Или часть возвращена + брак учтен
    """
    try:
        # Получаем запись материала
        lot_material = db.query(LotMaterialDB).filter(LotMaterialDB.id == id).first()
        if not lot_material:
            raise HTTPException(status_code=404, detail="Материал не найден")
        
        # Проверка: уже закрыт?
        if lot_material.closed_at:
            raise HTTPException(status_code=400, detail="Материал уже закрыт")
        
        # Обновляем данные
        lot_material.defect_bars = request.defect_bars
        if request.notes:
            lot_material.notes = (lot_material.notes or "") + f"\n[Закрытие] {request.notes}"
        lot_material.closed_at = datetime.now(timezone.utc)
        lot_material.closed_by = request.closed_by
        
        db.commit()
        db.refresh(lot_material)
        
        # Получаем связанные данные для ответа
        lot = db.query(LotDB).filter(LotDB.id == lot_material.lot_id).first()
        machine = db.query(MachineDB).filter(MachineDB.id == lot_material.machine_id).first() if lot_material.machine_id else None
        drawing_number = None
        lot_status = None
        if lot:
            lot_status = lot.status
            if lot.part_id:
                part = db.query(PartDB).filter(PartDB.id == lot.part_id).first()
                if part:
                    drawing_number = part.drawing_number
        
        # Получаем статус последней наладки
        setup_status = None
        if lot_material.lot_id and lot_material.machine_id:
            last_setup = (
                db.query(SetupDB.status)
                .filter(SetupDB.lot_id == lot_material.lot_id)
                .filter(SetupDB.machine_id == lot_material.machine_id)
                .order_by(SetupDB.created_at.desc())
                .first()
            )
            if last_setup:
                setup_status = last_setup[0]
        
        return {
            "id": lot_material.id,
            "lot_id": lot_material.lot_id,
            "lot_number": lot.lot_number if lot else None,
            "machine_id": lot_material.machine_id,
            "machine_name": machine.name if machine else None,
            "drawing_number": drawing_number,
            "material_type": lot_material.material_type,
            "diameter": lot_material.diameter,
            "issued_bars": lot_material.issued_bars or 0,
            "returned_bars": lot_material.returned_bars or 0,
            "defect_bars": lot_material.defect_bars or 0,
            "used_bars": (lot_material.issued_bars or 0) - (lot_material.returned_bars or 0) - (lot_material.defect_bars or 0),
            "issued_at": lot_material.issued_at,
            "status": lot_material.status,
            "notes": lot_material.notes,
            "closed_at": lot_material.closed_at,
            "closed_by": lot_material.closed_by,
            "created_at": lot_material.created_at,
            "lot_status": lot_status,
            "setup_status": setup_status
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error closing material: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка при закрытии материала: {str(e)}")


@router.get("/check-pending/{machine_id}", response_model=List[LotMaterialOut])
def check_pending_materials(
    machine_id: int,
    show_all: bool = Query(False, description="Для админа: показать все незакрытые (не только последнюю)"),
    db: Session = Depends(get_db_session)
):
    """
    Проверить незакрытые материалы на станке для предупреждения
    
    Возвращает материалы где:
    - setup.status = 'completed' (наладка завершена)
    - lot.status IN ('in_production', 'post_production') (лот активен)
    - closed_at IS NULL (материал не закрыт)
    - Свежие (за последние 7 дней)
    
    По умолчанию: только последняя
    Для админа (show_all=true): все незакрытые
    """
    try:
        # Подзапрос для получения последней наладки по каждому лоту
        latest_setup_subq = (
            db.query(
                SetupDB.lot_id,
                SetupDB.machine_id,
                SetupDB.status.label('setup_status'),
                SetupDB.end_time,
                func.row_number().over(
                    partition_by=[SetupDB.lot_id, SetupDB.machine_id],
                    order_by=SetupDB.created_at.desc()
                ).label('rn')
            )
            .filter(SetupDB.status == 'completed')
            .filter(SetupDB.end_time >= datetime.now(timezone.utc) - timedelta(days=7))
            .subquery()
        )
        
        query = (
            db.query(
                LotMaterialDB,
                LotDB.lot_number,
                LotDB.status.label('lot_status'),
                MachineDB.name.label('machine_name'),
                PartDB.drawing_number,
                latest_setup_subq.c.setup_status,
                latest_setup_subq.c.end_time
            )
            .outerjoin(LotDB, LotMaterialDB.lot_id == LotDB.id)
            .outerjoin(MachineDB, LotMaterialDB.machine_id == MachineDB.id)
            .outerjoin(PartDB, LotDB.part_id == PartDB.id)
            .outerjoin(
                latest_setup_subq,
                and_(
                    LotMaterialDB.lot_id == latest_setup_subq.c.lot_id,
                    LotMaterialDB.machine_id == latest_setup_subq.c.machine_id,
                    latest_setup_subq.c.rn == 1
                )
            )
            .filter(LotMaterialDB.machine_id == machine_id)
            .filter(LotMaterialDB.closed_at == None)
            .filter(latest_setup_subq.c.setup_status == 'completed')
            .filter(LotDB.status.in_(['in_production', 'post_production']))
            .order_by(latest_setup_subq.c.end_time.desc())
        )
        
        # Для админа показываем все, иначе только последнюю
        if not show_all:
            query = query.limit(1)
        
        results = query.all()
        
        # Формируем результат
        return [
            {
                "id": m.id,
                "lot_id": m.lot_id,
                "lot_number": lot_number,
                "machine_id": m.machine_id,
                "machine_name": machine_name,
                "drawing_number": drawing_number,
                "material_type": m.material_type,
                "diameter": m.diameter,
                "issued_bars": m.issued_bars or 0,
                "returned_bars": m.returned_bars or 0,
                "defect_bars": m.defect_bars or 0,
                "used_bars": (m.issued_bars or 0) - (m.returned_bars or 0) - (m.defect_bars or 0),
                "issued_at": m.issued_at,
                "status": m.status,
                "notes": m.notes,
                "closed_at": m.closed_at,
                "closed_by": m.closed_by,
                "created_at": m.created_at,
                "lot_status": lot_status,
                "setup_status": setup_status
            }
            for m, lot_number, lot_status, machine_name, drawing_number, setup_status, end_time in results
        ]
    except Exception as e:
        logger.error(f"Error checking pending materials: {e}", exc_info=True)
        return []


@router.get("/lot-materials", response_model=List[LotMaterialOut])
def get_lot_materials(
    lot_id: Optional[int] = Query(None, description="ID лота"),
    machine_id: Optional[int] = Query(None, description="ID станка"),
    status: Optional[str] = Query(None, description="Статус (pending/issued/partially_returned/completed/returned)"),
    db: Session = Depends(get_db_session)
):
    """Получить материалы по лоту, станку или статусу (ОПТИМИЗИРОВАНО - один SQL запрос)"""
    try:
        # ОПТИМИЗАЦИЯ: Используем LEFT JOIN LATERAL для получения статуса наладки
        # вместо N+1 запросов в цикле Python
        
        from sqlalchemy import func, literal_column
        from sqlalchemy.orm import aliased
        
        # Подзапрос: последняя наладка для каждой пары (lot_id, machine_id)
        latest_setup_subq = (
            db.query(
                SetupDB.lot_id,
                SetupDB.machine_id,
                SetupDB.status.label('setup_status'),
                func.row_number().over(
                    partition_by=[SetupDB.lot_id, SetupDB.machine_id],
                    order_by=SetupDB.created_at.desc()
                ).label('rn')
            )
            .subquery()
        )
        
        # Основной запрос с JOIN к подзапросу
        query = (
            db.query(
                LotMaterialDB,
                LotDB.lot_number,
                LotDB.status.label('lot_status'),
                MachineDB.name.label('machine_name'),
                PartDB.drawing_number,
                latest_setup_subq.c.setup_status
            )
            .outerjoin(LotDB, LotMaterialDB.lot_id == LotDB.id)
            .outerjoin(MachineDB, LotMaterialDB.machine_id == MachineDB.id)
            .outerjoin(PartDB, LotDB.part_id == PartDB.id)
            .outerjoin(
                latest_setup_subq,
                (latest_setup_subq.c.lot_id == LotMaterialDB.lot_id) &
                (latest_setup_subq.c.machine_id == LotMaterialDB.machine_id) &
                (latest_setup_subq.c.rn == 1)
            )
        )
        
        # Применяем фильтры
        if lot_id:
            query = query.filter(LotMaterialDB.lot_id == lot_id)
        if machine_id:
            query = query.filter(LotMaterialDB.machine_id == machine_id)
        if status:
            query = query.filter(LotMaterialDB.status == status)
        
        # Выполняем запрос (ОДИН запрос вместо N+1!)
        results = query.order_by(LotMaterialDB.created_at.desc()).all()
        
        # Формируем результат
        output = []
        for m, lot_number, lot_status, machine_name, drawing_number, setup_status in results:
            output.append({
                "id": m.id,
                "lot_id": m.lot_id,
                "lot_number": lot_number,
                "machine_id": m.machine_id,
                "machine_name": machine_name,
                "drawing_number": drawing_number,
                "material_type": m.material_type,
                "diameter": m.diameter,
                "issued_bars": m.issued_bars or 0,
                "returned_bars": m.returned_bars or 0,
                "defect_bars": m.defect_bars or 0,
                "used_bars": (m.issued_bars or 0) - (m.returned_bars or 0) - (m.defect_bars or 0),
                "issued_at": m.issued_at,
                "status": m.status,
                "notes": m.notes,
                "closed_at": m.closed_at,
                "closed_by": m.closed_by,
                "created_at": m.created_at,
                "lot_status": lot_status,
                "setup_status": setup_status
            })
        
        return output
    except Exception as e:
        logger.error(f"Error fetching lot materials: {e}", exc_info=True)
        return []


@router.get("/lot-materials/{id}", response_model=LotMaterialDetailOut)
def get_lot_material_detail(
    id: int,
    db: Session = Depends(get_db_session)
):
    """Получить детальную информацию о выдаче материала с историей операций"""
    try:
        lot_material = db.query(LotMaterialDB).filter(LotMaterialDB.id == id).first()
        if not lot_material:
            raise HTTPException(status_code=404, detail=f"Запись материала {id} не найдена")
        
        lot = db.query(LotDB).filter(LotDB.id == lot_material.lot_id).first()
        machine = db.query(MachineDB).filter(MachineDB.id == lot_material.machine_id).first() if lot_material.machine_id else None
        
        # Получаем drawing_number
        drawing_number = None
        if lot and lot.part_id:
            part = db.query(PartDB).filter(PartDB.id == lot.part_id).first()
            if part:
                drawing_number = part.drawing_number
        
        # Получаем историю операций
        operations = db.query(MaterialOperationDB).filter(
            MaterialOperationDB.lot_material_id == id
        ).order_by(MaterialOperationDB.performed_at.desc()).all()
        
        operations_out = []
        for op in operations:
            performer = db.query(EmployeeDB).filter(EmployeeDB.id == op.performed_by).first() if op.performed_by else None
            operations_out.append({
                "id": op.id,
                "lot_material_id": op.lot_material_id,
                "operation_type": op.operation_type,
                "quantity_bars": op.quantity_bars,
                "diameter": op.diameter,
                "performed_by": op.performed_by,
                "performer_name": performer.full_name if performer else None,
                "performed_at": op.performed_at,
                "notes": op.notes,
                "created_at": op.created_at
            })
        
        return {
            "id": lot_material.id,
            "lot_id": lot_material.lot_id,
            "lot_number": lot.lot_number if lot else None,
            "machine_id": lot_material.machine_id,
            "machine_name": machine.name if machine else None,
            "drawing_number": drawing_number,
            "material_type": lot_material.material_type,
            "diameter": lot_material.diameter,
            "issued_bars": lot_material.issued_bars or 0,
            "returned_bars": lot_material.returned_bars or 0,
            "used_bars": (lot_material.issued_bars or 0) - (lot_material.returned_bars or 0),
            "issued_at": lot_material.issued_at,
            "status": lot_material.status,
            "notes": lot_material.notes,
            "created_at": lot_material.created_at,
            "operations": operations_out
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching lot material detail: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка: {str(e)}")


@router.get("/history", response_model=List[MaterialOperationOut])
def get_material_history(
    lot_id: Optional[int] = Query(None, description="ID лота"),
    machine_id: Optional[int] = Query(None, description="ID станка"),
    operation_type: Optional[str] = Query(None, description="Тип операции (issue/add/return/correction)"),
    limit: int = Query(100, description="Лимит записей"),
    db: Session = Depends(get_db_session)
):
    """Получить историю операций с материалом"""
    try:
        query = db.query(MaterialOperationDB).join(LotMaterialDB)
        
        if lot_id:
            query = query.filter(LotMaterialDB.lot_id == lot_id)
        if machine_id:
            query = query.filter(LotMaterialDB.machine_id == machine_id)
        if operation_type:
            query = query.filter(MaterialOperationDB.operation_type == operation_type)
        
        operations = query.order_by(MaterialOperationDB.performed_at.desc()).limit(limit).all()
        
        result = []
        for op in operations:
            performer = db.query(EmployeeDB).filter(EmployeeDB.id == op.performed_by).first() if op.performed_by else None
            result.append({
                "id": op.id,
                "lot_material_id": op.lot_material_id,
                "operation_type": op.operation_type,
                "quantity_bars": op.quantity_bars,
                "diameter": op.diameter,
                "performed_by": op.performed_by,
                "performer_name": performer.full_name if performer else None,
                "performed_at": op.performed_at,
                "notes": op.notes,
                "created_at": op.created_at
            })
        
        return result
    except Exception as e:
        logger.error(f"Error fetching material history: {e}", exc_info=True)
        return []
