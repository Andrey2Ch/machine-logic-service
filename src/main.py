import logging
from dotenv import load_dotenv
import os
import traceback # <--- ДОБАВИТЬ ЭТОТ ИМПОРТ

# Загружаем переменные окружения из .env файла
# Это должно быть В САМОМ НАЧАЛЕ, до других импортов, использующих env vars
load_dotenv()

from fastapi import FastAPI, HTTPException, Query, Response # Добавил Response для заголовков пагинации, если понадобится
from fastapi.middleware.cors import CORSMiddleware
from src.models.setup import SetupStatus
from typing import Optional, Dict, List
from pydantic import BaseModel, Field
from enum import Enum
from sqlalchemy.orm import Session, aliased, selectinload
from fastapi import Depends, Body
from src.database import Base, initialize_database, get_db_session
from src.models.models import SetupDB, ReadingDB, MachineDB, EmployeeDB, PartDB, LotDB, BatchDB, CardDB
from datetime import datetime, timezone
from src.utils.sheets_handler import save_to_sheets
import asyncio
import httpx
import aiohttp
from src.services.notification_service import send_setup_approval_notifications, send_batch_discrepancy_alert
from sqlalchemy import func, desc, case, text, or_, and_
from sqlalchemy.exc import IntegrityError

# Duplicate lot schemas removed - importing from schemas
from src.schemas.lot import LotStatus, LotBase, LotCreate, LotResponse, LotQuantityUpdate

# Router imports
from src.routers.parts import router as parts_router
from src.routers.employees import router as employees_router  
from src.routers.machines import router as machines_router
from src.routers.readings import router as readings_router
from src.routers.setups import router as setups_router
from src.routers.batches import router as batches_router
from src.routers.lots import router as lots_router

logger = logging.getLogger(__name__)

app = FastAPI(title="Machine Logic Service", debug=True)

# Include routers
app.include_router(parts_router)
app.include_router(employees_router) 
app.include_router(machines_router)
app.include_router(readings_router)
app.include_router(setups_router)
app.include_router(batches_router)
app.include_router(lots_router)

# Возвращаем универсальное разрешение CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # <-- Снова разрешаем все источники
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"], 
    expose_headers=["X-Total-Count"]  # <--- ДОБАВЛЕНО ЗДЕСЬ
)


