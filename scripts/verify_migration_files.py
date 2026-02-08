import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    migrations_dir = root / "migrations"
    sql_files = sorted(migrations_dir.glob("*.sql"))

    missing = []
    for file in sql_files:
        content = file.read_text(encoding="utf-8")
        if "schema_migrations" not in content:
            missing.append(file.name)

    if missing:
        print("Migrations missing schema_migrations log:")
        for name in missing:
            print(f"  - {name}")
        return 1

    print("All migrations contain schema_migrations log.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
