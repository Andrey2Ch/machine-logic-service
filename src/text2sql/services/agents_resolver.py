from __future__ import annotations

from typing import Any, Dict, List, Set
import re
from sqlalchemy import text
from sqlalchemy.orm import Session


def _normalize_machine_name(name: str) -> str:
    s = (name or "").strip().lower()
    # убираем префикс вида m_2_
    s = re.sub(r"^m_\d+_", "", s)
    # заменяем не буквенно-цифровые на дефис
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def _normalize_person_name(name: str) -> str:
    return (name or "").strip().lower()


def resolve_entities(question: str, db: Session) -> Dict[str, Any]:
    """Резолвит сущности (employees/machines) и timeframe/intent из вопроса.

    Возвращает:
      {
        "intent": str,
        "timeframe": str | None,  # e.g., 'yesterday'
        "employees": List[int],
        "machines": List[int]
      }
    """
    qlow = (question or "").lower()

    # 1) timeframe (минимум: вчера)
    timeframe = None
    if "вчера" in qlow or "yesterday" in qlow:
        timeframe = "yesterday"

    # 2) intent (очень грубо)
    #   - кто/имена/наладчик → списки имён
    #   - сколько/скольких/скольких станках → агрегаты
    if any(w in qlow for w in ("кто", "имена", "наладчик", "оператор")):
        intent = "list_machinists"
    elif any(w in qlow for w in ("сколько", "скольки", "скольких")) and "стан" in qlow:
        intent = "count_machines_by_machinists"
    else:
        intent = "generic"

    # 3) employees: подберём id по подстроке из вопроса (простая эвристика)
    employees: List[int] = []
    try:
        rows = db.execute(text(
            """
            SELECT id, COALESCE(full_name, username)::text AS name
            FROM employees
            WHERE is_active IS NULL OR is_active = TRUE
            """
        )).fetchall()
        names: List[tuple[int, str]] = [(r.id, _normalize_person_name(r.name)) for r in rows if r.name]
        # ключевые токены из вопроса (кириллица/латиница), длина >= 3
        tokens = [t for t in re.findall(r"[\wа-яА-Я]+", qlow) if len(t) >= 3]
        cand: Set[int] = set()
        for eid, nm in names:
            for t in tokens:
                if t in nm:
                    cand.add(eid)
                    break
        employees = list(cand)[:10]
    except Exception:
        employees = []

    # 4) machines (на будущее; сейчас редко встречается в таких вопросах)
    machines: List[int] = []
    try:
        rows = db.execute(text("SELECT id, name FROM machines WHERE is_active=TRUE")).fetchall()
        canon = [(r.id, _normalize_machine_name(r.name)) for r in rows]
        tokens = [t for t in re.findall(r"[\w]+", qlow) if len(t) >= 2]
        cand: Set[int] = set()
        for mid, mname in canon:
            for t in tokens:
                if t in mname:
                    cand.add(mid)
                    break
        machines = list(cand)[:10]
    except Exception:
        machines = []

    return {
        "intent": intent,
        "timeframe": timeframe,
        "employees": employees,
        "machines": machines,
    }


