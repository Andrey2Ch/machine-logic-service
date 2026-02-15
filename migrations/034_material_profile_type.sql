-- Add material profile type (round/hexagon/square) for warehouse and lot material flows

ALTER TABLE IF EXISTS material_batches
  ADD COLUMN IF NOT EXISTS profile_type VARCHAR(20) DEFAULT 'round';

UPDATE material_batches
SET profile_type = 'round'
WHERE profile_type IS NULL;

ALTER TABLE IF EXISTS lot_materials
  ADD COLUMN IF NOT EXISTS shape VARCHAR(20) DEFAULT 'round';

UPDATE lot_materials
SET shape = 'round'
WHERE shape IS NULL;

ALTER TABLE IF EXISTS material_operations
  ADD COLUMN IF NOT EXISTS shape VARCHAR(20);

INSERT INTO schema_migrations (version, applied_at)
VALUES ('034_material_profile_type', NOW())
ON CONFLICT (version) DO NOTHING;

