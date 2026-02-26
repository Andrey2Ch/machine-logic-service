-- Link parts to the material catalog (material_groups / material_subgroups)
-- Replaces free-text "material" column with structured FK references

ALTER TABLE parts
    ADD COLUMN IF NOT EXISTS material_group_id INTEGER
        REFERENCES material_groups(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS material_subgroup_id INTEGER
        REFERENCES material_subgroups(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_parts_material_group ON parts (material_group_id);
CREATE INDEX IF NOT EXISTS idx_parts_material_subgroup ON parts (material_subgroup_id);

COMMENT ON COLUMN parts.material_group_id
    IS 'FK to material_groups catalog — primary material classification';
COMMENT ON COLUMN parts.material_subgroup_id
    IS 'FK to material_subgroups catalog — specific material grade (e.g. 304L, 316)';

INSERT INTO schema_migrations (version, applied_at)
VALUES ('040_parts_material_catalog', NOW())
ON CONFLICT (version) DO NOTHING;