# Событие startup для инициализации БД
@app.on_event("startup")
async def startup_event():
    initialize_database()
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
    Получить показания для конкретного станка
    """
    readings = db.query(ReadingDB).filter(
        ReadingDB.machine_id == machine_id
    ).order_by(ReadingDB.created_at.desc()).limit(100).all()
    
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

# Добавляем Pydantic модель для ответа
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
        from_attributes = True # Pydantic v2, было orm_mode
        populate_by_name = True # Pydantic v2, было allow_population_by_field_name

# Duplicated /setups/qa-view endpoint removed - using router version

# Модель для тела запроса на утверждение
class ApproveSetupPayload(BaseModel):
    qa_id: int

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

        if setup.status != 'pending_qc':
            raise HTTPException(
                status_code=400,
                detail=f"Нельзя разрешить наладку в статусе '{setup.status}'. Ожидался статус 'pending_qc'"
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

# REMOVED: Duplicate /machines/operator-view endpoint - using router version
async def get_operator_machines_view(db: Session = Depends(get_db_session)):
    """
    Получает список ВСЕХ активных станков с информацией
    о последней активной наладке и последнем показании.
    """
    logger.info(f"Fetching operator machine view for ALL operators") # Обновляем лог
    try:
        # 1. Получаем все активные станки
        machines = db.query(MachineDB).filter(MachineDB.is_active == True).order_by(MachineDB.name).all()
        print("DEBUG: machines from DB:", machines)
        logger.debug(f"Found {len(machines)} active machines.")

        result_list = []
        active_setup_statuses = ['created', 'pending_qc', 'allowed', 'started']

        for machine in machines:
            logger.debug(f"Processing machine: {machine.name} (ID: {machine.id})")
            # 2. Находим последнее показание для станка
            last_reading_data = db.query(ReadingDB.reading, ReadingDB.created_at)\
                .filter(ReadingDB.machine_id == machine.id)\
                .order_by(ReadingDB.created_at.desc())\
                .first()
            logger.debug(f"Last reading data for machine {machine.id}: {last_reading_data}")

            # 3. Находим последнюю активную наладку и ее СТАТУС
            active_setup = db.query(
                    SetupDB.id, 
                    SetupDB.planned_quantity,
                    SetupDB.additional_quantity,
                    PartDB.drawing_number,
                    SetupDB.status # <-- Добавляем статус
                )\
                .join(PartDB, SetupDB.part_id == PartDB.id)\
                .filter(SetupDB.machine_id == machine.id)\
                .filter(SetupDB.status.in_(active_setup_statuses))\
                .filter(SetupDB.end_time == None)\
                .order_by(SetupDB.created_at.desc())\
                .first()
            
            # Формируем элемент ответа
            machine_view = OperatorMachineViewItem(
                id=machine.id,
                name=machine.name,
                last_reading=last_reading_data.reading if last_reading_data else None,
                last_reading_time=last_reading_data.created_at if last_reading_data else None,
                setup_id=active_setup.id if active_setup else None,
                drawing_number=active_setup.drawing_number if active_setup else None,
                planned_quantity=active_setup.planned_quantity if active_setup else None,
                additional_quantity=active_setup.additional_quantity if active_setup else None,
                status=active_setup.status if active_setup else 'idle'
            )
            result_list.append(machine_view)

        logger.info(f"Successfully prepared operator machine view")
        print("DEBUG: result to return:", result_list)
        return result_list

    except Exception as e:
        logger.error(f"Error fetching operator machine view: {e}", exc_info=True) # Обновляем лог
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
                'batch_time': batch_obj.batch_time,
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
        if batch.current_location != 'warehouse_counted':
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
        if batch.current_location not in ['inspection', 'warehouse_counted']:
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

@app.post("/batches/merge")
async def merge_batches(payload: BatchMergePayload, db: Session = Depends(get_db_session)):
    """Слить несколько батчей в один."""
    try:
        if len(payload.batch_ids) < 2:
            raise HTTPException(status_code=400, detail="Need at least two batches to merge")
        batches = db.query(BatchDB).filter(BatchDB.id.in_(payload.batch_ids)).all()
        if len(batches) != len(payload.batch_ids):
            raise HTTPException(status_code=404, detail="Some batches not found")
        lot_ids = set(b.lot_id for b in batches)
        if len(lot_ids) != 1:
            raise HTTPException(status_code=400, detail="Batches belong to different lots")

        total_qty = sum(b.current_quantity for b in batches)
        new_batch = BatchDB(
            setup_job_id=batches[0].setup_job_id,
            lot_id=batches[0].lot_id,
            initial_quantity=total_qty,
            current_quantity=total_qty,
            current_location=payload.target_location,
            operator_id=None,
            parent_batch_id=None,
            batch_time=datetime.now()
        )
        db.add(new_batch)
        db.flush()

        for b in batches:
            b.current_location = 'archived'
        db.commit()
        return {'success': True, 'new_batch_id': new_batch.id}
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error merging batches: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error while merging batches")

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
        final_qc_locations = ['good', 'defect', 'rework_repair']
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
        batches = (
            db.query(BatchDB, PartDB, LotDB, EmployeeDB, MachineDB, CardDB)
            .select_from(BatchDB)
            .join(LotDB, BatchDB.lot_id == LotDB.id)
            .join(PartDB, LotDB.part_id == PartDB.id)
            .outerjoin(EmployeeDB, BatchDB.operator_id == EmployeeDB.id)
            .outerjoin(SetupDB, BatchDB.setup_job_id == SetupDB.id)
            .outerjoin(MachineDB, SetupDB.machine_id == MachineDB.id)
            .outerjoin(CardDB, BatchDB.id == CardDB.batch_id)
            .filter(BatchDB.current_location.in_(['production', 'sorting'])) # Включены 'production' и 'sorting'
            .order_by(BatchDB.batch_time.asc())
            .all()
        )

        result = []
        for row in batches:
            batch_obj, part_obj, lot_obj, emp_obj, machine_obj, card_obj = row
            # Собираем данные как есть из БД
            item_data = {
                'id': batch_obj.id,
                'lot_id': batch_obj.lot_id,
                'drawing_number': part_obj.drawing_number if part_obj else None,
                'lot_number': lot_obj.lot_number if lot_obj else None,
                'current_quantity': batch_obj.current_quantity, # Теперь имя совпадает
                'batch_time': batch_obj.batch_time,
                'operator_name': emp_obj.full_name if emp_obj else None,
                'machine_name': machine_obj.name if machine_obj else None,
                'card_number': card_obj.card_number if card_obj else None
            }
            # Валидируем и добавляем
            result.append(WarehousePendingBatchItem.model_validate(item_data))
        return result
    except Exception as e:
        logger.error(f"Error fetching warehouse pending batches: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while fetching pending batches")

@app.post("/batches/{batch_id}/accept-warehouse")
async def accept_batch_on_warehouse(batch_id: int, payload: AcceptWarehousePayload, db: Session = Depends(get_db_session)):
    """Принять батч на склад: обновить кол-во и статус."""
    try:
        # Получаем батч и связанные сущности одним запросом для эффективности
        batch_data = db.query(BatchDB, LotDB, PartDB)\
            .join(LotDB, BatchDB.lot_id == LotDB.id)\
            .join(PartDB, LotDB.part_id == PartDB.id)\
            .filter(BatchDB.id == batch_id)\
            .first()

        if not batch_data:
            raise HTTPException(status_code=404, detail="Batch not found or related Lot/Part missing")
        
        batch, lot, part = batch_data

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

                if percentage_diff > 5.0:
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
        batch.current_location = 'warehouse_counted'
        batch.warehouse_employee_id = payload.warehouse_employee_id
        batch.warehouse_received_at = datetime.now()
        batch.operator_id = payload.warehouse_employee_id # Обновляем оператора на кладовщика
        batch.updated_at = datetime.now() 

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

class LotInfoItem(BaseModel):
    id: int
    drawing_number: str
    lot_number: str
    inspector_name: Optional[str] = None
    planned_quantity: Optional[int] = None
    machine_name: Optional[str] = None

    class Config:
        from_attributes = True # Pydantic v2
        populate_by_name = True # Pydantic v2, было allow_population_by_field_name

# --- LOT ENDPOINTS MOVED TO ROUTERS ---

@app.get("/api/morning-report")
async def morning_report():
    return {"message": "Morning report is working!"}

@app.get("/debug/batches-summary")
async def get_batches_summary(db: Session = Depends(get_db_session)):
    """Быстрое получение сводки по всем батчам для отладки"""
    try:
        # Статистика по статусам батчей
        status_stats = db.query(
            BatchDB.current_location,
            func.count(BatchDB.id).label('count')
        ).group_by(BatchDB.current_location).all()
        
        # Батчи с qc_pending
        qc_pending_batches = db.query(BatchDB).filter(
            BatchDB.current_location == 'qc_pending'
        ).all()
        
        # Батчи лота 88 (ID=32)
        lot_88_batches = db.query(BatchDB).filter(
            BatchDB.lot_id == 32
        ).all()
        
        result = {
            "status_statistics": {stat.current_location: stat.count for stat in status_stats},
            "qc_pending_batches": [
                {
                    "id": batch.id,
                    "lot_id": batch.lot_id,
                    "quantity": batch.current_quantity
                } for batch in qc_pending_batches
            ],
            "lot_88_batches": [
                {
                    "id": batch.id,
                    "location": batch.current_location,
                    "quantity": batch.current_quantity,
                    "qc_inspector_id": batch.qc_inspector_id
                } for batch in lot_88_batches
            ]
        }
        
        return result
        
    except Exception as e:
        logger.error(f"Error in batches summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

class BatchLabelInfo(BaseModel):
    id: int  # ID самого батча
    lot_id: int 
    drawing_number: str
    lot_number: str
    machine_name: str
    operator_name: str
    operator_id: int
    batch_time: datetime
    shift: str
    start_time: Optional[str] = None # Изменено
    end_time: Optional[str] = None   # Изменено
    initial_quantity: int
    current_quantity: int
    batch_quantity: int
    # Новые поля для складской информации
    warehouse_received_at: Optional[datetime] = None
    warehouse_employee_name: Optional[str] = None
    recounted_quantity: Optional[int] = None

@app.get("/machines/{machine_id}/active-batch-label", response_model=BatchLabelInfo)
async def get_active_batch_label(machine_id: int, db: Session = Depends(get_db_session)):
    active_batch = db.query(BatchDB)\
        .join(SetupDB, BatchDB.setup_job_id == SetupDB.id)\
        .filter(SetupDB.machine_id == machine_id)\
        .filter(BatchDB.current_location == 'production')\
        .order_by(desc(BatchDB.batch_time)) \
        .first()

    if not active_batch:
        raise HTTPException(status_code=404, detail="Активный батч в производстве не найден для этого станка")

    setup = db.query(SetupDB).filter(SetupDB.id == active_batch.setup_job_id).first()
    part = db.query(PartDB).filter(PartDB.id == setup.part_id).first() if setup else None
    lot = db.query(LotDB).filter(LotDB.id == active_batch.lot_id).first()
    machine = db.query(MachineDB).filter(MachineDB.id == machine_id).first()
    operator = db.query(EmployeeDB).filter(EmployeeDB.id == active_batch.operator_id).first()

    # Определение времени начала и конца батча
    determined_start_time: Optional[str] = None
    final_end_time_str = active_batch.batch_time.strftime("%H:%M") if active_batch.batch_time else None

    # Новая логика для determined_start_time
    if active_batch.initial_quantity == 0 and setup and setup.start_time:
        determined_start_time = setup.start_time.strftime("%H:%M")
    else:
        previous_direct_batch = db.query(BatchDB.batch_time)\
            .filter(BatchDB.lot_id == active_batch.lot_id)\
            .filter(BatchDB.setup_job_id == active_batch.setup_job_id)\
            .filter(BatchDB.id != active_batch.id)\
            .filter(BatchDB.created_at < active_batch.created_at)\
            .order_by(desc(BatchDB.created_at)) \
            .first()
        
        if previous_direct_batch and previous_direct_batch.batch_time:
            determined_start_time = previous_direct_batch.batch_time.strftime("%H:%M")
        elif setup and setup.start_time: # Fallback
            determined_start_time = setup.start_time.strftime("%H:%M")

    # Расчет initial_quantity, current_quantity, batch_quantity (оставляем как было, если корректно)
    # Эта логика должна соответствовать тому, как поля хранятся в BatchDB и что ожидает этикетка
    # initial_quantity - показание счетчика в начале этого батча
    # current_quantity - показание счетчика в конце этого батча
    # batch_quantity - количество деталей в этом батче (current_quantity - initial_quantity)

    # final_initial_quantity - это initial_quantity самого active_batch, т.е. показание счетчика в его начале
    final_initial_quantity = active_batch.initial_quantity
    
    # final_current_quantity - это показание счетчика в конце active_batch.
    # Оно равно active_batch.initial_quantity + active_batch.current_quantity (где current_quantity - это кол-во В батче)
    final_current_quantity = active_batch.initial_quantity + active_batch.current_quantity
    
    # final_batch_quantity - это количество деталей В active_batch, т.е. active_batch.current_quantity
    final_batch_quantity = active_batch.current_quantity

    # Вычисляем смену
    calculated_shift = "N/A"
    if active_batch.batch_time:
        hour = active_batch.batch_time.hour
        if 6 <= hour < 18:
            calculated_shift = "1"  # Дневная смена
        else:
            calculated_shift = "2"  # Ночная смена

    return BatchLabelInfo(
        id=active_batch.id,
        lot_id=active_batch.lot_id,
        drawing_number=part.drawing_number if part else "N/A",
        lot_number=lot.lot_number if lot else "N/A",
        machine_name=machine.name if machine else "N/A",
        operator_name=operator.full_name if operator else "N/A",
        operator_id=active_batch.operator_id,
        batch_time=active_batch.batch_time,
        shift=calculated_shift, # Используем вычисленную смену
        start_time=determined_start_time,
        end_time=final_end_time_str,
        initial_quantity=final_initial_quantity,
        current_quantity=final_current_quantity,
        batch_quantity=final_batch_quantity,
        warehouse_received_at=active_batch.warehouse_received_at,
        warehouse_employee_name=active_batch.operator_name,
        recounted_quantity=active_batch.recounted_quantity
    )

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

@app.post("/batches", response_model=CreateBatchResponse)
async def create_batch(payload: CreateBatchInput, db: Session = Depends(get_db_session)):
    """
    Создать новый батч (в том числе для переборки). batch_quantity=None, статус по умолчанию 'sorting'.
    """
    lot = db.query(LotDB).filter(LotDB.id == payload.lot_id).first()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    part = db.query(PartDB).filter(PartDB.drawing_number == payload.drawing_number).first()
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    machine = db.query(MachineDB).filter(MachineDB.id == payload.machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")
    operator = db.query(EmployeeDB).filter(EmployeeDB.id == payload.operator_id).first()
    if not operator:
        raise HTTPException(status_code=404, detail="Operator not found")

    now = datetime.now()
    hour = now.hour
    shift = "1" if 6 <= hour < 18 else "2"

    new_batch = BatchDB(
        lot_id=payload.lot_id,
        initial_quantity=0, # <--- ИЗМЕНЕНО: ставим 0 по умолчанию для батчей 'sorting'
        current_quantity=0, # <--- ИЗМЕНЕНО: ставим 0 по умолчанию для батчей 'sorting'
        recounted_quantity=None,
        current_location=payload.status or 'sorting',
        operator_id=payload.operator_id,
        batch_time=now,
        created_at=now
    )
    db.add(new_batch)
    db.commit()
    db.refresh(new_batch)

    return CreateBatchResponse(
        batch_id=new_batch.id,
        lot_number=lot.lot_number,
        drawing_number=part.drawing_number,
        machine_name=machine.name,
        operator_id=operator.id,
        created_at=now,
        shift=shift
    )

# --- START NEW ENDPOINT --- 
class EmployeeItem(BaseModel):
    id: int
    full_name: Optional[str] = None
    role_id: Optional[int] = None # Добавим роль, может пригодиться для фильтрации на фронте

    class Config:
        from_attributes = True # Pydantic v2

@app.get("/employees", response_model=List[EmployeeItem])
async def get_employees(db: Session = Depends(get_db_session)):
    """
    Получить список всех активных сотрудников с role_id = 1 (операторы)
    """
    try:
        employees = db.query(EmployeeDB)\
            .filter(EmployeeDB.role_id == 1)\
            .filter(EmployeeDB.is_active == True)\
            .order_by(EmployeeDB.full_name)\
            .all()
        return employees # Pydantic автоматически преобразует в EmployeeItem
    except Exception as e:
        logger.error(f"Error fetching employees: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while fetching employees")
# --- END NEW ENDPOINT ---

# Duplicate lot schemas removed - using schemas from src.schemas.lot
async def update_lot_quantity(
    lot_id: int, 
    quantity_update: LotQuantityUpdate, 
    db: Session = Depends(get_db_session)
):
    """
    Обновить дополнительное количество для лота.
    Доступно только для лотов в статусах 'new' и 'in_production'.
    total_planned_quantity = initial_planned_quantity + additional_quantity
    """
    try:
        # Найти лот
        lot = db.query(LotDB).options(selectinload(LotDB.part)).filter(LotDB.id == lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail=f"Лот с ID {lot_id} не найден")
        
        # Проверить, что лот в подходящем статусе
        allowed_statuses = [LotStatus.NEW, LotStatus.IN_PRODUCTION]
        if lot.status not in allowed_statuses:
            raise HTTPException(
                status_code=400, 
                detail=f"Изменение количества доступно только для статусов: {', '.join(allowed_statuses)}. Текущий статус: '{lot.status}'"
            )
        
        # Рассчитать новое общее количество
        initial_quantity = lot.initial_planned_quantity or 0
        new_total_quantity = initial_quantity + quantity_update.additional_quantity
        
        # Обновить total_planned_quantity
        lot.total_planned_quantity = new_total_quantity
        db.commit()
        db.refresh(lot)
        
        logger.info(f"Количество лота {lot_id} обновлено: initial={initial_quantity}, additional={quantity_update.additional_quantity}, total={new_total_quantity}")
        
        return lot
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при обновлении количества лота {lot_id}: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера при обновлении количества: {str(e)}")

# Duplicate lot endpoint removed - available through router

# === ОТЧЕТНОСТЬ И АНАЛИТИКА ДЛЯ ORDER MANAGER ===

class LotSummaryReport(BaseModel):
    """Сводный отчет по лотам"""
    total_lots: int
    lots_by_status: Dict[str, int]
    total_planned_quantity: int
    total_produced_quantity: int
    average_completion_time_hours: Optional[float] = None
    on_time_delivery_rate: float  # Процент лотов, выполненных в срок

class LotDetailReport(BaseModel):
    """Детальный отчет по конкретному лоту"""
    lot_id: int
    lot_number: str
    drawing_number: str
    material: Optional[str]
    status: str
    initial_planned_quantity: Optional[int]
    total_produced_quantity: int
    total_good_quantity: int
    total_defect_quantity: int
    total_rework_quantity: int
    created_at: Optional[datetime]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    due_date: Optional[datetime]
    is_overdue: bool
    completion_time_hours: Optional[float]
    setups_count: int
    batches_count: int
    machines_used: List[str]
    operators_involved: List[str]

class ProductionPerformanceReport(BaseModel):
    """Отчет по производительности"""
    period_start: datetime
    period_end: datetime
    total_setups: int
    total_batches: int
    total_produced_quantity: int
    average_cycle_time_seconds: Optional[float]
    machine_utilization: Dict[str, float]  # Процент использования по станкам
    operator_productivity: Dict[str, int]  # Количество деталей по операторам

class QualityReport(BaseModel):
    """Отчет по качеству"""
    period_start: datetime
    period_end: datetime
    total_inspected_quantity: int
    good_quantity: int
    defect_quantity: int
    rework_quantity: int
    defect_rate: float  # Процент брака
    rework_rate: float  # Процент переборки
    quality_by_drawing: Dict[str, Dict[str, int]]  # Качество по чертежам

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

@app.get("/reports/lots/{lot_id}/detail", response_model=LotDetailReport, tags=["Reports"])
async def get_lot_detail_report(lot_id: int, db: Session = Depends(get_db_session)):
    """
    Получить детальный отчет по конкретному лоту.
    Включает полную информацию о жизненном цикле лота.
    """
    try:
        # Получаем лот с деталью
        lot = db.query(LotDB).options(selectinload(LotDB.part)).filter(LotDB.id == lot_id).first()
        if not lot:
            raise HTTPException(status_code=404, detail=f"Лот с ID {lot_id} не найден")
        
        # Получаем все наладки для лота
        setups = db.query(SetupDB).options(
            selectinload(SetupDB.machine),
            selectinload(SetupDB.operator)
        ).filter(SetupDB.lot_id == lot_id).all()
        
        # Получаем все батчи для лота
        batches = db.query(BatchDB).filter(BatchDB.lot_id == lot_id).all()
        
        # Подсчет количеств
        total_produced_quantity = sum(batch.current_quantity for batch in batches 
                                    if batch.current_location in ['warehouse_counted', 'good', 'defect', 'rework_repair'])
        
        total_good_quantity = sum(batch.current_quantity for batch in batches 
                                if batch.current_location == 'good')
        
        total_defect_quantity = sum(batch.current_quantity for batch in batches 
                                  if batch.current_location == 'defect')
        
        total_rework_quantity = sum(batch.current_quantity for batch in batches 
                                  if batch.current_location == 'rework_repair')
        
        # Определение временных меток
        started_at = min(setup.start_time for setup in setups if setup.start_time) if setups else None
        completed_at = max(setup.end_time for setup in setups if setup.end_time and setup.status == 'completed') if setups else None
        
        # Расчет времени выполнения
        completion_time_hours = None
        if started_at and completed_at:
            completion_time_hours = (completed_at - started_at).total_seconds() / 3600
        
        # Проверка просрочки
        is_overdue = False
        if lot.due_date and lot.status != 'completed':
            is_overdue = datetime.now(timezone.utc) > lot.due_date.replace(tzinfo=timezone.utc)
        elif lot.due_date and completed_at:
            is_overdue = completed_at.replace(tzinfo=timezone.utc) > lot.due_date.replace(tzinfo=timezone.utc)
        
        # Список станков и операторов
        machines_used = list(set(setup.machine.name for setup in setups if setup.machine))
        operators_involved = list(set(setup.operator.full_name for setup in setups if setup.operator and setup.operator.full_name))
        
        return LotDetailReport(
            lot_id=lot.id,
            lot_number=lot.lot_number,
            drawing_number=lot.part.drawing_number if lot.part else 'N/A',
            material=lot.part.material if lot.part else None,
            status=lot.status or 'unknown',
            initial_planned_quantity=lot.initial_planned_quantity,
            total_produced_quantity=total_produced_quantity,
            total_good_quantity=total_good_quantity,
            total_defect_quantity=total_defect_quantity,
            total_rework_quantity=total_rework_quantity,
            created_at=lot.created_at,
            started_at=started_at,
            completed_at=completed_at,
            due_date=lot.due_date,
            is_overdue=is_overdue,
            completion_time_hours=completion_time_hours,
            setups_count=len(setups),
            batches_count=len(batches),
            machines_used=machines_used,
            operators_involved=operators_involved
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при генерации детального отчета по лоту {lot_id}: {e}", exc_info=True)
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
            recounted_quantity=last_batch.recounted_quantity
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
    Гибкий поиск станка по коду с учетом различных форматов:
    SR-32, SR32, sr 32, SR 32 и т.д.
    """
    # Извлекаем только цифры из кода
    import re
    digits = re.findall(r'\d+', machine_code)
    if not digits:
        return None
    
    machine_number = int(digits[0])
    
    # Ищем станок по номеру в различных форматах
    possible_names = [
        f"SR-{machine_number}",
        f"SR{machine_number}",
        f"sr-{machine_number}",
        f"sr{machine_number}",
        f"Станок {machine_number}",
        f"Machine {machine_number}",
        str(machine_number)
    ]
    
    for name in possible_names:
        machine = db.query(MachineDB).filter(
            func.lower(MachineDB.name) == name.lower()
        ).first()
        if machine:
            return machine
    
    # Если точного совпадения нет, ищем по содержанию номера
    machine = db.query(MachineDB).filter(
        MachineDB.name.ilike(f"%{machine_number}%")
    ).first()
    
    return machine

