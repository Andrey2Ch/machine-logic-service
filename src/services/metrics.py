from sqlalchemy.orm import Session
from sqlalchemy import func
from ..models.models import BatchDB, SetupDB, EmployeeDB


# --- Runtime SQL capture (TEXT2SQL) ---
import os
import logging
import socket
from sqlalchemy import event, text as sa_text
import src.database as db  # важно: брать engine динамически из модуля
from typing import Any
try:
    from psycopg2.extras import Json as PgJson  # адаптация JSON для psycopg2
except Exception:  # на всякий
    PgJson = None  # type: ignore

_capture_installed = False
_log = logging.getLogger("text2sql.capture")

def install_sql_capture(route_getter=None, user_getter=None, role_getter=None):
    global _capture_installed
    if _capture_installed:
        _log.info("capture: already installed")
        return
    if db.engine is None:
        _log.warning("capture: engine is None, skip")
        return
    env_flag = os.getenv('TEXT2SQL_CAPTURE', '0').lower()
    if env_flag not in {'1', 'true', 'yes'}:
        _log.info(f"capture: disabled by env TEXT2SQL_CAPTURE={env_flag}")
        return

    host = socket.gethostname()

    @event.listens_for(db.engine, "before_cursor_execute")
    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        try:
            context._t2s_start = os.times()[4]
        except Exception:
            context._t2s_start = None

    @event.listens_for(db.engine, "after_cursor_execute")
    def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        try:
            # защитимся от рекурсии: если это наша внутренняя вставка/сейвпоинт — выходим
            try:
                if getattr(conn, 'info', None) and conn.info.get('t2s_skip'):
                    return
            except Exception:
                pass

            stmt = (statement or '').strip()
            head = stmt.split(None, 1)[0].lower() if stmt else ''
            if head not in {'select','insert','update','delete','create','alter','drop','truncate','grant','revoke'}:
                return
            if head in {'set','commit','begin'}:
                return
            # пропускаем собственные операции и системные savepoint/rollback/release
            lstmt = stmt.lower()
            if 'text2sql_captured' in lstmt:
                return
            if lstmt.startswith('savepoint') or lstmt.startswith('release savepoint') or lstmt.startswith('rollback to savepoint'):
                return

            duration_ms = None
            try:
                if getattr(context, '_t2s_start', None) is not None:
                    duration_ms = int((os.times()[4] - context._t2s_start) * 1000)
            except Exception:
                pass

            import json
            params_obj: Any = None
            try:
                # оставим исходный объект параметров, если сериализуемо
                if parameters:
                    # попытка привести к dict/list
                    if isinstance(parameters, (dict, list, tuple)):
                        json.dumps(parameters)  # проверка сериализации
                        params_obj = parameters
                    else:
                        params_obj = None
                else:
                    params_obj = None
            except Exception:
                params_obj = None

            route = route_getter() if route_getter else None
            user_id = user_getter() if user_getter else None
            role = role_getter() if role_getter else None

            if len(stmt) > 4000:
                stmt = stmt[:4000]

            # Пишем в рамках текущего соединения через SAVEPOINT (без нового коннекта)
            payload = {
                'sql': stmt,
                'params_json': PgJson(params_obj) if (PgJson and params_obj is not None) else None,
                'duration_ms': duration_ms,
                'rows': cursor.rowcount if hasattr(cursor, 'rowcount') else None,
                'route': route,
                'user_id': user_id,
                'role': role,
                'host': host,
            }
            nested = None
            try:
                # выставляем флаг, чтобы listener пропустил наши внутренние SQL
                if getattr(conn, 'info', None) is not None:
                    conn.info['t2s_skip'] = True
                nested = conn.begin_nested()
                conn.execute(sa_text(
                    "insert into text2sql_captured(sql, params_json, duration_ms, rows_affected, route, user_id, role, source_host) "
                    "values (:sql, :params_json, :duration_ms, :rows, :route, :user_id, :role, :host)"
                ), payload)
                nested.commit()
            except Exception:
                if nested is not None:
                    try:
                        nested.rollback()
                    except Exception:
                        pass
                # не пробрасываем и не логируем stack (во избежание рекурсии)
                _log.warning("capture: insert skipped due to error")
            finally:
                try:
                    if getattr(conn, 'info', None) is not None and 't2s_skip' in conn.info:
                        conn.info.pop('t2s_skip', None)
                except Exception:
                    pass
        except Exception:
            # минимальное логирование без exc_info, чтобы избежать рекурсивной печати
            _log.warning("capture: outer failure while processing statement")
            return

    _capture_installed = True
    _log.info("capture: installed (TEXT2SQL_CAPTURE=1)")


