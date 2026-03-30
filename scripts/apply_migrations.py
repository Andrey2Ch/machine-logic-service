"""
apply_migrations.py — применяет новые SQL миграции из migrations/ к БД.

Запускается автоматически в entrypoint.sh перед стартом uvicorn.
Идемпотентен: пропускает уже применённые версии.
"""

import os
import sys
from pathlib import Path

import psycopg2


def get_dsn() -> str:
    return os.environ.get("DATABASE_URL") or "postgresql://postgres:postgres@localhost:5432/isramat_bot"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    migrations_dir = root / "migrations"
    sql_files = sorted(migrations_dir.glob("*.sql"))

    if not sql_files:
        print("[migrations] No migration files found.")
        return 0

    conn = psycopg2.connect(get_dsn())
    conn.autocommit = False
    cur = conn.cursor()

    try:
        # Убедимся что таблица schema_migrations существует
        cur.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        conn.commit()

        cur.execute("SELECT version FROM schema_migrations;")
        applied = {row[0] for row in cur.fetchall()}

        def is_applied(stem: str) -> bool:
            if stem in applied:
                return True
            # Совместимость: "021_ai_assistant_tables" совпадает с записью "021"
            prefix = stem.split("_")[0]
            if prefix.isdigit() and prefix in applied:
                return True
            return False

        pending = [f for f in sql_files if not is_applied(f.stem)]

        if not pending:
            print(f"[migrations] All {len(sql_files)} migrations already applied.")
            return 0

        print(f"[migrations] {len(applied)} applied, {len(pending)} pending.")

        for sql_file in pending:
            print(f"[migrations] Applying {sql_file.name} ...")
            sql = sql_file.read_text(encoding="utf-8")
            try:
                cur.execute(sql)
                conn.commit()
                print(f"[migrations] ✓ {sql_file.name}")
            except Exception as e:
                conn.rollback()
                print(f"[migrations] ✗ {sql_file.name} FAILED: {e}", file=sys.stderr)
                return 1

        print(f"[migrations] Done. Applied {len(pending)} migration(s).")
        return 0

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
