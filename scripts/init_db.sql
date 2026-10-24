-- One-time PostgreSQL setup. Run as a superuser through scripts/init_db.ps1,
-- which passes the role passwords from .env as psql variables.
-- Safe to re-run: every statement is idempotent.

\set ON_ERROR_STOP on

-- Owner role: owns all tables, used only by setup scripts and tests.
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'owner_user', :'owner_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'owner_user') \gexec
SELECT format('ALTER ROLE %I PASSWORD %L', :'owner_user', :'owner_password') \gexec

-- App role: used by the running API, gets only the privileges granted below.
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'app_user', :'app_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'app_user') \gexec
SELECT format('ALTER ROLE %I PASSWORD %L', :'app_user', :'app_password') \gexec

SELECT format('CREATE DATABASE %I OWNER %I', :'db_name', :'owner_user')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'db_name') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'test_db_name', :'owner_user')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'test_db_name') \gexec

\connect :db_name
\ir init_db_schemas.sql

\connect :test_db_name
\ir init_db_schemas.sql
