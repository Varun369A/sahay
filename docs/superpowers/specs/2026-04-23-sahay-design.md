# Sahay — Design Spec

**Date:** 2026-04-23
**Status:** Approved for implementation
**Owner:** Varun (varunkrissss@gmail.com)

---

## 1. Problem

Humans in 2026 are getting lazier. Daily digital chores — replying to WhatsApp, reading emails, posting on socials, navigating apps — pile up. Existing AI assistants (Siri, Google Assistant, Alexa, Pi, Rabbit, Humane Pin) all failed to deliver because they tried to do too much without trust, reliability, or distribution.

Computer-use agents (Anthropic Computer Use, OpenAI Operator, Manus) achieve only ~70% reliability through trial-and-error vision — unusable for consumers.

## 2. Solution

**Sahay** — Android-first voice-driven personal assistant with a novel architecture: **doc-grounded playbooks**. For each supported app, Sahay ingests the app's official user guide and generates a structured action playbook. At runtime, the agent executes the playbook step-by-step against the live accessibility tree. Vision is fallback only.

This pushes reliability toward 95% on common flows and creates a compounding moat — every playbook works for every user; the library grows over time.

## 3. Scope (v1)

**In scope:**
- Android app (sideload APK first, Play Store after)
- Voice-driven (Tamil, Hindi, English at launch)
- 10 supported apps at launch: WhatsApp, Gmail, YouTube, Netflix, Calendar, Maps, Notes, LinkedIn, Instagram, system Settings
- Workflow automation (recurring tasks: "every Monday 9am post LinkedIn")
- File management + image generation + image edit (R2-backed)
- Email-OTP signup (no password)
- Claude-style usage tiers (free / pro / power) with pay-as-you-go overage

**Out of scope (v1):**
- iOS (Apple does not allow this architecture; reconsider in year 2)
- Payment apps (GPay, PhonePe, Paytm, banking — hard-blocked)
- Video generation
- Multi-device sync (single device per user in v1)
- Team/collaboration features

## 4. Architecture

```
┌─ ANDROID APP (Kotlin) ───────────────────────────────┐
│  UI (Compose) + Voice IO (Sarvam STT) + AccessibilityService │
│  Local memory (Room + SQLCipher) + EncryptedSharedPrefs       │
│  Sahay Core Engine: intent parser, playbook executor,         │
│  screen tree reader, confirmation guard, payment blocklist    │
└──────────────────────┬───────────────────────────────────────┘
                       │ HTTPS — only abstract intents, never screen content
┌─ BACKEND (Railway, FastAPI) ─────────────────────────┐
│  /auth  /users  /playbooks  /actions  /usage  /billing  /files │
└──┬───────┬──────────┬───────────┬───────────┬─────────────────┘
   ▼       ▼          ▼           ▼           ▼
 Neon    Anthropic  Resend     Cloudflare  Upstash
Postgres  Claude    (email)       R2        Redis
```

### Privacy boundary

Screen content (messages, contacts, photos, screenshots) never leaves the device. Backend receives only:

- `intent` (e.g., `send_whatsapp_message`)
- `app_package`
- `screen_state_hash`
- `outcome` (success/failed/aborted)

Per-user "compressed memory profile" lives in the cloud (~2 KB, no PII) — generated nightly via batched Claude Opus calls. Used for personalized planning without re-sending content.

## 5. Tech stack

| Layer | Choice |
|---|---|
| Android language | Kotlin |
| Android UI | Jetpack Compose + Material 3 |
| Android local DB | Room + SQLCipher |
| Speech-to-text | Sarvam Speech API (multilingual) |
| Text-to-speech | Android TTS (free), ElevenLabs (Pro tier) |
| Backend | Python 3.12 + FastAPI |
| Backend host | Railway |
| Database | Neon Postgres (with row-level security) |
| Cache + rate limit | Upstash Redis |
| File storage | Cloudflare R2 |
| Email (OTP) | Resend |
| LLM — default | Claude Haiku 4.5 |
| LLM — quality | Claude Sonnet 4.6 |
| LLM — ingestion + nightly | Claude Opus 4.7 (batched) |
| Image generation | Replicate (Flux Schnell) |
| Auth | JWT (access 15min) + refresh (30 days, rotated) |
| Billing | Razorpay (India launch); Stripe later |
| Observability | Sentry |
| CI/CD | GitHub Actions → Railway |

