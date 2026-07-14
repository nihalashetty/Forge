-- Postgres Row-Level Security for Forge (defense-in-depth tenant isolation).
--
-- Forge already scopes every query by tenant_id (forge.db.scoping.tenant_scoped). RLS
-- makes that a DB-level guarantee: even a query that forgets the filter returns only the
-- current tenant's rows. Apply this AFTER `alembic upgrade head` on a Postgres database.
--
-- NOTE: the version-history, eval-history, and platform-hardening tables (entity_versions,
-- eval_runs, eval_results, api_keys, project_members, user_security) are created by Alembic
-- migration 0004 - run `alembic upgrade head` before applying this file.
--
-- Runtime: the application must set the tenant for each connection/transaction, e.g.
--   SET LOCAL app.current_tenant = '<tenant-uuid>';
-- (run it at the start of each request's DB transaction). With no setting, policies match
-- nothing, so a misconfigured connection is fail-closed.

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'users','projects','workflows','tools','agents','auth_providers','secrets',
    'kb_sources','qa_pairs','mcp_clients','threads','runs','traces','spans',
    'triggers','channels','handoff_requests','datasets','memories',
    'entity_versions','eval_runs','eval_results',
    'api_keys','project_members','user_security'
  ]
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY;', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY;', t);
    EXECUTE format($f$
      CREATE POLICY tenant_isolation ON %I
        USING (tenant_id = current_setting('app.current_tenant', true))
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true));
    $f$, t);
  END LOOP;
END $$;

-- audit_logs: tenant-isolated AND append-only (finding g). SELECT/INSERT/DELETE are permitted
-- (DELETE only serves the time-based retention purge in services/retention.py); there is
-- deliberately NO "FOR UPDATE" policy, so under FORCE RLS an UPDATE matches no policy and is
-- denied - existing audit records are immutable and can never be silently rewritten.
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON audit_logs;
DROP POLICY IF EXISTS audit_select ON audit_logs;
DROP POLICY IF EXISTS audit_insert ON audit_logs;
DROP POLICY IF EXISTS audit_delete ON audit_logs;
CREATE POLICY audit_select ON audit_logs FOR SELECT
  USING (tenant_id = current_setting('app.current_tenant', true));
CREATE POLICY audit_insert ON audit_logs FOR INSERT
  WITH CHECK (tenant_id = current_setting('app.current_tenant', true));
CREATE POLICY audit_delete ON audit_logs FOR DELETE
  USING (tenant_id = current_setting('app.current_tenant', true));
