"""
Downtime Supervisor — проактивный мониторинг простоев станков.

Периодически опрашивает MTConnect API, находит станки с uiMode='idle'
дольше порога и отправляет уведомление в WhatsApp операторам.

Поток уведомлений:
  1. Алерт → группа операторов текущей смены
  2. Через 7 мин без ответа → эскалация наладчику (личное сообщение)

Env vars:
    DOWNTIME_SUPERVISOR_DRY_RUN  — '1' (default) = только логи, '0' = реальная отправка
    DOWNTIME_SUPERVISOR_THRESHOLD_MIN — порог простоя в минутах (default: 10)
    DOWNTIME_SUPERVISOR_COOLDOWN_MIN  — минимум между повторными алертами (default: 30)
    DOWNTIME_SUPERVISOR_INTERVAL_SEC  — интервал проверки в секундах (default: 120)
    DOWNTIME_ESCALATION_DELAY_MIN    — задержка эскалации наладчику (default: 7)
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

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
ESCALATION_DELAY_MIN = float(os.getenv('DOWNTIME_ESCALATION_DELAY_MIN', '7'))

# machine_name -> время последнего отправленного алерта
_last_alert_sent: Dict[str, datetime] = {}
# machine_name -> время когда супервизор впервые увидел станок idle
_first_seen_idle: Dict[str, datetime] = {}


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
    """Определяет активную смену прямо сейчас (A или B), или None если нерабочее время.

    Не проверяет расписание — только время суток и чередование смен.
    Используй _active_shift_from_schedule() для проверки с учётом week_schedule.
    """
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


def _active_shift_from_schedule(get_db_session) -> Optional[str]:
    """Определяет активную смену с учётом week_schedule.

    Возвращает 'A', 'B' или None (выходной / нерабочее время).
    """
    from datetime import date as date_cls
    from sqlalchemy import text

    now = datetime.now(_IL_TZ)
    today = now.date()
    hour = now.hour

    # Воскресенье недели (начало недели в израильском календаре)
    days_since_sunday = (today.weekday() + 1) % 7
    week_start = today - __import__('datetime').timedelta(days=days_since_sunday)

    # Колонка текущего дня: 0=вс,1=пн...6=сб
    day_cols = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
    dow = (today.weekday() + 1) % 7
    col = day_cols[dow]

    try:
        db = next(get_db_session())
        try:
            row = db.execute(
                text(f"SELECT {col} AS code FROM week_schedule WHERE week_start = :ws"),
                {"ws": week_start}
            ).fetchone()
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[DowntimeSupervisor] Не удалось прочитать расписание: {e}")
        row = None

    # Если записи нет — используем дефолт (пн-чт рабочие)
    code = row.code if row else (1 if dow in (1, 2, 3, 4) else 0)

    if code == 0:
        return None  # выходной день

    iso_week = week_start.isocalendar()[1]
    shift_a_is_day = ((iso_week - SHIFT_A_DAY_WEEK) % 2) == 0

    if code == 1:  # полный день: обе смены 6:00-6:00
        is_day = 6 <= hour < 18
        return ("A" if shift_a_is_day else "B") if is_day else ("B" if shift_a_is_day else "A")
    elif code == 2:  # короткий день: только дневная смена 6:00-12:00
        if 6 <= hour < 12:
            return "A" if shift_a_is_day else "B"
        return None
    elif code == 3:  # только ночная смена 18:00-6:00
        if hour >= 18 or hour < 6:
            return "B" if shift_a_is_day else "A"
        return None

    return None


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


def _format_duration(minutes: float) -> str:
    """Форматирует минуты в 'X ч Y мин' или 'Y мин'."""
    total = round(minutes)
    if total >= 60:
        h, m = divmod(total, 60)
        return f"{h} ч {m} мин" if m else f"{h} ч"
    return f"{total} мин"


def _is_machinist_on_duty(employee_shift: Optional[str]) -> bool:
    """Проверяет работает ли наладчик сейчас.
    shift='A'/'B' — сменный, NULL — дневной (6:00-18:00 каждый день).
    """
    current_shift = _active_shift()
    now_hour = datetime.now(_IL_TZ).hour
    is_day = 6 <= now_hour < 18

    if employee_shift is None:
        return is_day
    return employee_shift == current_shift


def _find_machinist_by_name(machinist_name: str, get_db_session) -> Optional[dict]:
    """Найти наладчика по имени. Возвращает {full_name, whatsapp_phone, shift}."""
    if not machinist_name:
        return None
    try:
        from sqlalchemy import text
        db = next(get_db_session())
        try:
            row = db.execute(text("""
                SELECT full_name, whatsapp_phone, shift
                FROM employees
                WHERE role_id = 2
                  AND is_active = true
                  AND full_name = :name
                LIMIT 1
            """), {"name": machinist_name}).fetchone()
            if row and row[1]:
                return {"full_name": row[0], "whatsapp_phone": row[1], "shift": row[2]}
            return None
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[DowntimeSupervisor] _find_machinist_by_name error: {e}")
        return None


def _find_on_duty_machinists(get_db_session) -> List[dict]:
    """Найти всех наладчиков которые сейчас на смене."""
    try:
        from sqlalchemy import text
        db = next(get_db_session())
        try:
            rows = db.execute(text("""
                SELECT full_name, whatsapp_phone, shift
                FROM employees
                WHERE role_id = 2
                  AND is_active = true
                  AND whatsapp_phone IS NOT NULL
                  AND whatsapp_phone != ''
            """)).fetchall()
            return [
                {"full_name": r[0], "whatsapp_phone": r[1], "shift": r[2]}
                for r in rows
                if _is_machinist_on_duty(r[2])
            ]
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[DowntimeSupervisor] _find_on_duty_machinists error: {e}")
        return []


def _get_operator_group_for_shift(shift: Optional[str]) -> Optional[str]:
    """Получить JID группы операторов для текущей смены."""
    from src.services.whatsapp_client import WHATSAPP_GROUP_OPERATORS_A, WHATSAPP_GROUP_OPERATORS_B
    if shift == 'A':
        return WHATSAPP_GROUP_OPERATORS_A or None
    elif shift == 'B':
        return WHATSAPP_GROUP_OPERATORS_B or None
    return None


async def _send_idle_alert(
    machine_name: str,
    idle_minutes: float,
    machinist_name: Optional[str],
    get_db_session,
    shift: Optional[str] = None,
) -> None:
    """Отправить уведомление о простое в группу операторов текущей смены."""
    duration = _format_duration(idle_minutes)

    if shift is None:
        shift = _active_shift_from_schedule(get_db_session)
    operator_name = _get_operator_for_machine(machine_name, shift, get_db_session) if shift else None

    operator_info = f"\n👤 Оператор: {operator_name}" if operator_name else ""
    machinist_info = f"\n🔧 Наладчик: {machinist_name}" if machinist_name else ""

    message = (
        f"⚠️ *Станок {machine_name} простаивает уже {duration}.*"
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
        from src.services.whatsapp_client import send_whatsapp_to_group
        group_jid = _get_operator_group_for_shift(shift)
        if not group_jid:
            logger.warning(f"[DowntimeSupervisor] Нет группы операторов для смены {shift}")
            return
        await send_whatsapp_to_group(group_jid, message)
        logger.info(f"[DowntimeSupervisor] Алерт отправлен в группу операторов (смена {shift}) для {machine_name}")
    except Exception as e:
        logger.error(f"[DowntimeSupervisor] Ошибка отправки WhatsApp: {e}", exc_info=True)

    # Запускаем эскалацию наладчику через ESCALATION_DELAY_MIN минут
    asyncio.create_task(_escalate_to_machinist(
        machine_name, idle_minutes, machinist_name, get_db_session
    ))


async def _escalate_to_machinist(
    machine_name: str,
    idle_minutes: float,
    machinist_name: Optional[str],
    get_db_session,
) -> None:
    """Через N минут проверить — ответил ли оператор. Если нет — написать наладчику лично."""
    await asyncio.sleep(ESCALATION_DELAY_MIN * 60)

    # Проверяем ответил ли уже оператор
    if _has_recent_reason(machine_name, get_db_session):
        logger.info(f"[Escalation] {machine_name}: оператор уже ответил — эскалация отменена")
        return

    duration = _format_duration(idle_minutes + ESCALATION_DELAY_MIN)
    message = (
        f"🔔 *{machine_name}* простаивает уже {duration}.\n"
        f"Оператор не указал причину.\n"
        f"Пожалуйста, разберись с ситуацией."
    )

    if DRY_RUN:
        print(f"[Escalation] DRY RUN — эскалация НЕ отправлена: {message!r}")
        return

    from src.services.whatsapp_client import send_whatsapp_personal

    # Пробуем отправить наладчику сетапа если он на смене
    setup_machinist = _find_machinist_by_name(machinist_name, get_db_session)
    if setup_machinist and _is_machinist_on_duty(setup_machinist['shift']):
        await send_whatsapp_personal(setup_machinist['whatsapp_phone'], message)
        logger.info(f"[Escalation] {machine_name}: личное сообщение наладчику сетапа {setup_machinist['full_name']}")
        return

    # Наладчик сетапа не на смене — шлём всем наладчикам на смене
    on_duty = _find_on_duty_machinists(get_db_session)
    if not on_duty:
        logger.warning(f"[Escalation] {machine_name}: нет наладчиков на смене")
        return

    for m in on_duty:
        await send_whatsapp_personal(m['whatsapp_phone'], message)

    names = ', '.join(m['full_name'] for m in on_duty)
    logger.info(f"[Escalation] {machine_name}: сообщение отправлено {len(on_duty)} наладчикам: {names}")


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
    # Проверяем расписание — не отправляем алерты в нерабочее время
    shift = _active_shift_from_schedule(get_db_session)
    if shift is None:
        logger.debug("[DowntimeSupervisor] Нерабочее время по расписанию — проверка пропущена")
        return

    machines = await _fetch_machines()
    if not machines:
        return

    for machine in machines:
        data = machine.get('data', {})
        ui_mode = data.get('uiMode')
        name = machine.get('name', 'Unknown')
        machinist = data.get('operatorName')

        if ui_mode != 'idle':
            if name in _last_alert_sent and ui_mode == 'working':
                del _last_alert_sent[name]
                logger.debug(f"[DowntimeSupervisor] {name} вернулся в работу — cooldown сброшен")
            # Сбрасываем отслеживание idle при любом не-idle статусе
            _first_seen_idle.pop(name, None)
            continue

        # Пропускаем станки с серой рамкой (нет активного производства)
        # Логика рамки дашборда: жёлтая = есть executionStatus + есть qaName
        exec_status = (data.get('executionStatus') or data.get('execution') or '').upper()
        qa_name = data.get('qaName') or data.get('qa') or ''
        if not exec_status or not qa_name:
            logger.debug(f"[DowntimeSupervisor] {name}: серая рамка (exec={exec_status!r}, qa={qa_name!r}) — пропущен")
            continue

        # Отслеживаем время с момента когда МЫ впервые увидели станок idle
        now = datetime.now(timezone.utc)
        if name not in _first_seen_idle:
            _first_seen_idle[name] = now
            logger.debug(f"[DowntimeSupervisor] {name}: первый раз idle — запоминаем время")

        our_idle_min = (now - _first_seen_idle[name]).total_seconds() / 60

        if _should_alert(name, our_idle_min):
            if _has_recent_reason(name, get_db_session):
                logger.debug(f"[DowntimeSupervisor] {name}: причина уже записана — алерт пропущен")
                continue
            logger.info(f"[DowntimeSupervisor] IDLE ALERT: {name} = {our_idle_min:.1f} мин, наладчик={machinist}")
            await _send_idle_alert(name, our_idle_min, machinist, get_db_session, shift=shift)
            _last_alert_sent[name] = now
        else:
            logger.debug(f"[DowntimeSupervisor] {name}: idle={our_idle_min:.1f} мин — пропущен (порог или cooldown)")


async def downtime_supervisor_task(get_db_session) -> None:
    """Фоновая задача супервизора простоев. Запускается при старте приложения."""
    import os
    import tempfile

    # Используем файл-лок чтобы гарантировать запуск только в одном воркере
    lock_path = os.path.join(tempfile.gettempdir(), "downtime_supervisor.lock")
    try:
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
        f"интервал={CHECK_INTERVAL_SEC}с | "
        f"эскалация={ESCALATION_DELAY_MIN}мин"
    )

    # Первая проверка — через 30 сек после старта
    await asyncio.sleep(30)

    try:
        while True:
            try:
                await _check_once(get_db_session)
            except Exception as e:
                logger.error(f"[DowntimeSupervisor] Ошибка в цикле проверки: {e}", exc_info=True)

            await asyncio.sleep(CHECK_INTERVAL_SEC)
    finally:
        try:
            os.unlink(lock_path)
        except OSError:
            pass
