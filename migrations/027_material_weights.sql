-- PRD: Material weight fields and density on catalogs
-- 2026-02-03

-- 1) Add density to catalogs
ALTER TABLE material_groups
    ADD COLUMN IF NOT EXISTS density_kg_m3 NUMERIC(10,3);

ALTER TABLE material_subgroups
    ADD COLUMN IF NOT EXISTS density_kg_m3 NUMERIC(10,3);

-- 2) Add weight fields to batches
ALTER TABLE material_batches
    ADD COLUMN IF NOT EXISTS weight_per_meter_kg NUMERIC(10,4),
    ADD COLUMN IF NOT EXISTS weight_kg NUMERIC(12,4);

-- 3) Migration version tracking
INSERT INTO schema_migrations (version, applied_at)
VALUES ('027_material_weights', NOW())
ON CONFLICT (version) DO NOTHING;
