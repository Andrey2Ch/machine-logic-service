"""
@file: machine-logic-service/src/routers/materials.py
@description: Роутер для обработки API-запросов, связанных с управлением материалами (сырьём).
@dependencies: fastapi, sqlalchemy, pydantic
@created: 2025-11-30
@updated: 2025-12-01 - Добавлены endpoints для управления материалом (add-bars, return, history)
"""
import logging
import os
import httpx
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func, text, tuple_
from sqlalchemy.exc import IntegrityError
from src.database import get_db_session
from typing import List, Optional
from pydantic import BaseModel
import math
from src.models.models import (
    MaterialTypeDB, 
    LotMaterialDB, 
    LotDB, 
    MachineDB, 
    EmployeeDB,
    PartDB,
    MaterialOperationDB,
    SetupDB,
    WarehouseMovementDB,
    InventoryPositionDB,
    MaterialSubgroupDB,
)
from src.services.notification_service import send_material_low_notification
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
SYSTEM_NOLOT_PREFIX = "NOLOT-M"
SYSTEM_NOLOT_PART_DWG = "__SYSTEM_NOLOT_PART__"

router = APIRouter(
    prefix="/materials",
    tags=["Materials"],
    responses={404: {"description": "Not found"}},
)

# Дефолтные параметры расчета материала (fallback)
DEFAULT_BAR_LENGTH_MM = 3000.0
DEFAULT_BLADE_WIDTH_MM = 3.0
DEFAULT_FACING_ALLOWANCE_MM = 0.5
DEFAULT_MIN_REMAINDER_MM = 300.0


def _normalize_profile_type(value: Optional[str]) -> str:
    normalized = (value or "").strip().lower()
    if normalized in ("hex", "hexagon"):
        return "hexagon"
    if normalized == "square":
        return "square"
    return "round"


def _resolve_calc_params(
    *,
    machine: Optional[MachineDB],
    request: Optional[object],
    lot_material: Optional[LotMaterialDB]
) -> dict:
    return {
        "bar_length_mm": (
            (request.bar_length_mm if request else None)
            or (lot_material.bar_length_mm if lot_material else None)
        ),
        "blade_width_mm": (
            (request.blade_width_mm if request else None)
            or (lot_material.blade_width_mm if lot_material else None)
            or (machine.material_blade_width_mm if machine else None)
            or DEFAULT_BLADE_WIDTH_MM
        ),
        "facing_allowance_mm": (
            (request.facing_allowance_mm if request else None)
            or (lot_material.facing_allowance_mm if lot_material else None)
            or (machine.material_facing_allowance_mm if machine else None)
            or DEFAULT_FACING_ALLOWANCE_MM
        ),
        "min_remainder_mm": (
            (request.min_remainder_mm if request else None)
            or (lot_material.min_remainder_mm if lot_material else None)
            or (machine.material_min_remainder_mm if machine else None)
            or DEFAULT_MIN_REMAINDER_MM
        ),
    }


def _calculate_bars_needed(
    *,
    part_length_mm: Optional[float],
    quantity_parts: int,
    bar_length_mm: Optional[float],
    blade_width_mm: float,
    facing_allowance_mm: float,
    min_remainder_mm: float
) -> Optional[int]:
    if not part_length_mm or not bar_length_mm or quantity_parts <= 0:
        return None
    usable_length = bar_length_mm - min_remainder_mm
    length_per_part = part_length_mm + facing_allowance_mm + blade_width_mm
    if usable_length <= 0 or length_per_part <= 0:
        return None
    parts_per_bar = math.floor(usable_length / length_per_part)
    if parts_per_bar <= 0:
        return None
    return int(math.ceil(quantity_parts / parts_per_bar))


def _normalize_machine_name(name: Optional[str]) -> str:
    if not name:
        return ""
    normalized = name
    if normalized.startswith('M_') and '_' in normalized[2:]:
        parts = normalized.split('_', 2)
        if len(parts) >= 3:
            normalized = parts[2]
    return normalized.replace('_', '-').upper()


def _get_source_batch_for_lot_material(
    *,
    db: Session,
    lot_material: LotMaterialDB
) -> Optional[WarehouseMovementDB]:
    """
    Находит исходную складскую выдачу (movement_type='issue') для этой записи lot_material.
    Используем последнюю выдачу по паре lot + machine как источник batch/location для возврата.
    """
    # Приоритет: явная привязка к исходной складской выдаче
    if lot_material.material_receipt_id:
        linked = (
            db.query(WarehouseMovementDB)
            .filter(WarehouseMovementDB.movement_id == lot_material.material_receipt_id)
            .first()
        )
        if linked and linked.movement_type == "issue":
            return linked

    if not lot_material.lot_id or not lot_material.machine_id:
        return None

    return (
        db.query(WarehouseMovementDB)
        .filter(WarehouseMovementDB.related_lot_id == lot_material.lot_id)
        .filter(WarehouseMovementDB.related_machine_id == lot_material.machine_id)
        .filter(WarehouseMovementDB.movement_type == "issue")
        .order_by(WarehouseMovementDB.performed_at.desc(), WarehouseMovementDB.movement_id.desc())
        .first()
    )


