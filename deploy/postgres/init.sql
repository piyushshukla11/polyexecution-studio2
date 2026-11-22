-- PolyExecution Studio 2 Disposable PostgreSQL Sandbox Initialization
-- Restricts permissions, locks down engine-level file system calls, and configures sandbox role

CREATE USER pes2_runner WITH PASSWORD 'pes2_sandbox_runner_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;

-- Create disposable sandbox database template
GRANT CONNECT ON DATABASE pes2_sandbox TO pes2_runner;

\c pes2_sandbox;

-- Restrict public schema permissions
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO pes2_runner;

-- Disable administrative system and file I/O functions for unprivileged runner role
REVOKE EXECUTE ON FUNCTION pg_read_file(text) FROM PUBLIC, pes2_runner;
REVOKE EXECUTE ON FUNCTION pg_read_file(text, bigint, bigint) FROM PUBLIC, pes2_runner;
REVOKE EXECUTE ON FUNCTION pg_read_binary_file(text) FROM PUBLIC, pes2_runner;
REVOKE EXECUTE ON FUNCTION pg_read_binary_file(text, bigint, bigint) FROM PUBLIC, pes2_runner;
REVOKE EXECUTE ON FUNCTION pg_ls_dir(text) FROM PUBLIC, pes2_runner;
