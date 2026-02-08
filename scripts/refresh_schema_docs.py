import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    schema_out = root / "docs" / "schema_docs.md"
    os.environ.setdefault("SCHEMA_MD_OUT", str(schema_out))

    script_path = root / "scripts" / "generate_schema_docs.py"
    if not script_path.exists():
        print(f"generate_schema_docs.py not found at {script_path}")
        return 1

    return subprocess.call([sys.executable, str(script_path)])


if __name__ == "__main__":
    sys.exit(main())
