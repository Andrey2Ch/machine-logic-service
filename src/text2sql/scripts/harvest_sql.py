import os
import re
import json
import hashlib
from pathlib import Path

ROOTS = [
    r"C:\\Projects\\machine-logic-service",
    r"C:\\Projects\\isramat-dashboard",
    r"C:\\Projects\\TG_bot",
    r"C:\\Projects\\MTConnect",
]

OUT_BASE = Path(__file__).resolve().parents[1] / 'knowledge' / 'harvest'

SQL_EXTS = {'.sql'}
TEXT_EXTS = {'.py', '.ts', '.tsx', '.js'}

SQL_PATTERN = re.compile(r"\b(select|insert|update|delete|create|alter|drop|truncate|grant|revoke)\b", re.IGNORECASE)

EXCLUDE_DIRS = {"node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".next", ".cache"}

def ensure_dirs(project: str):
    (OUT_BASE / project / 'sql_raw').mkdir(parents=True, exist_ok=True)
    (OUT_BASE / project / 'pairs' / 'select').mkdir(parents=True, exist_ok=True)
    (OUT_BASE / project / 'pairs' / 'dml').mkdir(parents=True, exist_ok=True)

def normalize_sql(sql: str) -> str:
    s = sql.strip().replace('\r\n', '\n')
    s = re.sub(r"\s+", " ", s)
    return s

def classify(sql: str) -> str:
    head = sql.strip().split(None, 1)[0].lower() if sql.strip() else ''
    return 'dml' if head in {'insert','update','delete','create','alter','drop','truncate','grant','revoke'} else 'select'

def checksum(s: str) -> str:
    return hashlib.sha256(s.encode('utf-8')).hexdigest()[:16]

def _split_statements(sql_blob: str) -> list[str]:
    parts = re.split(r";\s*(?:\n|$)", sql_blob, flags=re.MULTILINE)
    return [p.strip() for p in parts if p.strip()]


def _looks_like_sql_start(snippet: str) -> bool:
    s = snippet.lstrip("({[ \t\n\r")
    head = (s.split(None, 1)[0].lower() if s else '')
    return head in {"select","insert","update","delete","create","alter","drop","truncate","grant","revoke"}


def extract_sql_from_text(content: str):
    blocks: list[str] = []
    # 1) Fenced blocks ```sql ... ```
    for m in re.finditer(r"```sql\s*([\s\S]*?)```", content, re.IGNORECASE):
        blocks.extend(_split_statements(m.group(1)))

    # 2) Prisma $queryRaw...`...`
    for m in re.finditer(r"\$queryRaw(?:Unsafe)?`([\s\S]*?)`", content, re.IGNORECASE):
        blocks.extend(_split_statements(m.group(1)))

    # 3) sqlalchemy text("..."), allow triple quotes
    for m in re.finditer(r"text\(\s*(?:r|f|rf|fr)?(?:\"\"\"([\s\S]*?)\"\"\"|'\"([\s\S]*?)\"'|\"([\s\S]*?)\"|'([\s\S]*?)')\s*\)", content, re.IGNORECASE):
        grp = next((g for g in m.groups() if g is not None), None)
        if grp:
            blocks.extend(_split_statements(grp))

    # 4) Triple-quoted blobs in code — try as last resort
    for m in re.finditer(r"(?:r|f|rf|fr)?\"\"\"([\s\S]*?)\"\"\"", content, re.IGNORECASE):
        blob = m.group(1)
        if SQL_PATTERN.search(blob):
            blocks.extend(_split_statements(blob))
    for m in re.finditer(r"(?:r|f|rf|fr)?'''([\s\S]*?)'''", content, re.IGNORECASE):
        blob = m.group(1)
        if SQL_PATTERN.search(blob):
            blocks.extend(_split_statements(blob))

    # 5) Fallback: strict split by ';' on the whole content (only if nothing found above)
    if not blocks:
        parts = re.split(r";\s*(?:\n|$)", content, flags=re.MULTILINE)
        for part in parts:
            if SQL_PATTERN.search(part):
                blocks.append(part.strip())

    # Filter: keep only those that look like SQL start
    filtered: list[str] = []
    for b in blocks:
        if _looks_like_sql_start(b):
            filtered.append(b)
    return filtered

def scan_root(root: str):
    project = Path(root).name
    ensure_dirs(project)
    seen = set()
    shard_counts = {'select': 0, 'dml': 0}
    stats = {'files': 0, 'stmts': 0, 'kept': 0, 'skipped': 0}
    shard_size = 5000
    writers = {
        'select': None,
        'dml': None,
    }
    try:
        for path, dirnames, files in os.walk(root):
            # фильтруем каталоги на лету
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for fn in files:
                p = Path(path) / fn
                ext = p.suffix.lower()
                try:
                    if ext in SQL_EXTS:
                        text = p.read_text(encoding='utf-8', errors='ignore')
                        sql_blocks = extract_sql_from_text(text)
                    elif ext in TEXT_EXTS:
                        text = p.read_text(encoding='utf-8', errors='ignore')
                        sql_blocks = extract_sql_from_text(text)
                    else:
                        continue
                except Exception:
                    stats['skipped'] += 1
                    continue

                stats['files'] += 1
                for raw in sql_blocks:
                    norm = normalize_sql(raw)
                    if not norm:
                        stats['skipped'] += 1
                        continue
                    ch = checksum(norm)
                    if ch in seen:
                        stats['skipped'] += 1
                        continue
                    seen.add(ch)
                    kind = classify(norm)
                    stats['stmts'] += 1

                    # append to shard jsonl
                    idx = shard_counts[kind] // shard_size
                    out_dir = OUT_BASE / project / 'pairs' / kind
                    out_dir.mkdir(parents=True, exist_ok=True)
                    shard_path = out_dir / f'examples-{idx:03}.jsonl'
                    with shard_path.open('a', encoding='utf-8') as f:
                        rec = {
                            'question_ru': None,
                            'question_en': None,
                            'sql': norm,
                            'language': None,
                            'tags': [],
                            'source_project': project,
                            'source_path': str(p),
                            'is_dml': kind == 'dml',
                            'checksum': ch,
                        }
                        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
                    shard_counts[kind] += 1
                    stats['kept'] += 1
    finally:
        for w in writers.values():
            if w and not w.closed:
                w.close()
    print(f"[harvest] {project}: files={stats['files']} stmts={stats['stmts']} kept={stats['kept']} skipped={stats['skipped']} select={shard_counts['select']} dml={shard_counts['dml']}")

def main():
    for root in ROOTS:
        if os.path.isdir(root):
            scan_root(root)

if __name__ == '__main__':
    main()


