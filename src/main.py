import logging
from dotenv import load_dotenv
import os
import traceback # <--- ДОБАВИТЬ ЭТОТ ИМПОРТ

# Загружаем переменные окружения из .env файла
# Это должно быть В САМОМ НАЧАЛЕ, до других импортов, использующих env vars
load_dotenv()

from fastapi import FastAPI, HTTPException, Query, Response # Добавил Response для заголовков пагинации, если понадобится
from fastapi.middleware.cors import CORSMiddleware

# Импорт роутеров
from .routers import lots as lots_router
from .routers import qc as qc_router
from .routers import admin as admin_router
from .routers import analytics as analytics_router
from .routers import warehouse as warehouse_router
from .routers.warehouse import convert_to_israel_timezone
from .routers import events as events_router
from .routers import catalog as catalog_router
from .routers import employees as employees_router
from .routers import cards as cards_router
from src.models.setup import SetupStatus, BatchLabelInfo
from src.models.reports import LotSummaryReport, ProductionPerformanceReport, QualityReport
from typing import Optional, Dict, List, Union
from pydantic import BaseModel, Field
from enum import Enum
from sqlalchemy.orm import Session, aliased, selectinload
from fastapi import Depends, Body
from src.database import Base, initialize_database, get_db_session
from src.models.models import SetupDB, ReadingDB, MachineDB, EmployeeDB, PartDB, LotDB, BatchDB, CardDB
from datetime import datetime, timezone, date, timedelta
from src.utils.sheets_handler import save_to_sheets
import asyncio
import httpx
import aiohttp
from src.services.notification_service import send_setup_approval_notifications, send_batch_discrepancy_alert
from sqlalchemy import func, desc, case, text, or_, and_
from sqlalchemy.exc import IntegrityError
from src.services.metrics import install_sql_capture

logger = logging.getLogger(__name__)

app = FastAPI(title="Machine Logic Service", debug=True)

# Возвращаем универсальное разрешение CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # <-- Снова разрешаем все источники
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"], 
    expose_headers=["X-Total-Count"]  # <--- ДОБАВЛЕНО ЗДЕСЬ
)

# Подключение роутеров будет в конце файла после всех эндпоинтов


# Событие startup для инициализации БД
@app.on_event("startup")
async def startup_event():
    initialize_database()
    install_sql_capture()  # включит runtime-capture при TEXT2SQL_CAPTURE=1
    # Здесь можно добавить другие действия при старте, если нужно

# Pydantic модели для Деталей (Parts)
class PartBase(BaseModel):
    drawing_number: str = Field(..., description="Номер чертежа детали, должен быть уникальным")
    material: Optional[str] = Field(None, description="Материал детали")

class PartCreate(PartBase):
    pass

class PartUpdate(PartBase): # Для возможного будущего обновления
    drawing_number: Optional[str] = None # При обновлении можно разрешить менять не все поля
    material: Optional[str] = None

class PartResponse(PartBase):
    id: int
    created_at: Optional[datetime] # <--- СДЕЛАНО ОПЦИОНАЛЬНЫМ

    class Config:
        orm_mode = True

# --- Эндпоинты для Деталей (Parts) ---
@app.post("/parts/", response_model=PartResponse, status_code=201, tags=["Parts"])
async def create_part(part_in: PartCreate, db: Session = Depends(get_db_session)):
    """
    Создать новую деталь.
    - **drawing_number**: Номер чертежа (уникальный)
    - **material**: Материал (опционально)
    """
    logger.info(f"Запрос на создание детали: {part_in.model_dump()}")
    existing_part = db.query(PartDB).filter(PartDB.drawing_number == part_in.drawing_number).first()
    if existing_part:
        logger.warning(f"Деталь с номером чертежа {part_in.drawing_number} уже существует (ID: {existing_part.id})")
        raise HTTPException(status_code=409, detail=f"Деталь с номером чертежа '{part_in.drawing_number}' уже существует.")
    
    new_part = PartDB(
        drawing_number=part_in.drawing_number,
        material=part_in.material
        # created_at будет установлен по умолчанию
    )
    db.add(new_part)
    try:
        db.commit()
        db.refresh(new_part)
        logger.info(f"Деталь '{new_part.drawing_number}' успешно создана с ID {new_part.id}")
        return new_part
    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка при сохранении детали {part_in.drawing_number}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера при сохранении детали: {str(e)}")

@app.get("/parts/", response_model=List[PartResponse], tags=["Parts"])
async def get_parts(
    response: Response, 
    search: Optional[str] = Query(None, description="Поисковый запрос для номера чертежа или материала"),
    skip: int = Query(0, ge=0, description="Количество записей для пропуска (пагинация)"),
    limit: int = Query(100, ge=1, le=500, description="Максимальное количество записей для возврата (пагинация)"),
    db: Session = Depends(get_db_session)
):
    """
    Получить список всех деталей.
    Поддерживает поиск по `drawing_number` и `material` (частичное совпадение без учета регистра).
    Поддерживает пагинацию через `skip` и `limit`.
    """
    try:
        query = db.query(PartDB)
        
        if search:
            search_term = f"%{search.lower()}%"
            query = query.filter(
                (func.lower(PartDB.drawing_number).like(search_term)) |
                (func.lower(func.coalesce(PartDB.material, '')).like(search_term))
            )

        total_count = query.count()

        parts = query.order_by(PartDB.drawing_number).offset(skip).limit(limit).all()
        logger.info(f"Запрос списка деталей: search='{search}', skip={skip}, limit={limit}. Возвращено {len(parts)} из {total_count} деталей.")
        
        response.headers["X-Total-Count"] = str(total_count)
        # УДАЛЕНО: response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"
            
        return parts
    except Exception as e:
        logger.error(f"Ошибка при получении списка деталей (search='{search}', skip={skip}, limit={limit}): {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера при получении списка деталей: {str(e)}")

@app.get("/")
async def root():
    return {
        "service": "Machine Logic Service",
        "status": "running",
        "available_statuses": [status.value for status in SetupStatus]
    }

# === NEW: Update program cycle time by drawing_number ===
class ProgramCycleUpdate(BaseModel):
    drawing_number: str
    cycle_time_seconds: Optional[int] = None
    cycle_time_minutes: Optional[float] = None

@app.post("/programs/cycle-time", tags=["Parts"], summary="Update cycle time for active setups by drawing_number")
async def update_program_cycle_time(payload: ProgramCycleUpdate, db: Session = Depends(get_db_session)):
    try:
        if not payload.drawing_number:
            raise HTTPException(status_code=400, detail="drawing_number is required")

        # Resolve seconds from either seconds or minutes
        sec: Optional[int] = None
        if payload.cycle_time_seconds is not None and payload.cycle_time_seconds > 0:
            sec = int(payload.cycle_time_seconds)
        elif payload.cycle_time_minutes is not None and payload.cycle_time_minutes > 0:
            sec = int(round(payload.cycle_time_minutes * 60))
        else:
            raise HTTPException(status_code=400, detail="Provide cycle_time_seconds > 0 or cycle_time_minutes > 0")

        # Find part by drawing_number
        part = db.query(PartDB).filter(PartDB.drawing_number == payload.drawing_number).first()
        if not part:
            raise HTTPException(status_code=404, detail=f"Part with drawing_number '{payload.drawing_number}' not found")

        # Update active setups for this part (those without end_time)
        q = db.query(SetupDB).filter(SetupDB.part_id == part.id, SetupDB.end_time.is_(None))
        updated = 0
        for s in q.all():
            s.cycle_time = sec
            updated += 1

        db.commit()
        logger.info(f"Updated cycle_time for drawing {payload.drawing_number} to {sec}s across {updated} active setups")
        return {"success": True, "updated": updated, "cycle_time_seconds": sec}
    except HTTPException as http_e:
        raise http_e
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating program cycle time: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while updating program cycle time")

@app.get("/setup/{machine_id}/status")
async def get_setup_status(machine_id: int, db: Session = Depends(get_db_session)):
    """
    Получить текущий статус наладки для станка
    """
    # Получаем последнюю наладку для станка
    setup = db.query(SetupDB).filter(
        SetupDB.machine_id == machine_id
    ).order_by(SetupDB.created_at.desc()).first()
    
    if not setup:
        return {
            "machine_id": machine_id,
            "status": SetupStatus.IDLE.value,
            "message": "Станок простаивает"
        }
    
    return {
        "machine_id": machine_id,
        "status": setup.status,
        "message": f"Текущий статус: {setup.status}"
    }

@app.get("/setup/{machine_id}/all")
async def get_setup_history(machine_id: int, db: Session = Depends(get_db_session)):
    """
    Получить историю наладок для станка
    """
    setups = db.query(SetupDB).filter(
        SetupDB.machine_id == machine_id
    ).order_by(SetupDB.created_at.desc()).all()
    
    return {
        "machine_id": machine_id,
        "setups": [
            {
                "id": s.id,
                "status": s.status,
                "created_at": s.created_at,
                "start_time": s.start_time,
                "end_time": s.end_time
            } for s in setups
        ]
    }

class ReadingInput(BaseModel):
    machine_id: int
    operator_id: int
    value: int

@app.post("/readings")
async def save_reading(reading_input: ReadingInput, db: Session = Depends(get_db_session)):
    """
    Сохранить показания счетчика, обновить статус наладки и создать/обновить батч.
    """
    logger.info(f"Received reading save request: {reading_input}")
    # Используем reading_input вместо reading для ясности
    
    # Начинаем транзакцию
    trans = db.begin_nested() if db.in_transaction() else db.begin()
    try:
        # 1. Получаем последнюю активную наладку для станка
        setup = db.query(SetupDB)\
            .filter(SetupDB.machine_id == reading_input.machine_id)\
            .filter(SetupDB.status.in_(['created', 'pending_qc', 'allowed', 'started']))\
            .filter(SetupDB.end_time.is_(None))\
            .order_by(SetupDB.created_at.desc())\
            .first()

        logger.info(f"Found active setup: {setup.id if setup else None}, status: {setup.status if setup else None}")

        if not setup:
            raise HTTPException(status_code=404, detail="Активная наладка не найдена для этого станка")

        # 2. Сохраняем сами показания
        reading_db = ReadingDB(
            employee_id=reading_input.operator_id,
            machine_id=reading_input.machine_id,
            reading=reading_input.value,
            setup_job_id=setup.id,  # Связываем с активной наладкой
            created_at=datetime.now() # Фиксируем время явно
        )
        db.add(reading_db)
        db.flush() # Чтобы получить ID и время, если нужно
        logger.info(f"Reading record created: ID {reading_db.id}")

        # --- Логика обновления статуса наладки и работы с батчами ---
        new_setup_status = setup.status
        batch_message = ""

        # 3. Обновляем статус наладки, если нужно
        if reading_input.value == 0:
            if setup.status in ['created', 'allowed']:
                logger.info(f"Updating setup {setup.id} status from {setup.status} to started (reading is 0)")
                setup.status = 'started'
                setup.start_time = reading_db.created_at # Используем время показаний
                new_setup_status = 'started'
                batch_message = "Наладка активирована"
            elif setup.status != 'started':
                 raise HTTPException(
                     status_code=400, 
                     detail=f"Нельзя вводить нулевые показания в статусе {setup.status}"
                 )
            # Для статуса 'started' нулевые показания просто сохраняются, батч не создается
        
        elif reading_input.value > 0:
            # Для ненулевых показаний
            if setup.status in ['created', 'allowed']:
                # Случай пропуска нуля
                logger.info(f"Updating setup {setup.id} status from {setup.status} to started (reading > 0, zero skipped)")
                setup.status = 'started'
                setup.start_time = reading_db.created_at
                new_setup_status = 'started'
                batch_message = ("⚠️ Внимание! В начале работы необходимо вводить нулевые показания. "
                                 "Наладка автоматически активирована.")
                
                # Ищем существующий батч production (на всякий случай, хотя его не должно быть)
                existing_batch = db.query(BatchDB)\
                    .filter(BatchDB.setup_job_id == setup.id)\
                    .filter(BatchDB.current_location == 'production')\
                    .first()
                
                if not existing_batch:
                    logger.info(f"Creating initial production batch for setup {setup.id} (zero skipped)")
                    new_batch = BatchDB(
                        setup_job_id=setup.id,
                        lot_id=setup.lot_id,
                        initial_quantity=0, 
                        current_quantity=reading_input.value, # Текущее кол-во = показаниям
                        current_location='production',
                        batch_time=reading_db.created_at,
                        operator_id=reading_input.operator_id,
                        created_at=reading_db.created_at # Используем время показаний
                    )
                    db.add(new_batch)
                else:
                     logger.warning(f"Found existing production batch {existing_batch.id} when zero was skipped. Updating quantity.")
                     existing_batch.current_quantity = reading_input.value
                     existing_batch.operator_id = reading_input.operator_id
                     existing_batch.batch_time = reading_db.created_at

            elif setup.status == 'started':
                # Наладка уже была начата, ищем предыдущее показание
                prev_reading_obj = db.query(ReadingDB.reading)\
                    .filter(ReadingDB.machine_id == reading_input.machine_id)\
                    .filter(ReadingDB.created_at < reading_db.created_at)\
                    .order_by(ReadingDB.created_at.desc())\
                    .first()
                
                prev_reading = prev_reading_obj[0] if prev_reading_obj else 0 # Считаем 0, если нет предыдущего
                quantity_in_batch = reading_input.value - prev_reading
                logger.info(f"Prev reading: {prev_reading}, Current: {reading_input.value}, Diff: {quantity_in_batch}")

                if quantity_in_batch > 0:
                    # --- ИСПРАВЛЕНИЕ: Всегда создаем НОВЫЙ батч --- 
                    logger.info(f"Creating NEW production batch for setup {setup.id} (started state)")
                    new_batch = BatchDB(
                        setup_job_id=setup.id,
                        lot_id=setup.lot_id,
                        initial_quantity=prev_reading, # Начальное кол-во = предыдущие показания
                        current_quantity=quantity_in_batch, # Текущее кол-во = разница
                        current_location='production',
                        batch_time=reading_db.created_at,
                        operator_id=reading_input.operator_id,
                        created_at=reading_db.created_at # Используем время показаний
                    )
                    db.add(new_batch)
                    # --- Конец исправления ---
                else:
                     logger.warning(f"Quantity difference is not positive ({quantity_in_batch}), not creating batch.")
            else: # Статус не 'created', 'allowed', 'started'
                 raise HTTPException(
                     status_code=400, 
                     detail=f"Нельзя вводить показания в статусе {setup.status}"
                 )
        else: # reading_input.value < 0
             raise HTTPException(status_code=400, detail="Показания не могут быть отрицательными")

        # 4. Фиксируем транзакцию
        trans.commit()
        logger.info("Transaction committed successfully")

        # 5. Сохраняем в Google Sheets (вне транзакции)
        try:
            operator = db.query(EmployeeDB.full_name).filter(EmployeeDB.id == reading_input.operator_id).scalar() or "Unknown"
            machine = db.query(MachineDB.name).filter(MachineDB.id == reading_input.machine_id).scalar() or "Unknown"
            asyncio.create_task(save_to_sheets(
                operator=operator,
                machine=machine,
                reading=reading_input.value
            ))
        except Exception as sheet_error:
             logger.error(f"Error saving to Google Sheets: {sheet_error}", exc_info=True)
             # Не прерываем выполнение из-за ошибки Sheets

        return {
            "success": True,
            "message": batch_message if batch_message else "Показания успешно сохранены",
            "reading": reading_input.model_dump(), # Используем model_dump для Pydantic v2
            "new_status": new_setup_status
        }

    except HTTPException as http_exc:
        trans.rollback()
        logger.error(f"HTTPException in save_reading: {http_exc.detail}")
        raise http_exc
    except Exception as e:
        trans.rollback()
        logger.error(f"Error in save_reading: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while saving reading")

@app.get("/machines")
async def get_machines(db: Session = Depends(get_db_session)):
    """
    Получить список всех станков
    """
    machines = db.query(MachineDB).all()
    return {
        "machines": [
            {
                "id": m.id,
                "name": m.name,
                "type": m.type
            } for m in machines
        ]
    }

@app.get("/readings")
async def get_readings(db: Session = Depends(get_db_session)):
    """
    Получить последние показания
    """
    logger.info("--- Запрос GET /readings получен ---") # Лог начала
    try:
        logger.info("Выполняется запрос к ReadingDB...")
        readings = db.query(ReadingDB).order_by(ReadingDB.created_at.desc()).limit(100).all()
        logger.info(f"Запрос к ReadingDB выполнен, получено {len(readings)} записей.")
        
        # Формируем ответ
        response_data = {
            "readings": [
                {
                    "id": r.id,
                    "machine_id": r.machine_id,
                    "employee_id": r.employee_id,
                    "reading": r.reading,
                    # Преобразуем дату в строку ISO, чтобы избежать проблем сериализации
                    "created_at": r.created_at.isoformat() if r.created_at else None 
                } for r in readings
            ]
        }
        logger.info("--- Ответ для GET /readings сформирован успешно --- ")
        return response_data # Возвращаем словарь, FastAPI сам сделает JSON
        
    except Exception as e:
        logger.error(f"!!! Ошибка в GET /readings: {e}", exc_info=True) # Логируем ошибку
        # Поднимаем HTTPException, чтобы FastAPI вернул корректный JSON ошибки 500
        raise HTTPException(status_code=500, detail="Internal server error processing readings")

