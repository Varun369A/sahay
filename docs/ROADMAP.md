# Sahay — production-grade roadmap

This is the spine that turns the current prototype into an enterprise-ready
beta. Phases are sequential, each is independently shippable, and each
session that picks up a phase should follow its acceptance criteria
literally — don't expand scope mid-phase.

## How to drive a phase in a fresh session

```
You are continuing work on Sahay (https://github.com/Varun369A/sahay).
Open V:\claude\sahay. Read docs/ROADMAP.md.
Execute Phase <N>: <name>.
Commit per task with the phase tag (e.g. "feat(phase-2): …").
Push to origin/main when the phase is complete and acceptance passes.
```

That prompt + the phase definitions below are enough for a fresh session
to land the work without ambiguity.

---

## Phase 0 — Foundation (DONE)

What's already shipped (commits `9bdd64c..461ca5f`):

- Privacy fix (screenshot opt-in)
- Action vocabulary unify + parity test
- `expect` predicate + `StepVerifier`
- Bayesian reliability + closed loop + `failure_class`
- Stuck-rescue → Opus 4.7 escalation
- Idempotency keys on irreversible actions
- Prompt cache restructure (static + persona)
- Nightly Opus playbook improver + canary routing + auto-promote
- Admin dashboard endpoint
- Wake-word polish (acoustic context, telemetry, voice verifier)
- Selector chain fallback + selector trainer
- Goal-rewriter (Haiku) + per-contact tone profile + reply critic
- **Conversation continuity** (PendingClarification + user_reply)
- **Wake-word force-fit fix** (competitor phrases in Vosk grammar)

164 → 215 backend tests, all green. Android compiles clean.

---

## Phase 1 — Decisive personal-assistant feel  (1 session)

**Goal:** Sahay stops asking "which app should I use?" and starts deciding.
Voice changes by command. Camera works. Image generation works.

### Deliverables

1. **Voice gender switching by command**
   - Add `voice_gender` to `User` model (`female | male | default`)
   - `services/elevenlabs_client.py`: select voice id from settings based on
     user's gender preference
   - Agent tool `change_voice` with `gender` arg
   - System prompt rule: when user says "change to <gender> voice" or "use
     <gender> voice", emit `change_voice` and a `speak` confirmation in the
     newly-selected voice
   - Settings UI toggle as a fallback path
   - Acceptance: speak "switch to female voice" → next utterance comes back
     in Charlotte. Speak "switch to male voice" → next in George.

2. **Decisive system prompt update**
   - Update `_SYSTEM_STATIC` in `services/agent_engine.py`:
     - Section called "DEFAULT APPS" listing the obvious picks
       (image_gen → ChatGPT app, food → Zomato/Swiggy whichever installed,
       music → Spotify, browser → Chrome, navigation → Google Maps, etc.)
     - Rule 9: "do NOT ask 'which app?' if there's an obvious default OR
       only one app on the device matches the intent. ONLY ask when 2+
       apps with comparable capability are installed and the user's
       phrasing genuinely doesn't disambiguate."
   - Per-user override: `user_app_prefs` table (`intent` → `app_package`)
     so the user can pin "image_gen → Photoshop" once and never be asked.
   - Acceptance: "build me an image of a sunset" → opens ChatGPT, no
     question. "play despacito" → opens Spotify, no question.

3. **Camera tools**
   - New agent tools: `take_photo` and `record_video` with optional
     `share_to_contact` arg
   - Android `AgentLoop.execute`: launch `MediaStore.ACTION_IMAGE_CAPTURE`
     / `ACTION_VIDEO_CAPTURE`, wait for result via Activity result API
   - Save to a known directory; agent's next turn can read the path
   - Acceptance: "take a selfie" → camera opens in front-facing mode.
     Capture closes camera. Sahay says "Got it — anything else?"

4. **Image generation tool (wire existing Replicate)**
   - `services/replicate_client.py` already exists; surface as agent tool
     `generate_image(prompt, style?)`
   - Result returned as a URL the agent can `speak` about and optionally
     route to `stage_reply` / `share`
   - Acceptance: "generate an image of a cat" → returns URL, Sahay speaks
     "Done — saved to your files."

