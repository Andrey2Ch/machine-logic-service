from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple
import re
import json
import httpx
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


async def resolve_entities_llm(question: str, db: Session, api_key: str, examples: List[Tuple[str, str]] = None) -> Dict[str, Any]:
    """LLM-based Resolver Agent: извлекает entities, intent, timeframe из вопроса.
    
    Возвращает JSON:
    {
      "intent": str,  # "list_machinists" | "count_machines_by_machinists" | "generic"
      "timeframe": str | null,  # "yesterday" | "month:2025-08"
      "employees": List[str],  # имена/username
      "machines": List[str],   # названия станков
      "parts": List[str]       # drawing_number
    }
    """
    # 1) Детерминистический парсинг: извлекаем номера деталей из вопроса НАПРЯМУЮ
    part_candidates = re.findall(r"\b\d{4,}(?:-\d+)?\b", question)
    parts_resolved: List[int] = []
    if part_candidates:
        try:
            rows = db.execute(text("""
                SELECT id FROM parts WHERE drawing_number = ANY(:dnums)
            """), {"dnums": part_candidates}).fetchall()
            parts_resolved = [r.id for r in rows]
        except Exception:
            parts_resolved = []
    
    # 2) Загружаем доступные employees/machines для LLM (их немного, можно показать)
    try:
        emp_rows = db.execute(text("SELECT COALESCE(full_name, username)::text AS name FROM employees WHERE is_active IS NULL OR is_active = TRUE")).fetchall()
        employees_list = [r.name for r in emp_rows if r.name]
        
        mach_rows = db.execute(text("SELECT name FROM machines WHERE is_active=TRUE")).fetchall()
        machines_list = [r.name for r in mach_rows if r.name]
    except Exception:
        employees_list = []
        machines_list = []
    
    # 3) Вызываем LLM для извлечения employees/machines/intent/timeframe
    system_prompt = """You are a Resolver Agent. Your task is to extract structured information from a natural language question about manufacturing data.

Extract:
- intent: "list_machinists" (кто/имена наладчиков) | "count_machines_by_machinists" (сколько станков у наладчика) | "generic" (all other)
- timeframe: "yesterday" | "month:YYYY-MM" | null
- employees: list of employee names mentioned (exact match from available list)
- machines: list of machine names mentioned (exact match from available list)

Return ONLY valid JSON in this format:
{
  "intent": "...",
  "timeframe": "..." or null,
  "employees": ["..."],
  "machines": ["..."]
}

NOTE: Parts are detected separately via regex."""
    
    available_context = f"""Available employees: {', '.join(employees_list)}
Available machines: {', '.join(machines_list)}"""
    
    user_prompt = f"""{available_context}

Question: {question}

Extract entities and intent. Return JSON only."""
    
    # 3) Вызываем Claude API
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 1000,
        "temperature": 0.0,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        
        content = "".join(part.get("text", "") for part in data.get("content", []) if part.get("type") == "text")
        
        # Извлекаем JSON из ответа
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group(0))
        else:
            result = json.loads(content)
        
        # 4) Резолвим employees/machines имена в IDs
        resolved = {
            "intent": result.get("intent", "generic"),
            "timeframe": result.get("timeframe"),
            "employees": [],
            "machines": [],
            "parts": parts_resolved  # УЖЕ извлечены детерминистически выше!
        }
        
        # Employees
        if result.get("employees"):
            emp_names = [n.lower().strip() for n in result["employees"]]
            emp_rows = db.execute(text("""
                SELECT id, COALESCE(full_name, username)::text AS name
                FROM employees
                WHERE (is_active IS NULL OR is_active = TRUE)
                  AND LOWER(COALESCE(full_name, username)::text) = ANY(:names)
            """), {"names": emp_names}).fetchall()
            resolved["employees"] = [r.id for r in emp_rows]
        
        # Machines
        if result.get("machines"):
            mach_names = [n.strip() for n in result["machines"]]
            mach_rows = db.execute(text("""
                SELECT id FROM machines
                WHERE is_active=TRUE AND name = ANY(:names)
            """), {"names": mach_names}).fetchall()
            resolved["machines"] = [r.id for r in mach_rows]
        
        return resolved
    
    except Exception as e:
        print(f"ERROR: LLM Resolver failed: {e}")
        # Fallback to deterministic resolver
        return resolve_entities(question, db)


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

    # 1) timeframe: вчера / месяц ГГГГ (ru/en)
    timeframe = None
    if "вчера" in qlow or "yesterday" in qlow:
        timeframe = "yesterday"
    else:
        # месяцы ru/en → YYYY-MM
        months = {
            "январ": "01", "feb": "02", "феврал": "02", "март": "03", "march": "03",
            "апрел": "04", "april": "04", "май": "05", "may": "05", "июн": "06", "june": "06",
            "июл": "07", "july": "07", "август": "08", "aug": "08", "сентябр": "09", "sep": "09",
            "октябр": "10", "oct": "10", "ноябр": "11", "nov": "11", "декабр": "12", "dec": "12",
        }
        m = re.search(r"(январ|феврал|март|апрел|май|июн|июл|август|сентябр|октябр|ноябр|декабр|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[^\d]{0,10}(20\d{2})",
                      qlow)
        if m:
            mon_key = m.group(1)
            year = m.group(2)
            # нормализуем английские краткие формы
            map_en = {"jan":"январ","feb":"феврал","mar":"март","apr":"апрел","jun":"июн","jul":"июл",
                      "aug":"август","sep":"сентябр","oct":"октябр","nov":"ноябр","dec":"декабр"}
            mon_key = map_en.get(mon_key, mon_key)
            for k, mm in months.items():
                if mon_key.startswith(k):
                    timeframe = f"month:{year}-{mm}"
                    break

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

    # 4) machines
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

    # 5) parts (детали): ищем по drawing_number (например, "1036-02", "45679")
    parts: List[int] = []
    try:
        # паттерн: цифры + дефис + цифры, или просто 4+ цифры подряд
        part_candidates = re.findall(r"\b\d{4,}(?:-\d+)?\b", question)
        if part_candidates:
            rows = db.execute(text(
                """
                SELECT id, drawing_number
                FROM parts
                WHERE drawing_number = ANY(:dnums)
                """
            ), {"dnums": part_candidates}).fetchall()
            parts = [r.id for r in rows]
    except Exception:
        parts = []

    return {
        "intent": intent,
        "timeframe": timeframe,
        "employees": employees,
        "machines": machines,
        "parts": parts,
    }


