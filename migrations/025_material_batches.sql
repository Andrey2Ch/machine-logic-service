-- PRD: Material batches, storage locations, and warehouse movements
-- 2026-02-03

-- 1) Material batches
CREATE TABLE IF NOT EXISTS material_batches (
    batch_id TEXT PRIMARY KEY,
    material_type TEXT,
    diameter NUMERIC(10,3),
    bar_length NUMERIC(10,3),
    quantity_received INTEGER,
    supplier TEXT,
    supplier_doc_number TEXT,
    date_received DATE,
    cert_folder TEXT,
    allowed_drawings TEXT[],
    preferred_drawing TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_by INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_material_batches_status ON material_batches(status);
CREATE INDEX IF NOT EXISTS idx_material_batches_supplier_doc ON material_batches(supplier_doc_number);

-- 2) Storage locations
CREATE TABLE IF NOT EXISTS storage_locations (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    capacity INTEGER,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_storage_locations_type ON storage_locations(type);
CREATE INDEX IF NOT EXISTS idx_storage_locations_status ON storage_locations(status);

-- 3) Addressing segments (admin-managed)
CREATE TABLE IF NOT EXISTS storage_location_segments (
    segment_type TEXT NOT NULL, -- RACK / LEVEL / BIN / SUB / ZONE
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (segment_type, code)
);

-- 4) Inventory positions
CREATE TABLE IF NOT EXISTS inventory_positions (
    batch_id TEXT NOT NULL REFERENCES material_batches(batch_id) ON DELETE CASCADE,
    location_code TEXT NOT NULL REFERENCES storage_locations(code) ON DELETE RESTRICT,
    quantity INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (batch_id, location_code)
);

-- 5) Warehouse movements
CREATE TABLE IF NOT EXISTS warehouse_movements (
    movement_id BIGSERIAL PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES material_batches(batch_id) ON DELETE CASCADE,
    movement_type TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    from_location TEXT REFERENCES storage_locations(code) ON DELETE SET NULL,
    to_location TEXT REFERENCES storage_locations(code) ON DELETE SET NULL,
    related_lot_id INTEGER REFERENCES lots(id) ON DELETE SET NULL,
    cut_factor INTEGER,
    performed_by INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    performed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes TEXT
);

ALTER TABLE warehouse_movements
    ADD CONSTRAINT check_movement_type
    CHECK (movement_type IN ('receive', 'move', 'issue', 'return', 'writeoff'));

CREATE INDEX IF NOT EXISTS idx_warehouse_movements_batch ON warehouse_movements(batch_id);
CREATE INDEX IF NOT EXISTS idx_warehouse_movements_type ON warehouse_movements(movement_type);
CREATE INDEX IF NOT EXISTS idx_warehouse_movements_time ON warehouse_movements(performed_at);
