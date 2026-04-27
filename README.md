# Sahay

> Voice-driven personal assistant for Android that learns to operate any app from its official user guide and acts on your behalf — except where money is involved.

## What it is

You speak in any language ("send Amirtha a message," "post my LinkedIn update," "search Netflix for Dune"). Sahay opens the right app, taps the right buttons, types the right words — like a human would — and shows you a confirmation screen before anything irreversible happens.

**Sahay never touches payment apps.** GPay, PhonePe, Paytm, banking apps are hard-blocked at four layers: the device foreground check, the OS-level launch intent, the agent system prompt, and the playbook ingestor's refusal envelope.

## Architectural thesis

Most computer-use agents (Anthropic Computer Use, OpenAI Operator, Manus, Adept) learn through trial-and-error vision. Reliability sits at ~70% — unusable for consumer.

Sahay's wedge: **doc-grounded playbooks.** For every app, Sahay ingests the app's official user guide and generates a structured playbook (steps → screens → actions). At runtime, the playbook tells the agent exactly what to do; vision is only the fallback. Pushes reliability toward 95% on common flows.

Each playbook is built once per app and shared across all users (privacy boundary below). The library compounds — month 12 has 1,000 apps playbooked, all auto-tuning.

## The reliability layer

Doc-grounded playbooks alone aren't enough — apps drift, screens change, dialogs interrupt. Sahay closes the loop with a six-stage learning system. Every stage is independently observable and ships with telemetry the admin dashboard reads.

```
1.  Verify           Every step has an `expect` post-condition the
                     runtime polls before advancing. "The tap returned
                     true" is no longer mistaken for "the tap did the
                     right thing."  (engine/StepVerifier.kt)

2.  Score            Bayesian reliability per (app, intent, app_version)
                     with Laplace prior 0.5. Closed loop on every run;
                     per-step verifier failures dedicated counter.
                     (services/reliability_tracker.py)

3.  Classify         Failed runs labelled into 10 structured classes
                     (selector_stale, dialog_appeared, timing_issue,
                     ime_issue, gesture_rejected, …). Pure rule-based
                     on the device's failure_reason; LLM fallback for
                     unknown.  (services/failure_classifier.py)

4.  Improve          Nightly Opus 4.7 cron reads failure aggregates
                     for playbooks that dropped below 0.7, emits
                     patched specs as PlaybookRevisions.
                     (services/playbook_improver.py)

5.  Canary route     Sticky 10% traffic bucket per user (sha256 mod 100),
                     24h window, parent_score_baseline frozen at canary
                     creation so promotion math stays fair.
                     (services/canary_router.py)

6.  Auto-promote     Decider compares canary Bayesian score vs baseline.
                     +0.05 → promote, -0.03 → rollback, else → hold.
                     (services/canary_decider.py)
```

A failed run becomes structured signal (1+2+3) becomes a candidate patch (4) becomes routed traffic (5) becomes a measured promotion (6). No human in the loop.

## Other reliability features

- **Stuck detection → Opus rescue.** Same physical action twice in a row escalates ONE turn to Opus 4.7 with a focused notice and temperature bump. Audit log carries a `stuck_rescue` flag for measurement. ([agent_engine.py](backend/src/sahay/services/agent_engine.py))
- **Idempotency keys on irreversible actions.** SHA-256 keyed, 5-minute TTL, scoped per conversation. A network blip during `reply_to_notification` cannot cause a double-send. ([engine/IdempotencyTracker.kt](android/app/src/main/kotlin/io/sahay/engine/IdempotencyTracker.kt))
- **Confidence-floor tap fallback.** The "best-guess clickable" finder requires ≥2 distinct keywords + ≥1-keyword lead over the runner-up, and is disabled entirely on `confirm_required` steps. ([engine/PlaybookExecutor.kt](android/app/src/main/kotlin/io/sahay/engine/PlaybookExecutor.kt))
- **Acoustic context guard for wake word.** Blocks wake triggers during phone calls, VoIP, ringtone, or active music output. Auto-tunes to STRICT mode (final-recognition-only) when the device's measured false-positive rate ≥ 30%. ([voice/AcousticContext.kt](android/app/src/main/kotlin/io/sahay/voice/AcousticContext.kt) + [voice/WakeTelemetry.kt](android/app/src/main/kotlin/io/sahay/voice/WakeTelemetry.kt))