@app.get("/readings/{machine_id}")
async def get_machine_readings(machine_id: int, db: Session = Depends(get_db_session)):
    """
    Получить показания для конкретного станка (только для активной наладки)
    """
    # ИСПРАВЛЕНО: Берем показания только для активной наладки
    readings = db.execute(text("""
        SELECT mr.id, mr.employee_id, mr.reading, mr.created_at AT TIME ZONE 'Asia/Jerusalem' as created_at
        FROM machine_readings mr
        JOIN setup_jobs sj ON mr.setup_job_id = sj.id
        WHERE sj.machine_id = :machine_id
          AND sj.status = 'started'
          AND sj.end_time IS NULL
          AND mr.setup_job_id IS NOT NULL
        ORDER BY mr.created_at DESC
        LIMIT 100
    """), {"machine_id": machine_id}).fetchall()
    
    return {
        "machine_id": machine_id,
        "readings": [
            {
                "id": r.id,
                "employee_id": r.employee_id,
                "reading": r.reading,
                "created_at": r.created_at
            } for r in readings
        ]
    }

@app.get("/machines/{machine_id}/latest-batch")
async def get_latest_batch_id(machine_id: int, db: Session = Depends(get_db_session)):
    """
    Получить ID последнего созданного batch для станка
    """
    try:
        # Ищем последний batch для станка через setup_jobs
        latest_batch = db.execute(
            text("""
                SELECT b.id, b.current_quantity, b.batch_time
                FROM batches b
                JOIN setup_jobs sj ON b.setup_job_id = sj.id
                WHERE sj.machine_id = :machine_id
                AND b.current_location = 'production'
                ORDER BY b.batch_time DESC
                LIMIT 1
            """),
            {"machine_id": machine_id}
        ).fetchone()
        
        if not latest_batch:
            raise HTTPException(status_code=404, detail="Batch не найден для этого станка")
        
        return {
            "batch_id": latest_batch.id,
            "current_quantity": latest_batch.current_quantity,
            "batch_time": latest_batch.batch_time.isoformat() if latest_batch.batch_time else None
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching latest batch for machine {machine_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while fetching latest batch")

class SetupInput(BaseModel):
    machine_id: int
    operator_id: int
    drawing_number: str
    lot_number: str
    planned_quantity: int
    cycle_time_seconds: Optional[int] = 30

@app.post("/setup")
async def create_setup(setup: SetupInput, db: Session = Depends(get_db_session)):
    """
    Create a new setup for a machine
    """
    # Check if machine and operator exist
    machine = db.query(MachineDB).filter(MachineDB.id == setup.machine_id).first()
    operator = db.query(EmployeeDB).filter(EmployeeDB.id == setup.operator_id).first()
    
    if not machine or not operator:
        raise HTTPException(status_code=404, detail="Machine or operator not found")
    
    # Get or create part
    part = db.query(PartDB).filter(PartDB.drawing_number == setup.drawing_number).first()
    if not part:
        part = PartDB(
            drawing_number=setup.drawing_number,
            description=None,
            is_active=True
        )
        db.add(part)
        db.flush()  # Get the part ID
    
    # Get or create lot
    lot = db.query(LotDB).filter(
        LotDB.lot_number == setup.lot_number,
        LotDB.part_id == part.id
    ).first()
    if not lot:
        lot = LotDB(
            lot_number=setup.lot_number,
            part_id=part.id,
            is_active=True
        )
        db.add(lot)
        db.flush()  # Get the lot ID
    
    # Create new setup
    new_setup = SetupDB(
        machine_id=setup.machine_id,
        employee_id=setup.operator_id,
        planned_quantity=setup.planned_quantity,
        cycle_time=setup.cycle_time_seconds,
        status=SetupStatus.CREATED.value,
        created_at=datetime.now(),
        lot_id=lot.id,
        part_id=part.id
    )
    
    db.add(new_setup)
    db.commit()
    db.refresh(new_setup)
    
    return {
        "success": True,
        "message": "Setup created successfully",
        "setup": {
            "id": new_setup.id,
            "machine_id": new_setup.machine_id,
            "operator_id": new_setup.employee_id,
            "drawing_number": setup.drawing_number,
            "lot_number": setup.lot_number,
            "status": new_setup.status,
            "created_at": new_setup.created_at
        }
    }

# Модель для тела запроса на утверждение
class ApproveSetupPayload(BaseModel):
    qa_id: int

# Добавляем Pydantic модель для ответа


# Модель для ответа после утверждения (можно вернуть обновленную наладку)
class ApprovedSetupResponse(BaseModel): # Используем Pydantic, т.к. он уже есть
    id: int
    machineName: Optional[str] = Field(None, alias='machineName')
    drawingNumber: Optional[str] = Field(None, alias='drawingNumber')
    lotNumber: Optional[str] = Field(None, alias='lotNumber')
    machinistName: Optional[str] = Field(None, alias='machinistName')
    startTime: Optional[datetime] = Field(None, alias='startTime')
    status: Optional[str]
    qaName: Optional[str] = Field(None, alias='qaName')
    qaDate: Optional[datetime] = Field(None, alias='qaDate')

    class Config:
        from_attributes = True # Это для SQLAlchemy >= 2.0 и Pydantic v2
        populate_by_name = True # Pydantic v2, было allow_population_by_field_name

# Новый эндпоинт для утверждения наладки ОТК
@app.post("/setups/{setup_id}/approve", response_model=ApprovedSetupResponse)
async def approve_setup(
    setup_id: int,
    payload: ApproveSetupPayload,
    db: Session = Depends(get_db_session) # Получаем сессию БД
):
    """
    Утвердить наладку (изменить статус на 'allowed').
    Требует ID сотрудника ОТК в теле запроса.
    Отправляет уведомления через notification_service, используя SQLAlchemy.
    """
    try:
        # Найти наладку по ID
        setup = db.query(SetupDB).filter(SetupDB.id == setup_id).first()

        if not setup:
            raise HTTPException(status_code=404, detail=f"Наладка с ID {setup_id} не найдена")

        if setup.status not in ['pending_qc', 'created']:
            raise HTTPException(
                status_code=400,
                detail=f"Нельзя разрешить наладку в статусе '{setup.status}'. Ожидался статус 'pending_qc' или 'created'"
            )

        qa_employee_check = db.query(EmployeeDB).filter(EmployeeDB.id == payload.qa_id).first()
        if not qa_employee_check:
             raise HTTPException(status_code=404, detail=f"Сотрудник ОТК с ID {payload.qa_id} не найден")

        # Обновить статус, qa_id и qa_date
        setup.status = 'allowed' # Новый статус
        setup.qa_id = payload.qa_id
        setup.qa_date = datetime.now()

        db.commit() # Сохраняем изменения
        db.refresh(setup) # Обновляем объект setup из БД

        # --- Отправка уведомлений через новый сервис в фоне ---
        # Передаем сессию `db` в функцию уведомлений
        asyncio.create_task(send_setup_approval_notifications(db=db, setup_id=setup.id))
        # -------------------------------------------------------

        # --- ЗАПРОС ДЛЯ ФОРМИРОВАНИЯ ОТВЕТА (оптимизирован) ---
        # Можно получить связанные данные прямо из обновленного объекта setup,
        # если связи настроены в моделях SQLAlchemy (например, setup.employee, setup.qa)
        # Или выполнить запрос, как раньше, если нужно получить данные определенным образом.
        # Для примера оставим запрос, но немного изменим его для Pydantic v2

        Machinist = aliased(EmployeeDB)
        QAEmployee = aliased(EmployeeDB)

        result_data = db.query(
                SetupDB.id,
                MachineDB.name.label('machine_name'),
                PartDB.drawing_number.label('drawing_number'),
                LotDB.lot_number.label('lot_number'),
                Machinist.full_name.label('machinist_name'),
                SetupDB.start_time,
                SetupDB.status,
                QAEmployee.full_name.label('qa_name'),
                SetupDB.qa_date
            )\
            .select_from(SetupDB)\
            .join(Machinist, SetupDB.employee_id == Machinist.id)\
            .join(MachineDB, SetupDB.machine_id == MachineDB.id)\
            .join(PartDB, SetupDB.part_id == PartDB.id)\
            .join(LotDB, SetupDB.lot_id == LotDB.id)\
            .outerjoin(QAEmployee, SetupDB.qa_id == QAEmployee.id)\
            .filter(SetupDB.id == setup_id)\
            .first()

        if not result_data:
             print(f"Warning: Could not fetch details for the approved setup {setup_id} to generate API response.")
             # Используем данные из первоначальных объектов
             return ApprovedSetupResponse.model_validate({
                 'id': setup.id, 
                 'status': setup.status, 
                 'qa_date': setup.qa_date, 
                 'qa_name': qa_employee_check.full_name
             })
        
        # Преобразуем результат запроса (кортеж) в словарь для Pydantic
        response_data_dict = {
            "id": result_data.id,
            "machineName": result_data.machine_name,
            "drawingNumber": result_data.drawing_number,
            "lotNumber": result_data.lot_number,
            "machinistName": result_data.machinist_name,
            "startTime": result_data.start_time,
            "status": result_data.status,
            "qaName": result_data.qa_name,
            "qaDate": result_data.qa_date
        }

        # Используем model_validate для Pydantic v2
        response_item = ApprovedSetupResponse.model_validate(response_data_dict)
        
        return response_item

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        print(f"Error approving setup {setup_id}: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Internal server error while approving setup {setup_id}")

# Модель ответа остается прежней
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

# Изменяем путь и убираем operator_id из аргументов
@app.get("/machines/operator-view", response_model=List[OperatorMachineViewItem])
async def get_operator_machines_view(db: Session = Depends(get_db_session)):
    """
    Получает список ВСЕХ активных станков с информацией
    о последней активной наладке и последнем показании (ОПТИМИЗИРОВАННАЯ ВЕРСИЯ).
    """
    logger.info("Fetching optimized operator machine view for ALL operators")
    try:
        active_setup_statuses = ('created', 'pending_qc', 'allowed', 'started')
        
        sql_query = text(f"""
        WITH latest_readings AS (
            -- Находим последнее показание для каждой АКТИВНОЙ наладки
            SELECT 
                mr.setup_job_id,
                mr.machine_id,
                mr.reading, 
                mr.created_at AT TIME ZONE 'Asia/Jerusalem' as created_at,
                ROW_NUMBER() OVER (PARTITION BY mr.setup_job_id ORDER BY mr.created_at DESC) as rn
            FROM machine_readings mr
            WHERE mr.setup_job_id IS NOT NULL
        ),
        latest_setups AS (
            -- Находим последнюю активную наладку для каждого станка
            SELECT 
                id,
                planned_quantity,
                additional_quantity,
                part_id,
                status,
                machine_id,
                ROW_NUMBER() OVER (PARTITION BY machine_id ORDER BY created_at DESC) as rn
            FROM setup_jobs
            WHERE status IN :active_statuses AND end_time IS NULL
        )
        SELECT 
            m.id,
            m.name,
            lr.reading as last_reading,
            lr.created_at as last_reading_time,
            ls.id as setup_id,
            p.drawing_number,
            ls.planned_quantity,
            ls.additional_quantity,
            COALESCE(ls.status, 'idle') as status
        FROM machines m
        LEFT JOIN (
            SELECT * FROM latest_setups WHERE rn = 1
        ) ls ON m.id = ls.machine_id
        LEFT JOIN (
            SELECT * FROM latest_readings WHERE rn = 1
        ) lr ON ls.id = lr.setup_job_id  -- 🔧 СВЯЗЫВАЕМ ПО НАЛАДКЕ, А НЕ ПО СТАНКУ
        LEFT JOIN parts p ON ls.part_id = p.id
        WHERE m.is_active = true
        ORDER BY m.name;
        """)

        result = db.execute(sql_query, {"active_statuses": active_setup_statuses})
        rows = result.fetchall()

        # Используем .from_orm() для прямого преобразования в Pydantic модель
        result_list = [OperatorMachineViewItem.from_orm(row) for row in rows]
        
        logger.info(f"Successfully prepared operator machine view with {len(result_list)} machines.")
        return result_list

    except Exception as e:
        logger.error(f"Error fetching optimized operator machine view: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error fetching operator machine view")

async def check_lot_completion_and_update_status(lot_id: int, db: Session):
    """
    Проверяет, завершены ли все наладки для лота, и обновляет статус лота на 'post_production'
    """
    try:
        # Получаем информацию о лоте
        lot = db.query(LotDB).filter(LotDB.id == lot_id).first()
        if not lot:
            logger.warning(f"Lot {lot_id} not found")
            return
        
        logger.info(f"Checking completion status for lot {lot_id} (current status: {lot.status})")
        
        # Проверяем только если лот в статусе 'in_production'
        if lot.status != 'in_production':
            logger.info(f"Lot {lot_id} is not in 'in_production' status, skipping check")
            return
        
        # Проверяем, есть ли незавершенные наладки для этого лота
        active_setups = db.query(SetupDB).filter(
            SetupDB.lot_id == lot_id,
            SetupDB.status.in_(['created', 'pending_qc', 'allowed', 'started', 'queued']),
            SetupDB.end_time == None
        ).count()
        
        logger.info(f"Found {active_setups} active setups for lot {lot_id}")
        
        if active_setups == 0:
            # Все наладки завершены, переводим лот в статус 'post_production'
            logger.info(f"All setups completed for lot {lot_id}, updating status to 'post_production'")
            lot.status = 'post_production'
            db.commit()
            
            # Синхронизируем с Telegram-ботом
            try:
                await sync_lot_status_to_telegram_bot(lot_id, 'post_production')
            except Exception as sync_error:
                logger.error(f"Failed to sync lot status to Telegram bot: {sync_error}")
                # Не прерываем выполнение, если синхронизация не удалась
        
    except Exception as e:
        logger.error(f"Error checking lot completion for lot {lot_id}: {e}", exc_info=True)

async def sync_lot_status_to_telegram_bot(lot_id: int, status: str):
    """
    Синхронизирует статус лота с Telegram-ботом через прямое обновление БД
    """
    try:
        # Обновляем статус лота в БД напрямую (Telegram-бот использует ту же БД)
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import sessionmaker
        
        # Используем ту же БД, что и Telegram-бот
        DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://postgres:password@localhost:5432/isramat_bot')
        
        # Создаем отдельное соединение для синхронизации
        sync_engine = create_engine(DATABASE_URL)
        SyncSession = sessionmaker(bind=sync_engine)
        
        with SyncSession() as sync_session:
            # Обновляем статус лота в БД
            result = sync_session.execute(
                text("UPDATE lots SET status = :status WHERE id = :lot_id"),
                {"status": status, "lot_id": lot_id}
            )
            sync_session.commit()
            
            if result.rowcount > 0:
                logger.info(f"Successfully synced lot {lot_id} status to '{status}' in Telegram bot DB")
            else:
                logger.warning(f"No lot found with ID {lot_id} for status sync")
                
    except Exception as e:
        logger.error(f"Error syncing lot status to Telegram bot DB: {e}")


async def check_lot_auto_completion(lot_id: int, db: Session):
    """
    Проверяет, можно ли автоматически закрыть лот (перевести в статус 'closed').
    Условия:
    1. Лот в статусе 'post_production'
    2. Все батчи лота находятся в финальных статусах QC (good, defect)
    """
    try:
        # Получаем информацию о лоте
        lot = db.query(LotDB).filter(LotDB.id == lot_id).first()
        if not lot:
            logger.warning(f"Lot {lot_id} not found for auto-completion check")
            return
        
        logger.info(f"Checking auto-completion for lot {lot_id} (current status: {lot.status})")
        
        # Проверяем только если лот в статусе 'post_production'
        if lot.status != 'post_production':
            logger.info(f"Lot {lot_id} is not in 'post_production' status, skipping auto-completion check")
            return
        
        # Получаем все батчи лота
        batches = db.query(BatchDB).filter(BatchDB.lot_id == lot_id).all()
        if not batches:
            logger.info(f"No batches found for lot {lot_id}, skipping auto-completion check")
            return
        
        logger.info(f"Found {len(batches)} batches for lot {lot_id}")
        
        # Проверяем, что все батчи находятся в финальных статусах QC
        # (good, defect, archived - финальные статусы, rework_repair - промежуточный)
        final_qc_locations = ['good', 'defect', 'archived']
        non_final_batches = [batch for batch in batches if batch.current_location not in final_qc_locations]
        
        if non_final_batches:
            logger.info(f"Lot {lot_id} has {len(non_final_batches)} batches not in final QC status: {[b.current_location for b in non_final_batches]}")
            return
        
        # Все батчи в финальных статусах - можно закрывать лот
        logger.info(f"All batches for lot {lot_id} are in final QC status. Auto-closing lot.")
        
        # Закрываем лот
        lot.status = 'closed'
        db.commit()
        
        # Синхронизируем с Telegram-ботом
        try:
            logger.info(f"Attempting to sync lot {lot_id} status to Telegram bot")
            await sync_lot_status_to_telegram_bot(lot_id, 'closed')
            logger.info(f"Successfully synced lot {lot_id} status to Telegram bot")
        except Exception as sync_error:
            logger.error(f"Failed to sync lot auto-completion to Telegram bot: {sync_error}")
            # Не прерываем выполнение, если синхронизация не удалась
        
        logger.info(f"Successfully auto-closed lot {lot_id}")
        
    except Exception as e:
        logger.error(f"Error checking lot auto-completion for lot {lot_id}: {e}", exc_info=True)

@app.post("/setups/{setup_id}/complete")
async def complete_setup(setup_id: int, db: Session = Depends(get_db_session)):
    """
    Завершить наладку (изменить статус на 'completed').
    """
    logger.info(f"=== Starting setup completion for setup_id: {setup_id} ===")
    try:
        # Найти наладку по ID
        setup = db.query(SetupDB).filter(SetupDB.id == setup_id).first()
        logger.info(f"Found setup: {setup}")

        if not setup:
            logger.error(f"Setup {setup_id} not found")
            raise HTTPException(status_code=404, detail=f"Наладка с ID {setup_id} не найдена")

        logger.info(f"Current setup status: {setup.status}")
        if setup.status not in ['started', 'allowed']:
            logger.error(f"Invalid setup status for completion: {setup.status}")
            raise HTTPException(
                status_code=400,
                detail=f"Нельзя завершить наладку в статусе '{setup.status}'. Ожидался статус 'started' или 'allowed'"
            )

        # Получаем информацию о станке и детали
        machine = db.query(MachineDB).filter(MachineDB.id == setup.machine_id).first()
        part = db.query(PartDB).filter(PartDB.id == setup.part_id).first()
        lot = db.query(LotDB).filter(LotDB.id == setup.lot_id).first()
        operator = db.query(EmployeeDB).filter(EmployeeDB.id == setup.employee_id).first()

        logger.info(f"Related data - Machine: {machine.name if machine else 'Not found'}, "
                   f"Part: {part.drawing_number if part else 'Not found'}, "
                   f"Lot: {lot.lot_number if lot else 'Not found'}, "
                   f"Operator: {operator.full_name if operator else 'Not found'}")

        # Обновить статус и время завершения
        setup.status = 'completed'
        setup.end_time = datetime.now()
        logger.info(f"Updated setup status to 'completed' and set end_time to {setup.end_time}")

        # Проверяем, есть ли наладка в очереди
        queued_setup = db.query(SetupDB).filter(
            SetupDB.machine_id == setup.machine_id,
            SetupDB.status == 'queued',
            SetupDB.end_time == None
        ).order_by(SetupDB.created_at.asc()).first()

        if queued_setup:
            logger.info(f"Found queued setup {queued_setup.id}, activating it")
            # Активируем следующую наладку
            queued_setup.status = 'created'

        try:
            db.commit()
            logger.info("Successfully committed changes to database")
            db.refresh(setup)
            
            # Проверяем, завершены ли все наладки для лота и обновляем статус лота
            await check_lot_completion_and_update_status(setup.lot_id, db)
            
        except Exception as db_error:
            logger.error(f"Database error during commit: {db_error}")
            db.rollback()
            raise HTTPException(status_code=500, detail="Database error while completing setup")

        # Отправляем уведомление администраторам
        try:
            admin_notification = {
                "type": "setup_completed",
                "data": {
                    "machine_name": machine.name if machine else "Unknown",
                    "drawing_number": part.drawing_number if part else "Unknown",
                    "lot_number": lot.lot_number if lot else "Unknown",
                    "operator_name": operator.full_name if operator else "Unknown",
                    "completion_time": setup.end_time.isoformat() if setup.end_time else None,
                    "planned_quantity": setup.planned_quantity,
                    "additional_quantity": setup.additional_quantity
                }
            }
            logger.info(f"Prepared admin notification: {admin_notification}")

            # Отправляем уведомление через notification_service
            asyncio.create_task(send_setup_approval_notifications(
                db=db, 
                setup_id=setup.id, 
                notification_type="completion"
            ))
            logger.info("Notification task created")
        except Exception as notification_error:
            logger.error(f"Error preparing notification: {notification_error}")
            # Не прерываем выполнение, если уведомление не удалось отправить

        logger.info("=== Setup completion successful ===")
        return {
            "success": True,
            "message": "Наладка успешно завершена",
            "setup": {
                "id": setup.id,
                "status": setup.status,
                "end_time": setup.end_time
            }
        }

    except HTTPException as http_exc:
        logger.error(f"HTTP Exception in complete_setup: {http_exc}")
        raise http_exc
    except Exception as e:
        logger.error(f"Unexpected error in complete_setup: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Internal server error while completing setup {setup_id}: {str(e)}")

# --- BATCH MANAGEMENT ENDPOINTS ---

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
        from_attributes = True # Pydantic v2, было orm_mode
        populate_by_name = True # Pydantic v2, было allow_population_by_field_name

class StartInspectionPayload(BaseModel):
    inspector_id: int

class InspectBatchPayload(BaseModel):
    inspector_id: int
    good_quantity: int
    rejected_quantity: int
    rework_quantity: int
    qc_comment: Optional[str] = None

class BatchMergePayload(BaseModel):
    batch_ids: List[int]
    target_location: str

@app.get("/lots/{lot_id}/batches", response_model=List[BatchViewItem])
async def get_batches_for_lot(lot_id: int, db: Session = Depends(get_db_session)):
    """Вернуть ВСЕ НЕАРХИВНЫЕ батчи для указанного лота (ВРЕМЕННО)."""
    try:
        # Убираем фильтр по otk_visible_locations, оставляем только != 'archived'
        batches = db.query(BatchDB, PartDB, LotDB, EmployeeDB).select_from(BatchDB) \
            .join(LotDB, BatchDB.lot_id == LotDB.id) \
            .join(PartDB, LotDB.part_id == PartDB.id) \
            .outerjoin(EmployeeDB, BatchDB.operator_id == EmployeeDB.id) \
            .filter(BatchDB.lot_id == lot_id) \
            .filter(BatchDB.current_location != 'archived') \
            .all()

        result = []
        for row in batches:
            batch_obj, part_obj, lot_obj, emp_obj = row
            result.append({
                'id': batch_obj.id,
                'lot_id': lot_id,
                'drawing_number': part_obj.drawing_number if part_obj else None,
                'lot_number': lot_obj.lot_number if lot_obj else None,
                'current_quantity': batch_obj.current_quantity,
                'current_location': batch_obj.current_location,
                'batch_time': batch_obj.batch_time.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=3))) if batch_obj.batch_time else None,
                'warehouse_received_at': batch_obj.warehouse_received_at, 
                'operator_name': emp_obj.full_name if emp_obj else None, 
            })
        return result
    except Exception as e:
        logger.error(f"Error fetching batches for lot {lot_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while fetching batches")

