-- Roles and schemas of the three services (A1.2). Run by the stand administrator (POSTGRES_USER).
-- PostgreSQL runs this file on the first start of an empty volume (/docker-entrypoint-initdb.d).
-- It is idempotent: the tests apply it before every session, and a stand created before A1.2 gets it with
--   docker compose -f deploy/compose.yaml exec postgres psql -v ON_ERROR_STOP=1 -U gorky -d gorky -f /docker-entrypoint-initdb.d/10-roles.sql
--
-- Each service logs in as its own role and owns exactly one schema; it has no rights in the others.
-- Test stand only: every password equals the role name.

SET client_min_messages = warning;

DO $roles$
DECLARE
    service record;
    owned record;
BEGIN
    EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database());
    REVOKE ALL ON SCHEMA public FROM PUBLIC;

    FOR service IN
        SELECT * FROM (VALUES
            ('gorky_cabinet', 'cabinet'),
            ('gorky_grading', 'grading'),
            ('gorky_references', 'refs')
        ) AS services (roleName, schemaName)
    LOOP
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = service.roleName) THEN
            EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', service.roleName, service.roleName);
        END IF;
        EXECUTE format(
            'ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
            service.roleName
        );
        EXECUTE format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO %I', current_database(), service.roleName);
        EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I AUTHORIZATION %I', service.schemaName, service.roleName);
        EXECUTE format('ALTER SCHEMA %I OWNER TO %I', service.schemaName, service.roleName);
        EXECUTE format('REVOKE ALL ON SCHEMA %I FROM PUBLIC', service.schemaName);

        -- A stand migrated before A1.2 has tables owned by the administrator: hand them to the service.
        FOR owned IN
            SELECT c.relname, c.relkind
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = service.schemaName
              AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
              AND pg_get_userbyid(c.relowner) <> service.roleName
              AND NOT EXISTS (
                  SELECT FROM pg_depend d
                  WHERE d.objid = c.oid AND d.classid = 'pg_class'::regclass AND d.deptype IN ('a', 'i')
              )
        LOOP
            EXECUTE format(
                'ALTER %s %I.%I OWNER TO %I',
                CASE owned.relkind WHEN 'S' THEN 'SEQUENCE' WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW' ELSE 'TABLE' END,
                service.schemaName, owned.relname, service.roleName
            );
        END LOOP;
    END LOOP;
END
$roles$;
