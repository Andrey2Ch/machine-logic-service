"""
WhatsApp client for machine-logic-service
Uses GOWA (Go WhatsApp Web Multi-Device) API
Sends messages to GROUPS only (no personal messages)
"""
import os
import logging
import httpx
from typing import Optional, List
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

WHATSAPP_API_URL = os.getenv('WHATSAPP_API_URL', '')
WHATSAPP_ENABLED = os.getenv('WHATSAPP_ENABLED', 'false').lower() == 'true'

# WhatsApp Group JIDs
WHATSAPP_GROUP_MACHINISTS = os.getenv('WHATSAPP_GROUP_MACHINISTS', '')  # Наладчики
WHATSAPP_GROUP_OPERATORS_A = os.getenv('WHATSAPP_GROUP_OPERATORS_A', '')  # Смена I
WHATSAPP_GROUP_OPERATORS_B = os.getenv('WHATSAPP_GROUP_OPERATORS_B', '')  # Смена II
WHATSAPP_GROUP_QA = os.getenv('WHATSAPP_GROUP_QA', '')  # Бикорет (ОТК)
WHATSAPP_GROUP_ADMIN = os.getenv('WHATSAPP_GROUP_ADMIN', '')  # Руководство (optional)

# Автоматически добавляем протокол, если отсутствует
if WHATSAPP_ENABLED and WHATSAPP_API_URL and not WHATSAPP_API_URL.startswith(('http://', 'https://')):
    WHATSAPP_API_URL = f"https://{WHATSAPP_API_URL}"
    logger.info(f"Corrected WhatsApp API URL to: {WHATSAPP_API_URL}")

logger.info(f"WhatsApp API URL: {WHATSAPP_API_URL}")
logger.info(f"WhatsApp Enabled: {WHATSAPP_ENABLED}")

# Role ID -> Group JID(s) mapping
# Role IDs: 1=operator, 2=machinist, 3=admin, 5=qa
ROLE_ID_TO_GROUPS = {
    1: [WHATSAPP_GROUP_OPERATORS_A, WHATSAPP_GROUP_OPERATORS_B],  # Operators - both shifts
    2: [WHATSAPP_GROUP_MACHINISTS],  # Machinists
    3: [WHATSAPP_GROUP_ADMIN] if WHATSAPP_GROUP_ADMIN else [],  # Admin
    5: [WHATSAPP_GROUP_QA],  # QA
}


def strip_html(text: str) -> str:
    """Remove HTML tags and convert to WhatsApp formatting"""
    result = text.replace('<b>', '*').replace('</b>', '*')
    result = result.replace('<i>', '_').replace('</i>', '_')
    result = result.replace('<code>', '`').replace('</code>', '`')
    result = result.replace('<br>', '\n').replace('<br/>', '\n')
    return result


async def send_whatsapp_to_group(group_jid: str, message: str) -> bool:
    """
    Send message to a WhatsApp group
    
    Args:
        group_jid: Group JID (e.g., "972542239711-1444580538@g.us")
        message: Message text (HTML will be converted to WhatsApp format)
    
    Returns:
        bool: True if successful, False otherwise
    """
    if not WHATSAPP_ENABLED:
        return False
    
    if not WHATSAPP_API_URL:
        logger.warning("WHATSAPP_API_URL not configured")
        return False
    
    if not group_jid:
        logger.warning("No group JID provided")
        return False
    
    # Convert HTML to WhatsApp formatting
    clean_message = strip_html(message)
    
    try:
        url = f"{WHATSAPP_API_URL}/send/message"
        payload = {
            "phone": group_jid,
            "message": clean_message
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload)
            
            if response.status_code == 200:
                logger.info(f"WhatsApp message sent to group {group_jid}")
                return True
            else:
                logger.error(f"WhatsApp API error: {response.status_code} - {response.text}")
                return False
                
    except httpx.TimeoutException:
        logger.error(f"WhatsApp API timeout for group {group_jid}")
        return False
    except Exception as e:
        logger.error(f"WhatsApp send error: {e}")
        return False


async def send_whatsapp_to_role(db: Session, role_id: int, message: str, exclude_id: int = None) -> int:
    """
    Send message to WhatsApp group(s) for a specific role_id
    Note: db and exclude_id parameters kept for API compatibility but not used (groups only)
    
    Args:
        db: Database session (not used for group messages)
        role_id: Role ID (1=operator, 2=machinist, 3=admin, 5=qa)
        message: Message text
        exclude_id: Employee ID to exclude (not used for group messages)
    
    Returns:
        int: Number of groups message was sent to
    """
    if not WHATSAPP_ENABLED:
        return 0
    
    groups = ROLE_ID_TO_GROUPS.get(role_id, [])
    if not groups:
        logger.debug(f"No WhatsApp groups configured for role_id: {role_id}")
        return 0
    
    sent_count = 0
    for group_jid in groups:
        if group_jid:  # Skip empty JIDs
            success = await send_whatsapp_to_group(group_jid, message)
            if success:
                sent_count += 1
    
    logger.info(f"WhatsApp sent to {sent_count}/{len([g for g in groups if g])} groups for role_id {role_id}")
    return sent_count
