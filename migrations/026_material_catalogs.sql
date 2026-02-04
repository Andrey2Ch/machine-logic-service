-- PRD: Material group/subgroup catalogs for warehouse batches
-- 2026-02-03

-- 1) Material groups
CREATE TABLE IF NOT EXISTS material_groups (
    id SERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2) Material subgroups (scoped to group)
CREATE TABLE IF NOT EXISTS material_subgroups (
    id SERIAL PRIMARY KEY,
    group_id INTEGER NOT NULL REFERENCES material_groups(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (group_id, code)
);

CREATE INDEX IF NOT EXISTS idx_material_subgroups_group ON material_subgroups(group_id);
CREATE INDEX IF NOT EXISTS idx_material_groups_active ON material_groups(is_active);
CREATE INDEX IF NOT EXISTS idx_material_subgroups_active ON material_subgroups(is_active);

-- 3) Link catalogs to batches
ALTER TABLE material_batches
    ADD COLUMN IF NOT EXISTS material_group_id INTEGER REFERENCES material_groups(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS material_subgroup_id INTEGER REFERENCES material_subgroups(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_material_batches_group ON material_batches(material_group_id);
CREATE INDEX IF NOT EXISTS idx_material_batches_subgroup ON material_batches(material_subgroup_id);

-- 4) Migration version tracking
INSERT INTO schema_migrations (version, applied_at)
VALUES ('026_material_catalogs', NOW())
ON CONFLICT (version) DO NOTHING;
