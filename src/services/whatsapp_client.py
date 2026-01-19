"""
WhatsApp client for machine-logic-service
Uses GOWA (Go WhatsApp Web Multi-Device) API
Sends messages to GROUPS only (no personal messages)
Supports AI translation per role language settings
"""
import os
import re
import logging
import httpx
from typing import Optional, List
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def html_to_whatsapp(text: str) -> str:
    """
    Конвертирует HTML теги в WhatsApp форматирование.
    - <b>text</b> → *text*
    - <strong>text</strong> → *text*
    - <i>text</i> → _text_
    - <em>text</em> → _text_
    - <u>text</u> → text (WhatsApp не поддерживает underline)
    - <s>text</s> → ~text~
    - <code>text</code> → ```text```
    """
    if not text:
        return text
    
    # Bold: <b> or <strong>
    text = re.sub(r'<b>(.*?)</b>', r'*\1*', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<strong>(.*?)</strong>', r'*\1*', text, flags=re.DOTALL | re.IGNORECASE)
    
    # Italic: <i> or <em>
    text = re.sub(r'<i>(.*?)</i>', r'_\1_', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<em>(.*?)</em>', r'_\1_', text, flags=re.DOTALL | re.IGNORECASE)
    
    # Underline: <u> - просто убираем теги (WA не поддерживает)
    text = re.sub(r'<u>(.*?)</u>', r'\1', text, flags=re.DOTALL | re.IGNORECASE)
    
    # Strikethrough: <s>
    text = re.sub(r'<s>(.*?)</s>', r'~\1~', text, flags=re.DOTALL | re.IGNORECASE)
    
    # Code: <code>
    text = re.sub(r'<code>(.*?)</code>', r'```\1```', text, flags=re.DOTALL | re.IGNORECASE)
    
    return text

WHATSAPP_API_URL = os.getenv('WHATSAPP_API_URL', '')
WHATSAPP_ENABLED = os.getenv('WHATSAPP_ENABLED', 'false').lower() == 'true'

# WhatsApp Group JIDs
WHATSAPP_GROUP_MACHINISTS = os.getenv('WHATSAPP_GROUP_MACHINISTS', '')  # Наладчики
WHATSAPP_GROUP_OPERATORS_A = os.getenv('WHATSAPP_GROUP_OPERATORS_A', '')  # Смена I
WHATSAPP_GROUP_OPERATORS_B = os.getenv('WHATSAPP_GROUP_OPERATORS_B', '')  # Смена II
WHATSAPP_GROUP_QA = os.getenv('WHATSAPP_GROUP_QA', '')  # Бикорет (ОТК)
WHATSAPP_GROUP_ADMIN = os.getenv('WHATSAPP_GROUP_ADMIN', '')  # Руководство (optional)
WHATSAPP_GROUP_VIEWER = os.getenv('WHATSAPP_GROUP_VIEWER', '')  # Viewer (мониторинг)

# Автоматически добавляем протокол, если отсутствует
if WHATSAPP_ENABLED and WHATSAPP_API_URL and not WHATSAPP_API_URL.startswith(('http://', 'https://')):
    WHATSAPP_API_URL = f"https://{WHATSAPP_API_URL}"
    logger.info(f"Corrected WhatsApp API URL to: {WHATSAPP_API_URL}")

logger.info(f"WhatsApp API URL: {WHATSAPP_API_URL}")
logger.info(f"WhatsApp Enabled: {WHATSAPP_ENABLED}")

# Role ID -> Group JID(s) mapping
# Role IDs: 1=operator, 2=machinist, 3=admin, 5=qa, 7=viewer
ROLE_ID_TO_GROUPS = {
    1: [WHATSAPP_GROUP_OPERATORS_A, WHATSAPP_GROUP_OPERATORS_B],  # Operators - both shifts
    2: [WHATSAPP_GROUP_MACHINISTS],  # Machinists
    3: [WHATSAPP_GROUP_ADMIN] if WHATSAPP_GROUP_ADMIN else [],  # Admin
    5: [WHATSAPP_GROUP_QA],  # QA
    7: [WHATSAPP_GROUP_VIEWER] if WHATSAPP_GROUP_VIEWER else [],  # Viewer
}

# Role ID -> Language setting column name (for translation)
ROLE_ID_TO_LANG_COLUMN = {
    1: 'operators',
    2: 'machinists',
    3: 'admin',
    5: 'qa',
    7: 'viewer',
}


def strip_html(text: str) -> str:
    """Convert HTML tags to WhatsApp formatting"""
    return html_to_whatsapp(text)


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


async def send_whatsapp_personal(phone: str, message: str) -> bool:
    """
    Отправка личного WhatsApp сообщения по номеру телефона
    
    Args:
        phone: Номер телефона (например "972501234567")
        message: Текст сообщения (HTML конвертируется в WhatsApp формат)
    """
    if not WHATSAPP_ENABLED:
        return False
    
    if not WHATSAPP_API_URL:
        logger.warning("WHATSAPP_API_URL not configured")
        return False
    
    if not phone:
        return False
    
    # Убираем + и пробелы из номера
    clean_phone = phone.replace("+", "").replace(" ", "").replace("-", "")
    
    # Конвертируем израильский формат (0XX) в международный (972XX)
    if clean_phone.startswith("0") and len(clean_phone) == 10:
        clean_phone = "972" + clean_phone[1:]
        logger.debug(f"Converted local phone to international: {clean_phone[:6]}***")
    
    # Конвертируем HTML в WhatsApp формат
    clean_message = strip_html(message)
    
    try:
        url = f"{WHATSAPP_API_URL}/send/message"
        payload = {
            "phone": clean_phone,
            "message": clean_message
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload)
            
            if response.status_code == 200:
                logger.info(f"WhatsApp personal message sent to {clean_phone[:6]}***")
                return True
            else:
                logger.error(f"WhatsApp API error: {response.status_code} - {response.text}")
                return False
                
    except Exception as e:
        logger.error(f"WhatsApp personal send error: {e}")
        return False


async def send_whatsapp_to_role_personal(
    db: Session, 
    role_id: int, 
    message: str,
    notification_type: str = None
) -> int:
    """
    Отправка личных WhatsApp сообщений всем сотрудникам с указанной ролью
    Checks enabled flag from notification_settings!
    
    Args:
        db: Сессия БД
        role_id: ID роли
        message: Текст сообщения
        notification_type: Тип уведомления для перевода и проверки enabled
    
    Returns:
        Количество отправленных сообщений
    """
    if not WHATSAPP_ENABLED:
        return 0
    
    # Проверяем включено ли для этой роли в настройках
    lang_column = ROLE_ID_TO_LANG_COLUMN.get(role_id)
    if lang_column and notification_type:
        try:
            from src.routers.notification_settings import is_notification_enabled
            enabled = await is_notification_enabled(db, notification_type, lang_column)
            logger.info(f"WhatsApp personal check: {lang_column}/{notification_type} = {enabled}")
            if not enabled:
                logger.info(f"WhatsApp personal DISABLED for {lang_column} on {notification_type} - skipping")
                return 0
        except Exception as e:
            logger.warning(f"Failed to check notification enabled: {e}, proceeding anyway")
    
    from src.models.models import EmployeeDB
    
    # Получаем сотрудников с этой ролью и whatsapp_phone
    employees = db.query(EmployeeDB).filter(
        EmployeeDB.role_id == role_id,
        EmployeeDB.is_active == True,
        EmployeeDB.whatsapp_phone != None,
        EmployeeDB.whatsapp_phone != ''
    ).all()
    
    if not employees:
        logger.debug(f"No employees with whatsapp_phone for role_id: {role_id}")
        return 0
    
    # Переводим если нужно
    final_message = message
    lang_column = ROLE_ID_TO_LANG_COLUMN.get(role_id)
    
    if lang_column and notification_type:
        try:
            from src.routers.notification_settings import get_notification_language
            from src.services.ai_translate import translate_notification
            
            target_lang = await get_notification_language(db, notification_type, lang_column)
            
            if target_lang and target_lang != 'ru':
                final_message = await translate_notification(message, target_lang)
                logger.info(f"Translated personal WA to '{target_lang}' for role_id {role_id}")
        except Exception as e:
            logger.warning(f"Translation failed: {e}")
    
    sent_count = 0
    for emp in employees:
        success = await send_whatsapp_personal(emp.whatsapp_phone, final_message)
        if success:
            sent_count += 1
    
    logger.info(f"WhatsApp personal sent to {sent_count}/{len(employees)} employees for role_id {role_id}")
    return sent_count


async def send_whatsapp_to_role(
    db: Session, 
    role_id: int, 
    message: str, 
    exclude_id: int = None,
    notification_type: str = None
) -> int:
    """
    Send message to WhatsApp group(s) for a specific role_id
    Checks enabled flag from notification_settings!
    Supports AI translation based on role language settings
    
    Args:
        db: Database session (used for getting language settings)
        role_id: Role ID (1=operator, 2=machinist, 3=admin, 5=qa, 7=viewer)
        message: Message text (in Russian)
        exclude_id: Employee ID to exclude (not used for group messages)
        notification_type: Type of notification (for language lookup and enabled check)
    
    Returns:
        int: Number of groups message was sent to
    """
    if not WHATSAPP_ENABLED:
        return 0
    
    # Проверяем включено ли для этой роли в настройках
    lang_column = ROLE_ID_TO_LANG_COLUMN.get(role_id)
    if lang_column and notification_type:
        try:
            from src.routers.notification_settings import is_notification_enabled
            enabled = await is_notification_enabled(db, notification_type, lang_column)
            if not enabled:
                logger.debug(f"WhatsApp disabled for {lang_column} on {notification_type}")
                return 0
        except Exception as e:
            logger.warning(f"Failed to check notification enabled: {e}")
    
    groups = ROLE_ID_TO_GROUPS.get(role_id, [])
    if not groups:
        logger.debug(f"No WhatsApp groups configured for role_id: {role_id}")
        return 0
    
    # Get language for this role and translate if needed
    final_message = message
    lang_column = ROLE_ID_TO_LANG_COLUMN.get(role_id)
    
    if lang_column and notification_type:
        try:
            from src.routers.notification_settings import get_notification_language
            from src.services.ai_translate import translate_notification
            
            target_lang = await get_notification_language(db, notification_type, lang_column)
            
            if target_lang and target_lang != 'ru':
                # Translate the message
                final_message = await translate_notification(message, target_lang)
                logger.info(f"Translated message to '{target_lang}' for role_id {role_id}")
        except Exception as e:
            logger.warning(f"Translation failed, using original: {e}")
            final_message = message
    
    sent_count = 0
    for group_jid in groups:
        if group_jid:  # Skip empty JIDs
            success = await send_whatsapp_to_group(group_jid, final_message)
            if success:
                sent_count += 1
    
    logger.info(f"WhatsApp sent to {sent_count}/{len([g for g in groups if g])} groups for role_id {role_id}")
    return sent_count


async def send_whatsapp_to_all_enabled_roles(
    db: Session,
    message: str,
    notification_type: str
) -> int:
    """
    Отправляет WhatsApp уведомление ВСЕМ ролям, у которых включено в настройках.
    Проверяет enabled_* флаги из таблицы notification_settings.
    Viewer получает личные сообщения, остальные - в группы.
    
    Args:
        db: Сессия БД
        message: Текст сообщения
        notification_type: Тип уведомления (ключ в notification_settings)
    
    Returns:
        Общее количество отправленных сообщений/групп
    """
    if not WHATSAPP_ENABLED:
        return 0
    
    if not notification_type:
        logger.warning("notification_type is required for send_whatsapp_to_all_enabled_roles")
        return 0
    
    try:
        from src.routers.notification_settings import is_notification_enabled
        
        total_sent = 0
        
        # Роли и их маппинг: role_id -> (channel_name, is_personal)
        roles_config = [
            (1, 'operators', False),    # Operators - группа
            (2, 'machinists', False),   # Machinists - группа
            (3, 'admin', False),        # Admin - группа
            (5, 'qa', False),           # QA - группа
            (7, 'viewer', True),        # Viewer - личные!
        ]
        
        for role_id, channel, is_personal in roles_config:
            # Проверяем включено ли в настройках
            enabled = await is_notification_enabled(db, notification_type, channel)
            
            if not enabled:
                logger.debug(f"WhatsApp disabled for {channel} on {notification_type}")
                continue
            
            if is_personal:
                # Viewer - личные WhatsApp
                sent = await send_whatsapp_to_role_personal(db, role_id, message, notification_type)
            else:
                # Группы
                sent = await send_whatsapp_to_role(db, role_id, message, notification_type=notification_type)
            
            total_sent += sent
            logger.debug(f"WhatsApp sent to role {channel}: {sent}")
        
        logger.info(f"WhatsApp to all enabled roles for '{notification_type}': {total_sent} total")
        return total_sent
        
    except Exception as e:
        logger.error(f"Error in send_whatsapp_to_all_enabled_roles: {e}", exc_info=True)
        return 0
