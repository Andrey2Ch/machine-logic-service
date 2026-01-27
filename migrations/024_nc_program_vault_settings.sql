-- ============================================
-- Migration: CNC Program Vault — settings storage
-- Date: 2026-01-27
--
-- Goals:
-- - Store Program Vault UI/runtime settings in DB (admin-managed):
--   - default_history_limit (how many revisions to show by default)
--
-- Notes:
-- - We use a small generic key/value table to avoid frequent schema changes.
-- - Values are jsonb for forward-compatibility.
-- ============================================

begin;

create table if not exists app_settings (
    key text primary key,
    value jsonb not null,
    updated_at timestamp not null default now(),
    updated_by_employee_id integer null,
    constraint chk_app_settings_key_nonempty check (char_length(trim(key)) > 0),
    constraint chk_app_settings_value_is_object_or_scalar check (jsonb_typeof(value) in ('object', 'array', 'string', 'number', 'boolean', 'null'))
);

comment on table app_settings is 'Generic application settings (key/value jsonb) managed by admin UI.';
comment on column app_settings.key is 'Setting key. Example: nc_program_vault.default_history_limit';
comment on column app_settings.value is 'Setting value stored as jsonb.';
comment on column app_settings.updated_by_employee_id is 'Optional employee id who changed the setting.';

-- Seed default value (idempotent)
insert into app_settings (key, value)
values ('nc_program_vault.default_history_limit', to_jsonb(5))
on conflict (key) do nothing;

commit;

