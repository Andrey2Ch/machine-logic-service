import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]

    targets = [
        ("docs/schema_docs.md", root / "scripts" / "generate_schema_docs.py"),
        ("src/text2sql/docs/schema_docs.md", root / "src" / "text2sql" / "scripts" / "generate_schema_docs.py"),
    ]

    failed = False
    for label, script_path in targets:
        out_path = root / label
        if not script_path.exists():
            print(f"Script not found: {script_path}")
            failed = True
            continue

        print(f"Updating {label} ...")
        env = os.environ.copy()
        env["SCHEMA_MD_OUT"] = str(out_path)
        rc = subprocess.call([sys.executable, str(script_path)], env=env)
        if rc != 0:
            print(f"  FAILED (exit {rc})")
            failed = True
        else:
            print(f"  OK")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
