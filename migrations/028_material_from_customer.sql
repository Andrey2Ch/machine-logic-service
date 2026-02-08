-- Add flag for customer-provided material batches
ALTER TABLE material_batches
    ADD COLUMN IF NOT EXISTS from_customer BOOLEAN NOT NULL DEFAULT FALSE;

INSERT INTO schema_migrations (version, applied_at)
VALUES ('028_material_from_customer', NOW())
ON CONFLICT (version) DO NOTHING;
