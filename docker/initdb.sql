-- Local development only. Passwords here are throwaway values.
--
-- Two roles, neither a superuser (superusers bypass row-level security):
--   counselvault_owner  owns the tables and runs migrations
--   counselvault_app    what the API and worker connect as; it cannot change
--                       the schema, policies or triggers
CREATE ROLE counselvault_owner LOGIN CREATEDB PASSWORD 'owner-dev-password';
CREATE ROLE counselvault_app LOGIN PASSWORD 'app-dev-password';
CREATE DATABASE counselvault OWNER counselvault_owner;

-- Used only by the optional local streaming replica (docker compose --profile replica).
CREATE ROLE replicator LOGIN REPLICATION PASSWORD 'replicator-dev-password';
