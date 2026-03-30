"""
WhatsApp incoming message webhook.

GOWA (go-whatsapp-web-multidevice / wuzapi) posts incoming messages here.
We handle operator replies to downtime alerts: the operator sends a reason
code number (e.g. "3", "72") and we record it in machine_downtime_logs.

Configure in GOWA: set webhook URL to  https://<host>/webhooks/whatsapp
"""

import logging
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.database import get_db_session
from src.services.whatsapp_client import WHATSAPP_GROUP_AI_MANAGER, send_whatsapp_to_group

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


def _normalize_phone(raw: str) -> str:
    """
    Привести номер телефона к единому виду: только цифры, без '+'.
    '972501234567@s.whatsapp.net' → '972501234567'
    '+972-50-123-4567'            → '972501234567'
    '0501234567'                  → '0501234567'   (local IL format kept)
    """
    phone = re.sub(r'[^0-9]', '', raw.split('@')[0])
    return phone


def _parse_message_payload(payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """
    Извлечь из вебхука go-whatsapp-web-multidevice: sender_phone, group_jid, text.

    Формат (из документации):
    {
      "event": "message",
      "device_id": "...",
      "payload": {
        "chat_id": "120363424418675165@g.us",
        "from": "972501234567@s.whatsapp.net",
        "is_from_me": false,
        "body": "3"
      }
    }
    """
    if payload.get('event') != 'message':
        return None

    inner = payload.get('payload', {})
    if not isinstance(inner, dict):
        return None

    if inner.get('is_from_me', False):
        return None

    sender = inner.get('from', '')
    chat_id = inner.get('chat_id', '')
    body = inner.get('body', '')

    if not sender or not body:
        return None

    return {
        'sender_phone': _normalize_phone(sender),
        'group_jid': chat_id,
        'text': str(body).strip(),
    }


def _find_employee_machines(phone: str, db: Session) -> list[str]:
    """
    По номеру телефона найти все станки, за которыми закреплён этот сотрудник.
    Сравниваем нормализованные номера (только цифры).
    """
    rows = db.execute(text("""
        SELECT m.name
        FROM employees e
        JOIN employee_machine_assignments ema ON ema.employee_id = e.id
        JOIN machines m ON m.id = ema.machine_id
        WHERE e.is_active = true
          AND regexp_replace(e.whatsapp_phone, '[^0-9]', '', 'g') = :phone
    """), {"phone": phone}).fetchall()
    return [r[0] for r in rows]


def _find_pending_alert(machine_name: str, db: Session) -> Optional[int]:
    """
    Найти последний лог простоя для станка, в котором ещё нет кода причины.
    Ищем за последние 2 часа.
    """
    row = db.execute(text("""
        SELECT id FROM machine_downtime_logs
        WHERE machine_name = :machine_name
          AND reason_code IS NULL
          AND alert_sent_at > NOW() - INTERVAL '2 hours'
        ORDER BY alert_sent_at DESC
        LIMIT 1
    """), {"machine_name": machine_name}).fetchone()
    return row[0] if row else None


def _get_reason_name(code: int, db: Session) -> Optional[str]:
    row = db.execute(
        text("SELECT name_ru FROM stoppage_reasons WHERE code = :code"),
        {"code": code}
    ).fetchone()
    return row[0] if row else None


@router.post("/whatsapp")
async def whatsapp_incoming(
    request: Request,
    db: Session = Depends(get_db_session),
):
    """
    Входящий вебхук от GOWA — обработка ответов операторов на алерты простоя.
    """
    try:
        payload = await request.json()
    except Exception:
        return {"ok": False, "detail": "invalid json"}

    logger.debug(f"[WhatsAppWebhook] payload: {payload}")

    parsed = _parse_message_payload(payload)
    if not parsed:
        return {"ok": True, "detail": "ignored"}

    sender_phone = parsed['sender_phone']
    group_jid = parsed['group_jid']
    text_msg = parsed['text']

    # Проверяем что сообщение из нашей группы мониторинга
    if WHATSAPP_GROUP_AI_MANAGER:
        expected_id = WHATSAPP_GROUP_AI_MANAGER.split('@')[0]
        actual_id = group_jid.split('@')[0]
        if expected_id and actual_id and expected_id != actual_id:
            logger.debug(
                f"[WhatsAppWebhook] Ignored message from group {group_jid} "
                f"(expected {WHATSAPP_GROUP_AI_MANAGER})"
            )
            return {"ok": True, "detail": "not our group"}

    # Проверяем что сообщение — это число (код причины)
    if not re.fullmatch(r'\d{1,3}', text_msg):
        logger.debug(f"[WhatsAppWebhook] Ignored non-code message: {text_msg!r}")
        return {"ok": True, "detail": "not a code"}

    reason_code = int(text_msg)

    # Проверяем что такой код существует
    reason_name = _get_reason_name(reason_code, db)
    if reason_name is None:
        logger.info(f"[WhatsAppWebhook] Unknown code {reason_code} from {sender_phone}")
        if WHATSAPP_GROUP_AI_MANAGER:
            await send_whatsapp_to_group(
                WHATSAPP_GROUP_AI_MANAGER,
                f"❌ Код *{reason_code}* не найден в справочнике. Проверьте список."
            )
        return {"ok": False, "detail": "unknown code"}

    # Находим станки оператора
    machines = _find_employee_machines(sender_phone, db)
    if not machines:
        logger.info(f"[WhatsAppWebhook] No machine assignment for phone {sender_phone}")
        return {"ok": True, "detail": "no machine assignment"}

    # Ищем незакрытый алерт для каждого из станков оператора
    log_id = None
    matched_machine = None
    for machine in machines:
        log_id = _find_pending_alert(machine, db)
        if log_id:
            matched_machine = machine
            break

    if not log_id:
        logger.info(
            f"[WhatsAppWebhook] No pending alert for machines {machines} "
            f"(phone={sender_phone})"
        )
        return {"ok": True, "detail": "no pending alert"}

    # Записываем код причины
    db.execute(text("""
        UPDATE machine_downtime_logs
        SET reason_code = :code,
            reason_reported_at = NOW(),
            reporter_phone = :phone
        WHERE id = :id
    """), {"code": reason_code, "phone": sender_phone, "id": log_id})
    db.commit()

    logger.info(
        f"[WhatsAppWebhook] Recorded reason {reason_code} '{reason_name}' "
        f"for {matched_machine} (log_id={log_id}, phone={sender_phone})"
    )

    # Подтверждение в группу
    if WHATSAPP_GROUP_AI_MANAGER:
        await send_whatsapp_to_group(
            WHATSAPP_GROUP_AI_MANAGER,
            f"✅ *{matched_machine}* — причина зафиксирована:\n"
            f"*{reason_code}* — {reason_name}"
        )

    return {"ok": True, "machine": matched_machine, "reason_code": reason_code}
