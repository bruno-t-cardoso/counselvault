-- Local development only.
-- The app connects as a NON-superuser role: superusers bypass row-level security.
CREATE ROLE counselvault LOGIN CREATEDB PASSWORD 'counselvault';
CREATE DATABASE counselvault OWNER counselvault;
