"""
Роутер для учета рабочего времени сотрудников
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from typing import List, Optional
from datetime import datetime, date, timedelta
from src.database import get_db_session
from src.models.time_tracking import TimeEntryDB, WorkShiftDB, TerminalDB, FaceEmbeddingDB
from src.models.models import EmployeeDB
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/time-tracking", tags=["Time Tracking"])


# ==================== Pydantic модели ====================

class TimeEntryCreate(BaseModel):
    """Модель для создания записи входа/выхода"""
    employee_id: Optional[int] = None
    telegram_id: Optional[int] = None  # альтернатива employee_id
    entry_type: str = Field(..., pattern="^(check_in|check_out)$")
    method: str = Field(..., pattern="^(telegram|terminal|web|manual)$")
    
    # Геолокация (для Telegram)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_accuracy: Optional[float] = None
    
    # Терминал (для терминалов с Face Recognition)
    terminal_device_id: Optional[str] = None
    face_confidence: Optional[float] = None
    
    # Offline синхронизация
    client_timestamp: Optional[datetime] = None


class TimeEntryResponse(BaseModel):
    """Модель ответа с информацией о записи"""
    id: int
    employee_id: int
    employee_name: str
    entry_type: str
    entry_time: datetime
    method: str
    is_location_valid: bool
    
    class Config:
        from_attributes = True


class WorkShiftResponse(BaseModel):
    """Модель ответа с информацией о смене"""
    shift_date: date
    check_in_time: Optional[datetime]
    check_out_time: Optional[datetime]
    total_hours: Optional[float]
    status: str
    
    class Config:
        from_attributes = True


# ==================== Константы ====================

# Координаты завода для валидации геолокации
FACTORY_LAT = 32.0853  # TODO: заменить на реальные координаты
FACTORY_LON = 34.7818
MAX_DISTANCE_METERS = 100  # максимальное расстояние от завода


# ==================== Вспомогательные функции ====================

def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Вычисление расстояния между двумя координатами (формула Haversine)
    Возвращает расстояние в метрах
    """
    from math import radians, sin, cos, sqrt, atan2
    
    R = 6371000  # радиус Земли в метрах
    
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    
    return R * c


async def update_work_shift(employee_id: int, shift_date: date, db: Session):
    """
    Обновить агрегированные данные смены
    Вычисляет общее время работы на основе записей входа/выхода
    """
    # Получить все записи за день
    entries = db.query(TimeEntryDB).filter(
        TimeEntryDB.employee_id == employee_id,
        func.date(TimeEntryDB.entry_time) == shift_date
    ).order_by(TimeEntryDB.entry_time).all()
    
    # Найти первый вход и последний выход
    check_in = next((e for e in entries if e.entry_type == 'check_in'), None)
    check_out = next((e for e in reversed(entries) if e.entry_type == 'check_out'), None)
    
    total_hours = None
    status = 'incomplete'
    
    if check_in and check_out:
        delta = check_out.entry_time - check_in.entry_time
        total_hours = delta.total_seconds() / 3600
        status = 'complete'
    elif check_in:
        status = 'incomplete'
    else:
        status = 'absent'
    
    # Обновить или создать запись смены
    shift = db.query(WorkShiftDB).filter(
        WorkShiftDB.employee_id == employee_id,
        WorkShiftDB.shift_date == shift_date
    ).first()
    
    if shift:
        shift.check_in_time = check_in.entry_time if check_in else None
        shift.check_out_time = check_out.entry_time if check_out else None
        shift.total_hours = total_hours
        shift.status = status
        shift.updated_at = datetime.utcnow()
    else:
        shift = WorkShiftDB(
            employee_id=employee_id,
            shift_date=shift_date,
            check_in_time=check_in.entry_time if check_in else None,
            check_out_time=check_out.entry_time if check_out else None,
            total_hours=total_hours,
            status=status
        )
        db.add(shift)
    
    db.commit()


# ==================== Endpoints ====================

