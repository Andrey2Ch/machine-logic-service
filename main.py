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

# Импорт роутеров
from src.routers import parts, employees, machines, readings, setups, batches
# Импорт схем
from src.schemas.part import PartResponse
from src.schemas.employee import EmployeeItem
from src.schemas.machine import MachineItem, OperatorMachineViewItem, BatchLabelInfo, BatchAvailabilityInfo
from src.schemas.reading import ReadingInput, ReadingResponse
from src.schemas.setup import SetupInput, QaSetupViewItem, ApproveSetupPayload, ApprovedSetupResponse

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


# Событие startup для инициализации БД
@app.on_event("startup")
async def startup_event():
    initialize_database()
    # Здесь можно добавить другие действия при старте, если нужно

# Подключение роутеров
app.include_router(parts.router)
app.include_router(employees.router)
app.include_router(machines.router)
app.include_router(readings.router)
app.include_router(setups.router)
app.include_router(batches.router)



@app.get("/")
async def root():
    return {
        "service": "Machine Logic Service",
        "status": "running",
        "available_statuses": [status.value for status in SetupStatus]
    }





























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



# --- BATCH MANAGEMENT ENDPOINTS ---













# --- WAREHOUSE ACCEPTANCE ENDPOINTS ---







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
async def get_lots_pending_qc(
    db: Session = Depends(get_db_session), 
    current_user_qa_id: Optional[int] = Query(None, alias="qaId"),
    hideCompleted: Optional[bool] = Query(False, description="Скрыть завершенные лоты и лоты со всеми проверенными батчами")
):
    """
    Получить лоты для ОТК (старая логика с оптимизированной фильтрацией).
    
    1. Находит лоты с активными (неархивными) батчами.
    2. Для каждого такого лота извлекает детали из последней наладки, включая имя инспектора, плановое количество и имя станка.
    3. Опционально фильтрует по qaId, если он предоставлен (на основе qa_id в последней наладке).
    4. Опционально скрывает завершенные лоты (hideCompleted=True) - ОПТИМИЗИРОВАНО через SQL.
    """
    logger.info(f"Запрос /lots/pending-qc получен. qaId: {current_user_qa_id}, hideCompleted: {hideCompleted}")
    try:
        # 1. Базовый запрос на получение ID лотов с активными (неархивными) батчами
        base_lot_ids_query = db.query(BatchDB.lot_id)\
            .filter(BatchDB.current_location != 'archived') \
            .distinct()

        # 2. Если включен фильтр hideCompleted, дополнительно исключаем завершенные лоты
        if hideCompleted:
            # Исключаем лоты со статусом 'completed'
            base_lot_ids_query = base_lot_ids_query.join(LotDB, BatchDB.lot_id == LotDB.id)\
                .filter(LotDB.status != 'completed')
            
            # Исключаем лоты где ВСЕ батчи проверены
            # Проверенные = НЕ qc_pending И (ЕСТЬ inspector_id ИЛИ в финальных состояниях)
            # Логика: исключаем лот, если НЕТ непроверенных батчей
            unchecked_batch_exists = db.query(BatchDB.lot_id)\
                .filter(
                    or_(
                        BatchDB.current_location == 'qc_pending',  # qc_pending = непроверенный
                        and_(
                            BatchDB.qc_inspector_id.is_(None),  # НЕТ инспектора
                            BatchDB.current_location.notin_(['good', 'defect', 'archived'])  # И НЕ в финальных состояниях
                        )
                    )
                )\
                .distinct()\
                .subquery()
            
            # Включаем только лоты с непроверенными батчами
            base_lot_ids_query = base_lot_ids_query.filter(BatchDB.lot_id.in_(unchecked_batch_exists))

        lot_ids_with_active_batches_tuples = base_lot_ids_query.all()
        lot_ids = [item[0] for item in lot_ids_with_active_batches_tuples]

        if not lot_ids:
            logger.info("Не найдено лотов с активными батчами (после фильтрации).")
            return []
        
        logger.info(f"Найдены ID лотов с активными батчами: {lot_ids} (всего: {len(lot_ids)})")
        
        # 3. Основной запрос для данных по лотам и деталям
        lots_query = db.query(LotDB, PartDB).select_from(LotDB)\
            .join(PartDB, LotDB.part_id == PartDB.id)\
            .filter(LotDB.id.in_(lot_ids))

        lots_query_result = lots_query.all()
        logger.info(f"Всего лотов (с деталями) для обработки: {len(lots_query_result)}")
        
        result = []
        for lot_obj, part_obj in lots_query_result:
            logger.debug(f"Обработка лота ID: {lot_obj.id}, Номер: {lot_obj.lot_number}")
            
            planned_quantity_val = None
            inspector_name_val = None
            machine_name_val = None
            
            # 4. Находим последнюю наладку для данного лота, включая имя станка
            latest_setup_details = db.query(
                    SetupDB.planned_quantity,
                    SetupDB.qa_id,
                    EmployeeDB.full_name.label("inspector_name_from_setup"),
                    MachineDB.name.label("machine_name_from_setup"),
                    SetupDB.machine_id.label("setup_machine_id")
                )\
                .outerjoin(EmployeeDB, SetupDB.qa_id == EmployeeDB.id) \
                .outerjoin(MachineDB, SetupDB.machine_id == MachineDB.id) \
                .filter(SetupDB.lot_id == lot_obj.id)\
                .order_by(desc(SetupDB.created_at))\
                .first()

            passes_qa_filter = True

            if latest_setup_details:
                planned_quantity_val = latest_setup_details.planned_quantity
                machine_name_val = latest_setup_details.machine_name_from_setup
                logger.debug(f"Lot ID {lot_obj.id}: setup found, machine_id: {latest_setup_details.setup_machine_id}")

                if latest_setup_details.qa_id:
                    inspector_name_val = latest_setup_details.inspector_name_from_setup
                    if current_user_qa_id is not None and latest_setup_details.qa_id != current_user_qa_id:
                        passes_qa_filter = False
                elif current_user_qa_id is not None:
                    passes_qa_filter = False
            elif current_user_qa_id is not None:
                passes_qa_filter = False

            if not passes_qa_filter:
                continue

            item_data = {
                'id': lot_obj.id,
                'drawing_number': part_obj.drawing_number,
                'lot_number': lot_obj.lot_number,
                'inspector_name': inspector_name_val,
                'planned_quantity': planned_quantity_val,
                'machine_name': machine_name_val,
            }
            
            try:
                result.append(LotInfoItem.model_validate(item_data))
            except AttributeError:
                result.append(LotInfoItem.parse_obj(item_data))

        logger.info(f"Сформировано {len(result)} элементов для ответа /lots/pending-qc (оптимизированная версия).")
        return result

    except Exception as e:
        logger.error(f"Ошибка в /lots/pending-qc: {e}", exc_info=True)
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











