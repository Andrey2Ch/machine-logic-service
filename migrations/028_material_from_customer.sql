-- Add flag for customer-provided material batches
ALTER TABLE material_batches
    ADD COLUMN IF NOT EXISTS from_customer BOOLEAN NOT NULL DEFAULT FALSE;
