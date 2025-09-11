from sqlalchemy.orm import Session
from sqlalchemy import func
from ..models.models import BatchDB, SetupDB, EmployeeDB


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


