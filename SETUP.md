# Sahay — Setup Guide

> Fill the two env files, start the backend, connect Android. 15 min.

---

## Files you fill in

1. [`backend/.env`](backend/.env) — backend secrets (DB, Redis, Resend, Anthropic, R2, Replicate)
2. [`android/local.properties`](android/local.properties) — Android API URL + Sarvam STT key

Both are gitignored. Safe to paste real secrets.

---

## Step-by-step

### 1. Database (Neon) — 2 min
1. Go to [neon.tech](https://neon.tech), sign up (GitHub login works).
2. Create project → name it `sahay` → region closest to you (India = ap-southeast-1).
3. On the dashboard, switch the **Connection string** dropdown to **"Pooled connection"**.
4. Copy it — it looks like `postgres://neondb_owner:xxx@ep-xxx.ap-southeast-1.aws.neon.tech/neondb?sslmode=require`.
5. Paste into `backend/.env` → `DATABASE_URL=` (the backend auto-rewrites `postgres://` to `postgresql+asyncpg://` on boot).

### 2. Redis (Upstash) — 2 min
1. Go to [upstash.com](https://upstash.com), sign up.
2. Create Database → Redis → pick "Global" (or your closest region) → Enable TLS.
3. On the detail page, scroll to **Connect to your database** → copy the **TLS** URL (starts with `rediss://`).
4. Paste into `backend/.env` → `REDIS_URL=`.

### 3. JWT secret — 10 seconds

Run one of these and paste the 64-char hex output into `JWT_SECRET=`:

- **Git Bash / WSL / Mac / Linux**: `openssl rand -hex 32`
- **Windows PowerShell**: `-join ((1..32) | % { '{0:x2}' -f (Get-Random -Max 256) })`
- **Python**: `python -c "import secrets; print(secrets.token_hex(32))"`

### 4. Resend (email OTP) — 2 min
1. Sign up at [resend.com](https://resend.com).
2. **API Keys** → create one with Full access → paste into `RESEND_API_KEY=`.
3. For now, leave `RESEND_FROM_EMAIL=Sahay <onboarding@resend.dev>` — it works out of the box for testing.
4. When ready to launch: go to **Domains** → add `sahay.app` (or your domain) → follow the DNS instructions → then change the from address to `Sahay <noreply@mail.sahay.app>`.

### 5. Anthropic (Claude) — 1 min
1. [console.anthropic.com](https://console.anthropic.com) → **API Keys** → Create Key.
2. Paste into `ANTHROPIC_API_KEY=` (starts with `sk-ant-`).
3. Add billing in **Settings → Billing** (pay-as-you-go is fine).

### 6. Cloudflare R2 (files) — 5 min
1. Log in to [dash.cloudflare.com](https://dash.cloudflare.com).
2. In the sidebar, click **R2**. If first time, subscribe (free tier = 10 GB).
3. **Create bucket** → name it `sahay-files` → accept defaults.
4. Copy your **Account ID** from the right sidebar of any page → `R2_ACCOUNT_ID=`.
5. In R2 → **Manage R2 API Tokens** → **Create API Token**:
   - Permissions: "Object Read & Write"
   - Specify bucket: `sahay-files`
   - TTL: leave blank
   - Click Create. **Copy BOTH values shown** (they won't show again):
     - Access Key ID → `R2_ACCESS_KEY_ID=`
     - Secret Access Key → `R2_SECRET_ACCESS_KEY=`

### 7. Replicate (image gen) — 1 min
1. [replicate.com](https://replicate.com) → sign up with GitHub.
2. Account → **API tokens** → copy → paste into `REPLICATE_API_TOKEN=` (starts with `r8_`).

### 8. Sarvam (Android only — voice STT) — 2 min
1. [sarvam.ai](https://sarvam.ai) → sign up.
2. Dashboard → API Keys → copy.
3. Paste into `android/local.properties` → `SAHAY_SARVAM_KEY=`.

---

## Boot it locally

### Backend

```bash
cd V:/claude/sahay/backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Mac/Linux:
source .venv/bin/activate

pip install -e .
alembic upgrade head
python -m scripts.seed_playbooks

uvicorn sahay.main:app --reload --host 0.0.0.0 --port 8080
```

Visit [http://localhost:8080/health](http://localhost:8080/health) — should return `{"status": "ok"}`.
Open [http://localhost:8080/docs](http://localhost:8080/docs) for the Swagger UI (disable in production via `ENABLE_DOCS=false`).

### Android

1. Open `V:/claude/sahay/android` in Android Studio.
2. Let Gradle sync (first run pulls deps, ~5 min).
3. Connect your phone via USB (enable USB debugging) OR start an emulator (API 26+).
4. Click Run ▶.
5. Grant the 3 permissions when prompted (mic, notifications, accessibility).
6. Hold the mic button and say: *"play Dune on Netflix"*.

If the app can't reach the backend, edit `android/local.properties`:
- Emulator → `SAHAY_API_BASE=http://10.0.2.2:8080`
- Real phone on same Wi-Fi as laptop → `SAHAY_API_BASE=http://<laptop-LAN-IP>:8080`

---

## Deploy backend to Railway (when ready)

1. [railway.app](https://railway.app) → New Project → "Deploy from GitHub repo".
2. Point at the `backend/` directory (or set root dir in Railway settings).
3. Add every variable from `backend/.env` in Railway → Variables tab.
4. Railway auto-detects the `Dockerfile` and builds.
5. First deploy runs `alembic upgrade head` automatically (set in `Dockerfile CMD`).
6. Once deployed, run the seed once from the Railway shell:
   ```
   python -m scripts.seed_playbooks
   ```
7. Update `android/local.properties` → `SAHAY_API_BASE=https://<your-railway-domain>`.

---

## Quick sanity checks

| Test | Expected |
|---|---|
| `curl http://localhost:8080/health` | `{"status":"ok"}` |
| `curl -X POST http://localhost:8080/v1/auth/signup -d '{"email":"you@example.com","locale":"en"}' -H "Content-Type: application/json"` | `{"message":"If the email is valid...","expires_in_seconds":300}` + an email arrives |
| In the email, use the OTP at `/v1/auth/verify-otp` → get JWT back |
| Use JWT → `GET /v1/me` returns your profile |
| `GET /v1/playbooks/popular` with JWT → returns 10 seeded playbooks |

---

## Common gotchas

- **"invalid DATABASE_URL"** — Neon gave you `postgres://...`? Don't worry — the backend auto-rewrites. But if it still fails, manually change the scheme to `postgresql+asyncpg://`.
- **OTP emails not arriving** — using `onboarding@resend.dev`, they go to the email in the signup request, but check spam. Resend free tier also has a daily cap.
- **Android "network error"** — check `SAHAY_API_BASE` in `local.properties`; emulator MUST use `10.0.2.2`, not `localhost`.
- **Accessibility grant not detected** — after enabling in Settings, come back to Sahay. The PermissionsScreen rechecks on resume.
- **R2 "SignatureDoesNotMatch"** — double-check you copied both Access Key ID and Secret from the SAME create-token flow (not mixed from two tokens).
