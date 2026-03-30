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
from typing import Dict, Optional, Tuple

import httpx
import pytz

_IL_TZ = pytz.timezone("Asia/Jerusalem")
SHIFT_A_DAY_WEEK = int(os.getenv("SHIFT_A_DAY_WEEK", "2"))

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


def _active_shift() -> Optional[str]:
    """Определяет активную смену прямо сейчас (A или B), или None если нерабочее время."""
    now = datetime.now(_IL_TZ)
    hour = now.hour
    iso_week = now.isocalendar()[1]
    shift_a_is_day = ((iso_week - SHIFT_A_DAY_WEEK) % 2) == 0

    # Дневная смена 6:00-18:00, ночная 18:00-6:00
    is_day_time = 6 <= hour < 18
    if is_day_time:
        return "A" if shift_a_is_day else "B"
    else:
        return "B" if shift_a_is_day else "A"


def _get_operator_for_machine(machine_name: str, shift: str, get_db_session) -> Optional[str]:
    """Найти имя оператора закреплённого за станком в данную смену."""
    try:
        from sqlalchemy import text
        db = next(get_db_session())
        try:
            row = db.execute(text("""
                SELECT e.full_name
                FROM employee_machine_assignments ema
                JOIN employees e ON e.id = ema.employee_id
                JOIN machines m ON m.id = ema.machine_id
                WHERE m.name = :machine_name
                  AND e.shift = :shift
                  AND e.is_active = true
                  AND e.role_id = 1
                LIMIT 1
            """), {"machine_name": machine_name, "shift": shift}).fetchone()
            return row[0] if row else None
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[DowntimeSupervisor] Не удалось получить оператора для {machine_name}: {e}")
        return None


def _save_downtime_log(
    machine_name: str,
    idle_minutes: float,
    operator_name: Optional[str],
    machinist_name: Optional[str],
    get_db_session,
) -> Optional[int]:
    """Сохранить запись о простое в БД. Возвращает ID записи."""
    try:
        from sqlalchemy import text
        db = next(get_db_session())
        try:
            row = db.execute(text("""
                INSERT INTO machine_downtime_logs
                    (machine_name, alert_sent_at, idle_minutes, operator_name, machinist_name)
                VALUES
                    (:machine_name, NOW(), :idle_minutes, :operator_name, :machinist_name)
                RETURNING id
            """), {
                "machine_name": machine_name,
                "idle_minutes": idle_minutes,
                "operator_name": operator_name,
                "machinist_name": machinist_name,
            }).fetchone()
            db.commit()
            return row[0] if row else None
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[DowntimeSupervisor] Не удалось сохранить лог простоя для {machine_name}: {e}")
        return None


async def _send_idle_alert(
    machine_name: str,
    idle_minutes: float,
    machinist_name: Optional[str],
    get_db_session,
) -> None:
    """Отправить уведомление о простое."""
    idle_rounded = round(idle_minutes)

    # Получаем оператора из БД по смене
    shift = _active_shift()
    operator_name = _get_operator_for_machine(machine_name, shift, get_db_session) if shift else None

    operator_info = f"\n👤 Оператор: {operator_name}" if operator_name else ""
    machinist_info = f"\n🔧 Наладчик: {machinist_name}" if machinist_name else ""

    message = (
        f"⚠️ *Станок {machine_name} простаивает уже {idle_rounded} мин.*"
        f"{operator_info}"
        f"{machinist_info}\n"
        f"Пожалуйста, укажи причину простоя."
    )

    # Сохраняем лог в БД (независимо от DRY_RUN)
    _save_downtime_log(machine_name, idle_minutes, operator_name, machinist_name, get_db_session)

    if DRY_RUN:
        print(f"[DowntimeSupervisor] DRY RUN — алерт НЕ отправлен: {message!r}")
        return

    try:
        from src.services.whatsapp_client import send_whatsapp_to_group, WHATSAPP_GROUP_AI_MANAGER
        if not WHATSAPP_GROUP_AI_MANAGER:
            logger.warning("[DowntimeSupervisor] WHATSAPP_GROUP_AI_MANAGER не задан — сообщение не отправлено")
            return
        await send_whatsapp_to_group(WHATSAPP_GROUP_AI_MANAGER, message)
        logger.info(f"[DowntimeSupervisor] WhatsApp отправлен в AI Monitor для {machine_name}")
    except Exception as e:
        logger.error(f"[DowntimeSupervisor] Ошибка отправки WhatsApp: {e}", exc_info=True)


