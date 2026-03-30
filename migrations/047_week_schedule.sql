-- Migration 047: Weekly production schedule
-- Коды: 0=выходной, 1=полный день, 2=короткий день (до 12:00), 3=только ночная смена

CREATE TABLE IF NOT EXISTS week_schedule (
    week_start  DATE PRIMARY KEY,  -- всегда воскресенье
    sun         SMALLINT NOT NULL DEFAULT 0 CHECK (sun  IN (0,1,2,3)),
    mon         SMALLINT NOT NULL DEFAULT 1 CHECK (mon  IN (0,1,2,3)),
    tue         SMALLINT NOT NULL DEFAULT 1 CHECK (tue  IN (0,1,2,3)),
    wed         SMALLINT NOT NULL DEFAULT 1 CHECK (wed  IN (0,1,2,3)),
    thu         SMALLINT NOT NULL DEFAULT 1 CHECK (thu  IN (0,1,2,3)),
    fri         SMALLINT NOT NULL DEFAULT 0 CHECK (fri  IN (0,1,2,3)),
    sat         SMALLINT NOT NULL DEFAULT 0 CHECK (sat  IN (0,1,2,3)),
    notes       TEXT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO schema_migrations (version, applied_at)
VALUES ('047', NOW())
ON CONFLICT (version) DO NOTHING;
