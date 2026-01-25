"""
Server-side "gate" enforcement for program handover between setups.

We keep this logic in MLS to avoid bypassing the gate via direct HTTP calls
to /admin/setup/{id}/send-to-qc or /admin/setup/{id}/approve.

Important:
- DDL is CREATE IF NOT EXISTS (safe for production).
- If the table exists (created via Appsmith / TG_bot), we reuse it.
"""

import logging
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

HandoverStatus = str  # 'pending' | 'confirmed' | 'skipped' | 'not_required'


def ensure_setup_program_handover_table(db: Session) -> None:
    """
    Creates setup_program_handover table if it does not exist.
    This is intentionally idempotent (safe to run on every request).
    """
    try:
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS setup_program_handover (
                id BIGSERIAL PRIMARY KEY,
                next_setup_id INTEGER NOT NULL REFERENCES setup_jobs(id) ON DELETE CASCADE,
                prev_setup_id INTEGER NULL REFERENCES setup_jobs(id) ON DELETE SET NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                skip_reason TEXT NULL,
                decided_by_employee_id INTEGER NULL REFERENCES employees(id) ON DELETE SET NULL,
                decided_at TIMESTAMP NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """))
        db.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_setup_program_handover_next
            ON setup_program_handover(next_setup_id);
        """))
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_setup_program_handover_prev
            ON setup_program_handover(prev_setup_id);
        """))
        db.commit()
    except Exception as e:
        db.rollback()
        # Fail-open: we must not take prod down due to DDL issues
        logger.warning("setup_program_handover DDL failed (fail-open): %s", e)


def _find_prev_setup_id(db: Session, *, machine_id: int, next_setup_id: int) -> Optional[int]:
    """
    Choose previous setup for the gate.
    Priority:
      1) Active setup on the machine (excluding current), end_time is null
      2) Last completed/stopped setup
    """
    # Active setup (including started/queued/created/in_production/pending_qc/allowed)
    active = db.execute(
        text("""
            SELECT id
            FROM setup_jobs
            WHERE machine_id = :machine_id
              AND id <> :next_setup_id
              AND end_time IS NULL
              AND status IN ('started', 'queued', 'created', 'in_production', 'pending_qc', 'allowed')
            ORDER BY id DESC
            LIMIT 1
        """),
        {"machine_id": machine_id, "next_setup_id": next_setup_id},
    ).fetchone()

    if active and getattr(active, "id", None):
        return int(active.id)

    last_completed = db.execute(
        text("""
            SELECT id
            FROM setup_jobs
            WHERE machine_id = :machine_id
              AND id <> :next_setup_id
              AND status IN ('completed', 'stopped')
            ORDER BY end_time DESC NULLS LAST, id DESC
            LIMIT 1
        """),
        {"machine_id": machine_id, "next_setup_id": next_setup_id},
    ).fetchone()

    if last_completed and getattr(last_completed, "id", None):
        return int(last_completed.id)

    return None


def ensure_setup_program_handover_row(
    db: Session,
    *,
    next_setup_id: int,
    machine_id: Optional[int],
) -> Dict[str, Any]:
    """
    Ensures one row exists for next_setup_id and returns it.
    If machine_id is None, prev_setup_id is None and status becomes not_required.
    """
    ensure_setup_program_handover_table(db)

    try:
        existing = db.execute(
            text("""
                SELECT
                    id,
                    next_setup_id,
                    prev_setup_id,
                    status,
                    skip_reason,
                    decided_by_employee_id,
                    decided_at,
                    created_at
                FROM setup_program_handover
                WHERE next_setup_id = :next_setup_id
                LIMIT 1
            """),
            {"next_setup_id": next_setup_id},
        ).mappings().first()

        if existing:
            return dict(existing)

        prev_setup_id: Optional[int] = None
        if machine_id is not None:
            prev_setup_id = _find_prev_setup_id(db, machine_id=machine_id, next_setup_id=next_setup_id)

        status: HandoverStatus = "pending" if prev_setup_id else "not_required"

        db.execute(
            text("""
                INSERT INTO setup_program_handover (next_setup_id, prev_setup_id, status)
                VALUES (:next_setup_id, :prev_setup_id, :status)
                ON CONFLICT (next_setup_id) DO NOTHING
            """),
            {"next_setup_id": next_setup_id, "prev_setup_id": prev_setup_id, "status": status},
        )
        db.commit()

        created = db.execute(
            text("""
                SELECT
                    id,
                    next_setup_id,
                    prev_setup_id,
                    status,
                    skip_reason,
                    decided_by_employee_id,
                    decided_at,
                    created_at
                FROM setup_program_handover
                WHERE next_setup_id = :next_setup_id
                LIMIT 1
            """),
            {"next_setup_id": next_setup_id},
        ).mappings().first()

        if created:
            return dict(created)

        # Should not happen; fail-open
        logger.warning("Failed to create setup_program_handover row for setup_id=%s", next_setup_id)
        return {"next_setup_id": next_setup_id, "prev_setup_id": None, "status": "not_required"}

    except Exception as e:
        db.rollback()
        # Fail-open: do not break production transitions
        logger.warning("setup_program_handover read/insert failed (fail-open): %s", e)
        return {"next_setup_id": next_setup_id, "prev_setup_id": None, "status": "not_required"}


def is_setup_program_handover_satisfied(status: Optional[str]) -> bool:
    return status in ("confirmed", "skipped", "not_required")


def check_setup_program_handover_gate(
    db: Session,
    *,
    next_setup_id: int,
    machine_id: Optional[int],
) -> Tuple[bool, Dict[str, Any]]:
    """
    Returns (ok, row).
    ok=True when either gate is not required or it was confirmed/skipped.
    """
    row = ensure_setup_program_handover_row(db, next_setup_id=next_setup_id, machine_id=machine_id)
    prev_setup_id = row.get("prev_setup_id")
    status = row.get("status")
    required = bool(prev_setup_id)
    ok = (not required) or is_setup_program_handover_satisfied(status)
    return ok, row