@router.post("/entries", response_model=TimeEntryResponse)
async def create_time_entry(entry: TimeEntryCreate, db: Session = Depends(get_db_session)):
    """
    Создать запись входа/выхода
    
    Принимает либо employee_id, либо telegram_id для идентификации сотрудника.
    Проверяет геолокацию если она указана.
    Автоматически обновляет агрегированные данные смены.
    """
    logger.info(f"Создание записи времени: {entry.model_dump()}")
    
    # Определить employee_id
    if entry.employee_id:
        employee = db.query(EmployeeDB).filter(EmployeeDB.id == entry.employee_id).first()
    elif entry.telegram_id:
        employee = db.query(EmployeeDB).filter(EmployeeDB.telegram_id == entry.telegram_id).first()
    else:
        raise HTTPException(400, "Требуется employee_id или telegram_id")
    
    if not employee:
        raise HTTPException(404, "Сотрудник не найден")
    
    # Валидация геолокации
    is_location_valid = True
    if entry.latitude and entry.longitude:
        distance = calculate_distance(
            entry.latitude, entry.longitude,
            FACTORY_LAT, FACTORY_LON
        )
        is_location_valid = distance <= MAX_DISTANCE_METERS
        logger.info(f"Расстояние от завода: {distance:.2f}м, валидно: {is_location_valid}")
    
    # Создать запись
    db_entry = TimeEntryDB(
        employee_id=employee.id,
        entry_type=entry.entry_type,
        entry_time=entry.client_timestamp or datetime.utcnow(),
        method=entry.method,
        latitude=entry.latitude,
        longitude=entry.longitude,
        location_accuracy=entry.location_accuracy,
        is_location_valid=is_location_valid,
        terminal_device_id=entry.terminal_device_id,
        face_confidence=entry.face_confidence,
        client_timestamp=entry.client_timestamp
    )
    
    db.add(db_entry)
    db.commit()
    db.refresh(db_entry)
    
    # Обновить агрегированные данные смены
    await update_work_shift(employee.id, db_entry.entry_time.date(), db)
    
    logger.info(f"Создана запись ID {db_entry.id} для сотрудника {employee.full_name}")
    
    return TimeEntryResponse(
        id=db_entry.id,
        employee_id=employee.id,
        employee_name=employee.full_name,
        entry_type=db_entry.entry_type,
        entry_time=db_entry.entry_time,
        method=db_entry.method,
        is_location_valid=db_entry.is_location_valid
    )


@router.post("/entries/batch")
async def create_batch_entries(entries: List[TimeEntryCreate], db: Session = Depends(get_db_session)):
    """
    Пакетная загрузка записей (для offline синхронизации)
    
    Обрабатывает список записей, возвращает результат для каждой.
    Используется терминалами для синхронизации накопленных offline записей.
    """
    logger.info(f"Пакетная загрузка: {len(entries)} записей")
    results = []
    
    for entry in entries:
        try:
            result = await create_time_entry(entry, db)
            results.append({"success": True, "entry": result})
        except Exception as e:
            logger.error(f"Ошибка при обработке записи: {str(e)}")
            results.append({"success": False, "error": str(e)})
    
    return {"results": results, "total": len(entries), "success_count": sum(1 for r in results if r["success"])}


@router.get("/my-today")
async def get_my_today_entries(telegram_id: int, db: Session = Depends(get_db_session)):
    """
    Получить записи и смену сотрудника за сегодня
    
    Используется Telegram ботом для показа текущего статуса.
    """
    employee = db.query(EmployeeDB).filter(EmployeeDB.telegram_id == telegram_id).first()
    if not employee:
        raise HTTPException(404, "Сотрудник не найден")
    
    today = date.today()
    
    # Получить записи за сегодня
    entries = db.query(TimeEntryDB).filter(
        TimeEntryDB.employee_id == employee.id,
        func.date(TimeEntryDB.entry_time) == today
    ).order_by(TimeEntryDB.entry_time).all()
    
    # Получить смену
    shift = db.query(WorkShiftDB).filter(
        WorkShiftDB.employee_id == employee.id,
        WorkShiftDB.shift_date == today
    ).first()
    
    return {
        "entries": [TimeEntryResponse(
            id=e.id,
            employee_id=e.employee_id,
            employee_name=employee.full_name,
            entry_type=e.entry_type,
            entry_time=e.entry_time,
            method=e.method,
            is_location_valid=e.is_location_valid
        ) for e in entries],
        "shift": WorkShiftResponse.from_orm(shift) if shift else None
    }


