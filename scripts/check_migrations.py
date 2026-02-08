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
    versions = [file.stem for file in sql_files]

    if not versions:
        print("No migrations found.")
        return 0

    conn = psycopg2.connect(get_dsn())
    cur = conn.cursor()
    try:
        cur.execute(
            """
            select exists (
              select 1
              from information_schema.tables
              where table_schema = 'public'
                and table_name = 'schema_migrations'
            );
            """
        )
        has_table = cur.fetchone()[0]
        if not has_table:
            print("schema_migrations table not found. Cannot verify applied migrations.")
            return 2

        cur.execute("select version from schema_migrations;")
        applied = {row[0] for row in cur.fetchall()}
    finally:
        cur.close()
        conn.close()

    pending = [version for version in versions if version not in applied]
    extras = sorted(applied.difference(versions))

    print(f"Total migrations: {len(versions)}")
    print(f"Applied migrations: {len(applied)}")

    if pending:
        print("Pending migrations:")
        for version in pending:
            print(f"  - {version}")
    else:
        print("Pending migrations: none")

    if extras:
        print("Applied but missing locally:")
        for version in extras:
            print(f"  - {version}")

    return 1 if pending else 0


if __name__ == "__main__":
    sys.exit(main())
