-- Read-only Postgres role for ad-hoc data inspection (used by the
-- `DB Query (read-only)` GitHub Actions workflow / scripts/db_query.py).
--
-- Run this ONCE against the bot's database as a superuser/owner (e.g. from the
-- Railway "Data" tab query console, or `psql "$DATABASE_URL" -f this_file.sql`).
-- Replace 'CHANGE_ME_STRONG_PASSWORD' first.
--
-- The resulting role can SELECT from every current and future table but cannot
-- write, create, or alter anything. Build DATABASE_URL_RO from it:
--   postgresql://bot_readonly:CHANGE_ME_STRONG_PASSWORD@<host>:<port>/<db>

-- 1. The login role itself.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bot_readonly') THEN
    CREATE ROLE bot_readonly LOGIN PASSWORD 'CHANGE_ME_STRONG_PASSWORD';
  END IF;
END
$$;

-- 2. Connect + schema usage (no object creation).
GRANT CONNECT ON DATABASE current_database() TO bot_readonly;
GRANT USAGE ON SCHEMA public TO bot_readonly;

-- 3. SELECT on everything that exists today.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO bot_readonly;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO bot_readonly;

-- 4. SELECT on anything created later (e.g. after a new Alembic migration).
--    Note: default privileges apply to objects created by the role that runs
--    this. If migrations run as a different owner, re-run step 3 after they add
--    tables, or set FOR ROLE <migration_owner> below.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO bot_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON SEQUENCES TO bot_readonly;

-- 5. Belt-and-suspenders: make the role read-only at the session level too.
ALTER ROLE bot_readonly SET default_transaction_read_only = on;