## Privacy boundary (non-negotiable, code-enforced)

- **Screenshots are OFF by default.** The agent works from the Android accessibility tree (UI labels, button text, view hierarchy) alone. Screen pixels never leave the device unless the user explicitly opts in via Settings → Privacy → "Send screenshots to assist". ([data/secrets/UserPreferences.kt:36](android/app/src/main/kotlin/io/sahay/data/secrets/UserPreferences.kt))
- **Even when opted in, screenshots are blocked for payment/banking apps.** Hard-coded `PaymentBlocklist` check in the agent loop before any upload.
- **PII is tokenized before it reaches Claude.** Phone numbers, emails, OTPs, UPI IDs, Aadhaar, PAN, card numbers are replaced with stable tokens (`<PHONE_0>`, `<EMAIL_2>`, …) by an on-server firewall. Claude reasons about entities, never raw values.
- Per-user compressed memory profile (~2KB, no PII).
- All local data encrypted at rest (SQLCipher).
- One-tap data deletion (DPDP + GDPR compliant).

## Repo layout

```
sahay/
├── backend/        FastAPI service deployed to Railway
│   ├── src/sahay/
│   │   ├── services/    reliability, classifier, improver, canary,
│   │   │                dashboard, agent engine, action planner
│   │   ├── routers/     REST endpoints (auth, playbooks, actions,
│   │   │                agent, dashboard, …)
│   │   ├── domain/      pure-Python action / expectation vocabularies
│   │   │                (single source of truth for wire formats)
│   │   └── db/          SQLAlchemy models + RLS policies
│   ├── alembic/         migrations 0001..0008
│   ├── scripts/         improve_playbooks, decide_canaries,
│   │                    seed_playbooks
│   └── tests/           164 unit tests
├── android/        Kotlin + Jetpack Compose app
│   └── app/src/main/kotlin/io/sahay/
│       ├── engine/      ActionRunner, PlaybookExecutor, AgentLoop,
│       │                StepVerifier, IdempotencyTracker, action enum
│       ├── voice/       AcousticContext, WakeTelemetry, Sarvam STT,
│       │                ElevenLabs TTS
│       ├── service/     accessibility, wake-word, notification listener
│       └── ui/          Compose screens
├── infra/          DB migrations, RLS SQL, ops scripts
└── docs/           specs, plans, ADRs
```

## Run it locally

### Backend

```bash
cd backend
uv sync                        # or `pip install -e .`
cp .env.example .env           # then fill in ANTHROPIC_API_KEY etc.
alembic upgrade head
uvicorn sahay.main:app --reload
```

Key env vars: `ANTHROPIC_API_KEY`, `DATABASE_URL` (Postgres + RLS), `REDIS_URL`, `SARVAM_API_KEY`, `ELEVENLABS_API_KEY`, `R2_*`, `JWT_*`.

### Cron (the self-improvement loop)

```bash
# Nightly, in this order:
python -m scripts.improve_playbooks --limit 20      # Opus 4.7, ~₹3/playbook
python -m scripts.decide_canaries                   # free, pure SQL
```

### Android

```bash
cd android
./gradlew assembleDebug
adb install app/build/outputs/apk/debug/app-debug.apk
```

## Admin dashboard

`GET /v1/admin/dashboard?window_days=7` returns one JSON payload with:
- Headline (total runs, success rate, mean latency, distinct users + playbooks)
- Per-intent reliability table sorted lowest-score-first
- Top failure classes (`selector_stale: 38, dialog_appeared: 21, …`)
- Active canaries with live decider preview (`promote / rollback / hold`)
- Recent promotions

This is the operational truth — every number traces to actual user runs, not estimates.

## Status

13 production-grade enhancement layers shipped on top of the prototype: privacy fix, action-vocab unification, expect/verify, Bayesian reliability, stuck-rescue, idempotency, prompt-cache split, failure classifier, Opus improver, canary routing, auto-promotion, admin dashboard, voice/wake-word polish.

Up next: selector chain runtime + selector trainer (closes the inner loop on selector durability), STT confidence + goal-rewrite (the "feels like it understands me" gap), per-contact tone profile, wake-word voice enrollment.

See [docs/superpowers/specs/](docs/superpowers/specs/) for the original design + each task's implementation plan.

## License

TBD (private repo for now).
