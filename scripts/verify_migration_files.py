import sys
from pathlib import Path


LEGACY_MIGRATIONS = {
    "021_ai_assistant_tables.sql",
    "022_nc_program_vault.sql",
    "023_nc_program_channels.sql",
    "024_nc_program_vault_settings.sql",
    "025_material_batches.sql",
    "add_assigned_fields.sql",
    "add_avg_cycle_time_to_parts.sql",
    "add_is_operational_to_machines.sql",
    "add_material_fields.sql",
    "add_notification_settings.sql",
    "add_pinned_machine_to_parts.sql",
    "add_viewer_role.sql",
    "fix_viewer_notifications.sql",
    "remove_reading_submitted.sql",
    "rename_machines_for_mtconnect.sql",
}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    migrations_dir = root / "migrations"
    sql_files = sorted(migrations_dir.glob("*.sql"))

    missing = []
    for file in sql_files:
        if file.name in LEGACY_MIGRATIONS:
            continue
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
