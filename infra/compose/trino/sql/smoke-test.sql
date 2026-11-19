-- Lakehouse smoke test (make lakehouse-smoke-test). The Makefile drops the table afterwards.
drop table if exists iceberg.bronze.smoke_test;
create table iceberg.bronze.smoke_test (
  id integer,
  note varchar,
  created_at timestamp(6) with time zone
);
insert into iceberg.bronze.smoke_test values (1, 'hello lakehouse', current_timestamp);
select
  id,
  note,
  created_at
from iceberg.bronze.smoke_test;
-- One snapshot per commit (CREATE TABLE makes an empty one, without "added-*" keys).
-- "$snapshots" is an Iceberg metadata table, hence the special character.
select
  snapshot_id,
  operation,
  element_at(summary, 'added-data-files') as added_files,
  element_at(summary, 'total-records') as total_records
from iceberg.bronze."smoke_test$snapshots" -- noqa: RF05
order by committed_at;
