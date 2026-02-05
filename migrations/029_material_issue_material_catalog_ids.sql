-- Add material catalog links to issued materials (idempotent)
ALTER TABLE lot_materials
  ADD COLUMN IF NOT EXISTS material_group_id INTEGER NULL,
  ADD COLUMN IF NOT EXISTS material_subgroup_id INTEGER NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'fk_lot_materials_material_group'
  ) THEN
    ALTER TABLE lot_materials
      ADD CONSTRAINT fk_lot_materials_material_group
        FOREIGN KEY (material_group_id)
        REFERENCES material_groups(id)
        ON DELETE SET NULL;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'fk_lot_materials_material_subgroup'
  ) THEN
    ALTER TABLE lot_materials
      ADD CONSTRAINT fk_lot_materials_material_subgroup
        FOREIGN KEY (material_subgroup_id)
        REFERENCES material_subgroups(id)
        ON DELETE SET NULL;
  END IF;
END $$;