@app.post("/batches/{batch_id}/start-inspection")
async def start_batch_inspection(batch_id: int, payload: StartInspectionPayload, db: Session = Depends(get_db_session)):
    """Пометить батч как начатый к инспекции."""
    try:
        batch = db.query(BatchDB).filter(BatchDB.id == batch_id).first()
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")
        if batch.current_location not in ['warehouse_counted', 'sorting_warehouse', 'sorting']:
            raise HTTPException(status_code=400, detail="Batch cannot be inspected in its current state")
        batch.current_location = 'inspection'
        db.commit()
        db.refresh(batch)
        return {'success': True, 'batch': batch}
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error starting inspection for batch {batch_id}: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error while starting inspection")

@app.post("/batches/{batch_id}/inspect")
async def inspect_batch(batch_id: int, payload: InspectBatchPayload, db: Session = Depends(get_db_session)):
    """Разделить батч на good / defect / rework."""
    try:
        batch = db.query(BatchDB).filter(BatchDB.id == batch_id).first()
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")
        if batch.current_location not in ['inspection', 'warehouse_counted', 'sorting_warehouse', 'sorting']:
            raise HTTPException(status_code=400, detail="Batch is not in inspection state")

        total_requested = payload.good_quantity + payload.rejected_quantity + payload.rework_quantity
        if total_requested > batch.current_quantity:
            raise HTTPException(status_code=400, detail="Sum of quantities exceeds batch size")

        # Архивируем исходный батч
        batch.current_location = 'archived'

        created_batches = []
        def _create_child(qty: int, location: str):
            if qty <= 0:
                return None
            child = BatchDB(
                setup_job_id=batch.setup_job_id,
                lot_id=batch.lot_id,
                initial_quantity=qty,
                current_quantity=qty,
                recounted_quantity=None,
                current_location=location,
                operator_id=payload.inspector_id,
                parent_batch_id=batch.id,
                batch_time=datetime.now(),
            )
            db.add(child)
            db.flush()
            created_batches.append(child)
            return child

        _create_child(payload.good_quantity, 'good')
        _create_child(payload.rejected_quantity, 'defect')
        _create_child(payload.rework_quantity, 'rework_repair')

        db.commit()

        # Проверяем автоматическое закрытие лота после создания финальных батчей
        # (только для good и defect, rework_repair - промежуточный статус)
        try:
            logger.info(f"Calling check_lot_auto_completion for lot {batch.lot_id} after batch inspection")
            await check_lot_auto_completion(batch.lot_id, db)
            logger.info(f"Successfully completed check_lot_auto_completion for lot {batch.lot_id}")
        except Exception as auto_completion_error:
            logger.error(f"Error during auto-completion check for lot {batch.lot_id}: {auto_completion_error}")
            # Не прерываем выполнение, если проверка авто-закрытия не удалась

        return {
            'success': True,
            'created_batch_ids': [b.id for b in created_batches]
        }
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error inspecting batch {batch_id}: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error while inspecting batch")

# --- NEW BATCH MOVE ENDPOINT ---
class BatchMovePayload(BaseModel):
    target_location: str
    inspector_id: Optional[int] = None  # ID пользователя, выполняющего перемещение (для финальных статусов)
    # Можно добавить employee_id, если нужно отслеживать, кто переместил
    # employee_id: Optional[int] = None

@app.post("/batches/{batch_id}/move", response_model=BatchViewItem) # Используем BatchViewItem для ответа
async def move_batch(
    batch_id: int, 
    payload: BatchMovePayload, 
    db: Session = Depends(get_db_session)
):
    """Переместить батч в новую локацию."""
    try:
        batch = db.query(BatchDB).filter(BatchDB.id == batch_id).first()
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")
        
        if batch.current_location == 'archived':
            raise HTTPException(status_code=400, detail="Cannot move an archived batch")

        target_location = payload.target_location.strip()
        if not target_location:
            raise HTTPException(status_code=400, detail="Target location cannot be empty")
        
        # TODO: В идеале, здесь нужна валидация target_location по списку допустимых BatchLocation,
        #       аналогично тому, как это сделано на фронте с locationMap.
        #       Пока что принимаем любую непустую строку.

        logger.info(f"Moving batch {batch.id} from {batch.current_location} to {target_location}")

        # Проверяем, перемещается ли батч в финальное состояние QC
        final_qc_locations = ['good', 'defect']
        if target_location in final_qc_locations:
            # Если батч перемещается в финальное состояние, должен быть указан inspector_id
            if payload.inspector_id:
                # Проверяем, что inspector_id существует
                inspector = db.query(EmployeeDB).filter(EmployeeDB.id == payload.inspector_id).first()
                if not inspector:
                    raise HTTPException(status_code=400, detail=f"Inspector with ID {payload.inspector_id} not found")
                
                # Устанавливаем qc_inspector_id для отслеживания кто проверил
                batch.qc_inspector_id = payload.inspector_id
                batch.qc_inspected_at = datetime.now()
                logger.info(f"Batch {batch.id} moved to final QC state '{target_location}' by inspector {inspector.full_name} (ID: {payload.inspector_id})")
            else:
                # Если inspector_id не указан, но это финальное состояние - ошибка
                raise HTTPException(
                    status_code=400, 
                    detail=f"inspector_id is required when moving batch to final QC state '{target_location}'"
                )

        batch.current_location = target_location
        batch.updated_at = datetime.now() # Явно обновляем время изменения
        
        # Если нужно отслеживать, кто переместил:
        # if payload.employee_id:
        #     # Здесь можно обновить поле вроде batch.last_moved_by_id = payload.employee_id
        #     pass

        db.commit()
        db.refresh(batch)

        # Проверяем автоматическое закрытие лота, если батч перемещен в финальный статус QC
        if target_location in ['good', 'defect', 'archived']:
            try:
                await check_lot_auto_completion(batch.lot_id, db)
            except Exception as auto_completion_error:
                logger.error(f"Error during auto-completion check for lot {batch.lot_id}: {auto_completion_error}")
                # Не прерываем выполнение, если проверка авто-закрытия не удалась

        # Получаем связанные данные для ответа BatchViewItem
        lot = db.query(LotDB).filter(LotDB.id == batch.lot_id).first()
        part = db.query(PartDB).filter(PartDB.id == lot.part_id).first() if lot else None
        operator = db.query(EmployeeDB).filter(EmployeeDB.id == batch.operator_id).first()

        return BatchViewItem(
            id=batch.id,
            lot_id=batch.lot_id,
            drawing_number=part.drawing_number if part else None,
            lot_number=lot.lot_number if lot else None,
            current_quantity=batch.current_quantity,
            current_location=batch.current_location,
            batch_time=batch.batch_time,
            warehouse_received_at=batch.warehouse_received_at,
            operator_name=operator.full_name if operator else None
        )

    except HTTPException as http_exc:
        db.rollback() # Откатываем только если это наша HTTPException, иначе внешний try-except обработает
        raise http_exc
    except Exception as e:
        db.rollback()
        logger.error(f"Error moving batch {batch_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while moving batch")

# --- END NEW BATCH MOVE ENDPOINT ---

# --- WAREHOUSE ACCEPTANCE ENDPOINTS ---

class WarehousePendingBatchItem(BaseModel):
    id: int
    lot_id: int
    drawing_number: Optional[str]
    lot_number: Optional[str]
    # Убираем alias, используем имя поля из БД
    current_quantity: int 
    batch_time: Optional[datetime]
    operator_name: Optional[str]
    machine_name: Optional[str]  # Добавляем поле для названия станка
    card_number: Optional[int] = None  # Добавляем номер карточки
    current_location: str  # Добавляем статус батча для различения обычных и на переборку

    class Config:
        from_attributes = True # Pydantic v2, было orm_mode
        populate_by_name = True # Pydantic v2, было allow_population_by_field_name

class AcceptWarehousePayload(BaseModel):
    recounted_quantity: int
    warehouse_employee_id: int

@app.get("/warehouse/batches-pending", response_model=List[WarehousePendingBatchItem])
async def get_warehouse_pending_batches(db: Session = Depends(get_db_session)):
    """Получить список батчей, ожидающих приемки на склад (статус 'production' или 'sorting')."""
    try:
        # Используем алиасы для большей читаемости и избежания конфликтов
        SetupAlias = aliased(SetupDB)
        MachineAlias = aliased(MachineDB)

        batches_query = (
            db.query(
                BatchDB,
                PartDB.drawing_number,
                LotDB.lot_number,
                EmployeeDB.full_name.label("operator_name"),
                MachineAlias.name.label("machine_name"),
                CardDB.card_number
            )
            .join(LotDB, BatchDB.lot_id == LotDB.id)
            .join(PartDB, LotDB.part_id == PartDB.id)
            .outerjoin(EmployeeDB, BatchDB.operator_id == EmployeeDB.id)
            .outerjoin(SetupAlias, BatchDB.setup_job_id == SetupAlias.id)
            .outerjoin(MachineAlias, SetupAlias.machine_id == MachineAlias.id)
            .outerjoin(CardDB, BatchDB.id == CardDB.batch_id)
            .filter(BatchDB.current_location.in_(['production', 'sorting']))
            .order_by(BatchDB.batch_time.asc())
        )
        
        batches_data = batches_query.all()

        result = []
        for row in batches_data:
            batch_obj, drawing_number, lot_number, operator_name, machine_name, card_number = row
            item_data = {
                'id': batch_obj.id,
                'lot_id': batch_obj.lot_id,
                'drawing_number': drawing_number,
                'lot_number': lot_number,
                'current_quantity': batch_obj.current_quantity,
                'batch_time': convert_to_israel_timezone(batch_obj.batch_time),
                'operator_name': operator_name,
                'machine_name': machine_name,
                'card_number': card_number,
                'current_location': batch_obj.current_location,
            }
            result.append(WarehousePendingBatchItem.model_validate(item_data))
        return result
    except Exception as e:
        logger.error(f"Error fetching warehouse pending batches: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while fetching pending batches")

