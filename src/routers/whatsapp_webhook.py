"""
WhatsApp incoming message webhook.

GOWA (go-whatsapp-web-multidevice) posts incoming messages here.
We handle operator/machinist replies to downtime alerts.

Operator flow:
  - Writes "8" → matched by phone → their assigned machine → reason recorded

Machinist flow:
  - Replies (quoted reply) to an alert → machine extracted from quoted text → reason recorded

Configure in GOWA: WHATSAPP_WEBHOOK=https://<host>/webhooks/whatsapp
"""

import asyncio
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


# ─── helpers ────────────────────────────────────────────────────────────────

def _normalize_phone(raw: str) -> str:
    """'972501234567@s.whatsapp.net' → '972501234567'"""
    return re.sub(r'[^0-9]', '', raw.split('@')[0])


def _parse_payload(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Parse go-whatsapp-web-multidevice webhook payload.
    Returns dict with: sender_phone, group_jid, text, quoted_body (or None).
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
    body = inner.get('body', '').strip()

    if not sender or not body:
        return None

    return {
        'sender_phone': _normalize_phone(sender),
        'group_jid': chat_id,
        'text': body,
        'quoted_body': inner.get('quoted_body'),  # None if not a reply
    }


def _extract_machine_from_alert(text: str) -> Optional[str]:
    """Extract machine name from alert message body e.g. 'Станок SR-10 простаивает'"""
    m = re.search(r'Станок\s+(\S+)\s+простаивает', text)
    return m.group(1) if m else None


def _find_employee(phone: str, db: Session) -> Optional[Dict]:
    """Find employee by normalized phone. Returns {id, full_name, role_id} or None."""
    row = db.execute(text("""
        SELECT id, full_name, role_id
        FROM employees
        WHERE is_active = true
          AND regexp_replace(COALESCE(whatsapp_phone,''), '[^0-9]', '', 'g') = :phone
        LIMIT 1
    """), {"phone": phone}).fetchone()
    return {"id": row[0], "full_name": row[1], "role_id": row[2]} if row else None


def _find_employee_machines(employee_id: int, db: Session) -> list[str]:
    """Find all machines assigned to employee."""
    rows = db.execute(text("""
        SELECT m.name
        FROM employee_machine_assignments ema
        JOIN machines m ON m.id = ema.machine_id
        WHERE ema.employee_id = :eid
    """), {"eid": employee_id}).fetchall()
    return [r[0] for r in rows]


def _find_pending_alert(machine_name: str, db: Session) -> Optional[int]:
    """Find most recent unresolved downtime log for machine (within 2 hours)."""
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


def _record_reason(
    log_id: int,
    reason_code: int,
    reporter_phone: str,
    reporter_name: str,
    reporter_role: str,
    db: Session,
) -> None:
    db.execute(text("""
        UPDATE machine_downtime_logs
        SET reason_code         = :code,
            reason_reported_at  = NOW(),
            reporter_phone      = :phone,
            reporter_name       = :name,
            reporter_role       = :role
        WHERE id = :id
    """), {
        "code": reason_code,
        "phone": reporter_phone,
        "name": reporter_name,
        "role": reporter_role,
        "id": log_id,
    })
    db.commit()


# ─── LLM follow-up ──────────────────────────────────────────────────────────

async def _schedule_followup(
    machine_name: str,
    log_id: int,
    reason_code: int,
    reason_name: str,
    operator_name: Optional[str],
    delay_minutes: int = 20,
) -> None:
    """Wait delay_minutes, then check if machine is still idle and send LLM message."""
    await asyncio.sleep(delay_minutes * 60)

    # Check if machine is still idle via MTConnect
    try:
        import httpx
        import os
        mtc_url = os.getenv('MTCONNECT_API_URL', 'https://mtconnect-core-production.up.railway.app')
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{mtc_url}/api/machines")
            resp.raise_for_status()
            data = resp.json()
            machines = []
            machines.extend(data.get('machines', {}).get('mtconnect', []))
            machines.extend(data.get('machines', {}).get('adam', []))

        still_idle = False
        idle_minutes = 0
        for m in machines:
            if m.get('name') == machine_name:
                if m.get('data', {}).get('uiMode') == 'idle':
                    still_idle = True
                    idle_minutes = m.get('data', {}).get('idleTimeMinutes', 0) or 0
                break

        if not still_idle:
            logger.debug(f"[Followup] {machine_name} вернулся в работу — followup не нужен")
            return

    except Exception as e:
        logger.warning(f"[Followup] Не удалось проверить статус {machine_name}: {e}")
        return

    # Generate contextual LLM message
    message = await _generate_followup_message(
        machine_name, reason_code, reason_name, operator_name, int(idle_minutes)
    )

    if WHATSAPP_GROUP_AI_MANAGER and message:
        await send_whatsapp_to_group(WHATSAPP_GROUP_AI_MANAGER, message)
        logger.info(f"[Followup] Отправлен followup для {machine_name}: {message!r}")


async def _generate_followup_message(
    machine_name: str,
    reason_code: int,
    reason_name: str,
    operator_name: Optional[str],
    idle_minutes: int,
) -> Optional[str]:
    """Ask Claude to generate a contextual follow-up message."""
    import os
    import httpx

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return f"⏰ *{machine_name}* простаивает уже {idle_minutes} мин. Причина была: {reason_code} — {reason_name}. Когда ожидается возобновление работы?"

    operator_info = f"Оператор: {operator_name}." if operator_name else ""

    prompt = f"""Ты — ИИ-менеджер производственной смены на заводе Isramat.

Станок {machine_name} всё ещё простаивает уже {idle_minutes} минут.
Ранее была зафиксирована причина: {reason_code} — {reason_name}.
{operator_info}

Напиши короткое (1-2 предложения) сообщение в WhatsApp-группу операторов.
Спроси о статусе устранения проблемы — естественно, по-русски, без лишних формальностей.
Используй WhatsApp форматирование (*жирный*). Начни с эмодзи ⏰ и имени станка."""

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 150,
                    "messages": [{"role": "user", "content": prompt}],
                }
            )
            resp.raise_for_status()
            return resp.json()["content"][0]["text"].strip()
    except Exception as e:
        logger.warning(f"[Followup] LLM ошибка: {e}")
        return f"⏰ *{machine_name}* простаивает уже {idle_minutes} мин. (причина: {reason_name}). Когда ожидается возобновление?"


