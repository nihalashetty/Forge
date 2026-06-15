-- Postgres Row-Level Security for Forge (defense-in-depth tenant isolation).
--
-- Forge already scopes every query by tenant_id (forge.db.scoping.tenant_scoped). RLS
-- makes that a DB-level guarantee: even a query that forgets the filter returns only the
-- current tenant's rows. Apply this AFTER `alembic upgrade head` on a Postgres database.
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
    'audit_logs','triggers','channels','handoff_requests','datasets','memories'
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
