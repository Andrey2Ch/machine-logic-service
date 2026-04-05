"""
WhatsApp incoming message webhook.

GOWA posts incoming messages here.
Handles operator/machinist replies to downtime alerts and follow-ups.

Operator:   writes "8"           → matched by phone → machine → reason recorded
Machinist:  reply on alert + "8" → machine from quoted text → reason recorded

Follow-up responses (after LLM follow-up message):
  "1" → yes, fixed  → monitoring continues
  "0" → no, not yet → another follow-up in 7 min
  code → new reason → recorded, follow-up in 10 min
"""

import asyncio
import logging
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.database import get_db_session
from src.services.whatsapp_client import (
    WHATSAPP_GROUP_AI_MANAGER,
    WHATSAPP_GROUP_OPERATORS_A,
    WHATSAPP_GROUP_OPERATORS_B,
    send_whatsapp_to_group,
)

# All group JIDs we accept messages from (operator groups + AI manager)
_ALLOWED_GROUPS = set()
for _jid in (WHATSAPP_GROUP_OPERATORS_A, WHATSAPP_GROUP_OPERATORS_B, WHATSAPP_GROUP_AI_MANAGER):
    if _jid:
        _ALLOWED_GROUPS.add(_jid.split('@')[0])

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

# machine_name → True if we're waiting for a 0/1 follow-up response
_awaiting_followup: Dict[str, bool] = {}


# ─── helpers ────────────────────────────────────────────────────────────────

def _normalize_phone(raw: str) -> str:
    return re.sub(r'[^0-9]', '', raw.split('@')[0])


def _parse_payload(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
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
        'quoted_body': inner.get('quoted_body'),
    }


def _extract_machine_from_alert(text: str) -> Optional[str]:
    # Matches both operator alert "Станок SR-26 простаивает" and escalation "*SR-26* простаивает"
    m = re.search(r'Станок\s+(\S+)\s+простаивает', text)
    if m:
        return m.group(1)
    m = re.search(r'\*(\S+?)\*\s+простаивает', text)
    return m.group(1) if m else None


def _find_employee(phone: str, db: Session) -> Optional[Dict]:
    row = db.execute(text("""
        SELECT id, full_name, role_id
        FROM employees
        WHERE is_active = true
          AND regexp_replace(COALESCE(whatsapp_phone,''), '[^0-9]', '', 'g') = :phone
        LIMIT 1
    """), {"phone": phone}).fetchone()
    return {"id": row[0], "full_name": row[1], "role_id": row[2]} if row else None


def _find_employee_machines(employee_id: int, db: Session) -> list[str]:
    rows = db.execute(text("""
        SELECT m.name
        FROM employee_machine_assignments ema
        JOIN machines m ON m.id = ema.machine_id
        WHERE ema.employee_id = :eid
    """), {"eid": employee_id}).fetchall()
    return [r[0] for r in rows]


def _find_pending_alert(machine_name: str, db: Session) -> Optional[int]:
    row = db.execute(text("""
        SELECT id FROM machine_downtime_logs
        WHERE machine_name = :machine_name
          AND reason_code IS NULL
          AND alert_sent_at > NOW() - INTERVAL '4 hours'
        ORDER BY alert_sent_at DESC
        LIMIT 1
    """), {"machine_name": machine_name}).fetchone()
    return row[0] if row else None


def _get_reason_info(code: int, db: Session) -> Optional[Dict]:
    """Returns {name, is_long_term, category} for active codes, or None if not found."""
    row = db.execute(
        text("SELECT name_ru, is_long_term, category FROM stoppage_reasons WHERE code = :code AND is_active = true"),
        {"code": code}
    ).fetchone()
    return {"name": row[0], "is_long_term": row[1], "category": row[2]} if row else None


def _get_reason_name(code: int, db: Session) -> Optional[str]:
    """Only returns name for active codes (inactive = reserved or duplicate)."""
    info = _get_reason_info(code, db)
    return info["name"] if info else None


def _record_reason(log_id: int, reason_code: int, reporter_phone: str,
                   reporter_name: str, reporter_role: str, db: Session) -> None:
    db.execute(text("""
        UPDATE machine_downtime_logs
        SET reason_code        = :code,
            reason_reported_at = NOW(),
            reporter_phone     = :phone,
            reporter_name      = :name,
            reporter_role      = :role
        WHERE id = :id
    """), {"code": reason_code, "phone": reporter_phone,
           "name": reporter_name, "role": reporter_role, "id": log_id})
    db.commit()


