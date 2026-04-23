-- Sahay — Postgres Row-Level Security policies
-- Applied AFTER `alembic upgrade head` on each environment.
--
-- Strategy:
--   • Backend connects as a dedicated app role (NOT superuser, NOT BYPASSRLS).
--   • Before each request, set `app.user_id` from the JWT subject:
--       SET LOCAL app.user_id = '<uuid>';
--   • Every user-scoped table has an RLS policy that restricts rows to
--     `user_id = current_setting('app.user_id')::uuid`.
--   • If `app.user_id` is unset, queries return zero rows (defense in depth).
--
-- This is enforced regardless of any application-layer bug, so cross-tenant
-- leakage is impossible without DB-level privilege escalation.

BEGIN;

-- ─── App role ────────────────────────────────────────────────────────
-- Run once per environment. Replace password.
-- CREATE ROLE sahay_app LOGIN PASSWORD 'replace-me' NOINHERIT;
-- GRANT CONNECT ON DATABASE sahay TO sahay_app;
-- GRANT USAGE ON SCHEMA public TO sahay_app;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO sahay_app;
-- GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO sahay_app;

-- Helper: return current tenant or NULL.
CREATE OR REPLACE FUNCTION current_user_id() RETURNS uuid
    LANGUAGE plpgsql
    STABLE
    AS $$
BEGIN
    RETURN NULLIF(current_setting('app.user_id', true), '')::uuid;
EXCEPTION WHEN others THEN
    RETURN NULL;
END;
$$;

-- ─── refresh_tokens ──────────────────────────────────────────────────
ALTER TABLE refresh_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE refresh_tokens FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS rls_refresh_tokens_self ON refresh_tokens;
CREATE POLICY rls_refresh_tokens_self ON refresh_tokens
    USING (user_id = current_user_id())
    WITH CHECK (user_id = current_user_id());

-- ─── user_app_grants ─────────────────────────────────────────────────
ALTER TABLE user_app_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_app_grants FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS rls_user_app_grants_self ON user_app_grants;
CREATE POLICY rls_user_app_grants_self ON user_app_grants
    USING (user_id = current_user_id())
    WITH CHECK (user_id = current_user_id());

-- ─── action_logs ─────────────────────────────────────────────────────
ALTER TABLE action_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE action_logs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS rls_action_logs_self ON action_logs;
CREATE POLICY rls_action_logs_self ON action_logs
    USING (user_id = current_user_id())
    WITH CHECK (user_id = current_user_id());

-- ─── usage_periods ───────────────────────────────────────────────────
ALTER TABLE usage_periods ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_periods FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS rls_usage_periods_self ON usage_periods;
CREATE POLICY rls_usage_periods_self ON usage_periods
    USING (user_id = current_user_id())
    WITH CHECK (user_id = current_user_id());

-- ─── files ───────────────────────────────────────────────────────────
ALTER TABLE files ENABLE ROW LEVEL SECURITY;
ALTER TABLE files FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS rls_files_self ON files;
CREATE POLICY rls_files_self ON files
    USING (user_id = current_user_id())
    WITH CHECK (user_id = current_user_id());

-- ─── subscriptions ───────────────────────────────────────────────────
ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscriptions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS rls_subscriptions_self ON subscriptions;
CREATE POLICY rls_subscriptions_self ON subscriptions
    USING (user_id = current_user_id())
    WITH CHECK (user_id = current_user_id());

-- ─── users ───────────────────────────────────────────────────────────
-- Self-only read; updates also self-only.
-- Auth router uses a privileged path (set_local app.user_id = <uuid>) to issue tokens.
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS rls_users_self ON users;
CREATE POLICY rls_users_self ON users
    USING (id = current_user_id())
    WITH CHECK (id = current_user_id());

-- ─── otp_challenges + playbooks ──────────────────────────────────────
-- Intentionally NOT RLS-protected:
--   • otp_challenges: pre-auth, no user_id at insertion time.
--     Access is gated by app logic + rate limiting.
--   • playbooks: shared library across all users.
-- Both should be reachable only from app role (not exposed externally).

COMMIT;
