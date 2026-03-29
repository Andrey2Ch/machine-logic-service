"""
Downtime Supervisor — проактивный мониторинг простоев станков.

Периодически опрашивает MTConnect API, находит станки с uiMode='idle'
дольше порога и отправляет уведомление в WhatsApp операторам.

Env vars:
    DOWNTIME_SUPERVISOR_DRY_RUN  — '1' (default) = только логи, '0' = реальная отправка
    DOWNTIME_SUPERVISOR_THRESHOLD_MIN — порог простоя в минутах (default: 10)
    DOWNTIME_SUPERVISOR_COOLDOWN_MIN  — минимум между повторными алертами (default: 30)
    DOWNTIME_SUPERVISOR_INTERVAL_SEC  — интервал проверки в секундах (default: 120)
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Optional

import httpx

logger = logging.getLogger(__name__)

MTCONNECT_API_URL = os.getenv('MTCONNECT_API_URL', 'https://mtconnect-core-production.up.railway.app')
DRY_RUN = os.getenv('DOWNTIME_SUPERVISOR_DRY_RUN', '1').lower() not in ('0', 'false', 'no')
IDLE_THRESHOLD_MIN = float(os.getenv('DOWNTIME_SUPERVISOR_THRESHOLD_MIN', '10'))
ALERT_COOLDOWN_MIN = float(os.getenv('DOWNTIME_SUPERVISOR_COOLDOWN_MIN', '30'))
CHECK_INTERVAL_SEC = float(os.getenv('DOWNTIME_SUPERVISOR_INTERVAL_SEC', '120'))

# machine_name -> время последнего отправленного алерта
_last_alert_sent: Dict[str, datetime] = {}


async def _fetch_machines() -> list:
    """Получить все станки из MTConnect API."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{MTCONNECT_API_URL}/api/machines")
            response.raise_for_status()
            data = response.json()
            machines = []
            machines.extend(data.get('machines', {}).get('mtconnect', []))
            machines.extend(data.get('machines', {}).get('adam', []))
            return machines
    except Exception as e:
        logger.error(f"[DowntimeSupervisor] Failed to fetch machines: {e}")
        return []


def _should_alert(machine_name: str, idle_minutes: float) -> bool:
    """Проверить нужно ли отправлять алерт для этого станка."""
    if idle_minutes < IDLE_THRESHOLD_MIN:
        return False

    last_sent = _last_alert_sent.get(machine_name)
    if last_sent:
        elapsed_min = (datetime.now(timezone.utc) - last_sent).total_seconds() / 60
        if elapsed_min < ALERT_COOLDOWN_MIN:
            logger.debug(
                f"[DowntimeSupervisor] {machine_name}: cooldown active "
                f"({elapsed_min:.1f}/{ALERT_COOLDOWN_MIN} min)"
            )
            return False

    return True


async def _send_idle_alert(
    machine_name: str,
    idle_minutes: float,
    operator_name: Optional[str],
    get_db_session,
) -> None:
    """Отправить уведомление о простое."""
    idle_rounded = round(idle_minutes)
    operator_info = f"\n👤 Оператор: {operator_name}" if operator_name else ""

    message = (
        f"⚠️ *Станок {machine_name} простаивает уже {idle_rounded} мин.*"
        f"{operator_info}\n"
        f"Пожалуйста, укажи причину простоя."
    )

    if DRY_RUN:
        logger.info(f"[DowntimeSupervisor] DRY RUN — алерт НЕ отправлен: {message!r}")
        return

    try:
        from src.services.whatsapp_client import send_whatsapp_to_role
        db = next(get_db_session())
        try:
            sent = await send_whatsapp_to_role(
                db, role_id=1, message=message, notification_type='downtime_idle'
            )
            logger.info(f"[DowntimeSupervisor] WhatsApp отправлен в {sent} группу(ы) для {machine_name}")
        finally:
            db.close()
    except Exception as e:
        logger.error(f"[DowntimeSupervisor] Ошибка отправки WhatsApp: {e}", exc_info=True)


async def _check_once(get_db_session) -> None:
    """Одна итерация проверки всех станков."""
    machines = await _fetch_machines()
    if not machines:
        return

    for machine in machines:
        data = machine.get('data', {})
        ui_mode = data.get('uiMode')
        idle_min = data.get('idleTimeMinutes', 0) or 0
        name = machine.get('name', 'Unknown')
        operator = data.get('operatorName')

        if ui_mode != 'idle':
            # Если станок вышел из idle — сбрасываем cooldown чтобы при следующем idle алерт пришёл сразу
            if name in _last_alert_sent and ui_mode == 'working':
                del _last_alert_sent[name]
                logger.debug(f"[DowntimeSupervisor] {name} вернулся в работу — cooldown сброшен")
            continue

        if _should_alert(name, idle_min):
            logger.info(f"[DowntimeSupervisor] IDLE ALERT: {name} = {idle_min:.1f} мин, оператор={operator}")
            await _send_idle_alert(name, idle_min, operator, get_db_session)
            _last_alert_sent[name] = datetime.now(timezone.utc)
        else:
            logger.debug(f"[DowntimeSupervisor] {name}: idle={idle_min:.1f} мин — пропущен (порог или cooldown)")


async def downtime_supervisor_task(get_db_session) -> None:
    """Фоновая задача супервизора простоев. Запускается при старте приложения."""
    mode = "DRY RUN (сообщения не отправляются)" if DRY_RUN else "LIVE"
    logger.info(
        f"[DowntimeSupervisor] Запущен [{mode}] | "
        f"порог={IDLE_THRESHOLD_MIN}мин | "
        f"cooldown={ALERT_COOLDOWN_MIN}мин | "
        f"интервал={CHECK_INTERVAL_SEC}с"
    )

    # Первая проверка — через 30 сек после старта (даём приложению подняться)
    await asyncio.sleep(30)

    while True:
        try:
            await _check_once(get_db_session)
        except Exception as e:
            logger.error(f"[DowntimeSupervisor] Ошибка в цикле проверки: {e}", exc_info=True)

        await asyncio.sleep(CHECK_INTERVAL_SEC)