@router.get("/my-history")
async def get_my_history(
    telegram_id: int,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db_session)
):
    """
    Получить историю работы сотрудника за период
    
    По умолчанию возвращает последние 30 дней.
    """
    employee = db.query(EmployeeDB).filter(EmployeeDB.telegram_id == telegram_id).first()
    if not employee:
        raise HTTPException(404, "Сотрудник не найден")
    
    if not start_date:
        start_date = date.today() - timedelta(days=30)
    if not end_date:
        end_date = date.today()
    
    # Получить смены за период
    shifts = db.query(WorkShiftDB).filter(
        WorkShiftDB.employee_id == employee.id,
        WorkShiftDB.shift_date.between(start_date, end_date)
    ).order_by(desc(WorkShiftDB.shift_date)).all()
    
    total_hours = sum(float(s.total_hours or 0) for s in shifts)
    
    return {
        "shifts": [WorkShiftResponse.from_orm(s) for s in shifts],
        "total_hours": total_hours,
        "period": {"start": start_date, "end": end_date}
    }


@router.get("/entries/{entry_id}")
async def get_time_entry(entry_id: int, db: Session = Depends(get_db_session)):
    """Получить конкретную запись по ID"""
    entry = db.query(TimeEntryDB).filter(TimeEntryDB.id == entry_id).first()
    if not entry:
        raise HTTPException(404, "Запись не найдена")
    
    employee = db.query(EmployeeDB).filter(EmployeeDB.id == entry.employee_id).first()
    
    return TimeEntryResponse(
        id=entry.id,
        employee_id=entry.employee_id,
        employee_name=employee.full_name if employee else "Unknown",
        entry_type=entry.entry_type,
        entry_time=entry.entry_time,
        method=entry.method,
        is_location_valid=entry.is_location_valid
    )


# ==================== Face Recognition Endpoints ====================

@router.get("/face-embeddings")
async def get_face_embeddings(db: Session = Depends(get_db_session)):
    """
    Получить все активные face embeddings для распознавания
    
    Используется терминалом для загрузки базы лиц при старте.
    """
    embeddings = db.query(FaceEmbeddingDB).filter(
        FaceEmbeddingDB.is_active == True
    ).all()
    
    import pickle
    
    result = []
    for emb in embeddings:
        try:
            # Десериализовать embedding
            embedding_array = pickle.loads(emb.embedding)
            result.append({
                "id": emb.id,
                "employee_id": emb.employee_id,
                "embedding": embedding_array.tolist(),  # конвертировать в list для JSON
                "created_at": emb.created_at
            })
        except Exception as e:
            logger.error(f"Ошибка десериализации embedding {emb.id}: {str(e)}")
    
    return result


@router.post("/face-embeddings/upload")
async def upload_face_photo(
    employee_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db_session)
):
    """
    Загрузить фото лица для сотрудника
    
    Извлекает face embedding из фото и сохраняет в БД.
    Требует установленную библиотеку face_recognition.
    """
    try:
        import face_recognition
        import numpy as np
        import pickle
        import io
    except ImportError:
        raise HTTPException(500, "Библиотека face_recognition не установлена")
    
    # Проверить что сотрудник существует
    employee = db.query(EmployeeDB).filter(EmployeeDB.id == employee_id).first()
    if not employee:
        raise HTTPException(404, "Сотрудник не найден")
    
    # Прочитать изображение
    contents = await file.read()
    image = face_recognition.load_image_file(io.BytesIO(contents))
    
    # Получить face encoding
    encodings = face_recognition.face_encodings(image)
    if not encodings:
        raise HTTPException(400, "Лицо не обнаружено на фото")
    
    if len(encodings) > 1:
        raise HTTPException(400, "На фото обнаружено более одного лица")
    
    embedding = encodings[0]
    
    # Деактивировать старые embeddings этого сотрудника
    db.query(FaceEmbeddingDB).filter(
        FaceEmbeddingDB.employee_id == employee_id
    ).update({"is_active": False})
    
    # Сохранить новый embedding
    face_emb = FaceEmbeddingDB(
        employee_id=employee_id,
        embedding=pickle.dumps(embedding),
        photo_url=f"/uploads/faces/{employee_id}_{file.filename}"
    )
    db.add(face_emb)
    db.commit()
    db.refresh(face_emb)
    
    logger.info(f"Загружено фото для сотрудника {employee.full_name}, embedding ID {face_emb.id}")
    
    return {
        "success": True,
        "embedding_id": face_emb.id,
        "employee_id": employee_id,
        "employee_name": employee.full_name
    }