def aggregates_for_lots(db: Session, lot_ids: list[int]):
    """
    Возвращает агрегаты для лотов по батчам и показателям операторов/склада:
    - warehouse_received: сумма recounted_quantity по батчам с warehouse_received_at IS NOT NULL
    - good_qty: сумма current_quantity по current_location = 'good'
    - defect_qty: сумма current_quantity по current_location = 'defect'
    - operators_reported: максимум operator_reported_quantity
    - qa_inspector_name: последний известный инспектор (по имени), если есть
    """
    if not lot_ids:
        return {}

    agg = (
        db.query(
            BatchDB.lot_id.label('lot_id'),
            func.sum(func.coalesce(BatchDB.recounted_quantity, 0)).filter(BatchDB.warehouse_received_at.isnot(None)).label('warehouse_received'),
            func.sum(func.coalesce(BatchDB.current_quantity, 0)).filter(BatchDB.current_location == 'good').label('good_qty'),
            func.sum(func.coalesce(BatchDB.current_quantity, 0)).filter(BatchDB.current_location == 'defect').label('defect_qty'),
            func.max(func.coalesce(BatchDB.operator_reported_quantity, 0)).label('operators_reported'),
        )
        .filter(BatchDB.lot_id.in_(lot_ids))
        .group_by(BatchDB.lot_id)
        .all()
    )

    result = {}
    for row in agg:
        result[row.lot_id] = {
            'warehouse_received': int(row.warehouse_received or 0),
            'good_qty': int(row.good_qty or 0),
            'defect_qty': int(row.defect_qty or 0),
            'operators_reported': int(row.operators_reported or 0),
            'qa_inspector_name': None,  # заполним ниже из отдельного запроса
        }

    # Определяем последнего инспектора ОТК по каждой паре (lot_id, max(qa_date))
    sub_max_qc = (
        db.query(
            BatchDB.lot_id.label('lot_id'),
            func.max(BatchDB.qa_date).label('max_qc_date')
        )
        .filter(BatchDB.lot_id.in_(lot_ids), BatchDB.qc_inspector_id.isnot(None))
        .group_by(BatchDB.lot_id)
        .subquery()
    )

    qc_rows = (
        db.query(
            BatchDB.lot_id.label('lot_id'),
            EmployeeDB.full_name.label('qa_inspector_name')
        )
        .join(sub_max_qc, (BatchDB.lot_id == sub_max_qc.c.lot_id) & (BatchDB.qa_date == sub_max_qc.c.max_qc_date))
        .outerjoin(EmployeeDB, BatchDB.qc_inspector_id == EmployeeDB.id)
        .all()
    )

    for r in qc_rows:
        if r.lot_id in result:
            result[r.lot_id]['qa_inspector_name'] = r.qa_inspector_name or None

    # Фолбэк: если по батчам инспектор не найден, берем из последнего сетапа с qa_id
    missing_qc_lot_ids = [lid for lid, vals in result.items() if not vals['qa_inspector_name']]
    if missing_qc_lot_ids:
        sub_last_setup = (
            db.query(
                SetupDB.lot_id.label('lot_id'),
                func.max(SetupDB.created_at).label('max_created')
            )
            .filter(SetupDB.lot_id.in_(missing_qc_lot_ids), SetupDB.qa_id.isnot(None))
            .group_by(SetupDB.lot_id)
            .subquery()
        )

        setup_qc_rows = (
            db.query(
                SetupDB.lot_id.label('lot_id'),
                EmployeeDB.full_name.label('qa_inspector_name')
            )
            .join(sub_last_setup, (SetupDB.lot_id == sub_last_setup.c.lot_id) & (SetupDB.created_at == sub_last_setup.c.max_created))
            .outerjoin(EmployeeDB, SetupDB.qa_id == EmployeeDB.id)
            .all()
        )

        for r in setup_qc_rows:
            if r.lot_id in result and r.qa_inspector_name:
                result[r.lot_id]['qa_inspector_name'] = r.qa_inspector_name

    return result


def planned_resolved(initial: int | None, setup_total: int | None) -> int:
    """Единое правило планового количества."""
    base = initial or 0
    return setup_total if setup_total is not None else base


