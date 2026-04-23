# Sahay — Privacy Policy (template, last updated 2026-04-23)

> **This is a template, not legal advice.** Have a qualified lawyer review
> before publishing at sahay.app/privacy.

## What Sahay is

Sahay is an Android application that performs user-requested actions inside
other apps on your device, on your behalf, using Android's Accessibility
Service framework. You speak a command; Sahay opens the right app and taps
the right buttons.

## What we collect

| Data | Where it lives | Why |
|---|---|---|
| Email address | Server (Neon Postgres) | Account identifier; OTP delivery |
| Locale, display name | Server | UI personalisation |
| Tier + subscription state | Server | Billing |
| Action metadata: `{app_package, intent, success/fail, duration, tokens used}` | Server | Reliability scoring, billing, debugging |
| Files you generate or upload via Sahay | Cloudflare R2 (private bucket) | So you can access them across sessions |
| Auth tokens | Encrypted local storage on device only | Keeping you logged in |

## What we **DO NOT** collect

- The contents of any messages, emails, photos, or screens you view in other apps.
- Your contact list.
- Your browsing history.
- The audio of your voice (only the resulting text transcript is processed; audio is discarded after STT).
- Any data from apps on the **payment app blocklist** (GPay, PhonePe, Paytm, banking apps, etc.) — Sahay refuses to interact with them at all.

## How we process screen content

When you ask Sahay to do something, the Accessibility Service reads the
current screen on your device. **All processing of screen content happens
locally on your device.** The server only ever receives:

- The package name of the target app (e.g. `com.whatsapp`)
- The intent you requested (e.g. `send_message`)
- A short hash of the screen state (no readable content)
- The outcome (success / fail / aborted)

We never see your messages, contacts, or photos.

## Confirmations

Any action that sends, deletes, posts, or otherwise has a side effect
**always** shows you a fullscreen preview before executing. You must
explicitly tap "Proceed" — Sahay cannot bypass this even if asked.

## Kill switch

You can stop Sahay instantly at any time by pressing volume-down 3 times
within 2 seconds. Sahay will pause and require you to open the app to
resume.

## Sub-processors

| Provider | Purpose | Region |
|---|---|---|
| Railway | Application hosting | US |
| Neon | Postgres database | US/EU |
| Upstash | Redis (rate limits) | US |
| Cloudflare R2 | File storage | Global |
| Resend | Transactional email | US |
| Anthropic | Claude API (action planning) | US |
| Sarvam AI | Speech-to-text for Indic languages | India |
| Replicate | Image generation | US |
| Sentry | Error monitoring | US |

## Your rights

- **Access**: request a copy of your data → support@sahay.app
- **Erasure**: tap "Delete my account & data" in Settings, or email us. Effective within 24 hours; cascades through Postgres + Cloudflare R2.
- **Portability**: machine-readable export available on request.
- **Objection / restriction**: contact us.

## Children

Sahay is not intended for users under 13. We do not knowingly collect data
from children.

## Changes

We will notify you of material changes via email and require re-consent
where required by law.

## Contact

support@sahay.app
[Address — to be filled in before publication]

---

**India DPDP Act notice**: Sahay's Data Fiduciary registration is pending.
Once registered, the registration number will appear here.

**EU GDPR notice**: Our EU representative is [TBD] reachable at [TBD].
