-- Runs once on a fresh volume, as the postgres superuser, in database "app".
-- App roles are NOT superusers, so they can't use the reserved slots: connection exhaustion is real for them.
CREATE ROLE app LOGIN PASSWORD 'app';
CREATE ROLE reporting LOGIN PASSWORD 'reporting';  -- the runaway job in chaos scenario 5
GRANT ALL ON DATABASE app TO app;
GRANT CONNECT ON DATABASE app TO reporting;
CREATE TABLE IF NOT EXISTS jobs (id SERIAL PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL);
ALTER TABLE jobs OWNER TO app;
