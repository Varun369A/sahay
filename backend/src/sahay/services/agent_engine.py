"""Agent engine — Claude-driven tool-using loop, one step at a time.

Client calls `step(observation)` repeatedly. Each call:
  1. Builds a tool-use prompt with the observation (screen + goal + history)
  2. Selects a Claude model (Haiku default, Sonnet if screen complex / history long)
  3. Anthropic returns a tool_use block with the next action
  4. We translate that into an AgentTool the client can execute

Model cascade policy:
  • Haiku 4.5 for screens with clean accessibility trees and short history
  • Sonnet 4.6 when vision is needed (screenshot provided and tree is thin)
  • Opus 4.7 never in the hot path — too slow, too expensive for per-step
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

from anthropic.types import Message

from sahay.config import get_settings
from sahay.schemas.agent import AgentObservation, AgentTool
from sahay.services.claude_client import _PRICING_PAISE_PER_MTOK, _get_client
from sahay.util.logging import get_logger

settings = get_settings()
log = get_logger(__name__)


@dataclass(slots=True)
class AgentStepResult:
    action: AgentTool
    model: str
    tokens_used: int
    cost_inr_paise: int


# ─── Tool definitions (Anthropic's tool_use API) ─────────────────────


_TOOLS: list[dict[str, Any]] = [
    {
        "name": "tap_node",
        "description": "PREFERRED. Tap the indexed node from the current screen's "
                       "`nodes` list. Pass the `i` value from the node you want "
                       "(e.g. tap_node(node_index=7)). Always correct — we use the "
                       "node's real on-screen bounds.",
        "input_schema": {
            "type": "object",
            "required": ["node_index"],
            "properties": {
                "node_index": {"type": "integer",
                               "description": "The `i` value from the tree nodes list"},
                "reasoning": {"type": "string"},
            },
        },
    },
    {
        "name": "type_in_node",
        "description": "PREFERRED for text input. Focus the indexed editable node and "
                       "type text in one atomic step. Node must have edit:true or be "
                       "an EditText-like class.",
        "input_schema": {
            "type": "object",
            "required": ["node_index", "text"],
            "properties": {
                "node_index": {"type": "integer"},
                "text": {"type": "string"},
                "reasoning": {"type": "string"},
            },
        },
    },
    {
        "name": "tap",
        "description": "Fallback — tap at raw (x, y) pixel coords. ONLY when the target "
                       "isn't in the nodes list but you can clearly see it in the "
                       "screenshot. Coords in device pixels (0..screen_width, "
                       "0..screen_height). Prefer tap_node.",
        "input_schema": {
            "type": "object",
            "required": ["x", "y"],
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "reasoning": {"type": "string"},
            },
        },
    },
    {
        "name": "long_press_node",
        "description": "Long-press the indexed node from the tree (preferred). "
                       "Use for recording voice messages, context menus, drag handles. "
                       "You can also pass an optional hold_ms for longer holds (e.g. 3000 for "
                       "a 3-second WhatsApp voice message).",
        "input_schema": {
            "type": "object",
            "required": ["node_index"],
            "properties": {
                "node_index": {"type": "integer"},
                "duration_ms": {"type": "integer", "default": 1200, "minimum": 300, "maximum": 30000},
                "reasoning": {"type": "string"},
            },
        },
    },
    {
        "name": "long_press",
        "description": "Fallback — long-press at (x, y). Prefer long_press_node.",
        "input_schema": {
            "type": "object",
            "required": ["x", "y"],
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "duration_ms": {"type": "integer", "default": 1200},
                "reasoning": {"type": "string"},
            },
        },
    },
    {
        "name": "type",
        "description": "Type text into the currently focused input field. "
                       "If no field is focused, first tap() on the input field.",
        "input_schema": {
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {"type": "string"},
                "reasoning": {"type": "string"},
            },
        },
    },
    {
        "name": "swipe",
        "description": "Swipe in a direction to scroll the screen.",
        "input_schema": {
            "type": "object",
            "required": ["direction"],
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                "distance": {"type": "integer", "default": 500},
                "reasoning": {"type": "string"},
            },
        },
    },
    {
        "name": "wait",
        "description": "Wait for the UI to settle (e.g. after a tap triggers navigation).",
        "input_schema": {
            "type": "object",
            "properties": {
                "duration_ms": {"type": "integer", "default": 1000, "minimum": 50, "maximum": 10000},
                "reasoning": {"type": "string"},
            },
        },
    },
    {
        "name": "back",
        "description": "Press the Android Back button. Use cautiously — back on an app's home screen exits the app.",
        "input_schema": {"type": "object", "properties": {"reasoning": {"type": "string"}}},
    },
    {
        "name": "home",
        "description": "Press the Android Home button. Returns to the launcher.",
        "input_schema": {"type": "object", "properties": {"reasoning": {"type": "string"}}},
    },
    {
        "name": "open_app",
        "description": "Launch an app by its package name (e.g. com.netflix.mediaclient).",
        "input_schema": {
            "type": "object",
            "required": ["app_package"],
            "properties": {
                "app_package": {"type": "string"},
                "reasoning": {"type": "string"},
            },
        },
    },
    {
        "name": "speak",
        "description": "Speak a sentence to the user via TTS. Use sparingly: to confirm progress, "
                       "explain an action, or give results. Keep under 20 words.",
        "input_schema": {
            "type": "object",
            "required": ["message"],
            "properties": {
                "message": {"type": "string"},
                "reasoning": {"type": "string"},
            },
        },
    },
    {
        "name": "ask_user",
        "description": "Pause the task and ask the user a question. Use only when genuinely "
                       "ambiguous (e.g. multiple Amirthas in contacts, unclear which file, "
                       "confirm a high-stakes action).",
        "input_schema": {
            "type": "object",
            "required": ["message"],
            "properties": {
                "message": {"type": "string"},
                "reasoning": {"type": "string"},
            },
        },
    },
    {
        "name": "done",
        "description": "Task is complete. Emit a 1-sentence summary for the user.",
        "input_schema": {
            "type": "object",
            "required": ["message"],
            "properties": {
                "message": {"type": "string"},
                "reasoning": {"type": "string"},
            },
        },
    },
    {
        "name": "give_up",
        "description": "Cannot continue (app crashed, missing permission, impossible step). "
                       "Return a clear reason.",
        "input_schema": {
            "type": "object",
            "required": ["message"],
            "properties": {
                "message": {"type": "string"},
                "reasoning": {"type": "string"},
            },
        },
    },
]


_SYSTEM = """You are Sahay — a fully-automated personal assistant agent living inside the user's Android phone. The user speaks a natural-language goal; YOU drive the phone screen-by-screen to accomplish it. No human intervention between steps.

