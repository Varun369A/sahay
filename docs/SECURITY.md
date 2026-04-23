# Sahay — Security Review (v1)

This file enumerates security controls in the v1 codebase, who enforces them,
and what's deferred. It is the checklist to walk before every release build.

## Threat model (one paragraph)

Sahay is a privileged Android app that uses the Accessibility Service to
inspect and act on other apps. The primary threats are:
1. **Cross-app information exfiltration** (someone tricks Sahay into leaking
   the contents of a banking app or messaging thread)
2. **Unauthorized destructive actions** (Sahay sends/posts/deletes without
   user intent)
3. **Cross-tenant data leakage** server-side
4. **Token theft / replay**
5. **Supply-chain compromise** of dependencies

The hard rules below are designed to make each of these strictly impossible
or hugely costly.

---

## Hard rules (non-negotiable)

| # | Rule | Where enforced |
|---|---|---|
| 1 | **Payment-app blocklist.** GPay/PhonePe/Paytm/banking — Sahay refuses to plan or execute actions there. | [`backend/.../domain/payment_blocklist.py`](../backend/src/sahay/domain/payment_blocklist.py) **AND** [`android/.../engine/PaymentBlocklist.kt`](../android/app/src/main/kotlin/io/sahay/engine/PaymentBlocklist.kt) — both checked on every plan AND every executor step. |
| 2 | **Confirmation guard.** Every step with `confirm_required=true` or under low reliability triggers a fullscreen [`ConfirmationActivity`](../android/app/src/main/kotlin/io/sahay/ui/screens/confirm/ConfirmationActivity.kt) showing what will happen. User must explicitly tap **Proceed**. | Android — `ActionRunner` |
| 3 | **Per-app allowlist.** Sahay does nothing in any app the user hasn't explicitly granted. Default = none. | Server `user_app_grants` table + Android settings UI |
| 4 | **Kill switch.** Volume-down 3× in 2s stops the foreground service immediately. | [`engine/KillSwitch.kt`](../android/app/src/main/kotlin/io/sahay/engine/KillSwitch.kt) wired in `SahayAccessibilityService.onKeyEvent`. |
| 5 | **On-device screen content.** Screen text/images NEVER leave the device. Server gets only `{app, intent, screen_state_hash, outcome}`. | [`ScreenTreeReader.summarize`](../android/app/src/main/kotlin/io/sahay/engine/ScreenTreeReader.kt) sanitises before any network send. |

---

## Authentication & authorization

- **Email-OTP only.** No passwords. 6-digit numeric, bcrypt-hashed at rest, 5-min TTL, max 5 attempts. ([`services/otp_service.py`](../backend/src/sahay/services/otp_service.py))
- **JWT access tokens** — HS256, 15-min TTL, contains `user_id` + `tier`.
- **Opaque refresh tokens** — 256 bits of entropy, SHA-256-hashed in DB, **rotated on every use**. Reuse triggers full revocation for that user. ([`services/jwt_service.py`](../backend/src/sahay/services/jwt_service.py))
- **OTP enumeration prevented** — signup and login return identical responses.
- **Burst limiter** — max 3 OTP requests per email per 10 min (DB) + Redis IP-rate-limit on top.
- **JWT secret** is `SecretStr`, loaded from env, never logged.

---

## Multi-tenancy & data isolation

- **Postgres Row-Level Security** on `users`, `refresh_tokens`, `user_app_grants`, `action_logs`, `usage_periods`, `files`, `subscriptions`. ([`infra/postgres-rls.sql`](../infra/postgres-rls.sql))
- Each request sets `app.user_id` via `SET LOCAL` from the JWT before any user-scoped query. ([`deps.py`](../backend/src/sahay/deps.py))
- App role does NOT have `BYPASSRLS`.
- Even with a SQL-injection bug, cross-tenant rows are unreachable.

---

## Network

- TLS-only enforced by Railway/Cloudflare.
- CORS — explicit origin list, no `*` in production.
- All cookies (when added) will be `Secure + HttpOnly + SameSite=Strict`.
- Rate limiting via Redis (per-user when authenticated, per-IP otherwise) on every public endpoint, with stricter limits on auth + image gen + delete-account.

---

## Storage

- **Cloudflare R2 bucket is private.** All access via presigned URLs (1-hour expiry by default).
- Files keyed under `users/{user_id}/...` for prefix-delete on right-to-erasure.
- **Android local storage** uses `EncryptedSharedPreferences` (Keystore-backed) for all tokens. Room DB will be wrapped with SQLCipher for any sensitive cache (in v1.1).
- `data_extraction_rules.xml` disallows cloud backup + device-transfer of secrets.
- `android:allowBackup="false"`.

---

## Privacy & compliance (DPDP / GDPR)

- **One-tap "Delete my account & data"** in [Settings](../android/app/src/main/kotlin/io/sahay/ui/screens/settings/SettingsScreen.kt) → server endpoint `/v1/me` (DELETE) cascades through Postgres + purges R2 prefix.
- Action logs contain **metadata only** — no message text, no contact names, no screen content.
- Per-user "compressed memory profile" (planned v1.1) is ~2KB and contains no PII.
- Full privacy policy template lives in [`docs/PRIVACY.md`](PRIVACY.md) — must be served at sahay.app/privacy before public launch.

---

## Secrets & supply chain

- All secrets via Railway env vars. `.env.example` is the only env file in the repo.
- Anthropic + Resend + Razorpay + Replicate + R2 keys stored as `SecretStr`; `.get_secret_value()` only at the point of use.
- ProGuard enabled in release; release APK is signed with a key NOT in the repo (passed via `-PSAHAY_KEYSTORE` etc).
- Dependencies pinned in `pyproject.toml` and `gradle/libs.versions.toml`. `pip-audit` and `gradle dependencyUpdates` should run weekly (CI to be added).

---

## Server-side input validation

- Every request body is a `pydantic` model with explicit constraints (max length, regex, enum).
- All Postgres queries via SQLAlchemy ORM — no string concatenation.
- File uploads capped at 25 MB; allowed MIME prefixes whitelisted; user folder path sanitised against `..`.
- Webhook signatures verified (Razorpay deferred to v2).

---

## Observability

- Structlog JSON logs in production, with `request_id` propagated through every log line.
- Sentry auto-wires when `SENTRY_DSN` is set (no PII via `send_default_pii=False`).
- Stack traces never returned in production responses (`is_production` check).

---

## Deferred to v1.1

- WAL audit log (immutable append-only of every action) — currently `action_logs` is mutable.
- SOC2 evidence collection (start day 90).
- Bug bounty program (start at 1k MAU).
- App Attest / Play Integrity check on the Android client (block rooted devices from sensitive flows).

---

## Pre-release checklist

Before every release build:

- [ ] All secrets confirmed in Railway, none committed to repo
- [ ] `ENABLE_DOCS=false` in production env
- [ ] `CORS_ORIGINS` set to actual domain(s), not `*`
- [ ] RLS policies applied (`psql -f infra/postgres-rls.sql`)
- [ ] `pip-audit` clean
- [ ] Dependency licenses reviewed
- [ ] APK built with release keystore (NOT debug)
- [ ] ProGuard rules tested (no runtime crashes from R8 stripping)
- [ ] Privacy policy + ToS live at the documented URLs
- [ ] Monitoring + alerts wired (Sentry, Railway logs, uptime)
