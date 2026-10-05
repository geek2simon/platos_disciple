/*
  Plato's Disciple - kb_agent permissions

  Run this script as postgres (or another database owner/superuser)
  while connected to the pd_market_kb database.
*/

-- Create the application login role.
-- Replace the placeholder password before running this script.
CREATE ROLE kb_agent
WITH
    LOGIN
    PASSWORD 'YOUR_OWN_PASSWORD'
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOREPLICATION;

-- Allow kb_agent to connect to the database.
GRANT CONNECT ON DATABASE pd_market_kb TO kb_agent;

-- Allow access to objects in the public schema.
GRANT USAGE ON SCHEMA public TO kb_agent;

-- Grant access to all existing tables.
GRANT SELECT, INSERT, UPDATE, DELETE
ON ALL TABLES IN SCHEMA public
TO kb_agent;

-- Grant access to identity/serial sequences used by existing tables.
GRANT USAGE, SELECT
ON ALL SEQUENCES IN SCHEMA public
TO kb_agent;

-- Automatically grant permissions on future tables created by postgres.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO kb_agent;

-- Automatically grant permissions on future sequences created by postgres.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
GRANT USAGE, SELECT ON SEQUENCES TO kb_agent;

-- Verification: all four values should return true.
SELECT
    has_table_privilege('kb_agent', 'public.document', 'SELECT') AS can_select,
    has_table_privilege('kb_agent', 'public.document', 'INSERT') AS can_insert,
    has_table_privilege('kb_agent', 'public.document', 'UPDATE') AS can_update,
    has_table_privilege('kb_agent', 'public.document', 'DELETE') AS can_delete;
