-- Таблица для runtime-capture SQL
create table if not exists text2sql_captured (
  id bigserial primary key,
  captured_at timestamptz not null default now(),
  duration_ms integer,
  is_error boolean default false,
  sql text not null,
  params_json jsonb,
  rows_affected integer,
  route text,
  user_id text,
  role text,
  source_host text
);

create index if not exists idx_text2sql_captured_time on text2sql_captured(captured_at);
create index if not exists idx_text2sql_captured_route on text2sql_captured(route);