WHAT YOU SEE:
• An accessibility tree JSON with numbered interactive nodes. Each has: `i` (index), `x`/`y` (screen center in real device pixels), `cls`, `text`, `cd` (contentDescription), `clk` (clickable), `edit` (editable).
• Optionally a JPEG screenshot for visual context. Use the tree for action targets; the screenshot only for understanding layout / confirming state.

IRONCLAD RULES:
1. PAYMENT APPS ARE FORBIDDEN. Never open, tap, or type into GPay / PhonePe / Paytm / banking / UPI / wallet apps. If the user asks, return tool=give_up.
2. Every turn, return ONE tool call. Nothing else.
3. **TOOL PREFERENCE:**
     • To tap a button / tile / menu item → tool=tap_node(node_index=I). I is the `i` of the node in the nodes list.
     • To type into a field → tool=type_in_node(node_index=I, text="..."). Use it on nodes where edit:true.
     • tool=tap(x,y) is only for cases where the target is visible in the screenshot but somehow not in the nodes list (rare — icons inside canvas views, WebViews).
     • NEVER emit a tap with a numeric viewId like "2131427347" — those are Android internal R.id ints and are unresolvable.
4. Be aggressive. Profile picker → tap the first profile node. Consent dialog → tap Allow / Continue. Search bar visible → tap_node on it. Only use ask_user for genuine semantic ambiguity (e.g. two "Amirtha" contacts).
5. When you need to search / type, look for the nearest node with edit:true and use type_in_node on it in one step. No separate tap-then-type.
6. Use speak() rarely — only to confirm progress or give a result.
7. When goal is achieved, return tool=done.
8. If the last 2 history entries were the same tool with the same target and nothing changed, TRY SOMETHING DIFFERENT — back, swipe, different node, or give_up.