@app.post("/batches/{batch_id}/accept-warehouse")
async def accept_batch_on_warehouse(batch_id: int, payload: AcceptWarehousePayload, db: Session = Depends(get_db_session)):
    """Принять батч на склад: обновить кол-во и статус."""
    try:
        # Получаем батч и связанные сущности одним запросом (OUTER JOIN'ы, чтобы не падать на отсутствующих связях)
        batch_data = db.query(BatchDB, LotDB, PartDB, MachineDB)\
            .outerjoin(LotDB, BatchDB.lot_id == LotDB.id)\
            .outerjoin(PartDB, LotDB.part_id == PartDB.id)\
            .outerjoin(SetupDB, BatchDB.setup_job_id == SetupDB.id)\
            .outerjoin(MachineDB, SetupDB.machine_id == MachineDB.id)\
            .filter(BatchDB.id == batch_id)\
            .first()

        if not batch_data:
            raise HTTPException(status_code=404, detail="Batch not found")
        
        batch, lot, part, machine = batch_data

        if batch.current_location not in ['production', 'sorting']:
            raise HTTPException(status_code=400, detail=f"Batch is not in an acceptable state for warehouse acceptance (current: {batch.current_location}). Expected 'production' or 'sorting'.")
        
        warehouse_employee = db.query(EmployeeDB).filter(EmployeeDB.id == payload.warehouse_employee_id).first()
        if not warehouse_employee:
            raise HTTPException(status_code=404, detail="Warehouse employee not found")

        if warehouse_employee.role_id not in [3, 6]: # Предполагаем ID 3=admin, 6=warehouse
             logger.warning(f"Employee {payload.warehouse_employee_id} with role {warehouse_employee.role_id} tried to accept batch.")
             raise HTTPException(status_code=403, detail="Insufficient permissions for warehouse acceptance")

        # Получаем оператора производства ДО перезаписи operator_id
        original_operator_id = batch.operator_id
        original_operator = db.query(EmployeeDB).filter(EmployeeDB.id == original_operator_id).first() if original_operator_id else None

        # Сохраняем кол-во от оператора и вводим кол-во кладовщика
        operator_reported_qty = batch.current_quantity # Это кол-во ДО приемки
        recounted_clerk_qty = payload.recounted_quantity
        
        # Записываем исторические данные
        batch.operator_reported_quantity = operator_reported_qty
        batch.recounted_quantity = recounted_clerk_qty
        
        # Рассчитываем и сохраняем расхождения
        batch.discrepancy_absolute = None
        batch.discrepancy_percentage = None 
        batch.admin_acknowledged_discrepancy = False
        notification_task = None

        if operator_reported_qty is not None: # Если было какое-то кол-во от оператора
            difference = recounted_clerk_qty - operator_reported_qty
            batch.discrepancy_absolute = difference
            
            if operator_reported_qty != 0:
                percentage_diff = abs(difference / operator_reported_qty) * 100
                batch.discrepancy_percentage = round(percentage_diff, 2)

                if percentage_diff > 10.0:  # 🔄 ИЗМЕНЕНО: порог с 5% на 10%
                    logger.warning(
                        f"Critical discrepancy for batch {batch.id}: "
                        f"Operator Qty: {operator_reported_qty}, "
                        f"Clerk Qty: {recounted_clerk_qty}, "
                        f"Diff: {difference} ({percentage_diff:.2f}%)"
                    )
                    
                    discrepancy_details = {
                        "batch_id": batch.id,
                        "drawing_number": part.drawing_number if part else 'N/A',
                        "lot_number": lot.lot_number if lot else 'N/A',
                        "machine_name": machine.name if machine else 'N/A',
                        "operator_name": original_operator.full_name if original_operator else 'Неизвестный оператор',
                        "warehouse_employee_name": warehouse_employee.full_name,
                        "original_qty": operator_reported_qty,
                        "recounted_qty": recounted_clerk_qty,
                        "discrepancy_abs": difference,
                        "discrepancy_perc": round(percentage_diff, 2)
                    }
                    notification_task = asyncio.create_task(
                        send_batch_discrepancy_alert(db=db, discrepancy_details=discrepancy_details)
                    )
            # Если operator_reported_qty == 0, процент не считаем, но абсолютное расхождение сохраняем
            # Можно добавить отдельное уведомление если operator=0, а clerk > 0?

        # Обновляем основные поля батча
        batch.current_quantity = recounted_clerk_qty # Актуальное кол-во теперь = пересчитанному кладовщиком
        
        # Определяем новый статус в зависимости от текущего
        if batch.current_location == 'sorting':
            batch.current_location = 'sorting_warehouse'  # Батчи на переборку с склада
        else:
            batch.current_location = 'warehouse_counted'  # Обычные батчи
            
        batch.warehouse_employee_id = payload.warehouse_employee_id
        batch.warehouse_received_at = datetime.now()
        # НЕ МЕНЯЕМ operator_id! Оставляем оригинального оператора для истории
        # batch.operator_id остается прежним - это оператор, который делал деталь
        batch.updated_at = datetime.now() 

        # --- АВТОМАТИЧЕСКИЙ ВОЗВРАТ КАРТОЧКИ ---
        # Ищем карточку, которая была привязана к этому батчу
        card = db.query(CardDB).filter(CardDB.batch_id == batch_id).first()
        if card:
            logger.info(f"Card #{card.card_number} (machine {card.machine_id}) was associated with batch {batch_id}. Returning to circulation.")
            card.status = 'free'
            card.batch_id = None
            card.last_event = datetime.now()
        else:
            # Это может быть нормально, если батч был создан без карточки (например, старая система)
            logger.info(f"No card found for accepted batch {batch_id}. Nothing to return.")
        # -----------------------------------------

        db.commit()
        db.refresh(batch)

        # Если была создана задача уведомления, дожидаемся ее (опционально, но безопасно)
        # Либо можно не ждать, если не критично, что запрос завершится до отправки уведомления
        # if notification_task:
        #     await notification_task 
        
        logger.info(f"Batch {batch_id} accepted on warehouse by employee {payload.warehouse_employee_id} with quantity {payload.recounted_quantity}")
        
        return {'success': True, 'message': 'Batch accepted successfully'}

    except HTTPException as http_exc:
        # Не откатываем здесь, так как db.commit() еще не было или ошибка до него
        raise http_exc
    except Exception as e:
        logger.error(f"Error accepting batch {batch_id} on warehouse: {e}", exc_info=True)
        db.rollback() # Откатываем, если ошибка произошла во время расчетов или до commit
        raise HTTPException(status_code=500, detail="Internal server error while accepting batch")

# --- END WAREHOUSE ACCEPTANCE ENDPOINTS ---

# --- LOTS MANAGEMENT ENDPOINTS ---

# <<< НОВЫЕ Pydantic МОДЕЛИ ДЛЯ LOT >>>
from enum import Enum

class LotStatus(str, Enum):
    """Статусы лотов для синхронизации между Telegram-ботом и FastAPI"""
    NEW = "new"                    # Новый лот от Order Manager
    IN_PRODUCTION = "in_production"  # Лот в производстве (после начала наладки)
    POST_PRODUCTION = "post_production"  # Лот после производства (все наладки завершены)
    CLOSED = "closed"              # Закрытый лот (все батчи проверены и распределены)
    CANCELLED = "cancelled"        # Отмененный лот
    ACTIVE = "active"             # Устаревший статус (для совместимости)

class LotBase(BaseModel):
    lot_number: str
    part_id: int
    initial_planned_quantity: Optional[int] = None # <--- СДЕЛАНО ОПЦИОНАЛЬНЫМ
    due_date: Optional[datetime] = None
    # Статус будет устанавливаться по умолчанию на бэкенде

class LotCreate(LotBase):
    # order_manager_id и created_by_order_manager_at будут добавлены на бэкенде
    # Обновляем для временного решения: клиент может передавать эти поля
    order_manager_id: Optional[int] = None
    created_by_order_manager_at: Optional[datetime] = None

class LotResponse(LotBase):
    id: int
    order_manager_id: Optional[int] = None
    created_by_order_manager_at: Optional[datetime] = None
    status: LotStatus
    created_at: Optional[datetime] = None # <--- СДЕЛАНО ОПЦИОНАЛЬНЫМ
    total_planned_quantity: Optional[int] = None # Общее количество (плановое + дополнительное)
    part: Optional[PartResponse] = None # Для возврата информации о детали вместе с лотом
    machine_name: Optional[str] = None  # 🔄 Название станка последней активной наладки

    class Config:
        from_attributes = True # <--- ИСПРАВЛЕНО с orm_mode
# <<< КОНЕЦ НОВЫХ Pydantic МОДЕЛЕЙ ДЛЯ LOT >>>

# <<< НОВЫЙ ЭНДПОИНТ POST /lots/ >>>
@app.post("/lots/", response_model=LotResponse, status_code=201, tags=["Lots"])
async def create_lot(
    lot_data: LotCreate, 
    db: Session = Depends(get_db_session),
    # current_user: EmployeeDB = Depends(get_current_active_user) # Раскомментировать, когда аутентификация будет готова
):
    try: # <--- НАЧАЛО БОЛЬШОГО TRY-БЛОКА
        logger.info(f"Запрос на создание лота: {lot_data.model_dump()}")

        # Проверка существования детали
        part = db.query(PartDB).filter(PartDB.id == lot_data.part_id).first()
        if not part:
            logger.warning(f"Деталь с ID {lot_data.part_id} не найдена при создании лота.")
            raise HTTPException(status_code=404, detail=f"Part with id {lot_data.part_id} not found")

        # Проверка уникальности номера лота
        existing_lot = db.query(LotDB).filter(LotDB.lot_number == lot_data.lot_number).first()
        if existing_lot:
            logger.warning(f"Лот с номером {lot_data.lot_number} уже существует (ID: {existing_lot.id}).")
            raise HTTPException(status_code=409, detail=f"Lot with lot_number '{lot_data.lot_number}' already exists.")

        db_lot_data = lot_data.model_dump(exclude_unset=True)
        logger.debug(f"Данные для создания LotDB (после model_dump): {db_lot_data}")
        
        # Если order_manager_id передан, а created_by_order_manager_at нет, устанавливаем текущее время
        if db_lot_data.get("order_manager_id") is not None:
            if db_lot_data.get("created_by_order_manager_at") is None:
                db_lot_data["created_by_order_manager_at"] = datetime.now(timezone.utc) # Используем UTC
        
        # Проверка ключевых полей перед созданием объекта
        if 'part_id' not in db_lot_data or db_lot_data['part_id'] is None:
            logger.error("Критическая ошибка: part_id отсутствует или None в db_lot_data перед созданием LotDB.")
            raise HTTPException(status_code=500, detail="Internal error: part_id is missing for LotDB creation.")
        
        if 'lot_number' not in db_lot_data or not db_lot_data['lot_number']:
            logger.error("Критическая ошибка: lot_number отсутствует или пуст в db_lot_data перед созданием LotDB.")
            raise HTTPException(status_code=500, detail="Internal error: lot_number is missing for LotDB creation.")

        logger.info(f"Попытка создать объект LotDB с данными: {db_lot_data} и status='{LotStatus.NEW.value}'")
        db_lot = LotDB(**db_lot_data, status=LotStatus.NEW.value) # Статус 'new' по умолчанию
        logger.info(f"Объект LotDB создан в памяти (ID пока нет).")

        db.add(db_lot)
        logger.info("Объект LotDB добавлен в сессию SQLAlchemy.")
        
        try:
            logger.info("Попытка выполнить db.flush().")
            db.flush()
            logger.info("db.flush() выполнен успешно.")
        except Exception as flush_exc:
            db.rollback()
            logger.error(f"Ошибка во время db.flush() при создании лота: {flush_exc}", exc_info=True)
            logger.error(f"Полный трейсбек ошибки flush: {traceback.format_exc()}")
            raise HTTPException(status_code=500, detail=f"Database flush error: {str(flush_exc)}")

        db.commit()
        logger.info("db.commit() выполнен успешно.")
        db.refresh(db_lot)
        logger.info(f"Лот '{db_lot.lot_number}' успешно создан с ID {db_lot.id}.")
        
        # Для LotResponse нам нужна информация о детали
        # Явно загружаем деталь, если она не была загружена через joinedload/selectinload в LotDB
        # или если LotResponse требует этого. В нашем случае LotResponse имеет part: Optional[PartResponse]
        # SQLAlchemy должен автоматически подтянуть связанную деталь, если сессия активна
        # и db_lot.part доступно.
        # Но для надежности можно сделать так, если возникают проблемы:
        # if not db_lot.part: # Это не сработает, т.к. part это relationship
        #    db_lot.part = db.query(PartDB).filter(PartDB.id == db_lot.part_id).first()
        # logger.info(f"Подготовленный для ответа лот: {db_lot}, связанная деталь: {db_lot.part}")

        return db_lot
    
    except HTTPException as http_e:
        # db.rollback() # FastAPI обработчик ошибок позаботится об этом, если транзакция была начата
        logger.error(f"HTTPException при создании лота: {http_e.status_code} - {http_e.detail}")
        raise http_e # Перебрасываем дальше, чтобы FastAPI вернул корректный HTTP ответ
    
    except IntegrityError as int_e:
        db.rollback()
        logger.error(f"IntegrityError при создании лота: {int_e}", exc_info=True)
        detailed_error = str(int_e.orig) if hasattr(int_e, 'orig') and int_e.orig else str(int_e)
        logger.error(f"Полный трейсбек IntegrityError: {traceback.format_exc()}")
        
        # Попытка определить, какое ограничение было нарушено
        if "uq_lot_number_global" in detailed_error.lower():
             raise HTTPException(status_code=409, detail=f"Lot with lot_number '{lot_data.lot_number}' already exists (race condition or concurrent request). Possible original error: {detailed_error}")
        elif "lots_part_id_fkey" in detailed_error.lower():
             raise HTTPException(status_code=404, detail=f"Part with id {lot_data.part_id} not found (race condition or concurrent request). Possible original error: {detailed_error}")
        else:
            raise HTTPException(status_code=500, detail=f"Database integrity error occurred. Possible original error: {detailed_error}")

    except Exception as e:
        db.rollback()
        logger.error(f"НЕПРЕДВИДЕННАЯ ОШИБКА СЕРВЕРА при создании лота: {e}", exc_info=True)
        detailed_traceback = traceback.format_exc()
        logger.error(f"ПОЛНЫЙ ТРЕЙСБЕК НЕПРЕДВИДЕННОЙ ОШИБКИ:\n{detailed_traceback}")
        # Временно возвращаем трейсбек в detail для отладки
        raise HTTPException(status_code=500, detail=f"Unexpected server error. Traceback: {detailed_traceback}")

# Эндпоинт для получения списка лотов (пример, может потребовать доработки)
@app.get("/lots/", response_model=List[LotResponse], tags=["Lots"])
async def get_lots(
    response: Response, 
    search: Optional[str] = Query(None, description="Поисковый запрос для номера лота"),
    part_search: Optional[str] = Query(None, description="Поисковый запрос для номера детали"),
    status_filter: Optional[str] = Query(None, description="Фильтр по статусам (через запятую, например: new,in_production)"),
    skip: int = Query(0, ge=0, description="Количество записей для пропуска (пагинация)"),
    limit: int = Query(100, ge=1, le=500, description="Максимальное количество записей для возврата (пагинация)"),
    db: Session = Depends(get_db_session)
):
    """
    Получить список всех лотов.
    Поддерживает поиск по `lot_number` (номер лота) и отдельный поиск по `drawing_number` (номер чертежа связанной детали) (частичное совпадение без учета регистра).
    Поддерживает пагинацию через `skip` и `limit`.
    Сортировка по убыванию ID лота (новые сверху).
    """
    query = db.query(LotDB).options(selectinload(LotDB.part))

    # Комбинированный поиск: если переданы оба параметра, используем OR
    if search and part_search:
        from sqlalchemy import or_  # локальный импорт для минимального изменения верхних импортов
        search_term = f"%{search.lower()}%"
        part_search_term = f"%{part_search.lower()}%"
        query = (
            query
            .outerjoin(LotDB.part)
            .filter(
                or_(
                    func.lower(LotDB.lot_number).like(search_term),
                    func.lower(PartDB.drawing_number).like(part_search_term)
                )
            )
        )
    else:
        # Поиск по номеру лота
        if search:
            search_term = f"%{search.lower()}%"
            query = query.filter(func.lower(LotDB.lot_number).like(search_term))
        # Поиск по номеру детали
        if part_search:
            part_search_term = f"%{part_search.lower()}%"
            query = query.join(LotDB.part).filter(func.lower(PartDB.drawing_number).like(part_search_term))
    
    # Фильтрация по статусам
    if status_filter:
        statuses = [status.strip() for status in status_filter.split(',') if status.strip()]
        if statuses:
            query = query.filter(LotDB.status.in_(statuses))
    
    total_count = query.count() 

    lots = query.order_by(LotDB.id.desc()).offset(skip).limit(limit).all()
    logger.info(f"Запрос списка лотов: search='{search}', part_search='{part_search}', skip={skip}, limit={limit}. Возвращено {len(lots)} из {total_count} лотов.")
    
    response.headers["X-Total-Count"] = str(total_count)
    # ---------- Добавляем название станка из последней активной наладки ----------
    if lots:
        lot_ids = [lot.id for lot in lots]
        active_statuses = ['created', 'started', 'pending_qc', 'allowed', 'in_production']
        setup_rows = (
            db.query(SetupDB.lot_id, MachineDB.name, SetupDB.created_at)
              .join(MachineDB, SetupDB.machine_id == MachineDB.id)
              .filter(SetupDB.lot_id.in_(lot_ids))
              .order_by(SetupDB.lot_id, SetupDB.created_at.desc())
              .all()
        )
        machine_map: Dict[int, str] = {}
        for lot_id, machine_name, _ in setup_rows:
            if lot_id not in machine_map:  # берем самый свежий (первый в сортировке)
                machine_map[lot_id] = machine_name

        for lot in lots:
            lot.machine_name = machine_map.get(lot.id)

    return lots

# <<< НОВЫЙ ЭНДПОИНТ ДЛЯ ОБНОВЛЕНИЯ СТАТУСА ЛОТА >>>
class LotStatusUpdate(BaseModel):
    status: LotStatus

class LotQuantityUpdate(BaseModel):
    additional_quantity: int = Field(..., ge=0, description="Дополнительное количество (неотрицательное число)")

