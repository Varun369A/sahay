-- Sahay — schema reference (mirrors alembic 0001 + RLS policies).
-- DO NOT apply this directly to production — use `alembic upgrade head`
-- followed by `psql -f infra/postgres-rls.sql`.
-- This file exists for human reference + tooling that needs raw SQL.

-- See:
--   backend/alembic/versions/0001_initial_schema.py
--   infra/postgres-rls.sql