## 6. Claude model strategy

| Workload | Model | Notes |
|---|---|---|
| Voice → intent parsing | Haiku 4.5 | Sub-second, JSON output |
| Action planning (known playbook, reliability > 0.9) | Haiku 4.5 | Cheap, common case |
| Action planning (unknown screen, vision fallback) | Sonnet 4.6 | Quality matters |
| Tone-aware reply drafting | Sonnet 4.6 | User notices bad tone |
| Confirmation summaries | Haiku 4.5 | Trivial |
| Playbook ingestion (one-time per app) | Opus 4.7 | Quality-critical |
| Nightly compressed memory generation | Opus 4.7 (batched, 50% off) | Async, batched |

**Cost discipline:** prompt caching mandatory; default Haiku; escalate by playbook reliability score.

## 7. Database schema

See `infra/postgres-schema.sql` (canonical). Highlights:

- `users` — email, locale, tier, verified flag
- `otp_challenges` — bcrypt-hashed code, 5-min expiry, max 5 attempts
- `refresh_tokens` — rotated on every refresh
- `playbooks` — per-app per-intent JSON, with reliability score, payment-app flag
- `user_app_grants` — explicit per-app authorization
- `action_logs` — metadata only, never content
- `usage_periods` — daily/weekly/monthly action + token + cost
- `files` — R2-backed user files
- `subscriptions` — Razorpay subscription state

**Multi-tenancy:** Postgres row-level security on all user-data tables. JWT user_id set as session variable; queries auto-filtered.

## 8. API surface (v1)

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/auth/signup` | Send OTP for new account |
| POST | `/v1/auth/login` | Send OTP for existing account |
| POST | `/v1/auth/verify-otp` | Verify code → JWT + refresh |
| POST | `/v1/auth/refresh` | Rotate refresh token |
| POST | `/v1/auth/logout` | Revoke refresh token |
| GET | `/v1/me` | Profile + tier + usage |
| PATCH | `/v1/me` | Update locale, name |
| POST | `/v1/playbooks/lookup` | Fetch playbook for app + intent |
| POST | `/v1/playbooks/contribute` | Suggest new app to ingest (admin-gated) |
| GET | `/v1/playbooks/popular` | Top playbooks |
| POST | `/v1/actions/plan` | Get next-step plan (enforces tier + blocklist) |
| POST | `/v1/actions/log` | Telemetry (success/fail/duration) |
| GET | `/v1/usage` | Current period vs tier limit |
| GET | `/v1/billing` | Subscription state |
| POST | `/v1/billing/upgrade` | Razorpay checkout |
| POST | `/v1/billing/webhook` | Razorpay webhook (signature-verified) |
| POST | `/v1/files/upload` | Multipart upload |
| POST | `/v1/files/generate-image` | Replicate Flux |
| PATCH | `/v1/files/:id/edit` | Edit existing image |
| GET | `/v1/files` | List by folder |

All authenticated endpoints require `Authorization: Bearer <jwt>`. Backend enforces tier on every call.

## 9. Tier + usage model

| Tier | Monthly | Daily actions | Weekly | Apps | Voice | Images |
|---|---|---|---|---|---|---|
| Free | ₹0 | 50 | 200 | 5 | EN only | 5/mo |
| Pro | ₹299 | 500 | 2,500 | All | Multi | 100/mo |
| Power | ₹999 | 2,000 | 10,000 | All + family | Multi + clone | 500/mo |
| Pay-as-you-go (over limit) | — | — | — | — | — | ₹0.50/action, ₹2/image |

Email warning at 80% of limit. Push notification + in-app prompt at 100%.

## 10. UI/UX

5 screens (Material 3, indigo `#3F3D9C` + cream `#FAF6E9` + saffron `#F4A340`):

1. **Onboarding** — email → OTP → permissions (accessibility, notifications, mic)
2. **Home** — large mic button (250 dp), recent commands, suggestions
3. **Logs** — action history with success/fail, undo where possible
4. **Files** — folders + generated/edited images
5. **Settings** — apps allowed, language, voice, billing, privacy, kill switch

**Confirmation modal** intercepts every destructive action with preview (e.g., contact profile pic before sending message).

## 11. Security & safety

**Hard rules:**
1. Payment-app blocklist (hardcoded)
2. Confirmation guard for all destructive intents
3. Kill switch (volume-down 3x in 2s)
4. Per-app allowlist (default = none)