@router.delete("/face-embeddings/{embedding_id}")
async def delete_face_embedding(embedding_id: int, db: Session = Depends(get_db_session)):
    """Удалить (деактивировать) face embedding"""
    embedding = db.query(FaceEmbeddingDB).filter(FaceEmbeddingDB.id == embedding_id).first()
    if not embedding:
        raise HTTPException(404, "Embedding не найден")
    
    embedding.is_active = False
    db.commit()
    
    return {"success": True, "message": "Embedding деактивирован"}


# ==================== Terminal Management ====================

class TerminalRegister(BaseModel):
    """Модель для регистрации терминала"""
    device_id: str
    device_name: str
    location_description: Optional[str] = None


@router.post("/terminals/register")
async def register_terminal(
    terminal: TerminalRegister,
    db: Session = Depends(get_db_session)
):
    """Зарегистрировать новый терминал"""
    existing = db.query(TerminalDB).filter(TerminalDB.device_id == terminal.device_id).first()
    
    if existing:
        # Обновить существующий
        existing.device_name = terminal.device_name
        existing.location_description = terminal.location_description or existing.location_description
        existing.last_seen_at = datetime.utcnow()
        existing.is_active = True
        db.commit()
        return {"success": True, "terminal_id": existing.id, "message": "Терминал обновлен"}
    else:
        # Создать новый
        new_terminal = TerminalDB(
            device_id=terminal.device_id,
            device_name=terminal.device_name,
            location_description=terminal.location_description,
            last_seen_at=datetime.utcnow()
        )
        db.add(new_terminal)
        db.commit()
        db.refresh(new_terminal)
        return {"success": True, "terminal_id": new_terminal.id, "message": "Терминал зарегистрирован"}


@router.get("/terminals")
async def list_terminals(db: Session = Depends(get_db_session)):
    """Получить список всех терминалов"""
    terminals = db.query(TerminalDB).all()
    return [
        {
            "id": t.id,
            "device_id": t.device_id,
            "device_name": t.device_name,
            "location_description": t.location_description,
            "is_active": t.is_active,
            "last_seen_at": t.last_seen_at
        }
        for t in terminals
    ]


# ==================== Employee Lookup ====================

@router.get("/employee/by-pin/{pin}")
async def get_employee_by_pin(pin: str, db: Session = Depends(get_db_session)):
    """
    Получить сотрудника по factory_number (PIN-код)
    
    Используется для веб-интерфейса с PIN-кодом
    """
    employee = db.query(EmployeeDB).filter(
        EmployeeDB.factory_number == pin,
        EmployeeDB.is_active == True
    ).first()
    
    if not employee:
        raise HTTPException(404, "Сотрудник с таким PIN не найден")
    
    return {
        "id": employee.id,
        "full_name": employee.full_name,
        "telegram_id": employee.telegram_id,
        "factory_number": employee.factory_number
    }


# ==================== Admin Reports ====================

@router.get("/admin/reports/daily")
async def get_daily_report(
    report_date: date,
    db: Session = Depends(get_db_session)
):
    """
    Ежедневный отчет по всем сотрудникам
    
    Показывает время прихода, ухода и общее количество часов за указанный день.
    """
    shifts = db.query(WorkShiftDB, EmployeeDB.full_name).join(
        EmployeeDB, EmployeeDB.id == WorkShiftDB.employee_id
    ).filter(
        WorkShiftDB.shift_date == report_date
    ).order_by(EmployeeDB.full_name).all()
    
    result = []
    total_hours = 0
    
    for shift, name in shifts:
        hours = float(shift.total_hours) if shift.total_hours else 0
        total_hours += hours
        
        result.append({
            "employee_name": name,
            "check_in": shift.check_in_time.isoformat() if shift.check_in_time else None,
            "check_out": shift.check_out_time.isoformat() if shift.check_out_time else None,
            "total_hours": hours,
            "status": shift.status
        })
    
    return {
        "date": report_date,
        "employees": result,
        "total_employees": len(result),
        "total_hours": total_hours
    }


