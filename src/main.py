from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from src.models.setup import SetupStatus
from typing import Optional, Dict
from pydantic import BaseModel
from sqlalchemy.orm import Session
from fastapi import Depends
from src.database import SessionLocal, engine
from src.models.models import Base, SetupDB, ReadingDB, MachineDB, EmployeeDB, PartDB, LotDB
from datetime import datetime
from src.utils.sheets_handler import save_to_sheets
import asyncio

app = FastAPI(title="Machine Logic Service", debug=True)

# Добавляем CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Создаем таблицы (если их нет)
Base.metadata.create_all(bind=engine)

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/")
async def root():
    return {
        "service": "Machine Logic Service",
        "status": "running",
        "available_statuses": [status.value for status in SetupStatus]
    }

@app.get("/setup/{machine_id}/status")
async def get_setup_status(machine_id: int, db: Session = Depends(get_db)):
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
async def get_setup_history(machine_id: int, db: Session = Depends(get_db)):
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
async def save_reading(reading: ReadingInput, db: Session = Depends(get_db)):
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
async def get_machines(db: Session = Depends(get_db)):
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
async def get_readings(db: Session = Depends(get_db)):
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
async def get_machine_readings(machine_id: int, db: Session = Depends(get_db)):
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
async def create_setup(setup: SetupInput, db: Session = Depends(get_db)):
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