**Standard:**
- JWT 15-min expiry; refresh rotation
- OTP: 6 digits, 5-min expiry, max 5 attempts, bcrypt-hashed
- Rate limiting via Upstash Redis
- TLS everywhere; AES-256 at rest; SQLCipher on Android
- Postgres RLS as defense-in-depth
- Razorpay webhook signature verification
- Secrets via Railway env vars; `.env.example` only in repo

**Compliance:** India DPDP + GDPR-ready. Privacy policy + ToS before public launch.

## 12. Implementation roadmap

| Week | Backend | Android | Validation |
|---|---|---|---|
| 1 | Repo, FastAPI skeleton, Postgres schema, alembic | AS project, Compose nav, design tokens | — |
| 2 | Auth: signup + OTP + JWT + refresh | Onboarding, login flow, secrets storage | Doc-grounding spike (kill criteria) |
| 3 | Playbook RAG ingestor for WhatsApp | AccessibilityService skeleton, ScreenTreeReader | First playbook works |
| 4 | Action planner + usage metering | PlaybookExecutor, ConfirmationGuard, Home | WhatsApp send-message E2E |
| 5 | 4 more playbooks | Voice IO (Sarvam), Logs screen | 5 apps working |
| 6 | Billing scaffolding (Razorpay), tier limits | Settings, app allowlist, kill switch | Tier enforcement live |
| 7 | Files endpoints + R2 + Replicate | Files screen, image edit | Image generated, edited, saved |
| 8 | Razorpay subscription + webhook + overage | Billing screen, upgrade flow | First test payment |
| 9 | Hardening: rate limits, RLS, Sentry | Bug bash, accessibility audit | 50-user closed beta |
| 10 | Top 10 failure fixes | Iterate on most-broken flows | 90% reliability on top intents |
| 11 | Privacy/ToS, deletion endpoint | Onboarding polish, App store assets | Legal sign-off |
| 12 | Production hardening, monitoring | APK signed, Play Store submission | Public launch |

## 13. Validation gate (week 2 — kill criteria)

Before committing further engineering, prove the doc-grounding thesis:

1. Build minimal Computer Use loop with Anthropic Computer Use API
2. Run 100 WhatsApp `send_message` tests **without** doc grounding → measure success
3. Run same 100 tests **with** WhatsApp Help Center docs in context → measure success
4. **If +15pp improvement: proceed. If not: rethink architecture before coding more.**

## 14. Open questions

- [ ] Razorpay vs PhonePe Payment Gateway for India billing — which has better webhook reliability?
- [ ] Sarvam Speech vs Whisper for STT — Sarvam better for Indian languages, Whisper for English
- [ ] Per-app reliability scoring algorithm — moving average vs Bayesian update?
- [ ] Community playbook contribution — gate on reputation, or staff-review only at launch?
- [ ] Kill switch — volume-down 3x is the proposed gesture; alternatives?

## 15. Out of v1, on roadmap

- iOS (year 2 — Shortcuts-based limited version)
- Payment apps (year 2 — partner with one bank, deep integration, never a generic agent)
- Browser extension companion (year 1 Q4)
- Multi-device sync
- Team accounts
- API for third-party developers (publish playbook contribution SDK)
- Self-hosted enterprise edition

---

## Appendix A — Folder structure

```
sahay/
├── README.md
├── .gitignore
├── android/
│   └── app/src/main/kotlin/io/sahay/
├── backend/
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── railway.toml
│   ├── .env.example
│   ├── alembic/
│   │   └── versions/
│   ├── tests/
│   └── src/sahay/
│       ├── main.py
│       ├── config.py
│       ├── deps.py
│       ├── routers/
│       ├── services/
│       ├── db/
│       ├── domain/
│       └── util/
├── infra/
│   ├── postgres-schema.sql
│   └── postgres-rls.sql
└── docs/
    └── superpowers/
        └── specs/
            └── 2026-04-23-sahay-design.md  ← this file
```

## Appendix B — Decision log

| Date | Decision | Reason |
|---|---|---|
| 2026-04-23 | Android-first, iOS later | Apple disallows accessibility-based app control |
| 2026-04-23 | Hard-block payment apps in v1 | Liability + reliability risk too high |
| 2026-04-23 | Doc-grounded playbooks (vs pure vision) | Reliability thesis — must validate week 2 |
| 2026-04-23 | Email-OTP only (no password) | Simpler UX, fewer attack surfaces |
| 2026-04-23 | Razorpay first | India-first launch |
| 2026-04-23 | Haiku 4.5 default | Cost discipline; escalate by reliability |
| 2026-04-23 | On-device screen processing | Privacy is the brand |

