"""Seed the playbooks table with 10 hand-crafted launch-day playbooks.

Run AFTER `alembic upgrade head`.

Usage:
    python -m scripts.seed_playbooks

These are starter playbooks — production playbooks should come from the
ingestor (services/playbook_ingestor.py) reading official app docs. We hand-
craft v1 because:
  • Launch needs to work day 1 without 10 Opus calls
  • These exact flows are well-known and verified
  • Manual seeds get a higher initial reliability_score (0.85)
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from sahay.db.models import Playbook
from sahay.db.session import session_scope

# ─── Hand-crafted playbooks ──────────────────────────────────────────


PLAYBOOKS: list[dict[str, Any]] = [
    # ─── WhatsApp ─────────────────────────────────────────────────────
    # Conditional flow: if already inside a chat, navigate back to chats list
    # before searching the recipient. Step 0 = launch (no-op if already running).
    # Vision resolver handles UI variants we didn't anticipate.
    {
        "app_package": "com.whatsapp",
        "app_name": "WhatsApp",
        "intent": "send_message",
        "reliability_score": 0.80,
        "source_doc_url": "https://faq.whatsapp.com/",
        "steps_json": {
            "app_package": "com.whatsapp",
            "app_name": "WhatsApp",
            "intent": "send_message",
            "inputs": ["recipient_name", "message_text"],
            "steps": [
                {
                    "step": 1, "description": "Launch or focus WhatsApp",
                    "target_screen": "any", "action": "launch",
                    "target_selector": {"package": "com.whatsapp"},
                    "params": {}, "confirm_required": False, "timeout_ms": 6000,
                    "on_failure": "abort",
                },
                {
                    "step": 2, "description": "Wait for WhatsApp UI to settle",
                    "target_screen": "any", "action": "wait",
                    "target_selector": {}, "params": {},
                    "confirm_required": False, "timeout_ms": 1500, "on_failure": None,
                },
                {
                    "step": 3, "description": "Navigate back to chats list (if inside an existing chat)",
                    "target_screen": "any", "action": "back",
                    "target_selector": {}, "params": {},
                    "confirm_required": False, "timeout_ms": 800, "on_failure": None,
                },
                {
                    "step": 4, "description": "Tap search icon to find the contact",
                    "target_screen": "chats_list", "action": "tap",
                    "target_selector": {"contentDescription": "Search"},
                    "params": {}, "confirm_required": False, "timeout_ms": 4000,
                    "on_failure": "retry_once",
                },
                {
                    "step": 5, "description": "Type the contact's name in search",
                    "target_screen": "search", "action": "type",
                    "target_selector": {"viewId": "com.whatsapp:id/search_src_text"},
                    "params": {"text": "{{recipient_name}}"},
                    "confirm_required": False, "timeout_ms": 3000, "on_failure": None,
                },
                {
                    "step": 6, "description": "Wait for results",
                    "target_screen": "search_results", "action": "wait",
                    "target_selector": {}, "params": {},
                    "confirm_required": False, "timeout_ms": 1200, "on_failure": None,
                },
                {
                    "step": 7, "description": "Tap the matching contact in the result list",
                    "target_screen": "search_results", "action": "tap",
                    "target_selector": {"text": "{{recipient_name}}"},
                    "params": {}, "confirm_required": False, "timeout_ms": 4000,
                    "on_failure": "ask_user",
                },
                {
                    "step": 8, "description": "Tap message input field and type the message",
                    "target_screen": "chat", "action": "type",
                    "target_selector": {"viewId": "com.whatsapp:id/entry"},
                    "params": {"text": "{{message_text}}"},
                    "confirm_required": False, "timeout_ms": 3000, "on_failure": None,
                },
                {
                    "step": 9, "description": "Tap send",
                    "target_screen": "chat", "action": "tap",
                    "target_selector": {"contentDescription": "Send"},
                    "params": {}, "confirm_required": False, "timeout_ms": 3000,
                    "on_failure": "ask_user",
                },
            ],
            "notes": (
                "Step 3 (back) is intentional — handles the case where WhatsApp is "
                "in foreground but inside a different chat. If already on chats list, "
                "back is a no-op and we proceed."
            ),
        },
    },

    # ─── Gmail ────────────────────────────────────────────────────────
    {
        "app_package": "com.google.android.gm",
        "app_name": "Gmail",
        "intent": "compose_email",
        "reliability_score": 0.80,
        "source_doc_url": "https://support.google.com/mail/",
        "steps_json": {
            "app_package": "com.google.android.gm",
            "app_name": "Gmail",
            "intent": "compose_email",
            "inputs": ["recipient_email", "subject", "body"],
            "steps": [
                {"step": 1, "description": "Launch Gmail", "target_screen": "any",
                 "action": "launch", "target_selector": {"package": "com.google.android.gm"},
                 "params": {}, "confirm_required": False, "timeout_ms": 5000,
                 "on_failure": "abort"},
                {"step": 2, "description": "Tap Compose FAB", "target_screen": "inbox",
                 "action": "tap", "target_selector": {"contentDescription": "Compose"},
                 "params": {}, "confirm_required": False, "timeout_ms": 3000,
                 "on_failure": "retry_once"},
                {"step": 3, "description": "Enter recipient", "target_screen": "compose",
                 "action": "type", "target_selector": {"viewId": "com.google.android.gm:id/to"},
                 "params": {"text": "{{recipient_email}}"}, "confirm_required": False,
                 "timeout_ms": 3000, "on_failure": None},
                {"step": 4, "description": "Enter subject", "target_screen": "compose",
                 "action": "type", "target_selector": {"viewId": "com.google.android.gm:id/subject"},
                 "params": {"text": "{{subject}}"}, "confirm_required": False,
                 "timeout_ms": 3000, "on_failure": None},
                {"step": 5, "description": "Enter body", "target_screen": "compose",
                 "action": "type", "target_selector": {"viewId": "com.google.android.gm:id/body_wrapper"},
                 "params": {"text": "{{body}}"}, "confirm_required": False,
                 "timeout_ms": 3000, "on_failure": None},
                {"step": 6, "description": "Send email (CONFIRMATION REQUIRED)",
                 "target_screen": "compose", "action": "tap",
                 "target_selector": {"contentDescription": "Send"},
                 "params": {}, "confirm_required": True, "timeout_ms": 3000,
                 "on_failure": "ask_user"},
            ],
            "notes": "",
        },
    },

    # ─── YouTube ──────────────────────────────────────────────────────
    {
        "app_package": "com.google.android.youtube",
        "app_name": "YouTube",
        "intent": "search_and_play",
        "reliability_score": 0.90,
        "source_doc_url": "https://support.google.com/youtube/",
        "steps_json": {
            "app_package": "com.google.android.youtube",
            "app_name": "YouTube",
            "intent": "search_and_play",
            "inputs": ["query"],
            "steps": [
                {"step": 1, "description": "Launch YouTube", "target_screen": "any",
                 "action": "launch", "target_selector": {"package": "com.google.android.youtube"},
                 "params": {}, "confirm_required": False, "timeout_ms": 5000,
                 "on_failure": "abort"},
                {"step": 2, "description": "Tap search", "target_screen": "home",
                 "action": "tap", "target_selector": {"contentDescription": "Search"},
                 "params": {}, "confirm_required": False, "timeout_ms": 3000,
                 "on_failure": "retry_once"},
                {"step": 3, "description": "Type query", "target_screen": "search",
                 "action": "type", "target_selector": {"viewId": "com.google.android.youtube:id/search_edit_text"},
                 "params": {"text": "{{query}}"}, "confirm_required": False,
                 "timeout_ms": 3000, "on_failure": None},
                {"step": 4, "description": "Submit search", "target_screen": "search",
                 "action": "tap", "target_selector": {"contentDescription": "Search"},
                 "params": {}, "confirm_required": False, "timeout_ms": 3000,
                 "on_failure": None},
                {"step": 5, "description": "Tap first result", "target_screen": "search_results",
                 "action": "tap", "target_selector": {"viewId": "com.google.android.youtube:id/thumbnail"},
                 "params": {}, "confirm_required": False, "timeout_ms": 5000,
                 "on_failure": "ask_user"},
            ],
            "notes": "",
        },
    },

    # ─── Netflix ─────────────────────────────────────────────────────
    # Conditional flow: handles profile picker (taps first profile),
    # handles being inside a movie (back to home first), then searches + plays.
    # Vision resolver fills gaps when Netflix changes its viewIds.
    {
        "app_package": "com.netflix.mediaclient",
        "app_name": "Netflix",
        "intent": "search_and_play",
        "reliability_score": 0.75,
        "source_doc_url": "https://help.netflix.com/",
        "steps_json": {
            "app_package": "com.netflix.mediaclient",
            "app_name": "Netflix",
            "intent": "search_and_play",
            "inputs": ["title"],
            "steps": [
                {"step": 1, "description": "Launch or focus Netflix", "target_screen": "any",
                 "action": "launch", "target_selector": {"package": "com.netflix.mediaclient"},
                 "params": {}, "confirm_required": False, "timeout_ms": 8000,
                 "on_failure": "abort"},
                {"step": 2, "description": "Wait for Netflix splash to clear", "target_screen": "any",
                 "action": "wait", "target_selector": {}, "params": {},
                 "confirm_required": False, "timeout_ms": 3500, "on_failure": None},
                {"step": 3, "description": "If profile picker is showing, tap the first profile (vision resolver handles it)",
                 "target_screen": "profile_picker", "action": "tap",
                 "target_selector": {"text": "Who's watching?"},
                 "params": {}, "confirm_required": False, "timeout_ms": 2000,
                 "on_failure": None},
                {"step": 4, "description": "If currently watching a video, back out to navigate", "target_screen": "any",
                 "action": "back", "target_selector": {}, "params": {},
                 "confirm_required": False, "timeout_ms": 800, "on_failure": None},
                {"step": 5, "description": "Tap search icon in bottom nav", "target_screen": "home",
                 "action": "tap", "target_selector": {"contentDescription": "Search"},
                 "params": {}, "confirm_required": False, "timeout_ms": 4000,
                 "on_failure": "retry_once"},
                {"step": 6, "description": "Type the title to search",
                 "target_screen": "search", "action": "type",
                 "target_selector": {"viewId": "com.netflix.mediaclient:id/search_edit_text"},
                 "params": {"text": "{{title}}"}, "confirm_required": False,
                 "timeout_ms": 3000, "on_failure": None},
                {"step": 7, "description": "Wait for search results", "target_screen": "search_results",
                 "action": "wait", "target_selector": {}, "params": {},
                 "confirm_required": False, "timeout_ms": 1500, "on_failure": None},
                {"step": 8, "description": "Tap the first matching title in results",
                 "target_screen": "search_results", "action": "tap",
                 "target_selector": {"contentDescription": "{{title}}"},
                 "params": {}, "confirm_required": False, "timeout_ms": 5000,
                 "on_failure": "ask_user"},
                {"step": 9, "description": "Tap play on the title details page",
                 "target_screen": "title_details", "action": "tap",
                 "target_selector": {"contentDescription": "Play"},
                 "params": {}, "confirm_required": False, "timeout_ms": 5000,
                 "on_failure": "ask_user"},
            ],
            "notes": "Step 3 + 4 are conditional cleanup — if not on profile picker / playback, those steps no-op.",
        },
    },

    # ─── Calendar ────────────────────────────────────────────────────
    {
        "app_package": "com.google.android.calendar",
        "app_name": "Google Calendar",
        "intent": "create_event",
        "reliability_score": 0.75,
        "source_doc_url": "https://support.google.com/calendar/",
        "steps_json": {
            "app_package": "com.google.android.calendar",
            "app_name": "Google Calendar",
            "intent": "create_event",
            "inputs": ["title", "datetime_iso"],
            "steps": [
                {"step": 1, "description": "Launch Calendar", "target_screen": "any",
                 "action": "launch", "target_selector": {"package": "com.google.android.calendar"},
                 "params": {}, "confirm_required": False, "timeout_ms": 5000,
                 "on_failure": "abort"},
                {"step": 2, "description": "Tap + FAB", "target_screen": "main",
                 "action": "tap", "target_selector": {"contentDescription": "Create new event, calendar, task or reminder"},
                 "params": {}, "confirm_required": False, "timeout_ms": 3000,
                 "on_failure": "retry_once"},
                {"step": 3, "description": "Tap Event", "target_screen": "create_picker",
                 "action": "tap", "target_selector": {"text": "Event"},
                 "params": {}, "confirm_required": False, "timeout_ms": 3000,
                 "on_failure": "ask_user"},
                {"step": 4, "description": "Enter title", "target_screen": "edit_event",
                 "action": "type", "target_selector": {"viewId": "com.google.android.calendar:id/title"},
                 "params": {"text": "{{title}}"}, "confirm_required": False,
                 "timeout_ms": 3000, "on_failure": None},
                {"step": 5, "description": "Save event (CONFIRMATION REQUIRED)",
                 "target_screen": "edit_event", "action": "tap",
                 "target_selector": {"text": "Save"},
                 "params": {}, "confirm_required": True, "timeout_ms": 3000,
                 "on_failure": "ask_user"},
            ],
            "notes": "datetime selection requires multi-step picker — covered by ask_user fallback.",
        },
    },

    # ─── Maps ────────────────────────────────────────────────────────
    {
        "app_package": "com.google.android.apps.maps",
        "app_name": "Google Maps",
        "intent": "navigate_to",
        "reliability_score": 0.88,
        "source_doc_url": "https://support.google.com/maps/",
        "steps_json": {
            "app_package": "com.google.android.apps.maps",
            "app_name": "Google Maps",
            "intent": "navigate_to",
            "inputs": ["destination"],
            "steps": [
                {"step": 1, "description": "Launch Maps", "target_screen": "any",
                 "action": "launch", "target_selector": {"package": "com.google.android.apps.maps"},
                 "params": {}, "confirm_required": False, "timeout_ms": 5000,
                 "on_failure": "abort"},
                {"step": 2, "description": "Tap search bar", "target_screen": "map",
                 "action": "tap", "target_selector": {"viewId": "com.google.android.apps.maps:id/search_omnibox_text_box"},
                 "params": {}, "confirm_required": False, "timeout_ms": 3000,
                 "on_failure": "retry_once"},
                {"step": 3, "description": "Type destination", "target_screen": "search",
                 "action": "type", "target_selector": {"viewId": "com.google.android.apps.maps:id/search_omnibox_edit_text"},
                 "params": {"text": "{{destination}}"}, "confirm_required": False,
                 "timeout_ms": 3000, "on_failure": None},
                {"step": 4, "description": "Tap first result", "target_screen": "search_results",
                 "action": "tap", "target_selector": {"viewId": "com.google.android.apps.maps:id/cardview"},
                 "params": {}, "confirm_required": False, "timeout_ms": 3000,
                 "on_failure": "ask_user"},
                {"step": 5, "description": "Start navigation", "target_screen": "place_details",
                 "action": "tap", "target_selector": {"contentDescription": "Directions"},
                 "params": {}, "confirm_required": False, "timeout_ms": 3000,
                 "on_failure": "ask_user"},
            ],
            "notes": "",
        },
    },

    # ─── Notes (Google Keep) ─────────────────────────────────────────
    {
        "app_package": "com.google.android.keep",
        "app_name": "Google Keep",
        "intent": "create_note",
        "reliability_score": 0.85,
        "source_doc_url": "https://support.google.com/keep/",
        "steps_json": {
            "app_package": "com.google.android.keep",
            "app_name": "Google Keep",
            "intent": "create_note",
            "inputs": ["title", "body"],
            "steps": [
                {"step": 1, "description": "Launch Keep", "target_screen": "any",
                 "action": "launch", "target_selector": {"package": "com.google.android.keep"},
                 "params": {}, "confirm_required": False, "timeout_ms": 5000,
                 "on_failure": "abort"},
                {"step": 2, "description": "Tap + new note", "target_screen": "main",
                 "action": "tap", "target_selector": {"contentDescription": "New text note"},
                 "params": {}, "confirm_required": False, "timeout_ms": 3000,
                 "on_failure": "retry_once"},
                {"step": 3, "description": "Type title", "target_screen": "edit_note",
                 "action": "type", "target_selector": {"viewId": "com.google.android.keep:id/editable_title"},
                 "params": {"text": "{{title}}"}, "confirm_required": False,
                 "timeout_ms": 3000, "on_failure": None},
                {"step": 4, "description": "Type body", "target_screen": "edit_note",
                 "action": "type", "target_selector": {"viewId": "com.google.android.keep:id/edit_note_text"},
                 "params": {"text": "{{body}}"}, "confirm_required": False,
                 "timeout_ms": 3000, "on_failure": None},
                {"step": 5, "description": "Save (back)", "target_screen": "edit_note",
                 "action": "back", "target_selector": {},
                 "params": {}, "confirm_required": False, "timeout_ms": 2000,
                 "on_failure": None},
            ],
            "notes": "",
        },
    },

    # ─── LinkedIn ────────────────────────────────────────────────────
    {
        "app_package": "com.linkedin.android",
        "app_name": "LinkedIn",
        "intent": "post_update",
        "reliability_score": 0.70,
        "source_doc_url": "https://www.linkedin.com/help/",
        "steps_json": {
            "app_package": "com.linkedin.android",
            "app_name": "LinkedIn",
            "intent": "post_update",
            "inputs": ["post_text"],
            "steps": [
                {"step": 1, "description": "Launch LinkedIn", "target_screen": "any",
                 "action": "launch", "target_selector": {"package": "com.linkedin.android"},
                 "params": {}, "confirm_required": False, "timeout_ms": 5000,
                 "on_failure": "abort"},
                {"step": 2, "description": "Tap Post button", "target_screen": "feed",
                 "action": "tap", "target_selector": {"contentDescription": "Post"},
                 "params": {}, "confirm_required": False, "timeout_ms": 3000,
                 "on_failure": "retry_once"},
                {"step": 3, "description": "Type post", "target_screen": "share_box",
                 "action": "type", "target_selector": {"viewId": "com.linkedin.android:id/share_text"},
                 "params": {"text": "{{post_text}}"}, "confirm_required": False,
                 "timeout_ms": 3000, "on_failure": None},
                {"step": 4, "description": "Tap Post (CONFIRMATION REQUIRED)",
                 "target_screen": "share_box", "action": "tap",
                 "target_selector": {"text": "Post"},
                 "params": {}, "confirm_required": True, "timeout_ms": 3000,
                 "on_failure": "ask_user"},
            ],
            "notes": "",
        },
    },

    # ─── Instagram (post DM only — posting media is multi-step) ──────
    {
        "app_package": "com.instagram.android",
        "app_name": "Instagram",
        "intent": "send_dm",
        "reliability_score": 0.75,
        "source_doc_url": "https://help.instagram.com/",
        "steps_json": {
            "app_package": "com.instagram.android",
            "app_name": "Instagram",
            "intent": "send_dm",
            "inputs": ["recipient_username", "message_text"],
            "steps": [
                {"step": 1, "description": "Launch Instagram", "target_screen": "any",
                 "action": "launch", "target_selector": {"package": "com.instagram.android"},
                 "params": {}, "confirm_required": False, "timeout_ms": 5000,
                 "on_failure": "abort"},
                {"step": 2, "description": "Tap DM icon", "target_screen": "feed",
                 "action": "tap", "target_selector": {"contentDescription": "Direct"},
                 "params": {}, "confirm_required": False, "timeout_ms": 3000,
                 "on_failure": "retry_once"},
                {"step": 3, "description": "Tap new message", "target_screen": "inbox",
                 "action": "tap", "target_selector": {"contentDescription": "New message"},
                 "params": {}, "confirm_required": False, "timeout_ms": 3000,
                 "on_failure": "retry_once"},
                {"step": 4, "description": "Type recipient", "target_screen": "new_dm",
                 "action": "type", "target_selector": {"viewId": "com.instagram.android:id/search_edit_text"},
                 "params": {"text": "{{recipient_username}}"},
                 "confirm_required": False, "timeout_ms": 3000, "on_failure": None},
                {"step": 5, "description": "Tap recipient", "target_screen": "new_dm",
                 "action": "tap", "target_selector": {"text": "{{recipient_username}}"},
                 "params": {}, "confirm_required": False, "timeout_ms": 3000,
                 "on_failure": "ask_user"},
                {"step": 6, "description": "Tap Chat", "target_screen": "new_dm",
                 "action": "tap", "target_selector": {"text": "Chat"},
                 "params": {}, "confirm_required": False, "timeout_ms": 3000,
                 "on_failure": None},
                {"step": 7, "description": "Type message", "target_screen": "thread",
                 "action": "type", "target_selector": {"viewId": "com.instagram.android:id/row_thread_composer_edittext"},
                 "params": {"text": "{{message_text}}"}, "confirm_required": False,
                 "timeout_ms": 3000, "on_failure": None},
                {"step": 8, "description": "Send (CONFIRMATION REQUIRED)",
                 "target_screen": "thread", "action": "tap",
                 "target_selector": {"contentDescription": "Send"},
                 "params": {}, "confirm_required": True, "timeout_ms": 3000,
                 "on_failure": "ask_user"},
            ],
            "notes": "",
        },
    },

    # ─── System Settings ─────────────────────────────────────────────
    {
        "app_package": "com.android.settings",
        "app_name": "System Settings",
        "intent": "open",
        "reliability_score": 0.95,
        "source_doc_url": None,
        "steps_json": {
            "app_package": "com.android.settings",
            "app_name": "System Settings",
            "intent": "open",
            "inputs": [],
            "steps": [
                {"step": 1, "description": "Launch Settings", "target_screen": "any",
                 "action": "launch", "target_selector": {"package": "com.android.settings"},
                 "params": {}, "confirm_required": False, "timeout_ms": 3000,
                 "on_failure": "abort"},
            ],
            "notes": "",
        },
    },
]


# ─── Runner ──────────────────────────────────────────────────────────


async def main() -> None:
    print(f"Upserting {len(PLAYBOOKS)} playbooks…")
    inserted = 0
    updated = 0
    async with session_scope() as db:
        for spec in PLAYBOOKS:
            stmt = select(Playbook).where(
                Playbook.app_package == spec["app_package"],
                Playbook.intent == spec["intent"],
                Playbook.app_version.is_(None),
            )
            existing = (await db.execute(stmt)).scalar_one_or_none()
            if existing is not None:
                # Update steps + reliability so iterating on playbooks works
                # via plain redeploy. Existing reliability_score is preserved
                # only if the seed sets a lower value (don't downgrade live data).
                existing.steps_json = spec["steps_json"]
                existing.app_name = spec["app_name"]
                existing.source_doc_url = spec.get("source_doc_url")
                if spec["reliability_score"] > float(existing.reliability_score):
                    existing.reliability_score = spec["reliability_score"]
                updated += 1
                continue
            db.add(
                Playbook(
                    app_package=spec["app_package"],
                    app_name=spec["app_name"],
                    intent=spec["intent"],
                    steps_json=spec["steps_json"],
                    source_doc_url=spec.get("source_doc_url"),
                    reliability_score=spec["reliability_score"],
                    is_payment_app=False,
                )
            )
            inserted += 1
    print(f"Inserted {inserted}, updated {updated}.")


if __name__ == "__main__":
    asyncio.run(main())