# --- START NEW ENDPOINT --- 




# <<< НОВЫЕ Pydantic МОДЕЛИ ДЛЯ LOT >>>
from enum import Enum

class LotStatus(str, Enum):
    """Статусы лотов для синхронизации между Telegram-ботом и FastAPI"""
    NEW = "new"                    # Новый лот от Order Manager
    IN_PRODUCTION = "in_production"  # Лот в производстве (после начала наладки)
    POST_PRODUCTION = "post_production"  # Лот после производства (все наладки завершены)
    COMPLETED = "completed"        # Завершенный лот
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
    # УДАЛЕНО: response.headers["Access-Control-Expose-Headers"] = "X-Total-Count" 
        
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

@app.patch("/lots/{lot_id}/quantity", response_model=LotResponse, tags=["Lots"])
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

@app.patch("/lots/{lot_id}/close", response_model=LotResponse, tags=["Lots"])
async def close_lot(lot_id: int, db: Session = Depends(get_db_session)):
    """
    Закрыть лот (перевести в статус 'completed').
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
        lot.status = 'completed'
        db.commit()
        db.refresh(lot)
        
        logger.info(f"Successfully closed lot {lot_id}")
        
        # Синхронизируем с Telegram-ботом
        try:
            await sync_lot_status_to_telegram_bot(lot_id, 'completed')
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

