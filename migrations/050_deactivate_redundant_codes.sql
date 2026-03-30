-- 050: Deactivate stoppage reason codes that duplicate MTConnect statuses
-- Code 1 (machine setup) is tracked via setupStatus in MTConnect
-- Codes 70-71 (production start/end) are tracked via uiMode
-- Code 1 is now reserved for follow-up "yes, fixed" reply

BEGIN;

UPDATE stoppage_reasons
SET is_active = false
WHERE code IN (1, 70, 71);

INSERT INTO schema_migrations (version, applied_at)
VALUES ('050_deactivate_redundant_codes', NOW())
ON CONFLICT (version) DO NOTHING;

COMMIT;
