"""
WhatsApp client for machine-logic-service
Uses GOWA (Go WhatsApp Web Multi-Device) API
"""
import os
import logging
import httpx
from typing import Optional, List
from sqlalchemy.orm import Session
from src.models.models import EmployeeDB

logger = logging.getLogger(__name__)

WHATSAPP_API_URL = os.getenv('WHATSAPP_API_URL', '')
WHATSAPP_ENABLED = os.getenv('WHATSAPP_ENABLED', 'false').lower() == 'true'

# Автоматически добавляем протокол, если отсутствует
if WHATSAPP_ENABLED and WHATSAPP_API_URL and not WHATSAPP_API_URL.startswith(('http://', 'https://')):
    WHATSAPP_API_URL = f"https://{WHATSAPP_API_URL}"
    logger.info(f"Corrected WhatsApp API URL to: {WHATSAPP_API_URL}")

logger.info(f"WhatsApp API URL: {WHATSAPP_API_URL}")
logger.info(f"WhatsApp Enabled: {WHATSAPP_ENABLED}")


async def send_whatsapp_message(phone: str, message: str) -> bool:
    """
    Отправляет сообщение через GOWA API
    
    Args:
        phone: Номер телефона (например, "972525163251")
        message: Текст сообщения (HTML теги будут удалены)
    
    Returns:
        bool: True если успешно, False иначе
    """
    if not WHATSAPP_ENABLED:
        return False
    
    if not WHATSAPP_API_URL:
        logger.warning("WHATSAPP_API_URL not configured")
        return False
    
    if not phone:
        logger.warning("No phone number provided for WhatsApp message")
        return False
    
    # Убираем HTML теги для WhatsApp
    clean_message = message.replace('<b>', '*').replace('</b>', '*')
    clean_message = clean_message.replace('<i>', '_').replace('</i>', '_')
    
    try:
        url = f"{WHATSAPP_API_URL}/send/message"
        payload = {
            "phone": phone,
            "message": clean_message
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload)
            
            if response.status_code == 200:
                logger.info(f"WhatsApp message sent to {phone}")
                return True
            else:
                logger.error(f"WhatsApp API error: {response.status_code} - {response.text}")
                return False
                
    except httpx.TimeoutException:
        logger.error(f"WhatsApp API timeout for phone {phone}")
        return False
    except Exception as e:
        logger.error(f"WhatsApp send error: {e}")
        return False


async def send_whatsapp_to_role(db: Session, role_id: int, message: str, exclude_id: int = None) -> int:
    """
    Отправляет сообщение всем пользователям с указанной ролью, у которых есть WhatsApp номер
    
    Args:
        db: Сессия базы данных
        role_id: ID роли
        message: Текст сообщения
        exclude_id: ID сотрудника для исключения
    
    Returns:
        int: Количество успешно отправленных сообщений
    """
    if not WHATSAPP_ENABLED:
        return 0
    
    try:
        query = db.query(EmployeeDB)\
            .filter(EmployeeDB.role_id == role_id)\
            .filter(EmployeeDB.is_active == True)\
            .filter(EmployeeDB.whatsapp_phone != None)\
            .filter(EmployeeDB.whatsapp_phone != '')
        
        if exclude_id is not None:
            query = query.filter(EmployeeDB.id != exclude_id)
        
        employees = query.all()
        
        sent_count = 0
        for emp in employees:
            if emp.whatsapp_phone:
                success = await send_whatsapp_message(emp.whatsapp_phone, message)
                if success:
                    sent_count += 1
                    logger.debug(f"WhatsApp sent to {emp.full_name} ({emp.whatsapp_phone})")
        
        logger.info(f"WhatsApp sent to {sent_count}/{len(employees)} users with role_id {role_id}")
        return sent_count
        
    except Exception as e:
        logger.error(f"Failed to send WhatsApp to role_id {role_id}: {e}", exc_info=True)
        return 0