## Appendix C — Competitor failure analysis & Sahay countermeasures

Every prior attempt at "Jarvis for everyone" failed in instructive ways. Each Sahay design choice below maps directly to a failure we are NOT going to repeat.

| Company | What they shipped | Why it failed | Sahay's countermeasure |
|---|---|---|---|
| **Rabbit R1** ($30M raised) | Hardware device, "Large Action Model" | LAM was vaporware (hardcoded Playwright scripts for ~5 services). Apps changed UI → broke. No real OAuth. Hardware was unnecessary. | **No hardware.** Use existing Android. **Doc-grounded playbooks** with self-heal (every failure adds an exception → playbook updates). **Accessibility tree** (visible to OS) instead of screen-scraping. |
| **Humane AI Pin** ($240M, sold for parts) | Wearable, voice-only | Hardware overheated. Battery dead in hours. Voice-only = no preview UX = no trust for payments. $700 + $24/mo. 2–5s latency. | **Phone screen IS the confirmation surface.** Destructive actions show preview modal with profile pic. Sub-second latency target. ₹299/mo, no hardware cost. |
| **Inflection Pi** ($1.5B, sold to Microsoft) | Conversational chatbot | No actions, just chat. Indistinguishable from ChatGPT. No retention loop. No revenue path. | **Action-first, not chat-first.** Daily-use loops baked in (recurring posts, daily brief, inbox triage). Tier-based revenue from day 1. |
| **Adept ACT-1** | Web agent, hand-crafted demos | Tried to be horizontal (any web app). 60–70% reliability. Hand-crafted didn't scale. | **Vertical** = consumer Android. **Auto-generated playbooks** from official app docs (scales to 1000+ apps). **Per-app reliability score** gates auto-execution; <90% requires user step-confirm. |
| **Anthropic Computer Use** | Pure vision API | Slow (~30s/action). Expensive (full screenshot tokens). Research-grade reliability. | **Accessibility tree first** (text, not pixels) — 100x cheaper, 10x faster. Vision via Claude only as fallback when tree is empty (canvas apps). |
| **OpenAI Operator** | Browser-only web agent | Browser-only. Slow. $200/mo enterprise pricing. | **Native Android** = where users live. **Haiku-default** model strategy = sub-rupee per action. |
| **Manus** | General-purpose AI agent | Viral demos, weak retention. Cherry-picked successes. No daily-use loop. | **Optimize for daily flows** (WhatsApp reply, LinkedIn post, Netflix search) NOT impressive one-off demos. **Action logs** show real reliability per intent — transparency builds trust. |
| **Apple Siri** (18 years, infinite budget) | OS-native assistant | Bad NLU. Limited to Apple apps. Never multilingual properly. Even Apple's own apps fail. | **Claude as the brain** (best reasoning, multilingual native). **Cross-app** via accessibility. **Sarvam STT** for Indic languages from day 1. |
| **Google Assistant / Gemini** (4 attempts) | OS-native | Mediocre cross-app control. Privacy concerns. Never personalized. | **Per-user compressed memory profile** (~2KB) personalises without leaking content. **Open-source on-device portion** for transparency. |
| **Alexa** (10+ years, billions invested) | Voice assistant | Limited to smart-home + Amazon shopping. No screen → no trust for complex actions. | Phone screen + voice + accessibility = full surface. **Hard payment blocklist** = users trust you more, not less. |

### The 7 design rules that fall out of this analysis

1. **No hardware.** Every hardware-first attempt died. Phone is enough.
2. **Doc-grounded, not vision-grounded.** Vision-only means 70% reliability forever.
3. **Accessibility tree first, vision fallback.** 100× cost difference.
4. **Always-confirm destructive actions.** Voice without preview = trust cliff.
5. **Hard payment blocklist.** Don't try to be everything; be trusted at *what you do*.
6. **Per-app reliability scoring.** Don't ship a flow until it's >90% reliable. Ship transparency on the rest.
7. **Daily-use loops, not demo magic.** Retention beats virality.

These rules are non-negotiable design constraints for v1.
