create or replace view batches_with_shifts as
select
  b.id                         as batch_id,
  b.batch_time,
  (b.initial_quantity - b.current_quantity) as produced,
  sj.id                        as setup_job_id,
  m.id                         as machine_id,
  m.name                       as machine_name,
  case
    when b.batch_time::time >= time '06:00' and b.batch_time::time < time '18:00' then 'day'
    else 'night'
  end as shift_name,
  case
    when b.batch_time::time >= time '06:00' and b.batch_time::time < time '18:00'
      then date_trunc('day', b.batch_time) + interval '6 hour'
    when b.batch_time::time >= time '18:00'
      then date_trunc('day', b.batch_time) + interval '18 hour'
    else
      date_trunc('day', b.batch_time - interval '1 day') + interval '18 hour'
  end as shift_start
from batches b
join setup_jobs sj on b.setup_job_id = sj.id
join machines m on sj.machine_id = m.id;

-- helper: last working day by machine name
-- select (max(shift_start))::date from batches_with_shifts where machine_name ilike '%SR-23%';

