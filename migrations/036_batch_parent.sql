-- 036: Add parent_batch_id to material_batches for cut-bar tracking
-- When bars are cut on the warehouse floor, a child batch is created
-- inheriting all properties but with a shorter bar_length.

BEGIN;

ALTER TABLE material_batches
  ADD COLUMN IF NOT EXISTS parent_batch_id TEXT
    REFERENCES material_batches(batch_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_material_batches_parent
  ON material_batches(parent_batch_id)
  WHERE parent_batch_id IS NOT NULL;

INSERT INTO schema_migrations (version, applied_at)
VALUES ('036_batch_parent', NOW())
ON CONFLICT (version) DO NOTHING;

COMMIT;