def _get_log_context(log_id: int, db: Session) -> Dict:
    row = db.execute(text("""
        SELECT operator_name, machinist_name, reason_code, machine_name,
               EXTRACT(EPOCH FROM (NOW() - alert_sent_at))/60 AS idle_min
        FROM machine_downtime_logs WHERE id = :id
    """), {"id": log_id}).fetchone()
    if not row:
        return {}
    return {
        "operator_name": row[0],
        "machinist_name": row[1],
        "reason_code": row[2],
        "machine_name": row[3],
        "idle_minutes": int(row[4] or 0),
    }


# ─── LLM ────────────────────────────────────────────────────────────────────

async def _generate_followup_message(
    machine_name: str, reason_code: int, reason_name: str,
    operator_name: Optional[str], idle_minutes: int,
) -> str:
    import os, httpx
    api_key = os.getenv("ANTHROPIC_API_KEY")
    fallback = (
        f"⏰ *{machine_name}* простаивает уже {idle_minutes} мин.\n"
        f"Причина: *{reason_code}* — {reason_name}\n"
        f"Устранили? *1* — да | *0* — нет | *код* — новая причина"
    )
    if not api_key:
        return fallback

    operator_info = f"Оператор: {operator_name}." if operator_name else ""
    prompt = (
        f"Ты — ИИ-менеджер производственной смены на заводе Isramat.\n"
        f"Станок {machine_name} всё ещё простаивает {idle_minutes} мин.\n"
        f"Ранее зафиксирована причина: {reason_code} — {reason_name}. {operator_info}\n\n"
        f"Напиши короткое (1 предложение) сообщение в WhatsApp-группу — спроси устранили ли проблему.\n"
        f"По-русски, без формальностей. Используй *жирный* для имени станка.\n"
        f"В конце добавь новую строку: *1* — да, починили | *0* — ещё нет | *код* — новая причина"
    )
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
                    "max_tokens": 200,
                    "messages": [{"role": "user", "content": prompt}],
                }
            )
            resp.raise_for_status()
            return resp.json()["content"][0]["text"].strip()
    except Exception as e:
        logger.warning(f"[Followup] LLM ошибка: {e}")
        return fallback


async def _check_machine_idle(machine_name: str) -> Optional[int]:
    """Returns idle_minutes if still idle, None if working."""
    import os, httpx
    try:
        mtc_url = os.getenv('MTCONNECT_API_URL', 'https://mtconnect-core-production.up.railway.app')
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{mtc_url}/api/machines")
            resp.raise_for_status()
            data = resp.json()
            machines = data.get('machines', {}).get('mtconnect', []) + \
                       data.get('machines', {}).get('adam', [])
        for m in machines:
            if m.get('name') == machine_name:
                if m.get('data', {}).get('uiMode') == 'idle':
                    return int(m.get('data', {}).get('idleTimeMinutes', 0) or 0)
                return None
    except Exception as e:
        logger.warning(f"[Followup] MTConnect error: {e}")
    return None


async def _send_followup(
    machine_name: str, log_id: int,
    reason_code: int, reason_name: str,
    operator_name: Optional[str], delay_minutes: int,
) -> None:
    """Wait, check machine, send follow-up if still idle."""
    await asyncio.sleep(delay_minutes * 60)

    idle_minutes = await _check_machine_idle(machine_name)
    if idle_minutes is None:
        _awaiting_followup.pop(machine_name, None)
        logger.info(f"[Followup] {machine_name} вернулся в работу")
        return

    message = await _generate_followup_message(
        machine_name, reason_code, reason_name, operator_name, idle_minutes
    )

    # Send follow-up to operator group of current shift
    from src.services.downtime_supervisor import _active_shift, _get_operator_group_for_shift
    group = _get_operator_group_for_shift(_active_shift()) or WHATSAPP_GROUP_AI_MANAGER
    if group:
        await send_whatsapp_to_group(group, message)

    _awaiting_followup[machine_name] = True
    logger.info(f"[Followup] Отправлен followup для {machine_name}")


# ─── webhook endpoint ────────────────────────────────────────────────────────

