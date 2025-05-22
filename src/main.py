import logging
from dotenv import load_dotenv
import os

# Загружаем переменные окружения из .env файла
# Это должно быть В САМОМ НАЧАЛЕ, до других импортов, использующих env vars
load_dotenv()

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from src.models.setup import SetupStatus
from typing import Optional, Dict, List
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, aliased, selectinload
from fastapi import Depends, Body
from src.database import Base, initialize_database, get_db_session
from src.models.models import SetupDB, ReadingDB, MachineDB, EmployeeDB, PartDB, LotDB, BatchDB
from datetime import datetime
from src.utils.sheets_handler import save_to_sheets
import asyncio
import httpx
from src.services.notification_service import send_setup_approval_notifications, send_batch_discrepancy_alert
from sqlalchemy import func, desc, case

logger = logging.getLogger(__name__)

app = FastAPI(title="Machine Logic Service", debug=True)

# Возвращаем универсальное разрешение CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # <-- Снова разрешаем все источники
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"], 
)


# Событие startup для инициализации БД
@app.on_event("startup")
async def startup_event():
    initialize_database()
    # Здесь можно добавить другие действия при старте, если нужно

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

# Эндпоинт для получения наладок для ОТК
@app.get("/setups/qa-view", response_model=List[QaSetupViewItem])
async def get_qa_view(db: Session = Depends(get_db_session)):
    """
    Получить список АКТИВНЫХ наладок для отображения ОТК.
    Возвращает данные, необходимые для отображения в таблице ОТК на дашборде,
    включая информацию об утверждении ОТК, если оно было.
    """
    try:
        active_statuses = ['created', 'pending_qc', 'allowed', 'started']

        # --- ДОПОЛНЯЕМ ЗАПРОС ДАННЫМИ ОТК --- 
        # Создаем псевдонимы для таблицы EmployeeDB
        OperatorEmployee = aliased(EmployeeDB, name="operator")
        QAEmployee = aliased(EmployeeDB, name="qa_approver")

        # Запрос к БД с необходимыми join'ами
        active_setups = db.query(
            SetupDB.id,
            MachineDB.name.label('machine_name'),
            PartDB.drawing_number.label('drawing_number'),
            LotDB.lot_number.label('lot_number'),
            OperatorEmployee.full_name.label('machinist_name'), # Наладчик
            SetupDB.start_time,
            SetupDB.status,
            QAEmployee.full_name.label('qa_name'), # <-- Добавлено имя ОТК
            SetupDB.qa_date # <-- Добавлена дата ОТК
        ).select_from(SetupDB) \
         .join(MachineDB, SetupDB.machine_id == MachineDB.id) \
         .join(OperatorEmployee, SetupDB.employee_id == OperatorEmployee.id) \
         .join(PartDB, SetupDB.part_id == PartDB.id) \
         .join(LotDB, SetupDB.lot_id == LotDB.id) \
         .outerjoin(QAEmployee, SetupDB.qa_id == QAEmployee.id) \
         .filter(SetupDB.status.in_(active_statuses)) \
         .order_by(SetupDB.created_at.asc()) \
         .all()
        # -----------------------------------------

        result_list = []
        for row in active_setups: 
            result_list.append(QaSetupViewItem(
                id=row.id,
                machine_name=row.machine_name,
                drawing_number=row.drawing_number,
                lot_number=row.lot_number,
                machinist_name=row.machinist_name,
                start_time=row.start_time,
                status=row.status,
                qa_name=row.qa_name, # <-- Передаем имя ОТК
                qa_date=row.qa_date # <-- Передаем дату ОТК
            ))

        return result_list

    except Exception as e:
        print(f"Error fetching QA view data: {e}")
        raise HTTPException(status_code=500, detail="Internal server error while fetching QA data")

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

# Изменяем путь и убираем operator_id из аргументов
@app.get("/machines/operator-view", response_model=List[OperatorMachineViewItem])
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

    class Config:
        from_attributes = True # Pydantic v2, было orm_mode
        populate_by_name = True # Pydantic v2, было allow_population_by_field_name
        # populate_by_name больше не нужен для этого поля

class AcceptWarehousePayload(BaseModel):
    recounted_quantity: int
    warehouse_employee_id: int