HOW TO REASON (silently):
- What is the goal?
- What is the current screen?
- What's one forward step that gets me closer?
- Emit that tool call.
"""


def _select_model(obs: AgentObservation) -> str:
    """Haiku for normal turns; Sonnet when we really need vision reasoning."""
    tree_len = len(obs.screen_tree_summary or "")
    history_len = len(obs.history)
    has_image = bool(obs.screenshot_base64)

    # Escalate if vision needed AND tree is thin, or very long history requires reasoning.
    if has_image and tree_len < 500:
        return settings.claude_model_quality          # Sonnet 4.6
    if history_len > 10:
        return settings.claude_model_quality
    return settings.claude_model_default              # Haiku 4.5


def _build_user_content(obs: AgentObservation) -> list[dict[str, Any]]:
    """Multimodal content blocks for Anthropic messages API."""
    parts: list[dict[str, Any]] = []

    # Textual context
    textual = (
        f"GOAL:\n{obs.goal}\n\n"
        f"Current foreground app: {obs.app_package or 'unknown'}\n"
        f"Screen dimensions: {obs.screen_width}x{obs.screen_height}\n\n"
        f"ACTION HISTORY ({len(obs.history)} prior steps):\n"
        + (json.dumps(obs.history[-8:], indent=2) if obs.history else "(none)")
        + "\n\n"
        f"CURRENT ACCESSIBILITY TREE (truncated):\n"
        + (obs.screen_tree_summary or "(empty)")[:12_000]
    )
    parts.append({"type": "text", "text": textual})

    # Optional screenshot — Android sends JPEG (PNG was 10x bigger).
    if obs.screenshot_base64:
        parts.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": obs.screenshot_base64,
            },
        })

    return parts


def _extract_tool_call(msg: Message) -> AgentTool:
    """Pull the tool_use block out of Claude's reply and coerce to AgentTool."""
    for block in msg.content:
        if getattr(block, "type", None) == "tool_use":
            name = block.name
            args = dict(block.input or {})
            return AgentTool(
                tool=name,
                node_index=args.get("node_index"),
                x=args.get("x"),
                y=args.get("y"),
                selector=args.get("selector") or {},
                text=args.get("text"),
                direction=args.get("direction"),
                distance=args.get("distance"),
                duration_ms=args.get("duration_ms"),
                app_package=args.get("app_package"),
                message=args.get("message"),
                reasoning=args.get("reasoning"),
            )
    # Fallback: Claude emitted only text, no tool — treat as give_up.
    text_blocks = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
    return AgentTool(
        tool="give_up",
        message=("no_tool_call_returned: " + " ".join(text_blocks))[:500],
    )


def _cost_paise(model: str, usage: Any) -> int:
    pricing = _PRICING_PAISE_PER_MTOK.get(model, _PRICING_PAISE_PER_MTOK["claude-haiku-4-5"])
    cached = getattr(usage, "cache_read_input_tokens", 0) or 0
    fresh_in = max(0, usage.input_tokens - cached)
    return (
        fresh_in * pricing["input"]
        + cached * pricing["cached_input"]
        + usage.output_tokens * pricing["output"]
    ) // 1_000_000


async def step(obs: AgentObservation) -> AgentStepResult:
    """One agent turn. Pick a model, call Claude with tool-use, return the next action."""
    # Safety: if the user's goal names a payment app, short-circuit before even calling Claude.
    goal_lower = obs.goal.lower()
    PAYMENT_KEYWORDS = ("gpay", "phonepe", "paytm", "upi", "bank", "bhim", "cred")
    if any(k in goal_lower for k in PAYMENT_KEYWORDS):
        return AgentStepResult(
            action=AgentTool(
                tool="give_up",
                message="Payment apps are blocked for your safety. Open your banking app yourself.",
            ),
            model="none",
            tokens_used=0,
            cost_inr_paise=0,
        )

    model = _select_model(obs)
    client = _get_client()

    msg: Message = await client.messages.create(
        model=model,
        system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
        tools=_TOOLS,
        tool_choice={"type": "any"},          # force a tool call
        messages=[{"role": "user", "content": _build_user_content(obs)}],
        max_tokens=512,
        temperature=0.0,
    )

    action = _extract_tool_call(msg)
    tokens = msg.usage.input_tokens + msg.usage.output_tokens
    cost = _cost_paise(model, msg.usage)

    log.info(
        "agent_step",
        step=obs.step_index,
        tool=action.tool,
        model=model,
        tokens=tokens,
        cost_paise=cost,
    )
    return AgentStepResult(action=action, model=model, tokens_used=tokens, cost_inr_paise=cost)
