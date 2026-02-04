import os
import sys
from pathlib import Path
import psycopg2

def _get_preamble(out_path: Path) -> str:
    if not out_path.exists():
        return "# Schema documentation for schema `public`\n\n"

    content = out_path.read_text(encoding='utf-8')
    marker = "\n## Таблицы\n"
    if marker in content:
        return content.split(marker, 1)[0] + marker + "\n"
    return "# Schema documentation for schema `public`\n\n"


def main():
    dsn = os.environ.get('DATABASE_URL') or 'postgresql://postgres:postgres@localhost:5432/isramat_bot'
    default_out = Path(__file__).parent.parent / "docs" / "schema_docs.md"
    out = Path(os.environ.get('SCHEMA_MD_OUT') or default_out)
    schema = os.environ.get('SCHEMA') or 'public'

    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(
        """
        select c.table_name,
               c.column_name,
               c.data_type,
               c.is_nullable,
               coalesce(pgd.description, '') as column_description
        from information_schema.columns c
        left join pg_catalog.pg_statio_all_tables st on st.relname = c.table_name
        left join pg_catalog.pg_description pgd on pgd.objoid = st.relid and pgd.objsubid = c.ordinal_position
        where c.table_schema = %s
        order by c.table_name, c.ordinal_position;
        """,
        (schema,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    by_table = {}
    for t, col, typ, nulls, descr in rows:
        by_table.setdefault(t, []).append((col, typ, nulls, descr))

    preamble = _get_preamble(out)

    with open(out, 'w', encoding='utf-8') as f:
        f.write(preamble)
        f.write("## Таблицы\n\n")
        for table, cols in by_table.items():
            f.write(f"## {table}\n\n")
            f.write("| column | type | nullable | description |\n")
            f.write("|---|---|---|---|\n")
            for col, typ, nulls, descr in cols:
                f.write(f"| {col} | {typ} | {nulls} | {descr.replace('|','\\|')} |\n")
            f.write("\n")

if __name__ == '__main__':
    sys.exit(main())


