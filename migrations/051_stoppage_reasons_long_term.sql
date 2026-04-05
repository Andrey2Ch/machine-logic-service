-- 051: Add is_long_term flag to stoppage_reasons
--
-- Long-term reasons: no follow-up after operator reports them.
--   machine:          1 (setup), 4 (maintenance), 6 (electronics)
--   work_and_material: ALL (70-74) — material issues never resolve in minutes

BEGIN;

ALTER TABLE stoppage_reasons
    ADD COLUMN IF NOT EXISTS is_long_term BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN stoppage_reasons.is_long_term IS
    'TRUE = reason is unlikely to resolve within minutes; skip WhatsApp follow-up after recording.';

-- machine category: long-term codes
UPDATE stoppage_reasons SET is_long_term = TRUE
WHERE code IN (1, 4, 6);

-- work_and_material category: all codes are long-term
UPDATE stoppage_reasons SET is_long_term = TRUE
WHERE category = 'work_and_material';

INSERT INTO schema_migrations (version, applied_at)
VALUES ('051_stoppage_reasons_long_term', NOW())
ON CONFLICT (version) DO NOTHING;

COMMIT;