def _apply_warehouse_return_sync(
    *,
    db: Session,
    lot_material: LotMaterialDB,
    quantity_bars: int,
    notes: Optional[str]
) -> None:
    """
    Синхронизирует возврат в складском контуре:
    1) пишет warehouse_movements (return),
    2) увеличивает inventory_positions для исходной batch/location.
    """
    if quantity_bars <= 0:
        return

    source_issue = _get_source_batch_for_lot_material(db=db, lot_material=lot_material)
    if not source_issue:
        logger.info(
            "Skip warehouse return sync: source issue movement not found for lot_material_id=%s",
            lot_material.id,
        )
        return

    target_location = source_issue.from_location or source_issue.to_location
    if not target_location:
        logger.warning(
            "Skip warehouse return sync: no target location in source issue movement_id=%s (lot_material_id=%s)",
            source_issue.movement_id,
            lot_material.id,
        )
        return

    movement_note = "Auto return sync from materials/lot-materials"
    if notes:
        movement_note = f"{movement_note}. {notes}"

    movement = WarehouseMovementDB(
        batch_id=source_issue.batch_id,
        movement_type="return",
        quantity=quantity_bars,
        from_location=None,
        to_location=target_location,
        related_lot_id=lot_material.lot_id,
        related_machine_id=lot_material.machine_id,
        performed_by=lot_material.returned_by,
        notes=movement_note[:2000],
    )
    db.add(movement)

    position = (
        db.query(InventoryPositionDB)
        .filter(InventoryPositionDB.batch_id == source_issue.batch_id)
        .filter(InventoryPositionDB.location_code == target_location)
        .first()
    )
    if position:
        position.quantity = int(position.quantity or 0) + int(quantity_bars)
        position.updated_at = datetime.now(timezone.utc)
    else:
        position = InventoryPositionDB(
            batch_id=source_issue.batch_id,
            location_code=target_location,
            quantity=int(quantity_bars),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(position)


def _fetch_mtconnect_counts() -> dict:
    mtconnect_api_url = os.getenv('MTCONNECT_API_URL', 'https://mtconnect-core-production.up.railway.app')
    counts: dict[str, Optional[int]] = {}
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(f"{mtconnect_api_url}/api/machines")
            if response.status_code != 200:
                return counts
            data = response.json()
            all_machines = []
            if data.get('machines', {}).get('mtconnect'):
                all_machines.extend(data['machines']['mtconnect'])
            if data.get('machines', {}).get('adam'):
                all_machines.extend(data['machines']['adam'])
            for m in all_machines:
                name = _normalize_machine_name(m.get('name', ''))
                counts[name] = m.get('data', {}).get('displayPartCount')
    except Exception as e:
        logger.warning(f"MTConnect API unavailable: {e}")
    return counts


def _get_produced_for_lot(
    *,
    db: Session,
    lot_id: int,
    fallback_machine_name: Optional[str],
    mtconnect_counts: dict
) -> Optional[int]:
    setup_query = text("""
        SELECT sj.id as setup_job_id, m.name as machine_name
        FROM setup_jobs sj
        JOIN machines m ON m.id = sj.machine_id
        WHERE sj.lot_id = :lot_id
          AND sj.end_time IS NULL
        ORDER BY sj.id DESC
        LIMIT 1
    """)
    setup_result = db.execute(setup_query, {"lot_id": lot_id}).fetchone()
    machine_name = setup_result.machine_name if setup_result else fallback_machine_name
    setup_job_id = setup_result.setup_job_id if setup_result else None

    produced = None
    if machine_name:
        normalized = _normalize_machine_name(machine_name)
        produced = mtconnect_counts.get(normalized)

    if produced is None:
        return None

    return int(produced)


def _get_cycle_time_seconds(
    *,
    db: Session,
    lot_id: int,
    machine_id: Optional[int],
    part_id: Optional[int]
) -> Optional[int]:
    cycle_time = None
    if machine_id:
        setup = db.query(SetupDB.cycle_time).filter(
            SetupDB.lot_id == lot_id,
            SetupDB.machine_id == machine_id,
            SetupDB.end_time == None
        ).order_by(SetupDB.id.desc()).first()
        if setup and setup[0]:
            cycle_time = setup[0]
    if not cycle_time and part_id:
        part = db.query(PartDB.avg_cycle_time).filter(PartDB.id == part_id).first()
        if part and part[0]:
            cycle_time = part[0]
    return int(cycle_time) if cycle_time else None


def _calculate_hours_by_material(
    *,
    net_issued_bars: int,
    part_length_mm: Optional[float],
    bar_length_mm: Optional[float],
    blade_width_mm: float,
    facing_allowance_mm: float,
    min_remainder_mm: float,
    cycle_time_sec: Optional[int],
    produced_parts: int = 0
) -> Optional[float]:
    if not bar_length_mm or not part_length_mm or not cycle_time_sec or net_issued_bars <= 0:
        return None
    usable_length = bar_length_mm - min_remainder_mm
    length_per_part = part_length_mm + facing_allowance_mm + blade_width_mm
    if usable_length <= 0 or length_per_part <= 0:
        return None
    parts_per_bar = math.floor(usable_length / length_per_part)
    if parts_per_bar <= 0:
        return None
    remaining_parts_by_material = max(0, (net_issued_bars * parts_per_bar) - produced_parts)
    hours = (remaining_parts_by_material * cycle_time_sec) / 3600.0
    return round(hours, 2)

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
    lot_id: Optional[int] = None
    drawing_number: Optional[str] = None  # מס' שרטוט
    material_type: Optional[str] = None  # סוג חומר - теперь необязательный
    material_group_id: Optional[int] = None
    material_subgroup_id: Optional[int] = None
    shape: Optional[str] = "round"
    diameter: float  # диаметр
    quantity_bars: int  # כמות במוטות
    bar_length_mm: Optional[float] = None
    blade_width_mm: Optional[float] = None
    facing_allowance_mm: Optional[float] = None
    min_remainder_mm: Optional[float] = None
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

class LinkSourceMovementRequest(BaseModel):
    movement_id: int

class MaterialOperationOut(BaseModel):
    id: int
    lot_material_id: int
    operation_type: str
    quantity_bars: int
    shape: Optional[str] = None
    diameter: Optional[float] = None
    bar_length_mm: Optional[float] = None
    blade_width_mm: Optional[float] = None
    facing_allowance_mm: Optional[float] = None
    min_remainder_mm: Optional[float] = None
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
    material_group_id: Optional[int] = None
    material_subgroup_id: Optional[int] = None
    source_movement_id: Optional[int] = None
    source_batch_id: Optional[str] = None
    shape: Optional[str] = None
    diameter: Optional[float] = None
    bar_length_mm: Optional[float] = None
    blade_width_mm: Optional[float] = None
    facing_allowance_mm: Optional[float] = None
    min_remainder_mm: Optional[float] = None
    issued_bars: int
    returned_bars: int
    defect_bars: int = 0
    used_bars: int  # issued - returned - defect
    remaining_bars: Optional[int] = None  # Остаток к выдаче (если можно рассчитать)
    planned_bars_remaining: Optional[int] = None  # План прутков для завершения
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
        part = None
        now = datetime.now(timezone.utc)
        request_shape = _normalize_profile_type(request.shape)

        # Проверяем станок
        machine = db.query(MachineDB).filter(MachineDB.id == request.machine_id).first()
        if not machine:
            raise HTTPException(status_code=404, detail=f"Станок {request.machine_id} не найден")

        # Переходный период: lot_id может не передаваться с фронта.
        # Тогда автоматически подбираем лот для выбранного станка:
        # 1) активный лот, назначенный на станок (kanban)
        # 2) последний setup по станку
        # 3) последняя незакрытая выдача материала по станку
        target_lot_id: Optional[int] = request.lot_id
        if target_lot_id is None:
            statuses_priority = ("in_production", "assigned", "new")
            target_lot = None
            for status_code in statuses_priority:
                target_lot = (
                    db.query(LotDB)
                    .filter(
                        LotDB.status == status_code,
                        LotDB.assigned_machine_id == request.machine_id,
                    )
                    .order_by(LotDB.created_at.desc())
                    .first()
                )
                if target_lot:
                    break

            if target_lot:
                target_lot_id = target_lot.id
            else:
                # Fallback: последний setup, где указан lot_id
                recent_setup = (
                    db.query(SetupDB)
                    .filter(
                        SetupDB.machine_id == request.machine_id,
                        SetupDB.lot_id.isnot(None),
                    )
                    .order_by(SetupDB.created_at.desc())
                    .first()
                )
                if recent_setup and recent_setup.lot_id:
                    target_lot_id = int(recent_setup.lot_id)

            if target_lot_id is None:
                # Fallback: последняя незакрытая выдача материала на станке
                recent_lot_material = (
                    db.query(LotMaterialDB)
                    .filter(
                        LotMaterialDB.machine_id == request.machine_id,
                        LotMaterialDB.lot_id.isnot(None),
                        LotMaterialDB.closed_at.is_(None),
                    )
                    .order_by(LotMaterialDB.created_at.desc())
                    .first()
                )
                if recent_lot_material and recent_lot_material.lot_id:
                    target_lot_id = int(recent_lot_material.lot_id)

            if target_lot_id is None:
                # Переходный режим для участков без привязки к лотам:
                # создаем/переиспользуем системный "технический" лот на станок.
                system_part = (
                    db.query(PartDB)
                    .filter(PartDB.drawing_number == SYSTEM_NOLOT_PART_DWG)
                    .first()
                )
                if not system_part:
                    system_part = PartDB(
                        drawing_number=SYSTEM_NOLOT_PART_DWG,
                        material="SYSTEM_NOLOT",
                    )
                    db.add(system_part)
                    db.flush()
                    logger.info("Created system part for no-lot flow: %s", SYSTEM_NOLOT_PART_DWG)

                system_lot_number = f"{SYSTEM_NOLOT_PREFIX}{request.machine_id}"
                system_lot = (
                    db.query(LotDB)
                    .filter(LotDB.lot_number == system_lot_number)
                    .order_by(LotDB.created_at.desc())
                    .first()
                )
                if not system_lot:
                    system_lot = LotDB(
                        lot_number=system_lot_number,
                        part_id=int(system_part.id),
                        status="in_production",
                        assigned_machine_id=request.machine_id,
                    )
                    db.add(system_lot)
                    db.flush()
                    logger.info(
                        "Created fallback system lot %s for machine_id=%s",
                        system_lot_number,
                        request.machine_id,
                    )
                elif system_lot.part_id is None:
                    # Бэкфилл старых тех-лотов, созданных до фикса.
                    system_lot.part_id = int(system_part.id)
                    db.flush()
                target_lot_id = int(system_lot.id)

        # Проверяем существование лота
        lot = db.query(LotDB).filter(LotDB.id == target_lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail=f"Лот {target_lot_id} не найден")
        
        # Проверяем диаметр по допускам станка (если заданы)
        if machine.min_diameter is not None and request.diameter < machine.min_diameter:
            raise HTTPException(
                status_code=400,
                detail=f"Диаметр {request.diameter}мм меньше минимального для станка ({machine.min_diameter}мм)"
            )
        if machine.max_diameter is not None and request.diameter > machine.max_diameter:
            raise HTTPException(
                status_code=400,
                detail=f"Диаметр {request.diameter}мм больше максимального для станка ({machine.max_diameter}мм)"
            )

        # Минимальная длина прутка (защита от мусорных значений вида 2мм)
        if request.bar_length_mm is not None and request.bar_length_mm < 500:
            raise HTTPException(
                status_code=400,
                detail="Длина прутка должна быть не меньше 500 мм"
            )
        
        # Получаем drawing_number из лота если не передан
        drawing_number = request.drawing_number
        if not drawing_number and lot.part_id:
            part = db.query(PartDB).filter(PartDB.id == lot.part_id).first()
            if part:
                drawing_number = part.drawing_number
        
        def _apply_issue_update(existing: LotMaterialDB) -> tuple[LotMaterialDB, dict, str]:
            """
            Применяет логику "выдачи" к существующей записи.
            Возвращает (lot_material, calc_params, operation_type).
            """
            calc_params_local = _resolve_calc_params(machine=machine, request=request, lot_material=existing)

            # Если запись была закрыта, переоткрываем (уникальный индекс не позволяет создать новую).
            if existing.closed_at is not None:
                existing.closed_at = None
                existing.closed_by = None
                existing.status = "issued"

            if existing.issued_at is None:
                existing.issued_at = now

            # Добавляем к существующей записи (used_bars вычисляется автоматически в PostgreSQL)
            existing.issued_bars = (existing.issued_bars or 0) + request.quantity_bars

            if request.notes:
                existing.notes = f"{existing.notes or ''}\n{request.notes}".strip()

            if request.bar_length_mm is not None and existing.bar_length_mm is None:
                existing.bar_length_mm = request.bar_length_mm

            if request.material_group_id is not None and existing.material_group_id is None:
                existing.material_group_id = request.material_group_id

            if request.material_subgroup_id is not None and existing.material_subgroup_id is None:
                existing.material_subgroup_id = request.material_subgroup_id

            if not existing.shape:
                existing.shape = request_shape

            if existing.blade_width_mm is None:
                existing.blade_width_mm = calc_params_local["blade_width_mm"]

            if existing.facing_allowance_mm is None:
                existing.facing_allowance_mm = calc_params_local["facing_allowance_mm"]

            if existing.min_remainder_mm is None:
                existing.min_remainder_mm = calc_params_local["min_remainder_mm"]

            return existing, calc_params_local, "add"

        # Ищем существующую запись по паре lot+machine+diameter+shape
        existing = (
            db.query(LotMaterialDB)
            .filter(
                LotMaterialDB.lot_id == target_lot_id,
                LotMaterialDB.machine_id == request.machine_id,
                LotMaterialDB.diameter == request.diameter,
                func.coalesce(LotMaterialDB.shape, "round") == request_shape,
            )
            .first()
        )

        created_new = False
        if existing:
            lot_material, calc_params, operation_type = _apply_issue_update(existing)
        else:
            calc_params = _resolve_calc_params(machine=machine, request=request, lot_material=None)
            # Создаём новую запись (НЕ включаем used_bars - это generated column в PostgreSQL!)
            lot_material = LotMaterialDB(
                lot_id=target_lot_id,
                machine_id=request.machine_id,
                material_type=request.material_type,
                material_group_id=request.material_group_id,
                material_subgroup_id=request.material_subgroup_id,
                shape=request_shape,
                diameter=request.diameter,
                bar_length_mm=calc_params["bar_length_mm"],
                blade_width_mm=calc_params["blade_width_mm"],
                facing_allowance_mm=calc_params["facing_allowance_mm"],
                min_remainder_mm=calc_params["min_remainder_mm"],
                issued_bars=request.quantity_bars,
                returned_bars=0,
                issued_at=now,
                status="issued",
                notes=request.notes,
            )
            db.add(lot_material)
            operation_type = "issue"
            created_new = True
        
        # Обновляем статус материала в лоте
        lot.material_status = "issued"
        
        # 🎯 ВАЖНО: Записываем фактический диаметр из материала в лот!
        # Кладовщик измеряет реальный диаметр при выдаче - это приоритетнее теоретического
        # Обновляем ВСЕГДА (даже если был заполнен при создании лота)
        if request.diameter:
            lot.actual_diameter = request.diameter
            logger.info(f"Updated lot {lot.id} actual_diameter to {request.diameter} from warehouse issue")
        lot.actual_profile_type = request_shape

        # Получаем ID для lot_material. Если запросов одновременно два (или мы не нашли existing по старой логике),
        # INSERT может упасть по уникальному индексу — тогда откатываем и обновляем существующую запись.
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            existing_after = (
                db.query(LotMaterialDB)
                .filter(
                    LotMaterialDB.lot_id == target_lot_id,
                    LotMaterialDB.machine_id == request.machine_id,
                    LotMaterialDB.diameter == request.diameter,
                    func.coalesce(LotMaterialDB.shape, "round") == request_shape,
                )
                .first()
            )
            if not existing_after:
                raise
            # Повторяем обновление уже существующей записи
            lot = db.query(LotDB).filter(LotDB.id == target_lot_id).first()
            machine = db.query(MachineDB).filter(MachineDB.id == request.machine_id).first()
            lot_material, calc_params, operation_type = _apply_issue_update(existing_after)
            lot.material_status = "issued"
            if request.diameter:
                lot.actual_diameter = request.diameter
            lot.actual_profile_type = request_shape
            created_new = False
            db.flush()
        
        # Записываем операцию в историю
        operation = MaterialOperationDB(
            lot_material_id=lot_material.id,
            operation_type=operation_type,
            quantity_bars=request.quantity_bars,
            shape=request_shape,
            diameter=request.diameter,
            bar_length_mm=calc_params["bar_length_mm"],
            blade_width_mm=calc_params["blade_width_mm"],
            facing_allowance_mm=calc_params["facing_allowance_mm"],
            min_remainder_mm=calc_params["min_remainder_mm"],
            notes=request.notes,
            performed_at=now
        )
        db.add(operation)
        
        db.commit()
        db.refresh(lot_material)

        # Auto-bind: if the part has no material catalog link, inherit from the issue request
        try:
            if part and not part.material_group_id and (request.material_group_id or request.material_subgroup_id):
                if request.material_group_id:
                    part.material_group_id = request.material_group_id
                if request.material_subgroup_id:
                    part.material_subgroup_id = request.material_subgroup_id
                    if not request.material_group_id:
                        sg = db.query(MaterialSubgroupDB).filter(MaterialSubgroupDB.id == request.material_subgroup_id).first()
                        if sg:
                            part.material_group_id = sg.group_id
                db.commit()
                logger.info("Auto-bound material group=%s subgroup=%s to part_id=%s", part.material_group_id, part.material_subgroup_id, part.id)
        except Exception as e:
            logger.warning("Auto-bind material to part failed (non-critical): %s", e)

        # Если есть данные для расчета — проверяем, хватит ли материала на 12 часов
        try:
            part_length_mm = part.part_length if part else None
            cycle_time_sec = _get_cycle_time_seconds(
                db=db,
                lot_id=lot_material.lot_id,
                machine_id=lot_material.machine_id,
                part_id=lot.part_id if lot else None
            )
            net_issued = (lot_material.issued_bars or 0) - (lot_material.returned_bars or 0) - (lot_material.defect_bars or 0)
            produced = _get_produced_for_lot(
                db=db,
                lot_id=lot_material.lot_id,
                fallback_machine_name=machine.name if machine else None,
                mtconnect_counts=_fetch_mtconnect_counts()
            )
            if produced is None:
                hours = None
            else:
                hours = _calculate_hours_by_material(
                    net_issued_bars=net_issued,
                    part_length_mm=part_length_mm,
                    bar_length_mm=lot_material.bar_length_mm,
                    blade_width_mm=lot_material.blade_width_mm or (machine.material_blade_width_mm if machine else None) or DEFAULT_BLADE_WIDTH_MM,
                    facing_allowance_mm=lot_material.facing_allowance_mm or (machine.material_facing_allowance_mm if machine else None) or DEFAULT_FACING_ALLOWANCE_MM,
                    min_remainder_mm=lot_material.min_remainder_mm or (machine.material_min_remainder_mm if machine else None) or DEFAULT_MIN_REMAINDER_MM,
                    cycle_time_sec=cycle_time_sec,
                    produced_parts=produced
                )
            if hours is not None and hours <= 12:
                try:
                    asyncio.run(send_material_low_notification(
                        db,
                        lot_material=lot_material,
                        machine_name=machine.name,
                        lot_number=lot.lot_number,
                        drawing_number=drawing_number or "—",
                        hours_remaining=hours,
                        net_issued_bars=net_issued,
                        bar_length_mm=lot_material.bar_length_mm
                    ))
                except Exception:
                    # fallback: if event loop already running or any error
                    try:
                        loop = asyncio.get_event_loop()
                        loop.create_task(send_material_low_notification(
                            db,
                            lot_material=lot_material,
                            machine_name=machine.name,
                            lot_number=lot.lot_number,
                            drawing_number=drawing_number or "—",
                            hours_remaining=hours,
                            net_issued_bars=net_issued,
                            bar_length_mm=lot_material.bar_length_mm
                        ))
                    except Exception as e:
                        logger.warning(f"Failed to schedule material low notification: {e}")
                lot_material.material_low_notified_at = datetime.now(timezone.utc)
                db.commit()
        except Exception as e:
            logger.warning(f"Material hours check failed: {e}")
        
        return {
            "id": lot_material.id,
            "lot_id": lot_material.lot_id,
            "lot_number": lot.lot_number,
            "machine_id": lot_material.machine_id,
            "machine_name": machine.name,
            "drawing_number": drawing_number,
            "material_type": lot_material.material_type,
            "shape": lot_material.shape,
            "diameter": lot_material.diameter,
            "bar_length_mm": lot_material.bar_length_mm,
            "blade_width_mm": lot_material.blade_width_mm,
            "facing_allowance_mm": lot_material.facing_allowance_mm,
            "min_remainder_mm": lot_material.min_remainder_mm,
            "issued_bars": lot_material.issued_bars or 0,
            "returned_bars": lot_material.returned_bars or 0,
            "defect_bars": lot_material.defect_bars or 0,
            "used_bars": (lot_material.issued_bars or 0) - (lot_material.returned_bars or 0) - (lot_material.defect_bars or 0),
            "remaining_bars": None,
            "planned_bars_remaining": None,
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
            shape=lot_material.shape,
            diameter=lot_material.diameter,
            bar_length_mm=lot_material.bar_length_mm,
            blade_width_mm=lot_material.blade_width_mm,
            facing_allowance_mm=lot_material.facing_allowance_mm,
            min_remainder_mm=lot_material.min_remainder_mm,
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
            "shape": lot_material.shape,
            "diameter": lot_material.diameter,
            "bar_length_mm": lot_material.bar_length_mm,
            "blade_width_mm": lot_material.blade_width_mm,
            "facing_allowance_mm": lot_material.facing_allowance_mm,
            "min_remainder_mm": lot_material.min_remainder_mm,
            "issued_bars": lot_material.issued_bars or 0,
            "returned_bars": lot_material.returned_bars or 0,
            "defect_bars": lot_material.defect_bars or 0,
            "used_bars": (lot_material.issued_bars or 0) - (lot_material.returned_bars or 0) - (lot_material.defect_bars or 0),
            "remaining_bars": None,
            "planned_bars_remaining": None,
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
        max_returnable = max(0, (
            (lot_material.issued_bars or 0)
            - (lot_material.returned_bars or 0)
            - (lot_material.defect_bars or 0)
        ))
        if request.quantity_bars > max_returnable:
            raise HTTPException(
                status_code=400, 
                detail=f"Нельзя вернуть {request.quantity_bars} прутков. Максимум: {max_returnable}"
            )
        
        # Обновляем количество (used_bars вычисляется автоматически в PostgreSQL)
        lot_material.returned_bars = (lot_material.returned_bars or 0) + request.quantity_bars
        lot_material.returned_at = datetime.now(timezone.utc)
        lot_material.returned_by = request.performed_by

        # Синхронизация со складскими партиями (warehouse_materials):
        # возвращаем в исходную batch/location по последней issue-операции.
        _apply_warehouse_return_sync(
            db=db,
            lot_material=lot_material,
            quantity_bars=request.quantity_bars,
            notes=request.notes,
        )
        
        # Обновляем статус (вычисляем used_bars для проверки)
        calculated_used = (
            (lot_material.issued_bars or 0)
            - (lot_material.returned_bars or 0)
            - (lot_material.defect_bars or 0)
        )
        if calculated_used == 0:
            lot_material.status = "returned"
        elif lot_material.returned_bars > 0:
            lot_material.status = "partially_returned"
        
        # Записываем операцию (отрицательное количество для возврата)
        operation = MaterialOperationDB(
            lot_material_id=id,
            operation_type="return",
            quantity_bars=-request.quantity_bars,  # Отрицательное для возврата
            shape=lot_material.shape,
            diameter=lot_material.diameter,
            bar_length_mm=lot_material.bar_length_mm,
            blade_width_mm=lot_material.blade_width_mm,
            facing_allowance_mm=lot_material.facing_allowance_mm,
            min_remainder_mm=lot_material.min_remainder_mm,
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
            "shape": lot_material.shape,
            "diameter": lot_material.diameter,
            "bar_length_mm": lot_material.bar_length_mm,
            "blade_width_mm": lot_material.blade_width_mm,
            "facing_allowance_mm": lot_material.facing_allowance_mm,
            "min_remainder_mm": lot_material.min_remainder_mm,
            "issued_bars": lot_material.issued_bars or 0,
            "returned_bars": lot_material.returned_bars or 0,
            "defect_bars": lot_material.defect_bars or 0,
            "used_bars": (lot_material.issued_bars or 0) - (lot_material.returned_bars or 0) - (lot_material.defect_bars or 0),
            "remaining_bars": None,
            "planned_bars_remaining": None,
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
            "shape": lot_material.shape,
            "diameter": lot_material.diameter,
            "bar_length_mm": lot_material.bar_length_mm,
            "blade_width_mm": lot_material.blade_width_mm,
            "facing_allowance_mm": lot_material.facing_allowance_mm,
            "min_remainder_mm": lot_material.min_remainder_mm,
            "issued_bars": lot_material.issued_bars or 0,
            "returned_bars": lot_material.returned_bars or 0,
            "defect_bars": lot_material.defect_bars or 0,
            "used_bars": (lot_material.issued_bars or 0) - (lot_material.returned_bars or 0) - (lot_material.defect_bars or 0),
            "remaining_bars": None,
            "planned_bars_remaining": None,
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
                "shape": m.shape,
                "diameter": m.diameter,
                "bar_length_mm": m.bar_length_mm,
                "blade_width_mm": m.blade_width_mm,
                "facing_allowance_mm": m.facing_allowance_mm,
                "min_remainder_mm": m.min_remainder_mm,
                "issued_bars": m.issued_bars or 0,
                "returned_bars": m.returned_bars or 0,
                "defect_bars": m.defect_bars or 0,
                "used_bars": (m.issued_bars or 0) - (m.returned_bars or 0) - (m.defect_bars or 0),
                "remaining_bars": None,
                "planned_bars_remaining": None,
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
    status_group: Optional[str] = Query(None, description="active/pending/closed/all"),
    db: Session = Depends(get_db_session)
):
    """Получить материалы по лоту, станку или статусу (ОПТИМИЗИРОВАНО v2 - без ROW_NUMBER)"""
    import time
    t_start = time.time()
    
    try:
        from sqlalchemy import func
        
        # ШАГ 1: Получаем материалы БЕЗ статуса наладки (быстрый запрос)
        base_query = (
            db.query(
                LotMaterialDB,
                LotDB.lot_number,
                LotDB.status.label('lot_status'),
                LotDB.total_planned_quantity,
                LotDB.initial_planned_quantity,
                MachineDB.name.label('machine_name'),
                MachineDB.material_blade_width_mm,
                MachineDB.material_facing_allowance_mm,
                MachineDB.material_min_remainder_mm,
                PartDB.drawing_number,
                PartDB.part_length,
            )
            .outerjoin(LotDB, LotMaterialDB.lot_id == LotDB.id)
            .outerjoin(MachineDB, LotMaterialDB.machine_id == MachineDB.id)
            .outerjoin(PartDB, LotDB.part_id == PartDB.id)
        )
        
        # Применяем базовые фильтры (без status_group пока)
        if lot_id:
            base_query = base_query.filter(LotMaterialDB.lot_id == lot_id)
        if machine_id:
            base_query = base_query.filter(LotMaterialDB.machine_id == machine_id)
        if status:
            base_query = base_query.filter(LotMaterialDB.status == status)
        if status_group == "closed":
            base_query = base_query.filter(LotMaterialDB.closed_at != None)
        elif status_group in ("active", "pending"):
            base_query = base_query.filter(LotMaterialDB.closed_at == None)
        
        base_results = base_query.order_by(LotMaterialDB.created_at.desc()).all()
        t_base = time.time()
        logger.info(f"[lot-materials] base_query took {(t_base - t_start)*1000:.0f}ms, rows={len(base_results)}")
        
        if not base_results:
            return []
        
        # ШАГ 2: Собираем уникальные пары (lot_id, machine_id)
        pairs = set()
        for row in base_results:
            m = row[0]  # LotMaterialDB
            pairs.add((m.lot_id, m.machine_id))
        
        # ШАГ 3: Получаем последний статус наладки ТОЛЬКО для нужных пар
        # Используем подзапрос с MAX(created_at) - намного быстрее ROW_NUMBER
        setup_statuses = {}
        if pairs:
            # Подзапрос: максимальная дата для каждой пары
            max_dates_subq = (
                db.query(
                    SetupDB.lot_id,
                    SetupDB.machine_id,
                    func.max(SetupDB.created_at).label('max_created')
                )
                .filter(
                    tuple_(SetupDB.lot_id, SetupDB.machine_id).in_(list(pairs))
                )
                .group_by(SetupDB.lot_id, SetupDB.machine_id)
                .subquery()
            )
            
            # Основной запрос: получаем статус по максимальной дате
            setup_rows = (
                db.query(SetupDB.lot_id, SetupDB.machine_id, SetupDB.status)
                .join(
                    max_dates_subq,
                    (SetupDB.lot_id == max_dates_subq.c.lot_id) &
                    (SetupDB.machine_id == max_dates_subq.c.machine_id) &
                    (SetupDB.created_at == max_dates_subq.c.max_created)
                )
                .all()
            )
            
            for lot_id_val, machine_id_val, setup_status in setup_rows:
                setup_statuses[(lot_id_val, machine_id_val)] = setup_status
        
        t_setups = time.time()
        logger.info(f"[lot-materials] setup_query took {(t_setups - t_base)*1000:.0f}ms, pairs={len(pairs)}")

        # ШАГ 4: Мапа source movement -> batch_id (для перехода в карточку партии)
        source_movement_ids = {
            int(m.material_receipt_id)
            for row in base_results
            for m in [row[0]]
            if getattr(m, "material_receipt_id", None) is not None
        }
        source_batch_by_movement = {}
        if source_movement_ids:
            movement_rows = (
                db.query(
                    WarehouseMovementDB.movement_id,
                    WarehouseMovementDB.batch_id,
                )
                .filter(WarehouseMovementDB.movement_id.in_(source_movement_ids))
                .all()
            )
            source_batch_by_movement = {
                int(movement_id): batch_id
                for movement_id, batch_id in movement_rows
            }
        
        # ШАГ 5: Объединяем и фильтруем по status_group
        results = []
        for row in base_results:
            m = row[0]
            setup_status = setup_statuses.get((m.lot_id, m.machine_id))
            
            # Фильтрация по status_group
            if status_group == "active":
                if setup_status == 'completed':
                    continue  # Пропускаем - это pending
            elif status_group == "pending":
                if setup_status != 'completed':
                    continue  # Пропускаем - это active
            
            results.append((*row, setup_status))
        
        if not results:
            return []
        
        # Формируем результат (расчет как в MaterialCalculator - без MTConnect)
        output = []
        for row in results:
            (
                m,
                lot_number,
                lot_status,
                total_planned_quantity,
                initial_planned_quantity,
                machine_name,
                machine_blade_width_mm,
                machine_facing_allowance_mm,
                machine_min_remainder_mm,
                drawing_number,
                part_length,
                setup_status
            ) = row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9], row[10], row[11]
            total_planned = total_planned_quantity or initial_planned_quantity or 0
            net_issued = (m.issued_bars or 0) - (m.returned_bars or 0) - (m.defect_bars or 0)
            blade_width_mm = m.blade_width_mm or machine_blade_width_mm or DEFAULT_BLADE_WIDTH_MM
            facing_allowance_mm = m.facing_allowance_mm or machine_facing_allowance_mm or DEFAULT_FACING_ALLOWANCE_MM
            min_remainder_mm = m.min_remainder_mm or machine_min_remainder_mm or DEFAULT_MIN_REMAINDER_MM
            bar_length_mm = m.bar_length_mm

            # Расчет прутков для ВСЕГО заказа (без MTConnect)
            total_bars_needed = None
            remaining_bars = None
            if total_planned and part_length and bar_length_mm:
                total_bars_needed = _calculate_bars_needed(
                    part_length_mm=part_length,
                    quantity_parts=total_planned,
                    bar_length_mm=bar_length_mm,
                    blade_width_mm=blade_width_mm,
                    facing_allowance_mm=facing_allowance_mm,
                    min_remainder_mm=min_remainder_mm
                )
                if total_bars_needed is not None:
                    # remaining_bars = сколько еще нужно выдать
                    remaining_bars = max(0, total_bars_needed - net_issued)
            output.append({
                "id": m.id,
                "lot_id": m.lot_id,
                "lot_number": lot_number,
                "machine_id": m.machine_id,
                "machine_name": machine_name,
                "drawing_number": drawing_number,
                "material_type": m.material_type,
                "source_movement_id": m.material_receipt_id,
                "source_batch_id": source_batch_by_movement.get(m.material_receipt_id) if m.material_receipt_id else None,
                "shape": m.shape,
                "diameter": m.diameter,
                "bar_length_mm": bar_length_mm,
                "blade_width_mm": blade_width_mm,
                "facing_allowance_mm": facing_allowance_mm,
                "min_remainder_mm": min_remainder_mm,
                "issued_bars": m.issued_bars or 0,
                "returned_bars": m.returned_bars or 0,
                "defect_bars": m.defect_bars or 0,
                "used_bars": net_issued,
                "remaining_bars": remaining_bars,
                "planned_bars_remaining": total_bars_needed,  # Всего прутков на заказ
                "issued_at": m.issued_at,
                "status": m.status,
                "notes": m.notes,
                "closed_at": m.closed_at,
                "closed_by": m.closed_by,
                "created_at": m.created_at,
                "lot_status": lot_status,
                "setup_status": setup_status
            })
        
        t_end = time.time()
        logger.info(f"[lot-materials] TOTAL {(t_end - t_start)*1000:.0f}ms, status_group={status_group}, output={len(output)}")
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
                "shape": op.shape,
                "diameter": op.diameter,
                "bar_length_mm": op.bar_length_mm,
                "blade_width_mm": op.blade_width_mm,
                "facing_allowance_mm": op.facing_allowance_mm,
                "min_remainder_mm": op.min_remainder_mm,
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
            "shape": lot_material.shape,
            "diameter": lot_material.diameter,
            "bar_length_mm": lot_material.bar_length_mm,
            "blade_width_mm": lot_material.blade_width_mm,
            "facing_allowance_mm": lot_material.facing_allowance_mm,
            "min_remainder_mm": lot_material.min_remainder_mm,
            "issued_bars": lot_material.issued_bars or 0,
            "returned_bars": lot_material.returned_bars or 0,
            "defect_bars": lot_material.defect_bars or 0,
            "used_bars": (lot_material.issued_bars or 0) - (lot_material.returned_bars or 0) - (lot_material.defect_bars or 0),
            "remaining_bars": None,
            "planned_bars_remaining": None,
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


@router.patch("/lot-materials/{id}/source-movement")
def link_source_movement(
    id: int,
    request: LinkSourceMovementRequest,
    db: Session = Depends(get_db_session)
):
    """
    Явно связывает lot_material с исходной складской выдачей (warehouse_movements.movement_id).
    Нужно для детерминированного возврата строго в исходную партию/локацию.
    """
    lot_material = db.query(LotMaterialDB).filter(LotMaterialDB.id == id).first()
    if not lot_material:
        raise HTTPException(status_code=404, detail=f"Запись материала {id} не найдена")

    movement = (
        db.query(WarehouseMovementDB)
        .filter(WarehouseMovementDB.movement_id == request.movement_id)
        .first()
    )
    if not movement:
        raise HTTPException(status_code=404, detail=f"Складское движение {request.movement_id} не найдено")
    if movement.movement_type != "issue":
        raise HTTPException(status_code=400, detail="Можно привязывать только движение issue")

    if lot_material.lot_id and movement.related_lot_id and lot_material.lot_id != movement.related_lot_id:
        raise HTTPException(status_code=400, detail="movement.related_lot_id не совпадает с lot_material.lot_id")
    if lot_material.machine_id and movement.related_machine_id and lot_material.machine_id != movement.related_machine_id:
        raise HTTPException(status_code=400, detail="movement.related_machine_id не совпадает с lot_material.machine_id")

    lot_material.material_receipt_id = movement.movement_id
    db.commit()

    return {
        "status": "ok",
        "lot_material_id": lot_material.id,
        "source_movement_id": movement.movement_id,
    }


@router.get("/lot-materials/{id}/material-hours")
def get_material_hours(
    id: int,
    db: Session = Depends(get_db_session)
):
    """
    Рассчитать, на сколько часов хватит выданного материала.
    Возвращает None, если данных недостаточно (нет длины прутка/цикла/длины детали).
    """
    lot_material = db.query(LotMaterialDB).filter(LotMaterialDB.id == id).first()
    if not lot_material:
        raise HTTPException(status_code=404, detail=f"Запись материала {id} не найдена")

    lot = db.query(LotDB).filter(LotDB.id == lot_material.lot_id).first()
    part = db.query(PartDB).filter(PartDB.id == lot.part_id).first() if lot and lot.part_id else None
    machine = db.query(MachineDB).filter(MachineDB.id == lot_material.machine_id).first() if lot_material.machine_id else None

    bar_length_mm = lot_material.bar_length_mm
    blade_width_mm = lot_material.blade_width_mm or (machine.material_blade_width_mm if machine else None) or DEFAULT_BLADE_WIDTH_MM
    facing_allowance_mm = lot_material.facing_allowance_mm or (machine.material_facing_allowance_mm if machine else None) or DEFAULT_FACING_ALLOWANCE_MM
    min_remainder_mm = lot_material.min_remainder_mm or (machine.material_min_remainder_mm if machine else None) or DEFAULT_MIN_REMAINDER_MM
    net_issued = (lot_material.issued_bars or 0) - (lot_material.returned_bars or 0) - (lot_material.defect_bars or 0)
    cycle_time_sec = _get_cycle_time_seconds(
        db=db,
        lot_id=lot_material.lot_id,
        machine_id=lot_material.machine_id,
        part_id=lot.part_id if lot else None
    )
    part_length_mm = part.part_length if part else None

    produced = _get_produced_for_lot(
        db=db,
        lot_id=lot_material.lot_id,
        fallback_machine_name=machine.name if machine else None,
        mtconnect_counts=_fetch_mtconnect_counts()
    )
    hours = None
    if produced is not None:
        hours = _calculate_hours_by_material(
            net_issued_bars=net_issued,
            part_length_mm=part_length_mm,
            bar_length_mm=bar_length_mm,
            blade_width_mm=blade_width_mm,
            facing_allowance_mm=facing_allowance_mm,
            min_remainder_mm=min_remainder_mm,
            cycle_time_sec=cycle_time_sec,
            produced_parts=produced
        )

    return {
        "lot_material_id": lot_material.id,
        "lot_id": lot_material.lot_id,
        "lot_number": lot.lot_number if lot else None,
        "machine_id": lot_material.machine_id,
        "machine_name": machine.name if machine else None,
        "part_length_mm": part_length_mm,
        "bar_length_mm": bar_length_mm,
        "cycle_time_sec": cycle_time_sec,
        "net_issued_bars": net_issued,
        "produced_parts": produced,
        "hours_remaining": hours
    }


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
                "shape": op.shape,
                "diameter": op.diameter,
                "bar_length_mm": op.bar_length_mm,
                "blade_width_mm": op.blade_width_mm,
                "facing_allowance_mm": op.facing_allowance_mm,
                "min_remainder_mm": op.min_remainder_mm,
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