# ─── webhook endpoint ────────────────────────────────────────────────────────

@router.post("/whatsapp")
async def whatsapp_incoming(
    request: Request,
    db: Session = Depends(get_db_session),
):
    """Incoming webhook from GOWA — process operator/machinist replies to downtime alerts."""
    try:
        payload = await request.json()
    except Exception:
        return {"ok": False, "detail": "invalid json"}

    logger.debug(f"[WhatsAppWebhook] payload: {payload}")

    parsed = _parse_payload(payload)
    if not parsed:
        return {"ok": True, "detail": "ignored"}

    sender_phone = parsed['sender_phone']
    group_jid = parsed['group_jid']
    text_msg = parsed['text']
    quoted_body = parsed['quoted_body']

    # Only process messages from our monitoring group
    if WHATSAPP_GROUP_AI_MANAGER:
        expected_id = WHATSAPP_GROUP_AI_MANAGER.split('@')[0]
        actual_id = group_jid.split('@')[0]
        if expected_id and actual_id and expected_id != actual_id:
            return {"ok": True, "detail": "not our group"}

    # Must be a 1-3 digit reason code
    if not re.fullmatch(r'\d{1,3}', text_msg):
        return {"ok": True, "detail": "not a code"}

    reason_code = int(text_msg)
    reason_name = _get_reason_name(reason_code, db)
    if reason_name is None:
        logger.info(f"[WhatsAppWebhook] Unknown code {reason_code} from {sender_phone}")
        if WHATSAPP_GROUP_AI_MANAGER:
            await send_whatsapp_to_group(
                WHATSAPP_GROUP_AI_MANAGER,
                f"❌ Код *{reason_code}* не найден в справочнике. Проверьте список."
            )
        return {"ok": False, "detail": "unknown code"}

    # Find the employee
    employee = _find_employee(sender_phone, db)
    if not employee:
        logger.info(f"[WhatsAppWebhook] Employee not found for phone {sender_phone}")
        return {"ok": True, "detail": "employee not found"}

    role_id = employee["role_id"]
    reporter_name = employee["full_name"]
    reporter_role = "machinist" if role_id == 2 else "operator"

    # ── Machinist: must use reply to identify machine ──
    if role_id == 2:
        if not quoted_body:
            if WHATSAPP_GROUP_AI_MANAGER:
                await send_whatsapp_to_group(
                    WHATSAPP_GROUP_AI_MANAGER,
                    f"❌ {reporter_name}, используй *ответ на сообщение* (reply) чтобы указать причину для нужного станка."
                )
            return {"ok": False, "detail": "machinist must use reply"}

        machine_name = _extract_machine_from_alert(quoted_body)
        if not machine_name:
            logger.info(f"[WhatsAppWebhook] Could not extract machine from quoted: {quoted_body!r}")
            return {"ok": False, "detail": "machine not found in quoted message"}

        log_id = _find_pending_alert(machine_name, db)
        if not log_id:
            return {"ok": True, "detail": "no pending alert for machine"}

    # ── Operator: matched by phone → assigned machines ──
    else:
        machines = _find_employee_machines(employee["id"], db)
        if not machines:
            logger.info(f"[WhatsAppWebhook] No machine assignment for {reporter_name}")
            return {"ok": True, "detail": "no machine assignment"}

        log_id = None
        machine_name = None
        for m in machines:
            log_id = _find_pending_alert(m, db)
            if log_id:
                machine_name = m
                break

        if not log_id:
            return {"ok": True, "detail": "no pending alert"}

    # Record the reason
    _record_reason(log_id, reason_code, sender_phone, reporter_name, reporter_role, db)

    logger.info(
        f"[WhatsAppWebhook] {reporter_role} {reporter_name} записал причину "
        f"{reason_code} '{reason_name}' для {machine_name} (log_id={log_id})"
    )

    # Confirmation to group
    if WHATSAPP_GROUP_AI_MANAGER:
        await send_whatsapp_to_group(
            WHATSAPP_GROUP_AI_MANAGER,
            f"✅ *{machine_name}* — причина зафиксирована ({reporter_name}):\n"
            f"*{reason_code}* — {reason_name}"
        )

    # Get operator name for follow-up context
    operator_name_for_followup = None
    try:
        row = db.execute(text(
            "SELECT operator_name FROM machine_downtime_logs WHERE id = :id"
        ), {"id": log_id}).fetchone()
        operator_name_for_followup = row[0] if row else None
    except Exception:
        pass

    # Schedule LLM follow-up check in 20 minutes
    asyncio.create_task(_schedule_followup(
        machine_name, log_id, reason_code, reason_name,
        operator_name_for_followup, delay_minutes=20
    ))

    return {"ok": True, "machine": machine_name, "reason_code": reason_code, "reporter": reporter_name}