@router.post("/whatsapp")
async def whatsapp_incoming(
    request: Request,
    db: Session = Depends(get_db_session),
):
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

    # Accept from operator groups, AI manager group, or personal messages (no group_jid)
    is_group = '@g.us' in group_jid
    if is_group:
        actual_id = group_jid.split('@')[0]
        if _ALLOWED_GROUPS and actual_id not in _ALLOWED_GROUPS:
            return {"ok": True, "detail": "not our group"}

    # Must be 1-3 digits
    if not re.fullmatch(r'\d{1,3}', text_msg):
        return {"ok": True, "detail": "not a code"}

    code = int(text_msg)

    # Find employee
    employee = _find_employee(sender_phone, db)
    if not employee:
        return {"ok": True, "detail": "employee not found"}

    role_id = employee["role_id"]
    reporter_name = employee["full_name"]
    reporter_role = "machinist" if role_id == 2 else "operator"

    # Determine reply destination: same group or AI manager fallback
    reply_jid = group_jid if is_group else WHATSAPP_GROUP_AI_MANAGER

    # Resolve machine name
    if role_id == 2:
        # Machinist must reply to alert (works from group or personal message)
        if not quoted_body:
            if reply_jid:
                await send_whatsapp_to_group(
                    reply_jid,
                    f"❌ {reporter_name}, используй *ответ на сообщение* (reply) чтобы указать станок."
                )
            return {"ok": False, "detail": "machinist must use reply"}
        machine_name = _extract_machine_from_alert(quoted_body)
        if not machine_name:
            return {"ok": False, "detail": "machine not found in quoted message"}
    else:
        machines = _find_employee_machines(employee["id"], db)
        if not machines:
            return {"ok": True, "detail": "no machine assignment"}
        machine_name = machines[0]  # operator assigned to one machine primarily

    # ── Follow-up response: 0 or 1 ──
    if _awaiting_followup.get(machine_name) and code in (0, 1):
        _awaiting_followup.pop(machine_name, None)

        if code == 1:
            # Fixed — just acknowledge
            if reply_jid:
                await send_whatsapp_to_group(
                    reply_jid,
                    f"👍 *{machine_name}* — отлично, следим за возобновлением работы."
                )
            return {"ok": True, "machine": machine_name, "followup": "resolved"}

        else:
            # Not fixed — follow-up again in 7 min
            # Find last log for context
            row = db.execute(text("""
                SELECT id, reason_code, operator_name,
                       EXTRACT(EPOCH FROM (NOW() - alert_sent_at))/60
                FROM machine_downtime_logs
                WHERE machine_name = :m
                ORDER BY alert_sent_at DESC LIMIT 1
            """), {"m": machine_name}).fetchone()

            if reply_jid:
                await send_whatsapp_to_group(
                    reply_jid,
                    f"🔄 *{machine_name}* — понял, проверим снова через 7 мин."
                )

            if row:
                rc = row[1]
                rn = _get_reason_name(rc, db) or ""
                asyncio.create_task(_send_followup(
                    machine_name, row[0], rc, rn, row[2], delay_minutes=7
                ))
            return {"ok": True, "machine": machine_name, "followup": "pending"}

    # ── Regular reason code ──
    reason_info = _get_reason_info(code, db)
    if reason_info is None:
        if reply_jid:
            await send_whatsapp_to_group(
                reply_jid,
                f"❌ Код *{code}* не найден в справочнике. Проверьте список."
            )
        return {"ok": False, "detail": "unknown code"}

    reason_name = reason_info["name"]
    is_long_term = reason_info["is_long_term"]
    reason_category = reason_info["category"]

    # Find pending alert
    log_id = _find_pending_alert(machine_name, db)
    if not log_id:
        return {"ok": True, "detail": "no pending alert"}

    _record_reason(log_id, code, sender_phone, reporter_name, reporter_role, db)

    logger.info(
        f"[WhatsAppWebhook] {reporter_role} {reporter_name} → "
        f"{machine_name}: код {code} '{reason_name}' long_term={is_long_term} (log_id={log_id})"
    )

    # Confirmation
    if reply_jid:
        await send_whatsapp_to_group(
            reply_jid,
            f"✅ *{machine_name}* — причина зафиксирована ({reporter_name}):\n"
            f"*{code}* — {reason_name}"
        )

    # Notify warehouse group for material/work reasons (if group is configured)
    if reason_category == "work_and_material":
        from src.services.whatsapp_client import WHATSAPP_GROUP_WAREHOUSE
        if WHATSAPP_GROUP_WAREHOUSE:
            ctx_w = _get_log_context(log_id, db)
            asyncio.create_task(send_whatsapp_to_group(
                WHATSAPP_GROUP_WAREHOUSE,
                f"📦 *{machine_name}* простаивает — материальная причина:\n"
                f"*{code}* — {reason_name}\n"
                f"Оператор: {ctx_w.get('operator_name') or '—'}"
            ))

    # Skip follow-up for long-term reasons
    if is_long_term:
        logger.info(f"[WhatsAppWebhook] {machine_name}: причина долгосрочная — follow-up не планируется")
        return {"ok": True, "machine": machine_name, "reason_code": code, "reporter": reporter_name}

    # Schedule LLM follow-up in 10 min
    ctx = _get_log_context(log_id, db)
    asyncio.create_task(_send_followup(
        machine_name, log_id, code, reason_name,
        ctx.get("operator_name"), delay_minutes=10
    ))

    return {"ok": True, "machine": machine_name, "reason_code": code, "reporter": reporter_name}
