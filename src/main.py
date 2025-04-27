import logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from src.models.setup import SetupStatus
from typing import Optional, Dict, List
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, aliased
from fastapi import Depends, Body
from src.database import Base, initialize_database, get_db_session
from src.models.models import SetupDB, ReadingDB, MachineDB, EmployeeDB, PartDB, LotDB
from datetime import datetime
from src.utils.sheets_handler import save_to_sheets
import asyncio
import httpx
import os
from src.services.notification_service import send_setup_approval_notifications

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
async def save_reading(reading: ReadingInput, db: Session = Depends(get_db_session)):
    """
    Сохранить показания счетчика
    """
    # Добавляем логирование
    print(f"Получен запрос на сохранение: {reading}")
    
    # Получаем последнюю наладку для станка
    setup = db.query(SetupDB).filter(
        SetupDB.machine_id == reading.machine_id
    ).order_by(SetupDB.created_at.desc()).first()
    
    print(f"Найдена наладка: {setup}")
    
    if not setup:
        raise HTTPException(status_code=404, detail="Наладка не найдена")
    
    # Проверяем статус и значение показаний
    if reading.value == 0:
        # Для нулевых показаний разрешаем только в статусах created или allowed
        if setup.status not in ["created", "allowed"]:
            raise HTTPException(
                status_code=400, 
                detail=f"Нельзя вводить нулевые показания в статусе {setup.status}"
            )
        setup.status = "started"
        setup.start_time = datetime.now()
    else:
        # Для ненулевых показаний разрешаем только в статусе started
        if setup.status != "started":
            raise HTTPException(
                status_code=400, 
                detail=f"Нельзя вводить показания в статусе {setup.status}"
            )
    
    # Получаем информацию о станке и операторе
    machine = db.query(MachineDB).filter(MachineDB.id == reading.machine_id).first()
    operator = db.query(EmployeeDB).filter(EmployeeDB.id == reading.operator_id).first()
    
    if not machine or not operator:
        raise HTTPException(status_code=404, detail="Станок или оператор не найдены")
    
    # Создаем запись о показаниях
    reading_db = ReadingDB(
        machine_id=reading.machine_id,
        employee_id=reading.operator_id,
        reading=reading.value
    )
    db.add(reading_db)
    db.commit()
    
    # Сохраняем в Google Sheets асинхронно
    asyncio.create_task(save_to_sheets(
        operator=operator.full_name or "Unknown",
        machine=machine.name or "Unknown",
        reading=reading.value
    ))
    
    return {
        "success": True,
        "message": "Показания сохранены",
        "reading": reading,
        "new_status": setup.status
    }

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
    readings = db.query(ReadingDB).order_by(ReadingDB.created_at.desc()).limit(100).all()
    
    return {
        "readings": [
            {
                "id": r.id,
                "machine_id": r.machine_id,
                "employee_id": r.employee_id,
                "reading": r.reading,
                "created_at": r.created_at
            } for r in readings
        ]
    }

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
        orm_mode = True
        allow_population_by_field_name = True # Разрешаем использовать alias

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
    machine_name: Optional[str] = Field(None, alias='machineName')
    drawing_number: Optional[str] = Field(None, alias='drawingNumber')
    lot_number: Optional[str] = Field(None, alias='lotNumber')
    machinist_name: Optional[str] = Field(None, alias='machinistName')
    start_time: Optional[datetime] = Field(None, alias='startTime')
    status: Optional[str]
    qa_name: Optional[str] = Field(None, alias='qaName')
    qa_date: Optional[datetime] = Field(None, alias='qaDate')

    class Config:
        # orm_mode = True # Это для SQLAlchemy < 2.0
        from_attributes = True # Это для SQLAlchemy >= 2.0 и Pydantic v2
        allow_population_by_field_name = True

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

# Обновляем модель ответа
class OperatorMachineViewItem(BaseModel):
    id: int # Machine ID
    name: Optional[str] = None # Machine name
    reading: Optional[str] = '' # Поле для ввода на фронте
    lastReading: Optional[int] = Field(None, alias='last_reading')
    lastReadingTime: Optional[datetime] = Field(None, alias='last_reading_time')
    setupId: Optional[int] = Field(None, alias='setup_id')
    drawingNumber: Optional[str] = Field(None, alias='drawing_number')
    plannedQuantity: Optional[int] = Field(None, alias='planned_quantity')
    additionalQuantity: Optional[int] = Field(None, alias='additional_quantity')
    status: Optional[str] = None # <-- Добавляем статус

    class Config:
        from_attributes = True
        allow_population_by_field_name = True

@app.get("/machines/operator-view/{operator_id}", response_model=List[OperatorMachineViewItem])
async def get_operator_machines_view(operator_id: int, db: Session = Depends(get_db_session)):
    """
    Получает список станков, доступных оператору, с информацией
    о последней активной наладке и последнем показании.
    Предполагается, что оператор имеет доступ ко всем активным станкам.
    """
    logger.info(f"Fetching operator machine view for operator_id: {operator_id}")
    try:
        # 1. Получаем все активные станки
        machines = db.query(MachineDB).filter(MachineDB.is_active == True).order_by(MachineDB.name).all()
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
                status=active_setup.status if active_setup else 'idle' # <-- Добавляем статус или 'idle'
            )
            result_list.append(machine_view)

        logger.info(f"Successfully prepared operator machine view for operator_id: {operator_id}")
        return result_list

    except Exception as e:
        logger.error(f"Error fetching operator machine view for operator_id {operator_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error fetching operator machine view")