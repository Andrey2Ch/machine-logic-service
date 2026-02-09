-- Add price fields for material batches (ILS)
ALTER TABLE material_batches
  ADD COLUMN IF NOT EXISTS price_per_meter_ils NUMERIC(12,4),
  ADD COLUMN IF NOT EXISTS price_per_kg_ils NUMERIC(12,4);

INSERT INTO schema_migrations (version, applied_at)
VALUES ('031_material_batch_prices', NOW())
ON CONFLICT (version) DO NOTHING;
