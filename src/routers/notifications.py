"""
API для отправки уведомлений в WhatsApp (через MLS)
"""
import logging
from typing import List, Optional, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.database import get_db_session
from src.services.whatsapp_client import (
    send_whatsapp_to_role,
    send_whatsapp_to_all_enabled_roles,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/notifications", tags=["Notifications"])


ROLE_NAME_TO_ID = {
    "operator": 1,
    "machinist": 2,
    "admin": 3,
    "qa": 5,
    "viewer": 7,
}


class WhatsAppNotifyRequest(BaseModel):
    notification_type: str
    message: str
    roles: Optional[List[str]] = None


@router.post("/whatsapp")
async def notify_whatsapp(
    request: WhatsAppNotifyRequest,
    db: Session = Depends(get_db_session)
):
    """
    Отправка WhatsApp уведомления с учетом настроек и языка.
    - Если roles не указаны: шлем всем ролям, у кого включено в admin/notifications.
    - Если roles указаны: шлем только этим ролям (с учетом enabled_*).
    """
    if not request.notification_type:
        raise HTTPException(status_code=400, detail="notification_type is required")
    if not request.message:
        raise HTTPException(status_code=400, detail="message is required")

    try:
        if not request.roles:
            sent_total = await send_whatsapp_to_all_enabled_roles(
                db, request.message, request.notification_type
            )
            return {"ok": True, "sent_total": sent_total, "details": None}

        details: Dict[str, int] = {}
        sent_total = 0

        for role in request.roles:
            role_id = ROLE_NAME_TO_ID.get(role)
            if not role_id:
                details[role] = 0
                continue
            sent = await send_whatsapp_to_role(
                db, role_id, request.message, notification_type=request.notification_type
            )
            details[role] = sent
            sent_total += sent

        return {"ok": True, "sent_total": sent_total, "details": details}

    except Exception as e:
        logger.error(f"WhatsApp notify error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
