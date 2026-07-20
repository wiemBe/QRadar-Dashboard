-- Enabled before Alembic runs. Hypertable creation itself lives in migrations,
-- so the schema stays reproducible from Alembic alone.
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
