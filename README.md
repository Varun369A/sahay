# Sahay

> Your AI hands. Voice-driven personal assistant for Android that learns to operate any app from its official user guide and acts on your behalf — except where money is involved.

## What it is

Sahay is a Jarvis-style personal AI assistant that lives on your Android phone. You speak in any language ("send Amirtha a message," "post my LinkedIn update," "search Netflix for Dune"). Sahay opens the right app, taps the right buttons, types the right words — like a human would — and shows you a confirmation screen before anything irreversible happens.

**Sahay never touches payment apps.** GPay, PhonePe, Paytm, banking apps are hard-blocked.

## Architectural thesis

Most computer-use agents (Anthropic Computer Use, OpenAI Operator, Manus, Adept) learn through trial-and-error vision. Reliability sits at ~70%, which is unusable for consumer.

Sahay's wedge: **doc-grounded playbooks.** For every app, Sahay ingests the app's official user guide and generates a structured playbook (steps → screens → actions). At runtime, the playbook tells the agent exactly what to do — vision is only a fallback. This pushes reliability toward 95% on common flows.

Each playbook is built once per app and shared across all users (with strict privacy boundaries). The library compounds — month 12 has 1,000 apps playbooked, all working reliably.

## Privacy boundary (non-negotiable)

- **Screenshots are OFF by default.** The agent works from the Android accessibility tree (UI labels, button text, view hierarchy) alone. Screen pixels never leave the device unless the user explicitly opts in via Settings → Privacy → "Send screenshots to assist".
- **Even when opted in, screenshots are blocked for payment/banking apps.** GPay, PhonePe, Paytm, banking apps are hard-blocked end-to-end.
- **PII is tokenized before it reaches Claude.** Phone numbers, emails, OTPs, UPI IDs, Aadhaar, PAN, card numbers are replaced with stable tokens (`<PHONE_0>`, `<EMAIL_2>`, …) by an on-server firewall. Claude reasons about entities, never raw values.
- Per-user "compressed memory profile" lives in the cloud (~2KB, no PII).
- All local data encrypted at rest (SQLCipher).
- One-tap data deletion (DPDP + GDPR compliant).

## Layout

```
sahay/
├── backend/        FastAPI service deployed to Railway
├── android/        Kotlin + Jetpack Compose app
├── infra/          Database migrations, ops scripts
└── docs/           Specs, plans, ADRs
```

## Status

In active development. See `docs/superpowers/specs/` for design and implementation plan.

## License

TBD (private repo for now).
