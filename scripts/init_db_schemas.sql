-- Per-database setup, included by init_db.sql after \connect.
-- Tables are created later by scripts/reset_db.py as the owner role; the default
-- privileges below give the app role DML on them automatically.

SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database()) \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'app_user') \gexec

SET ROLE :owner_user;
CREATE SCHEMA IF NOT EXISTS clinical;
CREATE SCHEMA IF NOT EXISTS platform;
CREATE SCHEMA IF NOT EXISTS knowledge;
RESET ROLE;

GRANT USAGE ON SCHEMA clinical, platform, knowledge TO :app_user;

ALTER DEFAULT PRIVILEGES FOR ROLE :owner_user IN SCHEMA clinical, platform, knowledge
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :app_user;
ALTER DEFAULT PRIVILEGES FOR ROLE :owner_user IN SCHEMA clinical, platform, knowledge
    GRANT USAGE, SELECT ON SEQUENCES TO :app_user;