### Files touched (estimate)

- `backend/src/sahay/db/models.py` — `User.voice_gender`, `user_app_prefs` table
- `backend/alembic/versions/0010_*.py` — migration
- `backend/src/sahay/services/elevenlabs_client.py` — voice id resolver
- `backend/src/sahay/services/agent_engine.py` — `_TOOLS` add 4 new entries,
  `_SYSTEM_STATIC` decisive update
- `backend/src/sahay/services/replicate_client.py` — surface `generate_image`
- `android/app/src/main/kotlin/io/sahay/engine/AgentLoop.kt` — handle
  `take_photo` / `record_video` / `change_voice` / `generate_image`
- Tests: agent_tool parity, voice id resolver, app-prefs CRUD

### Acceptance gate

Demo on a real phone:
1. "Switch to male voice" → confirmed in male voice within 2s
2. "Take a selfie" → camera launches, captures
3. "Generate an image of mountains at dusk" → image URL spoken back
4. "Play tujhe dekha to" → opens Spotify directly, no asking
5. Conversation continuity from Phase 0 still works

---

## Phase 2 — Top-10 app playbooks  (2 sessions)

**Goal:** the agent reliably operates the apps users actually use, every
time. No vision-fallback flailing on common flows.

### Deliverables

Hand-curated playbook YAML for 10 launch-day intents:

| App | Intent | Inputs |
|---|---|---|
| WhatsApp | send_text | recipient_name, message_text |
| WhatsApp | reply_to_last | reply_text |
| WhatsApp | voice_note | recipient_name, duration_seconds |
| Gmail | search_unread | query |
| Gmail | reply_thread | thread_query, reply_text |
| YouTube | search_play | query |
| Spotify | play_song | song_query |
| Google Maps | navigate | destination |
| Calendar | add_event | title, date, time |
| Chrome | search_web | query |

Each playbook:
- Has every step with an `expect` predicate
- Targets a specific app version range
- Comes with at least 1 fallback `selector_chain`
- Includes a `confirm_required` flag for irreversible steps

### Files touched

- `backend/scripts/seed_playbooks.py` — extend with 10 production playbooks
- `backend/tests/test_playbook_seeds.py` — validate every playbook against
  Pydantic schema; assert each step has `expect`; assert no payment apps

### Acceptance gate

Run each of the 10 intents 5 times on a real phone. Per-intent reliability
score after 5 runs ≥ 0.85. Zero wrong-send / wrong-call incidents.

---

## Phase 3 — Email intelligence  (3 sessions)

**Goal:** the user opens Sahay and hears "you have 3 emails worth your
attention today." Spam ignored, invoices flagged, replies staged.

### Session 3a — Gmail OAuth + fetch
- Google OAuth flow (consent screen, token refresh)
- `backend/src/sahay/services/gmail_client.py`: list unread, get message,
  send reply, archive
- DB: `user_email_tokens` table with refresh tokens (encrypted at rest)
- Endpoint `POST /v1/email/sync` triggers a fetch + classification

### Session 3b — Per-email classification + digest
- `services/email_classifier.py`: Haiku classifier with classes:
  `urgent_personal | urgent_work | invoice | newsletter | promotion | spam | reply_expected`
- `services/email_digest.py`: render top-N as a 30-second spoken brief
- Wire into morning briefing: existing `services/briefing.py` extended
- Auto-archive promotion + spam (with one-tap undo)

### Session 3c — Safe reply + reply critic integration
- Agent tool `email_reply(thread_id, draft)` — stages a reply through
  the Phase-0 reply critic (per-contact tone) before sending
- Confirm-then-send path matching the existing WhatsApp `stage_reply`
  semantics
- Acceptance: "reply to Rakesh saying I'll send Q2 numbers tonight" →
  draft staged in user's voice for that contact → user says "send" → goes

