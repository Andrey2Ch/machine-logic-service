"""
Week schedule router — управление производственным расписанием по неделям.

Коды дней:
  0 — выходной
  1 — полный день (обе смены, 6:00–6:00)
  2 — короткий день (дневная смена, 6:00–12:00)
  3 — только ночная смена (18:00–6:00)
"""

import os
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.database import get_db_session

router = APIRouter(prefix="/schedule", tags=["schedule"])

SHIFT_A_DAY_WEEK = int(os.getenv("SHIFT_A_DAY_WEEK", "14"))


def _shift_for_week(week_start: date) -> str:
    """Определяет какая смена (A или B) работает днём на данной неделе."""
    # ISO week number (понедельник = 1-й день по ISO, нам достаточно порядка)
    iso_week = week_start.isocalendar()[1]
    delta = (iso_week - SHIFT_A_DAY_WEEK) % 2
    return "A" if delta == 0 else "B"


def _sunday_of_week(d: date) -> date:
    """Возвращает воскресенье недели для данной даты (израильский календарь)."""
    # weekday(): 0=пн, 6=вс
    days_since_sunday = (d.weekday() + 1) % 7
    return d - timedelta(days=days_since_sunday)


class WeekScheduleIn(BaseModel):
    week_start: date
    sun: int = 0
    mon: int = 1
    tue: int = 1
    wed: int = 1
    thu: int = 1
    fri: int = 0
    sat: int = 0
    notes: Optional[str] = None


class WeekScheduleOut(BaseModel):
    week_start: date
    sun: int
    mon: int
    tue: int
    wed: int
    thu: int
    fri: int
    sat: int
    notes: Optional[str]
    shift_a_is_day: bool  # удобно для UI


@router.get("/weeks", response_model=list[WeekScheduleOut])
def get_weeks(weeks: int = 8, db: Session = Depends(get_db_session)):
    """Вернуть расписание на N недель начиная с текущей."""
    today = date.today()
    start = _sunday_of_week(today)
    rows = db.execute(
        text("""
            SELECT week_start, sun, mon, tue, wed, thu, fri, sat, notes
            FROM week_schedule
            WHERE week_start >= :start
            ORDER BY week_start
            LIMIT :limit
        """),
        {"start": start, "limit": weeks}
    ).fetchall()

    existing = {r.week_start: r for r in rows}
    result = []
    for i in range(weeks):
        ws = start + timedelta(weeks=i)
        if ws in existing:
            r = existing[ws]
            result.append(WeekScheduleOut(
                week_start=r.week_start, sun=r.sun, mon=r.mon,
                tue=r.tue, wed=r.wed, thu=r.thu, fri=r.fri, sat=r.sat,
                notes=r.notes, shift_a_is_day=(_shift_for_week(ws) == "A")
            ))
        else:
            # Дефолтное расписание если не заполнено
            result.append(WeekScheduleOut(
                week_start=ws, sun=0, mon=1, tue=1, wed=1, thu=1, fri=0, sat=0,
                notes=None, shift_a_is_day=(_shift_for_week(ws) == "A")
            ))
    return result


@router.put("/weeks/{week_start}", response_model=WeekScheduleOut)
def upsert_week(week_start: date, body: WeekScheduleIn, db: Session = Depends(get_db_session)):
    """Создать или обновить расписание недели."""
    for val in [body.sun, body.mon, body.tue, body.wed, body.thu, body.fri, body.sat]:
        if val not in (0, 1, 2, 3):
            raise HTTPException(status_code=400, detail="Код дня должен быть 0, 1, 2 или 3")

    db.execute(text("""
        INSERT INTO week_schedule (week_start, sun, mon, tue, wed, thu, fri, sat, notes, updated_at)
        VALUES (:ws, :sun, :mon, :tue, :wed, :thu, :fri, :sat, :notes, NOW())
        ON CONFLICT (week_start) DO UPDATE SET
            sun=EXCLUDED.sun, mon=EXCLUDED.mon, tue=EXCLUDED.tue,
            wed=EXCLUDED.wed, thu=EXCLUDED.thu, fri=EXCLUDED.fri,
            sat=EXCLUDED.sat, notes=EXCLUDED.notes, updated_at=NOW()
    """), {
        "ws": week_start, "sun": body.sun, "mon": body.mon, "tue": body.tue,
        "wed": body.wed, "thu": body.thu, "fri": body.fri, "sat": body.sat,
        "notes": body.notes
    })
    db.commit()

    return WeekScheduleOut(
        week_start=week_start, sun=body.sun, mon=body.mon, tue=body.tue,
        wed=body.wed, thu=body.thu, fri=body.fri, sat=body.sat,
        notes=body.notes, shift_a_is_day=(_shift_for_week(week_start) == "A")
    )


@router.get("/current-day")
def get_current_day(db: Session = Depends(get_db_session)):
    """Вернуть код текущего дня и активную смену — для супервизора."""
    from datetime import datetime
    import pytz
    tz = pytz.timezone("Asia/Jerusalem")
    now = datetime.now(tz)
    today = now.date()
    ws = _sunday_of_week(today)

    day_cols = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
    dow = (today.weekday() + 1) % 7  # 0=вс, 1=пн ... 6=сб
    col = day_cols[dow]

    row = db.execute(
        text(f"SELECT {col} as code FROM week_schedule WHERE week_start = :ws"),
        {"ws": ws}
    ).fetchone()

    code = row.code if row else (1 if dow in (1, 2, 3, 4) else 0)
    shift_a_is_day = _shift_for_week(ws) == "A"
    hour = now.hour

    # Определяем активную смену
    active_shift = None
    if code == 1:
        active_shift = "A" if (6 <= hour < 18) == shift_a_is_day else "B"
        if not (6 <= hour < 18) and not (18 <= hour or hour < 6):
            active_shift = None
    elif code == 2:
        active_shift = ("A" if shift_a_is_day else "B") if 6 <= hour < 12 else None
    elif code == 3:
        active_shift = ("B" if shift_a_is_day else "A") if (hour >= 18 or hour < 6) else None

    return {
        "date": today.isoformat(),
        "day_code": code,
        "shift_a_is_day": shift_a_is_day,
        "active_shift": active_shift,
        "hour": hour,
    }