@router.get("/admin/reports/monthly")
async def get_monthly_report(
    year: int,
    month: int,
    db: Session = Depends(get_db_session)
):
    """
    Месячный отчет - итоги по часам для каждого сотрудника
    
    Показывает общее количество часов и дней работы за месяц.
    """
    from calendar import monthrange
    
    start_date = date(year, month, 1)
    end_date = date(year, month, monthrange(year, month)[1])
    
    results = db.query(
        EmployeeDB.id,
        EmployeeDB.full_name,
        func.sum(WorkShiftDB.total_hours).label('total_hours'),
        func.count(WorkShiftDB.id).label('days_worked'),
        func.sum(
            case((WorkShiftDB.status == 'complete', 1), else_=0)
        ).label('complete_days')
    ).join(
        WorkShiftDB, WorkShiftDB.employee_id == EmployeeDB.id
    ).filter(
        WorkShiftDB.shift_date.between(start_date, end_date)
    ).group_by(
        EmployeeDB.id, EmployeeDB.full_name
    ).order_by(
        desc('total_hours')
    ).all()
    
    employees_data = []
    total_hours = 0
    
    for emp_id, name, hours, days, complete in results:
        hours_val = float(hours or 0)
        total_hours += hours_val
        
        employees_data.append({
            "employee_id": emp_id,
            "name": name,
            "total_hours": hours_val,
            "days_worked": days,
            "complete_days": complete
        })
    
    return {
        "period": f"{year}-{month:02d}",
        "start_date": start_date,
        "end_date": end_date,
        "employees": employees_data,
        "total_employees": len(employees_data),
        "total_hours": total_hours
    }


@router.get("/admin/reports/employee/{employee_id}")
async def get_employee_report(
    employee_id: int,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db_session)
):
    """
    Детальный отчет по конкретному сотруднику за период
    
    Включает все смены и записи входа/выхода.
    """
    employee = db.query(EmployeeDB).filter(EmployeeDB.id == employee_id).first()
    if not employee:
        raise HTTPException(404, "Сотрудник не найден")
    
    if not start_date:
        start_date = date.today() - timedelta(days=30)
    if not end_date:
        end_date = date.today()
    
    # Получить смены
    shifts = db.query(WorkShiftDB).filter(
        WorkShiftDB.employee_id == employee_id,
        WorkShiftDB.shift_date.between(start_date, end_date)
    ).order_by(desc(WorkShiftDB.shift_date)).all()
    
    # Получить все записи
    entries = db.query(TimeEntryDB).filter(
        TimeEntryDB.employee_id == employee_id,
        func.date(TimeEntryDB.entry_time).between(start_date, end_date)
    ).order_by(desc(TimeEntryDB.entry_time)).all()
    
    total_hours = sum(float(s.total_hours or 0) for s in shifts)
    complete_days = sum(1 for s in shifts if s.status == 'complete')
    
    return {
        "employee": {
            "id": employee.id,
            "name": employee.full_name,
            "telegram_id": employee.telegram_id
        },
        "period": {
            "start": start_date,
            "end": end_date
        },
        "summary": {
            "total_hours": total_hours,
            "days_worked": len(shifts),
            "complete_days": complete_days
        },
        "shifts": [
            {
                "date": s.shift_date,
                "check_in": s.check_in_time.isoformat() if s.check_in_time else None,
                "check_out": s.check_out_time.isoformat() if s.check_out_time else None,
                "total_hours": float(s.total_hours or 0),
                "status": s.status
            }
            for s in shifts
        ],
        "entries": [
            {
                "id": e.id,
                "type": e.entry_type,
                "time": e.entry_time.isoformat(),
                "method": e.method,
                "is_location_valid": e.is_location_valid
            }
            for e in entries
        ]
    }

