-- Read-only Postgres role for ad-hoc data inspection (used by the
-- `DB Query (read-only)` GitHub Actions workflow / scripts/db_query.py).
--
-- Run this ONCE against the bot's database as a superuser/owner. Easiest path:
-- Railway -> Postgres service -> "Console" tab (an interactive psql shell) ->
-- paste this in. Or: `psql "$DATABASE_URL" -f this_file.sql`.
--
-- Replace 'CHANGE_ME_STRONG_PASSWORD' with a password of your choosing first.
--
-- The resulting role can SELECT from every current and future table but cannot
-- write, create, or alter anything. Then build DATABASE_URL_RO from it (same
-- host/port/db as DATABASE_PUBLIC_URL, only the user:password changes):
--   postgresql://bot_readonly:CHANGE_ME_STRONG_PASSWORD@<host>:<port>/<db>

-- 1. The login role itself. (CONNECT is granted to PUBLIC by default, so the
--    role can connect without an explicit GRANT CONNECT.)
CREATE ROLE bot_readonly LOGIN PASSWORD 'CHANGE_ME_STRONG_PASSWORD';

-- 2. Let it use the schema and read every table that exists today.
GRANT USAGE ON SCHEMA public TO bot_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO bot_readonly;

-- 3. SELECT on anything created later (e.g. after a new Alembic migration).
--    Default privileges apply to objects created by the role running this; if
--    migrations run as a different owner, re-run step 2 after they add tables.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO bot_readonly;

-- 4. Belt-and-suspenders: make every session for this role read-only.
ALTER ROLE bot_readonly SET default_transaction_read_only = on;
