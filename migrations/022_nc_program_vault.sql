-- ============================================
-- Migration: CNC Program Vault (MVP tables)
-- Date: 2026-01-25
-- Description:
--   Adds minimal, additive schema for CNC program storage:
--   - file_blobs (dedup by sha256, storage_key in Railway Volume)
--   - nc_programs (logical program per part + machine_type + kind)
--   - nc_program_revisions (immutable revisions; last revision = current)
--   - nc_program_revision_files (link revision -> file blob)
--
-- Safety:
--   Uses CREATE TABLE/INDEX IF NOT EXISTS only.
-- ============================================

begin;

-- 1) File blobs (dedup by sha256)
create table if not exists file_blobs (
    id bigserial primary key,
    sha256 varchar(64) not null,
    size_bytes bigint not null,
    storage_key text not null,
    original_filename text null,
    mime_type text null,
    created_at timestamp not null default now(),
    constraint uq_file_blobs_sha256 unique (sha256),
    constraint chk_file_blobs_sha256_len check (char_length(sha256) = 64),
    constraint chk_file_blobs_size_nonneg check (size_bytes >= 0)
);

create index if not exists ix_file_blobs_created_at on file_blobs(created_at);

comment on table file_blobs is 'Immutable file blobs stored in Railway Volume; deduplicated by sha256.';
comment on column file_blobs.sha256 is 'Hex-encoded SHA-256 hash (64 chars).';
comment on column file_blobs.storage_key is 'Path/key inside Railway Volume storage.';

-- 2) Logical program record (per part + machine_type + kind)
create table if not exists nc_programs (
    id bigserial primary key,
    part_id integer not null references parts(id) on delete cascade,

    -- For MVP we bind to machines.type (string) rather than introducing machine_groups.
    -- Use "any" when the program is not tied to a specific machine type.
    machine_type varchar(50) not null default 'any',

    -- main/sub/op/macro ... (start with 'main')
    program_kind varchar(20) not null default 'main',

    title text null,
    comment text null,

    created_by_employee_id integer null references employees(id) on delete set null,
    created_at timestamp not null default now(),

    constraint chk_nc_programs_machine_type_nonempty check (char_length(machine_type) > 0),
    constraint chk_nc_programs_program_kind_nonempty check (char_length(program_kind) > 0)
);

create unique index if not exists ux_nc_programs_part_type_kind
    on nc_programs(part_id, machine_type, program_kind);

create index if not exists ix_nc_programs_part_id on nc_programs(part_id);
create index if not exists ix_nc_programs_machine_type on nc_programs(machine_type);

comment on table nc_programs is 'Logical CNC program entity per part + machine type + kind.';
comment on column nc_programs.machine_type is 'Matches machines.type; use ''any'' when not tied to a specific type.';

-- 3) Revisions (immutable)
create table if not exists nc_program_revisions (
    id bigserial primary key,
    program_id bigint not null references nc_programs(id) on delete cascade,
    rev_number integer not null,
    note text null,
    created_by_employee_id integer null references employees(id) on delete set null,
    created_at timestamp not null default now(),
    constraint chk_nc_program_revisions_rev_positive check (rev_number >= 1)
);

create unique index if not exists ux_nc_program_revisions_program_rev
    on nc_program_revisions(program_id, rev_number);

create index if not exists ix_nc_program_revisions_program_id on nc_program_revisions(program_id);
create index if not exists ix_nc_program_revisions_created_at on nc_program_revisions(created_at);

comment on table nc_program_revisions is 'Immutable revisions of CNC programs. Current = max(rev_number).';

-- 4) Link revision -> file blob(s)
-- For Swiss-type we require 2 files per revision: main + sub.
create table if not exists nc_program_revision_files (
    id bigserial primary key,
    revision_id bigint not null references nc_program_revisions(id) on delete cascade,
    file_id bigint not null references file_blobs(id) on delete restrict,
    -- 'main' or 'sub' for Swiss-type (MVP)
    role varchar(20) not null,
    created_at timestamp not null default now(),
    constraint chk_nc_program_revision_files_role_nonempty check (char_length(role) > 0),
    constraint chk_nc_program_revision_files_role_allowed check (role in ('main', 'sub'))
);

create unique index if not exists ux_nc_program_revision_files_rev_file
    on nc_program_revision_files(revision_id, file_id);

-- One role per revision (exactly main + sub will be enforced in API logic; DB enforces uniqueness)
create unique index if not exists ux_nc_program_revision_files_rev_role
    on nc_program_revision_files(revision_id, role);

create index if not exists ix_nc_program_revision_files_revision_id on nc_program_revision_files(revision_id);
create index if not exists ix_nc_program_revision_files_file_id on nc_program_revision_files(file_id);

comment on table nc_program_revision_files is 'Associates program revisions with stored file blobs. MVP requires two roles: main + sub.';

commit;