@app.get("/cards/free", tags=["Cards"])
async def get_free_cards(
    machine_id: int, 
    limit: int = Query(4, ge=1, le=20, description="Количество карточек для возврата (по умолчанию 4)"),
    db: Session = Depends(get_db_session)
):
    """Получить список свободных карточек для станка (по умолчанию первые 4)"""
    try:
        cards = db.query(CardDB).filter(
            CardDB.machine_id == machine_id,
            CardDB.status == 'free'
        ).order_by(CardDB.card_number).limit(limit).all()
        
        return {"cards": [card.card_number for card in cards]}
    except Exception as e:
        logger.error(f"Error fetching free cards for machine {machine_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while fetching free cards")

@app.patch("/cards/{card_number}/use", tags=["Cards"])
async def use_card(card_number: int, data: CardUseRequest, db: Session = Depends(get_db_session)):
    """Занять карточку (ОДИН КЛИК) - optimistic locking"""
    try:
        # Сначала находим станок по batch_id
        batch = db.query(BatchDB).filter(BatchDB.id == data.batch_id).first()
        if not batch:
            raise HTTPException(status_code=404, detail="Батч не найден")
        
        # Получаем machine_id из setup_job
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
        
        card.status = 'free'
        card.batch_id = None
        card.last_event = datetime.now()
        
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

        # Расчет количеств - ИСПРАВЛЕННАЯ ЛОГИКА V3
        # batch.initial_quantity - начальное показание счетчика  
        # batch.current_quantity - количество деталей в батче (из БД) - НЕ ИСПОЛЬЗУЕМ для этикетки!
        # Для этикетки нужно:
        # initial_quantity - начальное показание счетчика
        # current_quantity - последние РЕАЛЬНЫЕ показания со станка (из readings)
        # batch_quantity - РАЗНОСТЬ показаний счетчика (current - initial)
        
        initial_quantity = batch.initial_quantity  # Начальное показание счетчика

        # Получаем последние реальные показания со станка из таблицы readings
        if setup and setup.machine_id:
            last_reading = db.query(ReadingDB.reading).filter(
                ReadingDB.machine_id == setup.machine_id
            ).order_by(desc(ReadingDB.created_at)).first()
            
            current_quantity = last_reading[0] if last_reading else (batch.initial_quantity + batch.current_quantity)
        else:
            # Fallback если нет setup или machine_id
            current_quantity = batch.initial_quantity + batch.current_quantity

        # ПРАВИЛЬНЫЙ расчет batch_quantity как разности показаний
        batch_quantity = current_quantity - initial_quantity

        # Вычисляем смену (копируем логику из active-batch-label)
        calculated_shift = "N/A"
        if batch.batch_time:
            hour = batch.batch_time.hour
            if 6 <= hour < 18:
                calculated_shift = "1"  # Дневная смена
            else:
                calculated_shift = "2"  # Ночная смена

        return BatchLabelInfo(
            id=batch.id,
            lot_id=batch.lot_id,
            drawing_number=part.drawing_number if part else "N/A",
            lot_number=lot.lot_number if lot else "N/A",
            machine_name=machine.name if machine else "N/A",
            operator_name=operator.full_name if operator else "N/A",
            operator_id=batch.operator_id,
            batch_time=batch.batch_time,
            shift=calculated_shift,
            start_time=determined_start_time,
            end_time=final_end_time_str,
            initial_quantity=initial_quantity,
            current_quantity=current_quantity,
            batch_quantity=batch_quantity,
            warehouse_received_at=batch.warehouse_received_at,
            warehouse_employee_name=warehouse_employee.full_name if warehouse_employee else None,
            recounted_quantity=batch.recounted_quantity
        )

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Error fetching batch label info for batch {batch_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while fetching batch label info")