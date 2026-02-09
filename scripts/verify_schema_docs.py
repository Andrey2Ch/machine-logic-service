import re
import sys
from pathlib import Path


COLUMN_REGEX = re.compile(r"add\s+column\s+if\s+not\s+exists\s+([a-zA-Z0-9_]+)", re.IGNORECASE)
TABLE_REGEX = re.compile(r"create\s+table\s+if\s+not\s+exists\s+([a-zA-Z0-9_]+)", re.IGNORECASE)


def extract_expected_columns(migrations_dir: Path) -> set[str]:
    expected = set()
    for file in sorted(migrations_dir.glob("*.sql")):
        content = file.read_text(encoding="utf-8")
        for match in COLUMN_REGEX.findall(content):
            expected.add(match)
    return expected


def extract_expected_tables(migrations_dir: Path) -> set[str]:
    expected = set()
    for file in sorted(migrations_dir.glob("*.sql")):
        content = file.read_text(encoding="utf-8")
        for match in TABLE_REGEX.findall(content):
            expected.add(match)
    return expected


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    migrations_dir = root / "migrations"
    schema_docs = root / "docs" / "schema_docs.md"

    if not schema_docs.exists():
        print("docs/schema_docs.md not found.")
        return 1

    docs_text = schema_docs.read_text(encoding="utf-8")
    expected_columns = extract_expected_columns(migrations_dir)
    expected_tables = extract_expected_tables(migrations_dir)

    missing_columns = sorted(
        col for col in expected_columns if f"| {col} |" not in docs_text
    )
    missing_tables = sorted(
        table for table in expected_tables if f"## {table}" not in docs_text
    )

    if missing_columns or missing_tables:
        print("schema_docs.md is out of date.")
        if missing_tables:
            print("Missing tables:")
            for table in missing_tables:
                print(f"  - {table}")
        if missing_columns:
            print("Missing columns:")
            for col in missing_columns:
                print(f"  - {col}")
        return 1

    print("schema_docs.md includes tables/columns from migrations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