def _has_recent_reason(machine_name: str, get_db_session) -> bool:
    """Проверить — записана ли уже причина простоя за последние 2 часа."""
    try:
        from sqlalchemy import text
        db = next(get_db_session())
        try:
            row = db.execute(text("""
                SELECT 1 FROM machine_downtime_logs
                WHERE machine_name = :name
                  AND reason_code IS NOT NULL
                  AND reason_reported_at > NOW() - INTERVAL '2 hours'
                LIMIT 1
            """), {"name": machine_name}).fetchone()
            return row is not None
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[DowntimeSupervisor] _has_recent_reason error: {e}")
        return False


async def _check_once(get_db_session) -> None:
    """Одна итерация проверки всех станков."""
    machines = await _fetch_machines()
    if not machines:
        return

    for machine in machines:
        data = machine.get('data', {})
        ui_mode = data.get('uiMode')
        setup_status = data.get('setupStatus', 'idle')
        idle_min = data.get('idleTimeMinutes', 0) or 0
        name = machine.get('name', 'Unknown')
        machinist = data.get('operatorName')

        if ui_mode != 'idle':
            if name in _last_alert_sent and ui_mode == 'working':
                del _last_alert_sent[name]
                logger.debug(f"[DowntimeSupervisor] {name} вернулся в работу — cooldown сброшен")
            continue

        # Пропускаем станки без активной наладки (серая рамка — нет работы)
        if setup_status == 'idle':
            continue

        if _should_alert(name, idle_min):
            # Не слать новый алерт если причина уже записана (follow-up система работает)
            if _has_recent_reason(name, get_db_session):
                logger.debug(f"[DowntimeSupervisor] {name}: причина уже записана — алерт пропущен")
                continue
            logger.info(f"[DowntimeSupervisor] IDLE ALERT: {name} = {idle_min:.1f} мин, наладчик={machinist}")
            await _send_idle_alert(name, idle_min, machinist, get_db_session)
            _last_alert_sent[name] = datetime.now(timezone.utc)
        else:
            logger.debug(f"[DowntimeSupervisor] {name}: idle={idle_min:.1f} мин — пропущен (порог или cooldown)")


async def downtime_supervisor_task(get_db_session) -> None:
    """Фоновая задача супервизора простоев. Запускается при старте приложения."""
    import os
    import tempfile

    # Используем файл-лок чтобы гарантировать запуск только в одном воркере
    lock_path = os.path.join(tempfile.gettempdir(), "downtime_supervisor.lock")
    try:
        # Открываем файл для эксклюзивной записи (O_CREAT | O_EXCL — атомарно)
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        print(f"[DowntimeSupervisor] PID={os.getpid()} — супервизор уже запущен в другом воркере, пропускаем")
        return

    mode = "DRY RUN (сообщения не отправляются)" if DRY_RUN else "LIVE"
    print(
        f"[DowntimeSupervisor] Запущен [{mode}] | "
        f"порог={IDLE_THRESHOLD_MIN}мин | "
        f"cooldown={ALERT_COOLDOWN_MIN}мин | "
        f"интервал={CHECK_INTERVAL_SEC}с"
    )

    # Первая проверка — через 30 сек после старта (даём приложению подняться)
    await asyncio.sleep(30)

    try:
        while True:
            try:
                await _check_once(get_db_session)
            except Exception as e:
                logger.error(f"[DowntimeSupervisor] Ошибка в цикле проверки: {e}", exc_info=True)

            await asyncio.sleep(CHECK_INTERVAL_SEC)
    finally:
        # Освобождаем лок при завершении задачи
        try:
            os.unlink(lock_path)
        except OSError:
            pass
