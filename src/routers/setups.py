from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, text
from datetime import datetime
import logging
import asyncio

from src.database import get_db_session
from src.models.models import (
    SetupDB, MachineDB, PartDB, EmployeeDB, LotDB, BatchDB, ReadingDB
)
from src.schemas.setup import (
    SetupInput, QaSetupViewItem, ApproveSetupPayload, ApprovedSetupResponse
)
from src.services.notification_service import send_setup_approval_notifications

logger = logging.getLogger(__name__)

router = APIRouter(prefix="", tags=["Setups"])

@router.get("/setup/{machine_id}/status")
async def get_setup_status(machine_id: int, db: Session = Depends(get_db_session)):
    """
    Получить статус последней наладки для станка
    """
    setup = db.query(SetupDB).filter(
        SetupDB.machine_id == machine_id
    ).order_by(SetupDB.created_at.desc()).first()
    
    if not setup:
        return {"status": "no_setup", "message": "Наладок не найдено"}
    
    return {
        "setup_id": setup.id,
        "status": setup.status,
        "machine_id": setup.machine_id,
        "created_at": setup.created_at
    }

@router.get("/setup/{machine_id}/all")
async def get_setup_history(machine_id: int, db: Session = Depends(get_db_session)):
    """
    Получить историю наладок для станка
    """
    setups = db.query(SetupDB).join(PartDB, SetupDB.part_id == PartDB.id)\
        .filter(SetupDB.machine_id == machine_id)\
        .order_by(SetupDB.created_at.desc())\
        .all()
    
    return {
        "machine_id": machine_id,
        "setups": [
            {
                "id": s.id,
                "drawing_number": s.part.drawing_number if s.part else None,
                "status": s.status,
                "created_at": s.created_at
            } for s in setups
        ]
    }

@router.post("/setup")
async def create_setup(setup: SetupInput, db: Session = Depends(get_db_session)):
    """
    Создать новую наладку
    """
    logger.info(f"Creating setup for machine {setup.machine_id}")
    
    # Начинаем транзакцию
    trans = db.begin()
    try:
        # Находим часть по номеру чертежа
        part = db.query(PartDB).filter(PartDB.drawing_number == setup.drawing_number).first()
        if not part:
            trans.rollback()
            raise HTTPException(status_code=404, detail=f"Часть с номером чертежа {setup.drawing_number} не найдена")

        # Ищем лот
        lot = db.query(LotDB).filter(LotDB.lot_number == setup.lot_number).first()
        if not lot:
            trans.rollback()
            raise HTTPException(status_code=404, detail=f"Лот {setup.lot_number} не найден")

        # Проверяем, что лот содержит правильную часть
        if lot.part_id != part.id:
            trans.rollback()
            raise HTTPException(
                status_code=400, 
                detail=f"Лот {setup.lot_number} не содержит часть {setup.drawing_number}"
            )

        # Завершаем предыдущие наладки для этого станка
        prev_setups = db.query(SetupDB)\
            .filter(SetupDB.machine_id == setup.machine_id)\
            .filter(SetupDB.end_time.is_(None))\
            .all()
        
        current_time = datetime.now()
        for prev_setup in prev_setups:
            logger.info(f"Closing previous setup {prev_setup.id}")
            prev_setup.end_time = current_time
            prev_setup.status = 'completed'

        # Создаем новую наладку
        new_setup = SetupDB(
            machine_id=setup.machine_id,
            employee_id=setup.operator_id,
            part_id=part.id,
            lot_id=lot.id,
            planned_quantity=setup.planned_quantity,
            cycle_time_seconds=setup.cycle_time_seconds,
            status='created',
            created_at=current_time
        )
        
        db.add(new_setup)
        trans.commit()
        db.refresh(new_setup)
        
        logger.info(f"Setup {new_setup.id} created successfully")
        return {
            "success": True,
            "setup_id": new_setup.id,
            "message": "Наладка создана успешно"
        }
        
    except HTTPException as http_exc:
        trans.rollback()
        logger.error(f"HTTPException in create_setup: {http_exc.detail}")
        raise http_exc
    except Exception as e:
        trans.rollback()
        logger.error(f"Error creating setup: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while creating setup")