### Acceptance gate
- 24 hours running on the user's actual Gmail
- 0 emails sent without confirmation
- Daily digest catches every email the user would have flagged manually
- Promotion-bucket false-positive rate < 5% (real emails wrongly archived)

---

## Phase 4 — Cross-session memory + proactive intelligence  (2 sessions)

**Goal:** Sahay opens with "yesterday you were working on the deck for
Rakesh — still on that?" Picks up where the user left off.

### Session 4a — Session digest at end-of-session
- New `services/session_digest.py` — Haiku summarises the just-completed
  conversation into 2-3 LTM-facts (what we worked on, who was mentioned,
  what's open)
- Stored in `user_memory` with `category="session_digest"` + a TTL
- Triggered by AgentLoop on `done` / `give_up` / 90s of idle

### Session 4b — Proactive recall + morning briefing
- AgentLoop on first utterance of a new session: load last digest,
  inject as a "RESUME CONTEXT" block in the system prompt
- Briefing extended: schedule + open follow-ups + email digest +
  carry-forward from yesterday's open threads, in one 30-second speech

### Acceptance gate
- After a multi-step task ends, the next session within 24h opens with
  a recall line referencing what was worked on
- "Pick up where we left off" works — agent re-loads the same plan steps
  if any were unfinished

---

## Phase 5 — Reliability dashboard frontend  (1 session)

**Goal:** turn the existing `/v1/admin/dashboard` API into a live web page
the user (and investors) can open in a browser.

### Deliverables
- New `dashboard/` directory at repo root, Next.js + Tailwind
- Auth via existing JWT (admin tier required)
- Live tiles: headline, per-intent table, top failure classes (chart),
  active canaries with verdict, recent promotions
- Auto-refresh every 60s
- Hosted at `dashboard.sahay.app` (or Vercel preview)

### Acceptance gate
- A non-developer can open the URL, log in, and understand the system's
  health within 30 seconds
- Page is 90+ on Lighthouse mobile

---

## Phase 6 — Beta-launch readiness  (1 session)

**Goal:** ship a beta APK to 20 users with full instrumentation.

### Deliverables
- Sentry wired for crash reporting (Android + backend)
- Mixpanel-style event tracking on key actions (wake fired, task started,
  task done, confirm shown, error)
- Onboarding flow: voice ID enrollment, 3 wake-word recordings, role
  picker, mood picker, tutorial of one task
- App-store-ready: screenshots, copy, OG tags
- Privacy policy + DPDP/GDPR data deletion endpoint already wired —
  surface in Settings UI

### Acceptance gate
- Cold install → first successful task in under 90 seconds
- Volume-down kill switch tested on 3 OEMs (Samsung / Realme / Pixel)
- Demo video recorded (60-90 seconds, no edits, real phone, real user)

---

## Phase 7 — Investor pitch artifact  (0.5 session)

**Goal:** 10K Ventures application packet.

### Deliverables
- 10-slide deck (problem, wedge, traction proof from the dashboard, team)
- One-pager linking the dashboard URL + the demo video
- Honest metrics: per-intent success rate from the live dashboard, NOT
  estimates

### Acceptance gate
- Application sent
- Demo video received by 5 friends; ≥ 3 say "I want this"

---

## Total estimate

| Phase | Sessions |
|---|---|
| 1 — Decisive feel | 1 |
| 2 — Top-10 playbooks | 2 |
| 3 — Email intelligence | 3 |
| 4 — Cross-session memory | 2 |
| 5 — Dashboard frontend | 1 |
| 6 — Beta readiness | 1 |
| 7 — Pitch | 0.5 |
| **Total** | **10.5 sessions** |

At one focused session per evening that's two weeks of evenings to a
real beta. Each phase is independently shippable — you can pause after
any of them and the system remains coherent.

## Drift discipline

A phase is DONE when:
1. All deliverables for that phase are committed + pushed
2. Acceptance gate passes on a real device or in CI
3. Backend test suite stays green (`pytest` clean)
4. Android `assembleDebug` stays clean

A phase is NOT done because the code "looks finished" — only when the
acceptance gate fires. That's the discipline that turns a college
project into a production system.
