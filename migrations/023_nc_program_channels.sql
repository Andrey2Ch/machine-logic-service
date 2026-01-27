-- ============================================
-- Migration: CNC Program Vault — channels & profiles
-- Date: 2026-01-27
--
-- Goals:
-- - Support multi-channel programs per machine_type:
--     - Swiss: ch1+ch2 (aliases: main/sub)
--     - Citizen: nc (single file)
--     - Some machines: ch1+ch2+ch3
-- - Keep backward compatibility with existing data:
--     - existing roles: main/sub
-- - Introduce machine_type profiles to define required channels + UI labels.
--
-- Safety:
-- - Additive where possible.
-- - Loosen role constraint (regex) rather than hard-coded enum list.
-- ============================================

begin;

-- 1) Profiles: define channels per machine_type (folders like SR20/SR22/...)
create table if not exists nc_machine_type_profiles (
    machine_type text primary key,
    -- Example:
    -- [
    --   {"key":"ch1","label":"main"},
    --   {"key":"ch2","label":"sub"}
    -- ]
    channels jsonb not null,
    created_at timestamp not null default now(),
    updated_at timestamp not null default now(),
    constraint chk_nc_machine_type_profiles_machine_type_nonempty check (char_length(trim(machine_type)) > 0),
    constraint chk_nc_machine_type_profiles_channels_is_array check (jsonb_typeof(channels) = 'array')
);

comment on table nc_machine_type_profiles is 'Defines required CNC program channels per machine_type and their UI labels.';
comment on column nc_machine_type_profiles.channels is 'JSON array of channel descriptors: {key,label}. Keys like ch1/ch2/ch3 or nc.';

-- 2) Allow role/channel values beyond main/sub.
-- Existing migration 022 has constraint: chk_nc_program_revision_files_role_allowed check(role in ('main','sub'))
-- We drop it (if present) and replace with a regex-based constraint:
--   - legacy: main|sub
--   - new: ch<digits> (e.g. ch1, ch2, ch3, ch10)
--   - single-file: nc
alter table nc_program_revision_files
    drop constraint if exists chk_nc_program_revision_files_role_allowed;

alter table nc_program_revision_files
    add constraint chk_nc_program_revision_files_role_allowed
    check (
        role in ('main', 'sub', 'nc')
        or role ~ '^ch[1-9][0-9]*$'
    );

commit;