@router.get("/setups/qa-view", response_model=List[QaSetupViewItem])
async def get_qa_view(db: Session = Depends(get_db_session)):
    """
    Получить представление наладок для контроля качества
    """
    logger.info("Getting QA view of setups")
    try:
        qa_view_query = text("""
            SELECT 
                s.id,
                m.name as machine_name,
                p.drawing_number,
                l.lot_number,
                e.full_name as machinist_name,
                s.start_time,
                s.status,
                qa_emp.full_name as qa_name,
                s.qa_date
            FROM setup_jobs s
            JOIN machines m ON s.machine_id = m.id
            JOIN parts p ON s.part_id = p.id
            JOIN lots l ON s.lot_id = l.id
            JOIN employees e ON s.employee_id = e.id
            LEFT JOIN employees qa_emp ON s.qa_id = qa_emp.id
            WHERE s.status IN ('created', 'pending_qc', 'allowed')
            ORDER BY s.created_at DESC
        """)
        
        result = db.execute(qa_view_query)
        setups = result.fetchall()
        
        qa_setups = []
        for setup in setups:
            qa_setup = QaSetupViewItem(
                id=setup.id,
                machine_name=setup.machine_name,
                drawing_number=setup.drawing_number,
                lot_number=setup.lot_number,
                machinist_name=setup.machinist_name,
                start_time=setup.start_time,
                status=setup.status,
                qa_name=setup.qa_name,
                qa_date=setup.qa_date
            )
            qa_setups.append(qa_setup)
        
        logger.info(f"QA view returned {len(qa_setups)} setups")
        return qa_setups
        
    except Exception as e:
        logger.error(f"Error getting QA view: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error getting QA view")

@router.post("/setups/{setup_id}/approve", response_model=ApprovedSetupResponse)
async def approve_setup(
    setup_id: int,
    payload: ApproveSetupPayload,
    db: Session = Depends(get_db_session)
):
    """
    Одобрить наладку
    """
    logger.info(f"Approving setup {setup_id} by QA {payload.qa_id}")
    
    trans = db.begin()
    try:
        setup = db.query(SetupDB).filter(SetupDB.id == setup_id).first()
        if not setup:
            trans.rollback()
            raise HTTPException(status_code=404, detail="Наладка не найдена")
        
        if setup.status not in ['created', 'pending_qc']:
            trans.rollback()
            raise HTTPException(
                status_code=400, 
                detail=f"Нельзя одобрить наладку в статусе {setup.status}"
            )
        
        # Обновляем наладку
        setup.status = 'allowed'
        setup.qa_id = payload.qa_id
        setup.qa_date = datetime.now()
        
        trans.commit()
        
        # Отправляем уведомления асинхронно
        try:
            asyncio.create_task(send_setup_approval_notifications(setup_id, db))
        except Exception as notification_error:
            logger.warning(f"Failed to send approval notifications: {notification_error}")
        
        # Формируем ответ
        qa_name = db.query(EmployeeDB.full_name).filter(EmployeeDB.id == payload.qa_id).scalar()
        machine_name = db.query(MachineDB.name).filter(MachineDB.id == setup.machine_id).scalar()
        part = db.query(PartDB).filter(PartDB.id == setup.part_id).first()
        lot = db.query(LotDB).filter(LotDB.id == setup.lot_id).first()
        machinist_name = db.query(EmployeeDB.full_name).filter(EmployeeDB.id == setup.employee_id).scalar()
        
        response = ApprovedSetupResponse(
            id=setup.id,
            machineName=machine_name,
            drawingNumber=part.drawing_number if part else None,
            lotNumber=lot.lot_number if lot else None,
            machinistName=machinist_name,
            startTime=setup.start_time,
            status=setup.status,
            qaName=qa_name,
            qaDate=setup.qa_date
        )
        
        logger.info(f"Setup {setup_id} approved successfully")
        return response
        
    except HTTPException as http_exc:
        trans.rollback()
        logger.error(f"HTTPException in approve_setup: {http_exc.detail}")
        raise http_exc
    except Exception as e:
        trans.rollback()
        logger.error(f"Error approving setup: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while approving setup")

@router.post("/setups/{setup_id}/complete")
async def complete_setup(setup_id: int, db: Session = Depends(get_db_session)):
    """
    Завершить наладку
    """
    logger.info(f"Completing setup {setup_id}")
    
    trans = db.begin()
    try:
        setup = db.query(SetupDB).filter(SetupDB.id == setup_id).first()
        if not setup:
            trans.rollback()
            raise HTTPException(status_code=404, detail="Наладка не найдена")
        
        if setup.status != 'started':
            trans.rollback()
            raise HTTPException(
                status_code=400, 
                detail=f"Нельзя завершить наладку в статусе {setup.status}"
            )
        
        # Обновляем наладку
        setup.status = 'completed'
        setup.end_time = datetime.now()
        
        trans.commit()
        
        # Проверяем статус лота после коммита
        try:
            from main import check_lot_completion_and_update_status
            await check_lot_completion_and_update_status(setup.lot_id, db)
        except Exception as lot_error:
            logger.warning(f"Failed to check lot completion: {lot_error}")
        
        logger.info(f"Setup {setup_id} completed successfully")
        return {
            "success": True,
            "message": "Наладка завершена успешно",
            "setup_id": setup_id,
            "end_time": setup.end_time
        }
        
    except HTTPException as http_exc:
        trans.rollback()
        logger.error(f"HTTPException in complete_setup: {http_exc.detail}")
        raise http_exc
    except Exception as e:
        trans.rollback()
        logger.error(f"Error completing setup: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while completing setup") 