@app.get("/warehouse/batches-pending", response_model=List[WarehousePendingBatchItem])
async def get_warehouse_pending_batches(db: Session = Depends(get_db_session)):
    """Получить список батчей, ожидающих приемки на склад (статус 'production' или 'sorting')."""
    try:
        batches = (
            db.query(BatchDB, PartDB, LotDB, EmployeeDB)
            .select_from(BatchDB)
            .join(LotDB, BatchDB.lot_id == LotDB.id)
            .join(PartDB, LotDB.part_id == PartDB.id)
            .outerjoin(EmployeeDB, BatchDB.operator_id == EmployeeDB.id)
            .filter(BatchDB.current_location.in_(['production', 'sorting'])) # Включены 'production' и 'sorting'
            .order_by(BatchDB.batch_time.asc())
            .all()
        )

        result = []
        for row in batches:
            batch_obj, part_obj, lot_obj, emp_obj = row
            # Собираем данные как есть из БД
            item_data = {
                'id': batch_obj.id,
                'lot_id': batch_obj.lot_id,
                'drawing_number': part_obj.drawing_number if part_obj else None,
                'lot_number': lot_obj.lot_number if lot_obj else None,
                'current_quantity': batch_obj.current_quantity, # Теперь имя совпадает
                'batch_time': batch_obj.batch_time,
                'operator_name': emp_obj.full_name if emp_obj else None,
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

@app.get("/lots/pending-qc", response_model=List[LotInfoItem])
async def get_lots_pending_qc(db: Session = Depends(get_db_session), current_user_qa_id: Optional[int] = Query(None, alias="qaId")):
    """
    Получить лоты для ОТК на основе "старой" рабочей логики.
    1. Находит лоты с неархивными батчами.
    2. Для каждого такого лота извлекает детали из последней наладки, включая имя инспектора, плановое количество и имя станка.
    3. Опционально фильтрует по qaId, если он предоставлен (на основе qa_id в последней наладке).
    """
    logger.info(f"Запрос /lots/pending-qc (старая логика) получен. qaId: {current_user_qa_id}")
    try:
        # 1. Базовый запрос на получение ID лотов с активными (неархивными) батчами
        active_lot_ids_query = db.query(BatchDB.lot_id)\
            .filter(BatchDB.current_location != 'archived') \
            .distinct()
        
        lot_ids_with_active_batches_tuples = active_lot_ids_query.all()
        lot_ids = [item[0] for item in lot_ids_with_active_batches_tuples]

        if not lot_ids:
            logger.info("Не найдено лотов с активными (неархивными) батчами.")
            return []
        
        logger.info(f"Найдены следующие ID лотов с активными батчами: {lot_ids}")
        
        # 2. Основной запрос для данных по лотам и деталям
        lots_query = db.query(LotDB, PartDB).select_from(LotDB)\
            .join(PartDB, LotDB.part_id == PartDB.id)\
            .filter(LotDB.id.in_(lot_ids))

        lots_query_result = lots_query.all()
        logger.info(f"Всего лотов (с деталями) для дальнейшей обработки: {len(lots_query_result)}")
        
        result = []
        for lot_obj, part_obj in lots_query_result:
            logger.debug(f"Обработка лота ID: {lot_obj.id}, Номер: {lot_obj.lot_number}")
            
            planned_quantity_val = None
            inspector_name_val = None
            machine_name_val = None # Инициализируем machine_name
            
            # 3. Находим последнюю наладку для данного лота, включая имя станка
            # Используем outerjoin для EmployeeDB и MachineDB для большей устойчивости
            latest_setup_details = db.query(
                    SetupDB.planned_quantity,
                    SetupDB.qa_id,
                    EmployeeDB.full_name.label("inspector_name_from_setup"),
                    MachineDB.name.label("machine_name_from_setup"),
                    SetupDB.machine_id.label("setup_machine_id") # <--- ДОБАВЛЕНО ДЛЯ ЛОГИРОВАНИЯ
                )\
                .outerjoin(EmployeeDB, SetupDB.qa_id == EmployeeDB.id) \
                .outerjoin(MachineDB, SetupDB.machine_id == MachineDB.id) \
                .filter(SetupDB.lot_id == lot_obj.id)\
                .order_by(desc(SetupDB.created_at))\
                .first()

            passes_qa_filter = True # По умолчанию лот проходит фильтр

            if latest_setup_details:
                planned_quantity_val = latest_setup_details.planned_quantity
                machine_name_val = latest_setup_details.machine_name_from_setup # Получаем имя станка
                # Исправленный лог:
                logger.info(f"Lot ID {lot_obj.id}: latest_setup_details found. machine_id in setup (from latest_setup_details): {latest_setup_details.setup_machine_id}. machine_name_from_setup: {machine_name_val}")

                if latest_setup_details.qa_id:
                    inspector_name_val = latest_setup_details.inspector_name_from_setup
                    if current_user_qa_id is not None and latest_setup_details.qa_id != current_user_qa_id:
                        passes_qa_filter = False
                        logger.debug(f"  Лот НЕ проходит фильтр по qaId. Ожидался: {current_user_qa_id}, в наладке: {latest_setup_details.qa_id}")
                elif current_user_qa_id is not None: # Фильтр qaId есть, но в наладке qa_id не указан
                    passes_qa_filter = False
                    logger.debug(f"  Лот НЕ проходит фильтр по qaId. Ожидался: {current_user_qa_id}, в наладке qa_id отсутствует.")
                else: # Наладки не найдены, фильтра по qaId нет - просто нет данных о станке
                    logger.info(f"Lot ID {lot_obj.id}: latest_setup_details NOT found. machine_name will be None.")
            
            elif current_user_qa_id is not None: # Наладки не найдены, но фильтр по qaId активен
                 passes_qa_filter = False
                 logger.debug(f"  Последняя наладка для лота {lot_obj.id} не найдена. Лот НЕ проходит фильтр по qaId {current_user_qa_id}.")


            if not passes_qa_filter:
                continue # Пропускаем лот, если он не прошел фильтр по qaId
            
            item_data = {
                'id': lot_obj.id,
                'drawing_number': part_obj.drawing_number,
                'lot_number': lot_obj.lot_number,
                'inspector_name': inspector_name_val,
                'planned_quantity': planned_quantity_val,
                'machine_name': machine_name_val, # Добавляем имя станка в результат
            }
            
            try:
                result.append(LotInfoItem.model_validate(item_data)) # Pydantic v2
            except AttributeError:
                result.append(LotInfoItem.parse_obj(item_data)) # Pydantic v1 fallback
            logger.debug(f"  Лот ID: {lot_obj.id} добавлен в результаты.")

        logger.info(f"Сформировано {len(result)} элементов для ответа /lots/pending-qc (старая логика).")
        if not result and lot_ids: # Если были лоты с активными батчами, но они отфильтровались
             logger.info(f"  Все лоты были отфильтрованы (например, по qaId: {current_user_qa_id}).")
        return result

    except Exception as e:
        logger.error(f"Ошибка в /lots/pending-qc (старая логика): {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера при получении лотов для ОТК")

# --- END LOTS MANAGEMENT ENDPOINTS ---

# --- LOT ANALYTICS ENDPOINT ---

class LotAnalyticsResponse(BaseModel):
    accepted_by_warehouse_quantity: int = 0  # "Принято" (на складе)
    from_machine_quantity: int = 0           # "Со станка" (сырые)

@app.get("/lots/{lot_id}/analytics", response_model=LotAnalyticsResponse)
async def get_lot_analytics(lot_id: int, db: Session = Depends(get_db_session)):
    """
    Получить сводную аналитику по указанному лоту.
    "Принято" (accepted_by_warehouse_quantity) - сумма recounted_quantity по всем партиям лота, прошедшим приемку складом.
    "Со станка" (from_machine_quantity) - сумма current_quantity для "сырых" батчей (до приемки складом).
    """
    logger.error(f"DEBUG_ANALYTICS: Fetching simplified analytics for lot_id: {lot_id}")
    
    lot = db.query(LotDB).filter(LotDB.id == lot_id).first()
    if not lot:
        logger.warning(f"Lot with id {lot_id} not found for analytics.")
        return LotAnalyticsResponse()

    # accepted_by_warehouse_quantity: Сумма recounted_quantity для партий, обработанных складом.
    # Это количество, которое склад фактически посчитал.
    accepted_warehouse_query = db.query(BatchDB.recounted_quantity)\
        .filter(BatchDB.lot_id == lot_id)\
        .filter(BatchDB.recounted_quantity != None) # Условие, что партия прошла пересчет складом
    
    # Собираем только не-None значения для корректной суммы
    accepted_quantities_list = [q[0] for q in accepted_warehouse_query.all() if q[0] is not None]
    logger.error(f"DEBUG_ANALYTICS: For lot_id {lot_id}, recounted_quantities summed for 'accepted_by_warehouse': {accepted_quantities_list}")
    
    accepted_by_warehouse_result = sum(accepted_quantities_list)

    # from_machine_quantity: Последнее показание счетчика для машины, связанной с последней наладкой этого лота.
    from_machine_result = 0 # Значение по умолчанию
    
    # 1. Найти последнюю наладку для лота
    latest_setup_for_lot = db.query(SetupDB.machine_id)\
        .filter(SetupDB.lot_id == lot_id)\
        .order_by(SetupDB.created_at.desc())\
        .first()
    
    if latest_setup_for_lot:
        machine_id_for_lot = latest_setup_for_lot.machine_id
        # 2. Найти последнее показание для этой машины
        latest_reading_for_machine = db.query(ReadingDB.reading)\
            .filter(ReadingDB.machine_id == machine_id_for_lot)\
            .order_by(ReadingDB.created_at.desc())\
            .first()
        
        if latest_reading_for_machine:
            from_machine_result = latest_reading_for_machine.reading or 0 # Берем показание или 0, если None
            logger.error(f"DEBUG_ANALYTICS: For lot_id {lot_id}, found latest reading for machine {machine_id_for_lot}: {from_machine_result}")
        else:
            logger.error(f"DEBUG_ANALYTICS: For lot_id {lot_id}, no readings found for machine {machine_id_for_lot} (from latest setup)")
    else:
        logger.error(f"DEBUG_ANALYTICS: For lot_id {lot_id}, no setup found to determine machine_id for 'from_machine_quantity'")

    logger.error(f"DEBUG_ANALYTICS: For lot_id {lot_id}, accepted_by_warehouse={accepted_by_warehouse_result}, from_machine={from_machine_result}")

    return LotAnalyticsResponse(
        accepted_by_warehouse_quantity=accepted_by_warehouse_result,
        from_machine_quantity=from_machine_result
    )

# --- END LOT ANALYTICS ENDPOINT ---

@app.get("/api/morning-report")
async def morning_report():
    return {"message": "Morning report is working!"}

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
        batch_quantity=final_batch_quantity
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