@app.patch("/lots/{lot_id}/status", response_model=LotResponse, tags=["Lots"])
async def update_lot_status(
    lot_id: int,
    status_update: LotStatusUpdate,
    db: Session = Depends(get_db_session)
):
    """
    Обновить статус лота.
    Используется для синхронизации статусов между Telegram-ботом и FastAPI.
    """
    try:
        # Найти лот
        lot = db.query(LotDB).options(selectinload(LotDB.part)).filter(LotDB.id == lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail=f"Lot with id {lot_id} not found")
        
        old_status = lot.status
        lot.status = status_update.status.value
        
        db.commit()
        db.refresh(lot)
        
        logger.info(f"Статус лота {lot_id} обновлен с '{old_status}' на '{status_update.status.value}'")
        
        return lot
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка при обновлении статуса лота {lot_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while updating lot status")

# <<< ЭНДПОИНТ ДЛЯ ПОЛУЧЕНИЯ ОДНОГО ЛОТА >>>
@app.get("/lots/{lot_id}", response_model=LotResponse, tags=["Lots"])
async def get_lot(lot_id: int, db: Session = Depends(get_db_session)):
    """
    Получить информацию о конкретном лоте.
    """
    lot = db.query(LotDB).options(selectinload(LotDB.part)).filter(LotDB.id == lot_id).first()
    if not lot:
        raise HTTPException(status_code=404, detail=f"Lot with id {lot_id} not found")
    
    return lot

@app.patch("/lots/{lot_id}/quantity")
async def update_lot_quantity(
    lot_id: int,
    quantity_update: LotQuantityUpdate,
    db: Session = Depends(get_db_session)
):
    """
    Обновляет дополнительное количество для лота.
    Сохраняет additional_quantity в setup_jobs и пересчитывает total_planned_quantity в lots.
    """
    try:
        # Найти лот
        lot = db.query(LotDB).filter(LotDB.id == lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail="Лот не найден")
        
        # Найти setup_job для этого лота
        setup_job = db.query(SetupDB).filter(SetupDB.lot_id == lot_id).first()
        if not setup_job:
            raise HTTPException(status_code=404, detail="Наладка для лота не найдена")
        
        # ЖЕЛЕЗНАЯ СИНХРОНИЗАЦИЯ: planned_quantity = initial_planned_quantity
        initial_quantity = lot.initial_planned_quantity or 0
        setup_job.planned_quantity = initial_quantity
        
        # Обновить additional_quantity в setup_jobs
        setup_job.additional_quantity = quantity_update.additional_quantity
        
        # Пересчитать total_planned_quantity в lots
        lot.total_planned_quantity = initial_quantity + quantity_update.additional_quantity
        
        # Сохранить изменения
        db.commit()
        
        logger.info(f"Обновлено количество для лота {lot_id}: additional={quantity_update.additional_quantity}, total={lot.total_planned_quantity}")
        
        return {
            "success": True,
            "message": "Количество успешно обновлено",
            "lot_id": lot_id,
            "initial_planned_quantity": initial_quantity,
            "additional_quantity": quantity_update.additional_quantity,
            "total_planned_quantity": lot.total_planned_quantity
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка при обновлении количества лота {lot_id}: {e}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")

# Pydantic модель для обновления начального количества
class LotInitialQuantityUpdate(BaseModel):
    """Модель для обновления начального количества лота"""
    initial_planned_quantity: int = Field(ge=1, description="Начальное планируемое количество")

@app.patch("/lots/{lot_id}/initial-quantity")
async def update_lot_initial_quantity(
    lot_id: int,
    quantity_update: LotInitialQuantityUpdate,
    db: Session = Depends(get_db_session)
):
    """
    Обновляет начальное количество для лота.
    Синхронизирует с setup_jobs.planned_quantity и пересчитывает total_planned_quantity.
    """
    try:
        # Найти лот
        lot = db.query(LotDB).filter(LotDB.id == lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail="Лот не найден")
        
        # Найти setup_job для этого лота
        setup_job = db.query(SetupDB).filter(SetupDB.lot_id == lot_id).first()
        if not setup_job:
            raise HTTPException(status_code=404, detail="Наладка для лота не найдена")
        
        # Обновить initial_planned_quantity в lots
        lot.initial_planned_quantity = quantity_update.initial_planned_quantity
        
        # Синхронизировать с setup_jobs.planned_quantity
        setup_job.planned_quantity = quantity_update.initial_planned_quantity
        
        # Пересчитать total_planned_quantity
        additional_quantity = setup_job.additional_quantity or 0
        lot.total_planned_quantity = quantity_update.initial_planned_quantity + additional_quantity
        
        # Сохранить изменения
        db.commit()
        
        logger.info(f"Обновлено начальное количество для лота {lot_id}: initial={quantity_update.initial_planned_quantity}, total={lot.total_planned_quantity}")
        
        return {
            "success": True,
            "message": "Начальное количество успешно обновлено",
            "lot_id": lot_id,
            "initial_planned_quantity": quantity_update.initial_planned_quantity,
            "additional_quantity": additional_quantity,
            "total_planned_quantity": lot.total_planned_quantity
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Ошибка при обновлении начального количества лота {lot_id}: {e}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")

@app.patch("/lots/{lot_id}/close", response_model=LotResponse, tags=["Lots"])
async def close_lot(lot_id: int, db: Session = Depends(get_db_session)):
    """
    Закрыть лот (перевести в статус 'closed').
    Доступно только для лотов в статусе 'post_production'.
    """
    try:
        lot = db.query(LotDB).options(selectinload(LotDB.part)).filter(LotDB.id == lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail=f"Лот с ID {lot_id} не найден")
        
        logger.info(f"Attempting to close lot {lot_id} (current status: {lot.status})")
        
        if lot.status != 'post_production':
            raise HTTPException(
                status_code=400, 
                detail=f"Нельзя закрыть лот в статусе '{lot.status}'. Ожидался статус 'post_production'"
            )
        
        # Обновляем статус лота
        lot.status = 'closed'
        db.commit()
        db.refresh(lot)
        
        logger.info(f"Successfully closed lot {lot_id}")
        
        # Синхронизируем с Telegram-ботом
        try:
            await sync_lot_status_to_telegram_bot(lot_id, 'closed')
        except Exception as sync_error:
            logger.error(f"Failed to sync lot closure to Telegram bot: {sync_error}")
            # Не прерываем выполнение, если синхронизация не удалась
        
        return lot
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error closing lot {lot_id}: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера при закрытии лота: {str(e)}")

# <<< КОНЕЦ НОВЫХ ЭНДПОИНТОВ ДЛЯ ЛОТОВ >>>

# === ОТЧЕТНОСТЬ И АНАЛИТИКА ДЛЯ ORDER MANAGER ===

@app.get("/reports/lots-summary", response_model=LotSummaryReport, tags=["Reports"])
async def get_lots_summary_report(
    order_manager_id: Optional[int] = Query(None, description="ID менеджера заказов для фильтрации"),
    status_filter: Optional[str] = Query(None, description="Фильтр по статусу лота"),
    date_from: Optional[datetime] = Query(None, description="Начальная дата для фильтрации"),
    date_to: Optional[datetime] = Query(None, description="Конечная дата для фильтрации"),
    db: Session = Depends(get_db_session)
):
    """
    Получить сводный отчет по лотам.
    Включает статистику по статусам, количествам и производительности.
    """
    try:
        # Базовый запрос
        query = db.query(LotDB)
        
        # Применяем фильтры
        if order_manager_id:
            query = query.filter(LotDB.order_manager_id == order_manager_id)
        if status_filter:
            query = query.filter(LotDB.status == status_filter)
        if date_from:
            query = query.filter(LotDB.created_at >= date_from)
        if date_to:
            query = query.filter(LotDB.created_at <= date_to)
        
        lots = query.all()
        total_lots = len(lots)
        
        # Статистика по статусам
        lots_by_status = {}
        for lot in lots:
            status = lot.status or 'unknown'
            lots_by_status[status] = lots_by_status.get(status, 0) + 1
        
        # Подсчет количеств
        total_planned_quantity = sum(lot.initial_planned_quantity or 0 for lot in lots)
        
        # Подсчет произведенного количества через батчи
        lot_ids = [lot.id for lot in lots]
        if lot_ids:
            produced_batches = db.query(BatchDB).filter(
                BatchDB.lot_id.in_(lot_ids),
                BatchDB.current_location.in_(['warehouse_counted', 'good', 'defect', 'rework_repair'])
            ).all()
            total_produced_quantity = sum(batch.current_quantity for batch in produced_batches)
        else:
            total_produced_quantity = 0
        
        # Расчет среднего времени выполнения
        completed_lots = [lot for lot in lots if lot.status == 'completed']
        if completed_lots:
            completion_times = []
            for lot in completed_lots:
                if lot.created_at and lot.created_by_order_manager_at:
                    # Ищем время завершения последней наладки
                    last_setup = db.query(SetupDB).filter(
                        SetupDB.lot_id == lot.id,
                        SetupDB.status == 'completed'
                    ).order_by(SetupDB.end_time.desc()).first()
                    
                    if last_setup and last_setup.end_time:
                        completion_time = (last_setup.end_time - lot.created_by_order_manager_at).total_seconds() / 3600
                        completion_times.append(completion_time)
            
            average_completion_time_hours = sum(completion_times) / len(completion_times) if completion_times else None
        else:
            average_completion_time_hours = None
        
        # Расчет процента выполнения в срок
        on_time_count = 0
        lots_with_due_date = [lot for lot in completed_lots if lot.due_date]
        
        for lot in lots_with_due_date:
            last_setup = db.query(SetupDB).filter(
                SetupDB.lot_id == lot.id,
                SetupDB.status == 'completed'
            ).order_by(SetupDB.end_time.desc()).first()
            
            if last_setup and last_setup.end_time and last_setup.end_time <= lot.due_date:
                on_time_count += 1
        
        on_time_delivery_rate = (on_time_count / len(lots_with_due_date)) * 100 if lots_with_due_date else 0.0
        
        return LotSummaryReport(
            total_lots=total_lots,
            lots_by_status=lots_by_status,
            total_planned_quantity=total_planned_quantity,
            total_produced_quantity=total_produced_quantity,
            average_completion_time_hours=average_completion_time_hours,
            on_time_delivery_rate=on_time_delivery_rate
        )
        
    except Exception as e:
        logger.error(f"Ошибка при генерации сводного отчета по лотам: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при генерации отчета: {str(e)}")




@app.get("/reports/production-performance", response_model=ProductionPerformanceReport, tags=["Reports"])
async def get_production_performance_report(
    date_from: datetime = Query(..., description="Начальная дата периода"),
    date_to: datetime = Query(..., description="Конечная дата периода"),
    order_manager_id: Optional[int] = Query(None, description="ID менеджера заказов для фильтрации"),
    db: Session = Depends(get_db_session)
):
    """
    Получить отчет по производительности за указанный период.
    """
    try:
        # Базовый запрос для наладок в периоде
        setups_query = db.query(SetupDB).filter(
            SetupDB.created_at >= date_from,
            SetupDB.created_at <= date_to
        )
        
        if order_manager_id:
            setups_query = setups_query.join(LotDB).filter(LotDB.order_manager_id == order_manager_id)
        
        setups = setups_query.options(
            selectinload(SetupDB.machine),
            selectinload(SetupDB.operator)
        ).all()
        
        # Базовый запрос для батчей в периоде
        batches_query = db.query(BatchDB).filter(
            BatchDB.batch_time >= date_from,
            BatchDB.batch_time <= date_to
        )
        
        if order_manager_id:
            batches_query = batches_query.join(LotDB).filter(LotDB.order_manager_id == order_manager_id)
        
        batches = batches_query.all()
        
        # Подсчет основных метрик
        total_setups = len(setups)
        total_batches = len(batches)
        total_produced_quantity = sum(batch.current_quantity for batch in batches)
        
        # Среднее время цикла
        cycle_times = [setup.cycle_time_seconds for setup in setups if setup.cycle_time_seconds]
        average_cycle_time_seconds = sum(cycle_times) / len(cycle_times) if cycle_times else None
        
        # Использование станков (процент времени работы)
        machine_utilization = {}
        for setup in setups:
            if setup.machine and setup.start_time and setup.end_time:
                machine_name = setup.machine.name
                work_time = (setup.end_time - setup.start_time).total_seconds()
                
                if machine_name not in machine_utilization:
                    machine_utilization[machine_name] = 0
                machine_utilization[machine_name] += work_time
        
        # Конвертируем в проценты (предполагаем 8-часовой рабочий день)
        period_hours = (date_to - date_from).total_seconds() / 3600
        working_hours_per_day = 8
        max_work_time = min(period_hours, working_hours_per_day * ((date_to - date_from).days + 1))
        
        for machine_name in machine_utilization:
            utilization_hours = machine_utilization[machine_name] / 3600
            machine_utilization[machine_name] = (utilization_hours / max_work_time) * 100 if max_work_time > 0 else 0
        
        # Производительность операторов
        operator_productivity = {}
        for batch in batches:
            if batch.operator_id:
                operator = db.query(EmployeeDB).filter(EmployeeDB.id == batch.operator_id).first()
                if operator and operator.full_name:
                    operator_name = operator.full_name
                    if operator_name not in operator_productivity:
                        operator_productivity[operator_name] = 0
                    operator_productivity[operator_name] += batch.current_quantity
        
        return ProductionPerformanceReport(
            period_start=date_from,
            period_end=date_to,
            total_setups=total_setups,
            total_batches=total_batches,
            total_produced_quantity=total_produced_quantity,
            average_cycle_time_seconds=average_cycle_time_seconds,
            machine_utilization=machine_utilization,
            operator_productivity=operator_productivity
        )
        
    except Exception as e:
        logger.error(f"Ошибка при генерации отчета по производительности: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при генерации отчета: {str(e)}")

@app.get("/reports/quality", response_model=QualityReport, tags=["Reports"])
async def get_quality_report(
    date_from: datetime = Query(..., description="Начальная дата периода"),
    date_to: datetime = Query(..., description="Конечная дата периода"),
    order_manager_id: Optional[int] = Query(None, description="ID менеджера заказов для фильтрации"),
    db: Session = Depends(get_db_session)
):
    """
    Получить отчет по качеству за указанный период.
    """
    try:
        # Запрос батчей, прошедших инспекцию в периоде
        batches_query = db.query(BatchDB).filter(
            BatchDB.qc_date >= date_from,
            BatchDB.qc_date <= date_to,
            BatchDB.current_location.in_(['good', 'defect', 'rework_repair'])
        )
        
        if order_manager_id:
            batches_query = batches_query.join(LotDB).filter(LotDB.order_manager_id == order_manager_id)
        
        batches = batches_query.options(selectinload(BatchDB.lot).selectinload(LotDB.part)).all()
        
        # Подсчет количеств
        total_inspected_quantity = sum(batch.current_quantity for batch in batches)
        good_quantity = sum(batch.current_quantity for batch in batches if batch.current_location == 'good')
        defect_quantity = sum(batch.current_quantity for batch in batches if batch.current_location == 'defect')
        rework_quantity = sum(batch.current_quantity for batch in batches if batch.current_location == 'rework_repair')
        
        # Расчет процентов
        defect_rate = (defect_quantity / total_inspected_quantity) * 100 if total_inspected_quantity > 0 else 0.0
        rework_rate = (rework_quantity / total_inspected_quantity) * 100 if total_inspected_quantity > 0 else 0.0
        
        # Качество по чертежам
        quality_by_drawing = {}
        for batch in batches:
            if batch.lot and batch.lot.part:
                drawing_number = batch.lot.part.drawing_number
                if drawing_number not in quality_by_drawing:
                    quality_by_drawing[drawing_number] = {'good': 0, 'defect': 0, 'rework': 0}
                
                if batch.current_location == 'good':
                    quality_by_drawing[drawing_number]['good'] += batch.current_quantity
                elif batch.current_location == 'defect':
                    quality_by_drawing[drawing_number]['defect'] += batch.current_quantity
                elif batch.current_location == 'rework_repair':
                    quality_by_drawing[drawing_number]['rework'] += batch.current_quantity
        
        return QualityReport(
            period_start=date_from,
            period_end=date_to,
            total_inspected_quantity=total_inspected_quantity,
            good_quantity=good_quantity,
            defect_quantity=defect_quantity,
            rework_quantity=rework_quantity,
            defect_rate=defect_rate,
            rework_rate=rework_rate,
            quality_by_drawing=quality_by_drawing
        )
        
    except Exception as e:
        logger.error(f"Ошибка при генерации отчета по качеству: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при генерации отчета: {str(e)}")

# === КОНЕЦ ОТЧЕТНОСТИ ===

# --- START NEW ENDPOINT FOR SORTING LABELS ---
class BatchAvailabilityInfo(BaseModel):
    """Информация о доступности печати этикеток для станка"""
    machine_id: int
    machine_name: str
    has_active_batch: bool  # Есть ли активный батч в production
    has_any_batch: bool     # Есть ли любой батч (для переборки)
    last_batch_data: Optional[BatchLabelInfo] = None  # Данные последнего батча

@app.get("/machines/{machine_id}/batch-availability", response_model=BatchAvailabilityInfo)
async def get_batch_availability(machine_id: int, db: Session = Depends(get_db_session)):
    """
    Получить информацию о доступности печати этикеток для станка.
    Возвращает данные для обычных этикеток (только production) и этикеток на переборку (любой последний батч).
    """
    machine = db.query(MachineDB).filter(MachineDB.id == machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="Станок не найден")

    # Проверяем наличие активного батча в production
    active_batch = db.query(BatchDB)\
        .join(SetupDB, BatchDB.setup_job_id == SetupDB.id)\
        .filter(SetupDB.machine_id == machine_id)\
        .filter(BatchDB.current_location == 'production')\
        .order_by(desc(BatchDB.batch_time)) \
        .first()

    # Ищем последний батч независимо от статуса
    last_batch = db.query(BatchDB)\
        .join(SetupDB, BatchDB.setup_job_id == SetupDB.id)\
        .filter(SetupDB.machine_id == machine_id)\
        .order_by(desc(BatchDB.batch_time)) \
        .first()

    has_active_batch = active_batch is not None
    has_any_batch = last_batch is not None

    last_batch_data = None
    if last_batch:
        # Получаем данные для последнего батча (используем ту же логику что и в active-batch-label)
        setup = db.query(SetupDB).filter(SetupDB.id == last_batch.setup_job_id).first()
        part = db.query(PartDB).filter(PartDB.id == setup.part_id).first() if setup else None
        lot = db.query(LotDB).filter(LotDB.id == last_batch.lot_id).first()
        operator = db.query(EmployeeDB).filter(EmployeeDB.id == last_batch.operator_id).first()

        # Определение времени начала и конца батча
        determined_start_time: Optional[str] = None
        final_end_time_str = last_batch.batch_time.strftime("%H:%M") if last_batch.batch_time else None

        # Логика для determined_start_time
        if last_batch.initial_quantity == 0 and setup and setup.start_time:
            determined_start_time = setup.start_time.strftime("%H:%M")
        else:
            previous_direct_batch = None
            # Добавляем проверку на None для batch.created_at чтобы избежать ошибки SQLAlchemy
            if last_batch.created_at is not None:
                previous_direct_batch = db.query(BatchDB.batch_time)\
                    .filter(BatchDB.lot_id == last_batch.lot_id)\
                    .filter(BatchDB.setup_job_id == last_batch.setup_job_id)\
                    .filter(BatchDB.id != last_batch.id)\
                    .filter(BatchDB.created_at < last_batch.created_at)\
                    .order_by(desc(BatchDB.created_at)) \
                    .first()
            
            if previous_direct_batch and previous_direct_batch.batch_time:
                determined_start_time = previous_direct_batch.batch_time.strftime("%H:%M")
            elif setup and setup.start_time: # Fallback
                determined_start_time = setup.start_time.strftime("%H:%M")

        # Расчет количества
        final_initial_quantity = last_batch.initial_quantity
        final_current_quantity = last_batch.initial_quantity + last_batch.current_quantity
        final_batch_quantity = last_batch.current_quantity

        # Вычисляем смену
        calculated_shift = "N/A"
        if last_batch.batch_time:
            hour = last_batch.batch_time.hour
            if 6 <= hour < 18:
                calculated_shift = "1"
            else:
                calculated_shift = "2"

        last_batch_data = BatchLabelInfo(
            id=last_batch.id,
            lot_id=last_batch.lot_id,
            drawing_number=part.drawing_number if part else "N/A",
            lot_number=lot.lot_number if lot else "N/A",
            machine_name=machine.name,
            operator_name=operator.full_name if operator else "N/A",
            operator_id=last_batch.operator_id,
            batch_time=last_batch.batch_time,
            shift=calculated_shift,
            start_time=determined_start_time,
            end_time=final_end_time_str,
            initial_quantity=final_initial_quantity,
            current_quantity=final_current_quantity,
            batch_quantity=final_batch_quantity,
            warehouse_received_at=last_batch.warehouse_received_at,
            warehouse_employee_name=last_batch.operator_name,
            recounted_quantity=last_batch.recounted_quantity,
            qa_inspector_name=last_batch.qc_inspector_name
        )

    return BatchAvailabilityInfo(
        machine_id=machine_id,
        machine_name=machine.name,
        has_active_batch=has_active_batch,
        has_any_batch=has_any_batch,
        last_batch_data=last_batch_data
    )
# --- END NEW ENDPOINT FOR SORTING LABELS ---

# --- CARD SYSTEM ENDPOINTS ---

class CardUseRequest(BaseModel):
    """Запрос на использование карточки"""
    batch_id: int
    machine_id: Optional[int] = None  # Для батчей на переборку, где нет setup_job_id

class CardReservationRequest(BaseModel):
    """Запрос на резервирование карточки"""
    machine_id: int
    batch_id: int
    operator_id: int

class CardReservationResponse(BaseModel):
    """Ответ на резервирование карточки"""
    card_number: int
    machine_id: int
    batch_id: int
    operator_id: int
    reserved_until: datetime
    message: str

    class Config:
        from_attributes = True

class CardInfo(BaseModel):
    """Информация о карточке"""
    card_number: int
    machine_id: int
    machine_name: str
    status: str
    batch_id: Optional[int] = None
    last_event: datetime

    class Config:
        from_attributes = True

def find_machine_by_flexible_code(db: Session, machine_code: str) -> Optional[MachineDB]:
    """
    Гибкий поиск станка по коду (например: SR-32, SR32, sr 32, etc.)
    """
    # Убираем пробелы и дефисы, приводим к нижнему регистру
    clean_code = machine_code.replace('-', '').replace(' ', '').lower()
    
    # Ищем станки и пробуем найти совпадение
    machines = db.query(MachineDB).all()
    
    for machine in machines:
        if machine.name:
            # Очищаем имя станка для сравнения
            clean_machine_name = machine.name.replace('-', '').replace(' ', '').lower()
            
            # Проверяем точное совпадение
            if clean_machine_name == clean_code:
                return machine
                
            # Проверяем, содержится ли код в имени (для случаев типа "SR-32 Main")
            if clean_code in clean_machine_name or clean_machine_name in clean_code:
                return machine
    
    return None

@app.post("/cards/reserve", response_model=CardReservationResponse, tags=["Cards"])
async def reserve_card_transactional(data: CardReservationRequest, db: Session = Depends(get_db_session)):
    """
    🎯 НОВЫЙ ЭНДПОИНТ: Резервирование карточки с автоматическим назначением
    
    Решает проблему race condition:
    1. Атомарно находит и резервирует свободную карточку  
    2. Возвращает зарезервированную карточку оператору
    3. Автоматически освобождает карточку через 30 секунд если не использована
    """
    try:
        # Проверяем существование батча
        batch = db.query(BatchDB).filter(BatchDB.id == data.batch_id).first()
        if not batch:
            raise HTTPException(status_code=404, detail="Батч не найден")
        
        # Используем транзакцию для атомарного резервирования
        with db.begin():
            # Атомарно находим и резервируем первую свободную карточку
            result = db.execute(
                text("""UPDATE cards 
                       SET status = 'in_use', 
                           batch_id = :batch_id, 
                           last_event = NOW()
                       WHERE card_number = (
                           SELECT card_number 
                           FROM cards 
                           WHERE machine_id = :machine_id AND status = 'free' 
                           ORDER BY card_number 
                           LIMIT 1
                       ) AND machine_id = :machine_id AND status = 'free'
                       RETURNING card_number"""),
                {"machine_id": data.machine_id, "batch_id": data.batch_id}
            )
            
            reserved_card = result.fetchone()
            
            if not reserved_card:
                raise HTTPException(
                    status_code=409, 
                    detail="Нет свободных карточек для этого станка"
                )
            
            card_number = reserved_card[0]
            
            # Связь batch-card осуществляется через batch_id в таблице cards (уже обновлено выше)
        
        reserved_until = datetime.now() + timedelta(seconds=30)
        
        logger.info(f"Card {card_number} reserved for batch {data.batch_id} by operator {data.operator_id}")
        
        return CardReservationResponse(
            card_number=card_number,
            machine_id=data.machine_id,
            batch_id=data.batch_id,
            operator_id=data.operator_id,
            reserved_until=reserved_until,
            message=f"Карточка #{card_number} зарезервирована за батчем {data.batch_id}"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error reserving card for machine {data.machine_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка резервирования карточки")

@app.get("/cards/free", tags=["Cards"])
async def get_free_cards(
    machine_id: int, 
    limit: int = Query(4, ge=1, le=20, description="Количество карточек для возврата (по умолчанию 4)"),
    db: Session = Depends(get_db_session)
):
    """Получить список свободных карточек для станка (по умолчанию первые 4)"""
    try:
        # ПРАВИЛЬНАЯ ЛОГИКА: карточка занята ТОЛЬКО когда батч в 'production' или 'sorting'
        # После 'received' (принято на склад) карточка сразу освобождается!
        cards = db.execute(
            text("""
                SELECT c.card_number 
                FROM cards c
                LEFT JOIN batches b ON c.batch_id = b.id
                WHERE c.machine_id = :machine_id
                AND (
                    c.status = 'free' 
                    OR (
                        c.status = 'in_use' 
                        AND (
                            b.id IS NULL 
                            OR b.current_location NOT IN ('production', 'sorting')
                        )
                    )
                )
                ORDER BY c.card_number
                LIMIT :limit
            """),
            {"machine_id": machine_id, "limit": limit}
        ).fetchall()
        
        return {"cards": [card.card_number for card in cards]}
    except Exception as e:
        logger.error(f"Error fetching free cards for machine {machine_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while fetching free cards")

@app.get("/cards/used", tags=["Cards"])
async def get_cards_state(machine_id: int, db: Session = Depends(get_db_session)):
    """Возвращает полное состояние карточек (card_number, status, batch_id) для одного запроса фронту/боту."""
    try:
        rows = db.execute(
            text("""
                SELECT c.card_number, c.status, c.batch_id
                FROM cards c
                WHERE c.machine_id = :machine_id 
                ORDER BY c.card_number
            """), 
            {"machine_id": machine_id}
        ).fetchall()
        return {"cards": [{"card_number": r.card_number, "status": r.status, "batch_id": r.batch_id} for r in rows]}
    except Exception as e:
        logger.error(f"Error fetching cards state for machine {machine_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while fetching cards state")

@app.get("/cards/suggestions", tags=["Cards"])
async def get_card_suggestions(
    machine_id: int, 
    limit: int = Query(4, ge=1, le=8, description="Количество предлагаемых карточек (по умолчанию 4)"),
    db: Session = Depends(get_db_session)
):
    """
    🎯 УМНЫЙ АЛГОРИТМ ВЫБОРА КАРТОЧЕК
    Реализует логику из TG бота:
    1. Получает free/used карточки
    2. Применяет алгоритм "следующие после максимальной"
    3. Возвращает предлагаемые карточки для выбора
    """
    try:
        # Получаем свободные карточки
        free_cards_result = db.execute(
            text("""
                SELECT c.card_number 
                FROM cards c
                LEFT JOIN batches b ON c.batch_id = b.id
                WHERE c.machine_id = :machine_id
                AND (
                    c.status = 'free' 
                    OR (
                        c.status = 'in_use' 
                        AND (
                            b.id IS NULL 
                            OR b.current_location NOT IN ('production', 'sorting')
                        )
                    )
                )
                ORDER BY c.card_number
            """),
            {"machine_id": machine_id}
        ).fetchall()
        
        # Получаем используемые карточки
        used_cards_result = db.execute(
            text("""
                SELECT c.card_number 
                FROM cards c
                JOIN batches b ON c.batch_id = b.id
                WHERE c.machine_id = :machine_id 
                AND c.status = 'in_use'
                AND b.current_location IN ('production', 'sorting')
                ORDER BY c.card_number
            """), 
            {"machine_id": machine_id}
        ).fetchall()
        
        free_cards = [card.card_number for card in free_cards_result]
        used_cards = [card.card_number for card in used_cards_result]
        
        # 🎯 АЛГОРИТМ ВЫБОРА ИЗ TG БОТА:
        sorted_free_cards = sorted(free_cards)
        
        if used_cards:
            # Находим максимальную используемую карточку
            max_used_card = max(used_cards)
            
            # Ищем следующие свободные карточки после максимальной
            next_cards = [card for card in sorted_free_cards if card > max_used_card]
            
            if next_cards:
                # Есть карточки после максимальной - начинаем с них
                cards_to_show = next_cards[:limit]
            else:
                # Нет карточек после максимальной - начинаем сначала (по кругу)
                cards_to_show = sorted_free_cards[:limit]
        else:
            # Нет используемых карточек - начинаем с первых свободных
            cards_to_show = sorted_free_cards[:limit]
        
        # Если свободных карточек меньше лимита, показываем все что есть
        if len(cards_to_show) < limit and len(sorted_free_cards) < limit:
            cards_to_show = sorted_free_cards
        
        return {
            "cards": cards_to_show,
            "total_free": len(free_cards),
            "total_used": len(used_cards),
            "algorithm_info": {
                "max_used_card": max(used_cards) if used_cards else None,
                "cycled_to_start": bool(used_cards and not any(card > max(used_cards) for card in sorted_free_cards))
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting card suggestions for machine {machine_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while getting card suggestions")

@app.patch("/cards/{card_number}/use", tags=["Cards"])
async def use_card(card_number: int, data: CardUseRequest, db: Session = Depends(get_db_session)):
    """Занять карточку (ОДИН КЛИК) - optimistic locking"""
    try:
        # Сначала находим станок по batch_id
        batch = db.query(BatchDB).filter(BatchDB.id == data.batch_id).first()
        if not batch:
            raise HTTPException(status_code=404, detail="Батч не найден")
        
        # Получаем machine_id: либо из запроса (для батчей на переборку), либо из setup_job
        if data.machine_id:
            # Для батчей на переборку machine_id передается напрямую
            machine_id = data.machine_id
        else:
            # Для обычных батчей получаем machine_id из setup_job
            setup = db.query(SetupDB).filter(SetupDB.id == batch.setup_job_id).first()
            if not setup:
                raise HTTPException(status_code=404, detail="Наладка для батча не найдена")
            machine_id = setup.machine_id
        
        # Используем optimistic locking для исключения гонок
        result = db.execute(
            text("""UPDATE cards 
                   SET status = 'in_use', batch_id = :batch_id, last_event = NOW()
                   WHERE card_number = :card_number AND machine_id = :machine_id AND status = 'free'"""),
            {"card_number": card_number, "machine_id": machine_id, "batch_id": data.batch_id}
        )
        
        if result.rowcount == 0:
            # Проверяем, существует ли карточка
            card = db.query(CardDB).filter(
                CardDB.card_number == card_number, 
                CardDB.machine_id == machine_id
            ).first()
            if not card:
                raise HTTPException(status_code=404, detail="Карточка не найдена для этого станка")
            else:
                raise HTTPException(status_code=409, detail="Карточка уже занята")
        
        # Связь batch-card осуществляется через batch_id в таблице cards (уже обновлено выше)
        
        db.commit()
        
        logger.info(f"Card {card_number} (machine {machine_id}) successfully assigned to batch {data.batch_id}")
        return {"message": f"Карточка #{card_number} закреплена за батчем {data.batch_id}"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error using card {card_number}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while using card")

@app.patch("/cards/{card_number}/return", tags=["Cards"])
async def return_card(card_number: int, machine_id: int, db: Session = Depends(get_db_session)):
    """Вернуть карточку в оборот"""
    try:
        card = db.query(CardDB).filter(
            CardDB.card_number == card_number,
            CardDB.machine_id == machine_id
        ).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")
        
        # Сохраняем batch_id для очистки поля в батче
        batch_id = card.batch_id
        
        card.status = 'free'
        card.batch_id = None
        card.last_event = datetime.now()
        
        # Очищаем поле card_number в таблице batches
        if batch_id:
            db.execute(
                text("""UPDATE batches 
                       SET card_number = NULL
                       WHERE id = :batch_id"""),
                {"batch_id": batch_id}
            )
        
        db.commit()
        
        logger.info(f"Card {card_number} (machine {machine_id}) returned to circulation")
        return {"message": f"Карточка #{card_number} возвращена в оборот"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error returning card {card_number}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while returning card")

@app.patch("/cards/{card_number}/lost", tags=["Cards"])
async def mark_card_lost(card_number: int, machine_id: int, db: Session = Depends(get_db_session)):
    """Отметить карточку как потерянную"""
    try:
        card = db.query(CardDB).filter(
            CardDB.card_number == card_number,
            CardDB.machine_id == machine_id
        ).first()
        if not card:
            raise HTTPException(status_code=404, detail="Карточка не найдена")
        
        card.status = 'lost'
        card.last_event = datetime.now()
        
        db.commit()
        
        logger.info(f"Card {card_number} (machine {machine_id}) marked as lost")
        return {"message": f"Карточка #{card_number} отмечена как потерянная"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error marking card {card_number} as lost: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while marking card as lost")

@app.get("/cards/{card_number}/machine/{machine_code}", response_model=CardInfo, tags=["Cards"])
async def get_card_by_machine_code(card_number: int, machine_code: str, db: Session = Depends(get_db_session)):
    """Получить карточку по номеру и коду станка (гибкий поиск)"""
    try:
        # Ищем станок по гибкому коду
        machine = find_machine_by_flexible_code(db, machine_code)
        if not machine:
            raise HTTPException(status_code=404, detail=f"Станок с кодом '{machine_code}' не найден")
        
        # Ищем карточку
        card = db.query(CardDB).filter(
            CardDB.card_number == card_number,
            CardDB.machine_id == machine.id
        ).first()
        
        if not card:
            raise HTTPException(
                status_code=404, 
                detail=f"Карточка #{card_number} для станка {machine.name} не найдена"
            )
        
        return CardInfo(
            card_number=card.card_number,
            machine_id=card.machine_id,
            machine_name=machine.name,
            status=card.status,
            batch_id=card.batch_id,
            last_event=card.last_event
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting card {card_number} for machine {machine_code}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")

@app.get("/cards/search", tags=["Cards"])
async def search_card_by_number(card_number: int, db: Session = Depends(get_db_session)):
    """Поиск карточки только по номеру среди всех станков"""
    try:
        # Ищем все карточки с данным номером
        cards = db.query(CardDB).join(MachineDB).filter(
            CardDB.card_number == card_number
        ).all()
        
        if not cards:
            raise HTTPException(
                status_code=404, 
                detail=f"Карточка #{card_number} не найдена ни на одном станке"
            )
        
        # Если найдена только одна карточка, возвращаем её
        if len(cards) == 1:
            card = cards[0]
            machine = db.query(MachineDB).filter(MachineDB.id == card.machine_id).first()
            
            return {
                "card_number": card.card_number,
                "machine_id": card.machine_id,
                "machine_name": machine.name if machine else "Неизвестно",
                "status": card.status,
                "batch_id": card.batch_id,
                "last_event": card.last_event
            }
        
        # Если найдено несколько карточек, возвращаем список
        result = []
        for card in cards:
            machine = db.query(MachineDB).filter(MachineDB.id == card.machine_id).first()
            result.append({
                "card_number": card.card_number,
                "machine_id": card.machine_id,
                "machine_name": machine.name if machine else "Неизвестно",
                "status": card.status,
                "batch_id": card.batch_id,
                "last_event": card.last_event
            })
        
        return {"cards": result, "count": len(result)}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error searching card {card_number}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")

@app.get("/cards/{card_number}/batch", tags=["Cards"])
async def get_batch_by_card(card_number: int, machine_code: str = Query(..., description="Код станка (например: SR-32, SR32, sr32)"), db: Session = Depends(get_db_session)):
    """Получить информацию о батче по номеру карточки и коду станка для веб-дашборда"""
    try:
        # Ищем станок по гибкому коду
        machine = find_machine_by_flexible_code(db, machine_code)
        if not machine:
            raise HTTPException(status_code=404, detail=f"Станок с кодом '{machine_code}' не найден")
        
        # Ищем карточку
        card = db.query(CardDB).filter(
            CardDB.card_number == card_number,
            CardDB.machine_id == machine.id
        ).first()
        
        if not card:
            raise HTTPException(
                status_code=404, 
                detail=f"Карточка #{card_number} для станка {machine.name} не найдена"
            )
        
        # Если карточка свободна - нет батча
        if card.status == 'free' or not card.batch_id:
            raise HTTPException(
                status_code=404,
                detail=f"Карточка #{card_number} свободна, батч не найден"
            )
        
        # Получаем информацию о батче
        batch_query = db.query(BatchDB).join(SetupDB).join(LotDB).join(PartDB).join(MachineDB).outerjoin(
            EmployeeDB, SetupDB.employee_id == EmployeeDB.id
        ).filter(BatchDB.id == card.batch_id)
        
        batch_data = batch_query.first()
        
        if not batch_data:
            raise HTTPException(
                status_code=404,
                detail=f"Батч для карточки #{card_number} не найден в базе данных"
            )
        
        # Формируем ответ в формате, совместимом с существующим API
        return {
            "id": batch_data.id,
            "lot_id": batch_data.setup_job.lot.id,
            "drawing_number": batch_data.setup_job.lot.part.drawing_number,
            "lot_number": batch_data.setup_job.lot.lot_number,
            "machine_name": batch_data.setup_job.machine.name,
            "operator_name": batch_data.setup_job.employee.full_name if batch_data.setup_job.employee else None,
            "current_quantity": batch_data.current_quantity,
            "batch_time": batch_data.batch_time,
            "warehouse_received_at": batch_data.warehouse_received_at,
            "current_location": batch_data.current_location,
            "card_number": card.card_number,
            "card_status": card.status
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting batch by card {card_number} for machine {machine_code}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")

# --- END CARD SYSTEM ENDPOINTS ---

@app.get("/batches/{batch_id}/label-info", response_model=BatchLabelInfo)
async def get_batch_label_info(batch_id: int, db: Session = Depends(get_db_session)):
    """Получить данные для печати этикетки конкретного батча"""
    try:
        # Получаем батч
        batch = db.query(BatchDB).filter(BatchDB.id == batch_id).first()
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")

        # Получаем связанные данные
        setup = db.query(SetupDB).filter(SetupDB.id == batch.setup_job_id).first()
        part = db.query(PartDB).filter(PartDB.id == setup.part_id).first() if setup else None
        lot = db.query(LotDB).filter(LotDB.id == batch.lot_id).first()
        machine = db.query(MachineDB).filter(MachineDB.id == setup.machine_id).first() if setup else None
        operator = db.query(EmployeeDB).filter(EmployeeDB.id == batch.operator_id).first()

        # Получаем информацию о складском сотруднике, если батч был принят на склад
        warehouse_employee = None
        if batch.warehouse_employee_id:
            warehouse_employee = db.query(EmployeeDB).filter(EmployeeDB.id == batch.warehouse_employee_id).first()

        # Определение времени начала и конца батча (копируем логику из active-batch-label)
        determined_start_time: Optional[str] = None
        final_end_time_str = batch.batch_time.strftime("%H:%M") if batch.batch_time else None

        # Новая логика для determined_start_time
        if batch.initial_quantity == 0 and setup and setup.start_time:
            determined_start_time = setup.start_time.strftime("%H:%M")
        else:
            previous_direct_batch = None
            # Добавляем проверку на None для batch.created_at чтобы избежать ошибки SQLAlchemy
            if batch.created_at is not None:
                previous_direct_batch = db.query(BatchDB.batch_time)\
                    .filter(BatchDB.lot_id == batch.lot_id)\
                    .filter(BatchDB.setup_job_id == batch.setup_job_id)\
                    .filter(BatchDB.id != batch.id)\
                    .filter(BatchDB.created_at < batch.created_at)\
                    .order_by(desc(BatchDB.created_at)) \
                    .first()
            
            if previous_direct_batch and previous_direct_batch.batch_time:
                determined_start_time = previous_direct_batch.batch_time.strftime("%H:%M")
            elif setup and setup.start_time: # Fallback
                determined_start_time = setup.start_time.strftime("%H:%M")

        # Расчет количеств - ОКОНЧАТЕЛЬНАЯ ЛОГИКА
        # Для этикетки כמות нужно количество оператора = batch.operator_reported_quantity из БД
        
        initial_quantity = batch.initial_quantity  # Начальное показание счетчика

        # Получаем последние показания со станка (для информации)
        if setup and setup.machine_id:
            last_reading = db.query(ReadingDB.reading).filter(
                ReadingDB.machine_id == setup.machine_id
            ).order_by(desc(ReadingDB.created_at)).first()
            
            current_quantity = last_reading[0] if last_reading else (batch.initial_quantity + batch.current_quantity)
        else:
            # Fallback если нет setup или machine_id
            current_quantity = batch.initial_quantity + batch.current_quantity

        # ПРАВИЛЬНЫЙ batch_quantity = количество оператора из БД
        batch_quantity = batch.operator_reported_quantity or batch.current_quantity

        # Вычисляем смену (копируем логику из active-batch-label)
        calculated_shift = "N/A"
        if batch.batch_time:
            hour = batch.batch_time.hour
            if 6 <= hour < 18:
                calculated_shift = "1"  # Дневная смена
            else:
                calculated_shift = "2"  # Ночная смена

        # Получаем имя инспектора ОТК, если он есть в наладке
        qa_inspector = None
        if setup and setup.qa_id:
            qa_inspector = db.query(EmployeeDB).filter(EmployeeDB.id == setup.qa_id).first()

        return BatchLabelInfo(
            id=batch.id,
            lot_id=batch.lot_id,
            drawing_number=part.drawing_number if part else "N/A",
            lot_number=lot.lot_number if lot else "N/A",
            machine_name=machine.name if machine else "N/A",
            operator_name=operator.full_name if operator else "N/A",
            operator_id=batch.operator_id,
            factory_number=operator.factory_number if operator else None,
            batch_time=batch.batch_time,
            shift=calculated_shift,
            start_time=determined_start_time,
            end_time=final_end_time_str,
            initial_quantity=initial_quantity,
            current_quantity=current_quantity,
            batch_quantity=batch_quantity,
            warehouse_received_at=batch.warehouse_received_at,
            warehouse_employee_name=warehouse_employee.full_name if warehouse_employee else None,
            recounted_quantity=batch.recounted_quantity,
            qa_inspector_name=qa_inspector.full_name if qa_inspector else None # <-- ДОБАВЛЕНО
        )

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error fetching batch label info for batch {batch_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while fetching batch label info")

# ===================================================================
# ЕЖЕДНЕВЫЕ ОТЧЕТЫ ПРОИЗВОДСТВА (аналог Excel листов с датами)
# ===================================================================

class DailyProductionRecord(BaseModel):
    """Модель записи ежедневного отчета производства"""
    row_number: int
    morning_operator_name: str
    evening_operator_name: str
    machine_name: str
    part_code: str
    start_quantity: int
    morning_end_quantity: int
    evening_end_quantity: int
    cycle_time_seconds: int
    required_quantity_per_shift: Optional[float]
    morning_production: int
    morning_performance_percent: Optional[float]
    evening_production: int
    evening_performance_percent: Optional[float]
    machinist_name: Optional[str]
    planned_quantity: Optional[int]
    report_date: str
    generated_at: datetime

class DailyProductionReport(BaseModel):
    """Полный ежедневный отчет"""
    report_date: str
    total_machines: int
    records: List[DailyProductionRecord]
    summary: Dict

@app.get("/daily-production-report", response_model=DailyProductionReport, tags=["Daily Reports"])
async def get_daily_production_report(
    target_date: date = Query(default_factory=date.today, description="Дата для отчета (YYYY-MM-DD)"),
    db: Session = Depends(get_db_session)
):
    """
    Получить ежедневный отчет производства по дате
    
    Аналог Excel листов с датами (10.06.25, 09.06.25, etc.)
    Показывает производительность операторов по станкам за день
    """
    
    try:
        # SQL запрос для получения ежедневного отчета
        sql_query = text("""
        WITH daily_readings AS (
            SELECT 
                mr.employee_id,
                mr.machine_id,
                mr.reading as quantity,
                mr.created_at,
                e.full_name as operator_name,
                m.name as machine_name,
                
                CASE 
                    WHEN (DATE(mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem') = :target_date 
                          AND EXTRACT(HOUR FROM (mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem')) BETWEEN 6 AND 17) THEN 'morning'
                    WHEN (DATE(mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem') = :target_date 
                          AND EXTRACT(HOUR FROM (mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem')) >= 18) 
                         OR (DATE(mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem') = :target_date + INTERVAL '1 day' 
                             AND EXTRACT(HOUR FROM (mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem')) < 6) THEN 'evening'
                    ELSE NULL
                END as shift_type
                
            FROM machine_readings mr
            JOIN employees e ON mr.employee_id = e.id
            JOIN machines m ON mr.machine_id = m.id
            JOIN setup_jobs sj ON mr.setup_job_id = sj.id
            WHERE (
                -- Утренняя смена: 6:00-17:59 указанного дня (локальное время)
                (DATE(mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem') = :target_date 
                 AND EXTRACT(HOUR FROM (mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem')) BETWEEN 6 AND 17)
                OR
                -- Вечерняя смена: 18:00-23:59 указанного дня и 0:00-5:59 следующего дня (локальное время)
                (DATE(mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem') = :target_date 
                 AND EXTRACT(HOUR FROM (mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem')) >= 18)
                OR
                (DATE(mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem') = :target_date + INTERVAL '1 day' 
                 AND EXTRACT(HOUR FROM (mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem')) <= 5)
            )
            AND mr.setup_job_id IS NOT NULL
            -- ИСПРАВЛЕНО: включаем наладки активные в отчетный день (независимо от даты завершения)
            AND (
                -- Активные наладки
                (sj.status = 'started' AND sj.end_time IS NULL) 
                OR 
                -- Наладки, завершенные в отчетный день
                (sj.status = 'completed' AND DATE(sj.end_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem') = :target_date)
                OR
                -- Наладки, которые были активны в отчетный день, но завершились позже
                (sj.status = 'completed' 
                 AND sj.start_time <= DATE(:target_date) + INTERVAL '1 day'
                 AND sj.end_time >= DATE(:target_date))
            )
            AND e.is_active = true
            AND m.is_active = true
        ),
        
        start_readings AS (
            SELECT 
                m.id as machine_id,
                m.name as machine_name,
                CASE 
                    -- Показываем начальные показания только для актуальных станков
                    WHEN (
                        -- Станки с показаниями в отчетный период
                        EXISTS (
                            SELECT 1 FROM machine_readings mr 
                            WHERE mr.machine_id = m.id 
                            AND (
                                -- Утренняя смена: 6:00-17:59 отчетного дня
                                (DATE(mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem') = :target_date 
                                 AND EXTRACT(HOUR FROM (mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem')) BETWEEN 6 AND 17)
                                OR
                                -- Вечерняя смена: 18:00+ отчетного дня или до 5:59 следующего дня
                                (DATE(mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem') = :target_date 
                                 AND EXTRACT(HOUR FROM (mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem')) >= 18)
                                OR
                                (DATE(mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem') = :target_date + INTERVAL '1 day' 
                                 AND EXTRACT(HOUR FROM (mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem')) <= 5)
                            )
                        )
                        OR
                        -- Станки с активными наладками
                        EXISTS (
                            SELECT 1 FROM setup_jobs sj 
                            WHERE sj.machine_id = m.id 
                            AND sj.status IN ('started', 'created', 'pending_qc', 'allowed')
                            AND sj.end_time IS NULL
                        )
                        OR  
                        -- Станки с наладками, завершенными в отчетный день
                        EXISTS (
                            SELECT 1 FROM setup_jobs sj 
                            WHERE sj.machine_id = m.id 
                            AND sj.status = 'completed'
                            AND DATE(sj.end_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem') = :target_date
                        )
                        OR
                        -- Станки с наладками, которые были активны в отчетный день
                        EXISTS (
                            SELECT 1 FROM setup_jobs sj 
                            WHERE sj.machine_id = m.id 
                            AND sj.status = 'completed'
                            AND sj.start_time <= DATE(:target_date) + INTERVAL '1 day'
                            AND sj.end_time >= DATE(:target_date)
                        )
                    ) THEN 
                        COALESCE(
                            -- ПРИОРИТЕТ 1: Показания до 6:00 утра отчетного дня
                            (SELECT mr.reading
                             FROM machine_readings mr 
                             WHERE mr.machine_id = m.id 
                               AND (mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem')::time < '06:00:00'::time
                               AND DATE(mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem') = :target_date
                             ORDER BY mr.created_at DESC 
                             LIMIT 1
                            ),
                            -- ПРИОРИТЕТ 2: Последнее показание предыдущего дня
                            (SELECT mr.reading
                             FROM machine_readings mr 
                             WHERE mr.machine_id = m.id 
                               AND DATE(mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem') = :target_date - INTERVAL '1 day'
                             ORDER BY mr.created_at DESC 
                             LIMIT 1
                            ),
                            -- ПРИОРИТЕТ 3: Последнее известное показание вообще (для случаев простоя >1 дня)
                            (SELECT mr.reading
                             FROM machine_readings mr 
                             WHERE mr.machine_id = m.id 
                               AND DATE(mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem') < :target_date
                             ORDER BY mr.created_at DESC 
                             LIMIT 1
                            ),
                            -- ПРИОРИТЕТ 4: Последний резерв: 0
                            0
                        )
                    ELSE 
                        -- Для неактуальных станков показываем NULL (прочерк)
                        NULL
                END as start_quantity
            FROM machines m
            WHERE m.is_active = true
        ),
        
        shift_readings AS (
            SELECT 
                machine_id,
                machine_name,
                
                -- Утренняя смена: данные в диапазоне 6:00-18:00 отчетного дня
                MAX(CASE WHEN shift_type = 'morning' THEN operator_name END) as morning_operator,
                MAX(CASE WHEN shift_type = 'morning' THEN quantity END) as morning_end_quantity,
                
                -- Вечерняя смена: данные в диапазоне 18:00 отчетного дня - 6:00 следующего
                MAX(CASE WHEN shift_type = 'evening' THEN operator_name END) as evening_operator,
                MAX(CASE WHEN shift_type = 'evening' THEN quantity END) as evening_end_quantity
                
            FROM daily_readings
            WHERE shift_type IS NOT NULL
            GROUP BY machine_id, machine_name
        ),
        
        production_calc AS (
            SELECT
                st.machine_id,
                
                -- Вычисляем утреннее производство (NULL для неактуальных станков)
                CASE
                    WHEN st.start_quantity IS NULL THEN NULL  -- Неактуальные станки
                    WHEN COALESCE(sr.morning_end_quantity, st.start_quantity, 0) < COALESCE(st.start_quantity, 0)
                    THEN COALESCE(sr.morning_end_quantity, st.start_quantity, 0) -- Если счетчик сброшен, производство = конечное значение
                    ELSE COALESCE(sr.morning_end_quantity, st.start_quantity, 0) - COALESCE(st.start_quantity, 0)
                END as morning_production,
                
                -- Вычисляем вечернее производство (NULL для неактуальных станков)
                CASE
                    WHEN st.start_quantity IS NULL THEN NULL  -- Неактуальные станки
                    WHEN COALESCE(sr.evening_end_quantity, sr.morning_end_quantity, st.start_quantity, 0) < COALESCE(sr.morning_end_quantity, st.start_quantity, 0)
                    THEN COALESCE(sr.evening_end_quantity, sr.morning_end_quantity, st.start_quantity, 0) -- Если счетчик сброшен, производство = конечное значение
                    ELSE COALESCE(sr.evening_end_quantity, sr.morning_end_quantity, st.start_quantity, 0) - COALESCE(sr.morning_end_quantity, st.start_quantity, 0)
                END as evening_production
                
            FROM start_readings st
            LEFT JOIN shift_readings sr ON st.machine_id = sr.machine_id
        ),

        latest_setups AS (
            SELECT DISTINCT ON (m.id)
                m.id as machine_id,
                sj.part_id,
                sj.cycle_time,
                p.drawing_number as part_code,
                sj.planned_quantity,
                sj.employee_id as machinist_id,
                e.full_name as machinist_name
            FROM machines m
            LEFT JOIN setup_jobs sj ON m.id = sj.machine_id 
                AND (
                    -- ПРИОРИТЕТ 1: Активные наладки
                    (sj.status = 'started' AND sj.end_time IS NULL)
                    OR
                    -- ПРИОРИТЕТ 2: Наладки, завершенные в отчетный день
                    (sj.status = 'completed' AND DATE(sj.end_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem') = :target_date)
                    OR
                    -- ПРИОРИТЕТ 3: Наладки, которые были активны в отчетный день
                    (sj.status = 'completed' 
                     AND sj.start_time <= DATE(:target_date) + INTERVAL '1 day'
                     AND sj.end_time >= DATE(:target_date))
                )
            LEFT JOIN parts p ON sj.part_id = p.id
            LEFT JOIN employees e ON sj.employee_id = e.id
            WHERE m.is_active = true
            -- Сортируем так, чтобы активные наладки были приоритетнее завершенных
            ORDER BY m.id, 
                CASE WHEN sj.status = 'started' THEN 1 ELSE 2 END,
                sj.created_at DESC
        )
        
        SELECT 
            ROW_NUMBER() OVER (ORDER BY COALESCE(sr.machine_name, st.machine_name)) as row_number,
            
            COALESCE(sr.morning_operator, '--') as morning_operator_name,
            COALESCE(sr.evening_operator, '--') as evening_operator_name,
            
            COALESCE(sr.machine_name, st.machine_name) as machine_name,
            COALESCE(ls.part_code, '--') as part_code,
            
            -- Исходные показания для отображения (NULL для неактуальных станков)
            st.start_quantity,
            CASE 
                WHEN sr.morning_end_quantity IS NOT NULL THEN sr.morning_end_quantity
                WHEN st.start_quantity IS NOT NULL THEN st.start_quantity
                ELSE NULL
            END as morning_end_quantity,
            CASE 
                WHEN sr.evening_end_quantity IS NOT NULL THEN sr.evening_end_quantity
                WHEN sr.morning_end_quantity IS NOT NULL THEN sr.morning_end_quantity
                WHEN st.start_quantity IS NOT NULL THEN st.start_quantity
                ELSE NULL
            END as evening_end_quantity,
            
            COALESCE(ls.cycle_time, 0) as cycle_time_seconds,
            
            CASE 
                WHEN COALESCE(ls.cycle_time, 0) > 0 THEN (12 * 3600) / ls.cycle_time
                ELSE NULL
            END as required_quantity_per_shift,
            
            -- Используем вычисленное производство
            pc.morning_production,
            
            CASE 
                WHEN COALESCE(ls.cycle_time, 0) > 0 THEN 
                    (pc.morning_production * 100.0) / ((12 * 3600) / ls.cycle_time)
                ELSE NULL
            END as morning_performance_percent,
            
            -- Используем вычисленное производство
            pc.evening_production,
            
            CASE 
                WHEN COALESCE(ls.cycle_time, 0) > 0 THEN 
                    (pc.evening_production * 100.0) / ((12 * 3600) / ls.cycle_time)
                ELSE NULL
            END as evening_performance_percent,
            
            ls.machinist_name,
            ls.planned_quantity,
            
            :target_date as report_date,
            NOW() as generated_at

        FROM start_readings st
        LEFT JOIN shift_readings sr ON st.machine_id = sr.machine_id
        LEFT JOIN latest_setups ls ON st.machine_id = ls.machine_id
        LEFT JOIN production_calc pc ON st.machine_id = pc.machine_id

        ORDER BY st.machine_name;
        """)
        
        # Выполняем запрос
        result = db.execute(sql_query, {"target_date": target_date})
        rows = result.fetchall()
        
        # Формируем записи
        records = []
        for row in rows:
            record = DailyProductionRecord(
                row_number=row.row_number,
                morning_operator_name=row.morning_operator_name,
                evening_operator_name=row.evening_operator_name,
                machine_name=row.machine_name,
                part_code=row.part_code,
                # Конвертируем NULL в 0 для неактуальных станков
                start_quantity=row.start_quantity if row.start_quantity is not None else 0,
                morning_end_quantity=row.morning_end_quantity if row.morning_end_quantity is not None else 0,
                evening_end_quantity=row.evening_end_quantity if row.evening_end_quantity is not None else 0,
                cycle_time_seconds=row.cycle_time_seconds,
                required_quantity_per_shift=row.required_quantity_per_shift,
                # Конвертируем NULL в 0 для производства
                morning_production=row.morning_production if row.morning_production is not None else 0,
                morning_performance_percent=row.morning_performance_percent,
                evening_production=row.evening_production if row.evening_production is not None else 0,
                evening_performance_percent=row.evening_performance_percent,
                machinist_name=row.machinist_name,
                planned_quantity=row.planned_quantity,
                report_date=str(target_date),
                generated_at=row.generated_at
            )
            records.append(record)
        
        # Формируем сводку
        total_morning_production = sum(r.morning_production for r in records)
        total_evening_production = sum(r.evening_production for r in records)
        
        valid_morning_performances = [r.morning_performance_percent for r in records if r.morning_performance_percent is not None]
        valid_evening_performances = [r.evening_performance_percent for r in records if r.evening_performance_percent is not None]
        
        avg_morning_performance = sum(valid_morning_performances) / len(valid_morning_performances) if valid_morning_performances else 0
        avg_evening_performance = sum(valid_evening_performances) / len(valid_evening_performances) if valid_evening_performances else 0
        
        summary = {
            "total_morning_production": total_morning_production,
            "total_evening_production": total_evening_production,
            "total_daily_production": total_morning_production + total_evening_production,
            "average_morning_performance": round(avg_morning_performance, 2),
            "average_evening_performance": round(avg_evening_performance, 2),
            "active_machines": len(records),
            "machines_with_morning_operators": sum(1 for r in records if r.morning_operator_name != 'нет оператора'),
            "machines_with_evening_operators": sum(1 for r in records if r.evening_operator_name != 'нет оператора')
        }
        
        return DailyProductionReport(
            report_date=str(target_date),
            total_machines=len(records),
            records=records,
            summary=summary
        )
        
    except Exception as e:
        logger.error(f"Ошибка при генерации ежедневного отчета: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при генерации отчета: {str(e)}")

@app.get("/daily-production-dates", tags=["Daily Reports"])
async def get_available_dates(
    limit: int = Query(30, description="Количество дат для возврата"),
    db: Session = Depends(get_db_session)
):
    """
    Получить доступные даты для отчетов
    (дни, когда были записаны показания)
    """
    
    try:
        sql_query = text("""
        WITH localized AS (
            SELECT 
                -- если время < 06:00 локальное, относим к предыдущему дню (вечерняя смена)
                CASE 
                    WHEN EXTRACT(HOUR FROM (mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem')) < 6
                         THEN ((mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem') - INTERVAL '6 hour')::date
                    ELSE (mr.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem')::date
                END AS report_date
            FROM machine_readings mr
            JOIN employees e ON e.id = mr.employee_id
            WHERE e.is_active = true
        )
        SELECT report_date,
               COUNT(*) AS readings_count
        FROM localized
        GROUP BY report_date
        ORDER BY report_date DESC
        LIMIT :limit;
        """)
        
        result = db.execute(sql_query, {"limit": limit})
        rows = result.fetchall()
        
        return {
            "available_dates": [
                {
                    "date": str(row.report_date),
                    "readings_count": row.readings_count
                }
                for row in rows
            ]
        }
        
    except Exception as e:
        logger.error(f"Ошибка при получении доступных дат: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при получении доступных дат: {str(e)}")

# ===================================================================
# КОНЕЦ ЕЖЕДНЕВНЫХ ОТЧЕТОВ ПРОИЗВОДСТВА  
# ===================================================================

@app.get("/daily-completed-setups", tags=["Daily Reports"])
async def get_daily_completed_setups(
    target_date: date = Query(default_factory=date.today, description="Дата для получения завершенных сетапов"),
    db: Session = Depends(get_db_session)
):
    """
    Получить завершенные сетапы за конкретную дату.
    Аналогично логике morning report, но для конкретной даты.
    """
    
    try:
        sql_query = text("""
        WITH 
        last_readings AS (
            SELECT DISTINCT ON (sj.id)
                sj.id as setup_job_id,
                mr.reading as last_quantity,
                mr.created_at as last_reading_time
            FROM setup_jobs sj
            LEFT JOIN machine_readings mr ON sj.id = mr.setup_job_id
            WHERE sj.status = 'completed'
                AND DATE(sj.end_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem') = :target_date
                AND mr.setup_job_id IS NOT NULL
            ORDER BY sj.id, mr.created_at DESC
        ),
        completed_setups AS (
            SELECT 
                sj.id as setup_job_id,
                m.name as machine_name,
                p.drawing_number as part_code,
                l.lot_number,
                sj.planned_quantity,
                sj.additional_quantity,
                sj.cycle_time,
                sj.created_at as setup_created_at,
                sj.end_time as setup_completed_at,
                e.full_name as machinist_name,
                lr.last_quantity,
                EXTRACT(EPOCH FROM (sj.end_time - sj.created_at)) / 3600 as setup_duration_hours
            FROM setup_jobs sj
            JOIN machines m ON sj.machine_id = m.id
            LEFT JOIN parts p ON sj.part_id = p.id
            LEFT JOIN lots l ON sj.lot_id = l.id
            LEFT JOIN employees e ON sj.employee_id = e.id
            LEFT JOIN last_readings lr ON sj.id = lr.setup_job_id
            WHERE sj.status = 'completed'
                AND DATE(sj.end_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem') = :target_date
                AND m.is_active = true
        )
        SELECT 
            ROW_NUMBER() OVER (ORDER BY setup_completed_at DESC) as row_number,
            setup_job_id,
            machine_name,
            COALESCE(part_code, '-') as part_code,
            COALESCE(lot_number, '-') as lot_number,
            planned_quantity,
            COALESCE(additional_quantity, 0) as additional_quantity,
            (planned_quantity + COALESCE(additional_quantity, 0)) as total_planned_quantity,
            COALESCE(last_quantity, 0) as final_quantity,
            cycle_time,
            setup_created_at,
            setup_completed_at,
            COALESCE(machinist_name, '-') as machinist_name,
            ROUND(setup_duration_hours::numeric, 2) as setup_duration_hours,
            
            -- Процент выполнения
            CASE 
                WHEN (planned_quantity + COALESCE(additional_quantity, 0)) > 0 THEN
                    ROUND((COALESCE(last_quantity, 0) * 100.0) / (planned_quantity + COALESCE(additional_quantity, 0)), 1)
                ELSE 0
            END as completion_percent
            
        FROM completed_setups
        ORDER BY setup_completed_at DESC;
        """)
        
        result = db.execute(sql_query, {"target_date": target_date})
        rows = result.fetchall()
        
        # Формируем записи
        completed_setups = []
        for row in rows:
            setup = {
                "row_number": row.row_number,
                "setup_job_id": row.setup_job_id,
                "machine_name": row.machine_name,
                "part_code": row.part_code,
                "lot_number": row.lot_number,
                "planned_quantity": row.planned_quantity,
                "additional_quantity": row.additional_quantity,
                "total_planned_quantity": row.total_planned_quantity,
                "final_quantity": row.final_quantity,
                "cycle_time": row.cycle_time,
                "setup_created_at": row.setup_created_at,
                "setup_completed_at": row.setup_completed_at,
                "machinist_name": row.machinist_name,
                "setup_duration_hours": float(row.setup_duration_hours),
                "completion_percent": float(row.completion_percent)
            }
            completed_setups.append(setup)
        
        return {
            "date": str(target_date),
            "total_completed_setups": len(completed_setups),
            "completed_setups": completed_setups
        }
        
    except Exception as e:
        logger.error(f"Ошибка при получении завершенных сетапов: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при получении завершенных сетапов: {str(e)}")

# ===================================================================
# Allowed setups (QC approved) for a given date
# ===================================================================

@app.get("/daily-allowed-setups", tags=["Daily Reports"])
async def get_daily_allowed_setups(
    target_date: date = Query(default_factory=date.today, description="Дата для получения разрешенных (allowed) сетапов"),
    db: Session = Depends(get_db_session)
):
    """
    Получить сетапы, получившие разрешение ОТК (allowed) в выбранную дату (по IL времени).
    Возвращает сокращенный набор полей, согласованный с UI.
    """
    try:
        sql_query = text("""
        WITH allowed_setups AS (
            SELECT 
                sj.id                      AS setup_job_id,
                m.name                     AS machine_name,
                p.drawing_number           AS part_code,
                l.lot_number               AS lot_number,
                sj.planned_quantity        AS planned_quantity,
                sj.created_at              AS setup_created_at,
                sj.qa_date                 AS setup_allowed_at,
                em.full_name               AS machinist_name,
                eq.full_name               AS qa_employee_name,
                EXTRACT(EPOCH FROM (sj.qa_date - sj.created_at)) / 3600.0 AS duration_created_to_allowed_hours,
                CASE 
                  WHEN sj.pending_qc_date IS NOT NULL THEN EXTRACT(EPOCH FROM (sj.pending_qc_date - sj.created_at)) / 3600.0 
                  ELSE NULL 
                END AS duration_setup_hours,
                CASE 
                  WHEN sj.pending_qc_date IS NOT NULL THEN EXTRACT(EPOCH FROM (sj.qa_date - sj.pending_qc_date)) / 3600.0 
                  ELSE NULL 
                END AS duration_qc_hours
            FROM setup_jobs sj
            JOIN machines m ON m.id = sj.machine_id
            LEFT JOIN parts p ON p.id = sj.part_id
            LEFT JOIN lots l ON l.id = sj.lot_id
            LEFT JOIN employees em ON em.id = sj.employee_id
            LEFT JOIN employees eq ON eq.id = sj.qa_id
            WHERE (
                -- Классический случай: есть qa_date в выбранный день (IL)
                (sj.qa_date IS NOT NULL
                 AND DATE(sj.qa_date AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem') = :target_date)
                OR
                -- Расширенный случай: qa_date ещё нет, но QA уже указан и сетап создан/запущен в выбранный день (IL)
                (sj.qa_date IS NULL
                 AND sj.qa_id IS NOT NULL
                 AND (
                      DATE(sj.created_at  AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem') = :target_date
                   OR DATE(sj.start_time  AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jerusalem') = :target_date
                 ))
            )
        )
        SELECT 
            setup_job_id,
            machine_name,
            COALESCE(part_code, '-') AS part_code,
            COALESCE(lot_number, '-') AS lot_number,
            planned_quantity,
            setup_created_at,
            setup_allowed_at,
            COALESCE(machinist_name, '-') AS machinist_name,
            COALESCE(qa_employee_name, '-') AS qa_employee_name,
            duration_created_to_allowed_hours,
            duration_setup_hours,
            duration_qc_hours
        FROM allowed_setups
        ORDER BY setup_allowed_at DESC
        """)

        rows = db.execute(sql_query, {"target_date": target_date}).fetchall()
        result = []
        for r in rows:
            result.append({
                "setup_job_id": r.setup_job_id,
                "machine_name": r.machine_name,
                "part_code": r.part_code,
                "lot_number": r.lot_number,
                "planned_quantity": int(r.planned_quantity) if r.planned_quantity is not None else 0,
                "setup_created_at": r.setup_created_at,
                "setup_allowed_at": r.setup_allowed_at,
                "machinist_name": r.machinist_name,
                "qa_employee_name": r.qa_employee_name,
                "duration_created_to_allowed_hours": float(r.duration_created_to_allowed_hours) if r.duration_created_to_allowed_hours is not None else None,
                "duration_setup_hours": float(r.duration_setup_hours) if r.duration_setup_hours is not None else None,
                "duration_qc_hours": float(r.duration_qc_hours) if r.duration_qc_hours is not None else None,
            })

        return {
            "date": str(target_date),
            "total_allowed_setups": len(result),
            "allowed_setups": result
        }
    except Exception as e:
        logger.error(f"Ошибка при получении allowed сетапов: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при получении allowed сетапов: {str(e)}")
# ===================================================================

# ===================================================================
# ВНИМАНИЕ! Файл main.py перегружен. НЕ ДОБАВЛЯЙТЕ сюда новый код.
# Создавайте новые роутеры в папке src/routers и подключайте их через
# app.include_router(...).   
# ===================================================================

# --- ADMIN UTILITY ENDPOINTS ---

class ResetCardsPayload(BaseModel):
    machine_name: str

@app.post("/admin/reset-cards-for-machine", tags=["Admin Tools"], summary="Экстренный сброс карточек станка")
async def reset_cards_for_machine(payload: ResetCardsPayload, db: Session = Depends(get_db_session)):
    """
    ЭКСТРЕННЫЙ ИНСТРУМЕНТ: Сбрасывает все 'in_use' карточки для указанного станка в статус 'free'.
    Использовать для исправления застрявших карточек после старой ошибки.
    """
    machine_name = payload.machine_name
    logger.warning(f"Starting emergency reset for machine '{machine_name}'...")

    try:
        machine = db.query(MachineDB).filter(func.lower(MachineDB.name) == func.lower(machine_name)).first()
        if not machine:
            raise HTTPException(status_code=404, detail=f"Станок с именем '{machine_name}' не найден.")

        cards_to_reset = db.query(CardDB).filter(
            CardDB.machine_id == machine.id,
            CardDB.status == 'in_use'
        ).all()

        if not cards_to_reset:
            return {"message": f"Для станка '{machine_name}' нет карточек в статусе 'in_use'. Ничего не сделано."}

        count = 0
        for card in cards_to_reset:
            card.status = 'free'
            card.batch_id = None
            card.last_event = datetime.now()
            count += 1
        
        db.commit()
        
        message = f"Успешно сброшено {count} карточек для станка '{machine_name}'."
        logger.warning(message)
        return {"message": message}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error during emergency reset for machine {machine_name}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера при сбросе карточек для станка {machine_name}")

# Подключение роутеров
app.include_router(events_router.router)
app.include_router(lots_router.router)
app.include_router(qc_router.router)
app.include_router(admin_router.router)
app.include_router(analytics_router.router)
app.include_router(warehouse_router.router)
app.include_router(catalog_router.router)
app.include_router(employees_router.router)
app.include_router(cards_router.router)
from .text2sql.routers import router as text2sql_router
app.include_router(text2sql_router)
