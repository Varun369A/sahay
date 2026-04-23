"""Anthropic Claude client wrapper.

Centralised so we control:
  • Model selection by workload
  • Prompt caching (cuts cost ~70% for shared prompts)
  • Retries on transient errors
  • Token + cost accounting (returned alongside content)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from anthropic import AsyncAnthropic
from anthropic.types import Message
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from sahay.config import get_settings
from sahay.util.logging import get_logger

settings = get_settings()
log = get_logger(__name__)

# Indicative pricing in INR paise per 1M tokens (rounded — keep in sync).
# Used for usage-meter cost rollups; not for billing calculations.
_PRICING_PAISE_PER_MTOK: dict[str, dict[str, int]] = {
    "claude-haiku-4-5":  {"input":   83, "output":   416, "cached_input":  8},
    "claude-sonnet-4-6": {"input":  249, "output":  1245, "cached_input": 25},
    "claude-opus-4-7":   {"input": 1245, "output":  6225, "cached_input": 124},
}


Workload = Literal["default", "quality", "ingestion"]


def _model_for(workload: Workload) -> str:
    return {
        "default": settings.claude_model_default,
        "quality": settings.claude_model_quality,
        "ingestion": settings.claude_model_ingestion,
    }[workload]


@dataclass(slots=True)
class ClaudeResult:
    content: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_inr_paise: int


# Module-level singleton client.
_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),
            timeout=60.0,
            max_retries=0,  # we handle retries ourselves
        )
    return _client


async def complete(
    *,
    workload: Workload = "default",
    system: str | list[dict[str, Any]] | None = None,
    messages: list[dict[str, Any]],
    max_tokens: int = 1024,
    temperature: float = 0.0,
    cache_system: bool = True,
) -> ClaudeResult:
    """Single-shot completion.

    `system` may be a string (cached if cache_system=True) or a pre-built list
    of cache_control blocks for fine control.
    """
    model = _model_for(workload)
    client = _get_client()

    # Build system blocks with cache_control (Anthropic prompt caching).
    sys_param: list[dict[str, Any]] | str | None
    if isinstance(system, str) and cache_system:
        sys_param = [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]
    else:
        sys_param = system

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=0.5, max=4.0),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    ):
        with attempt:
            msg: Message = await client.messages.create(
                model=model,
                system=sys_param,  # type: ignore[arg-type]
                messages=messages,  # type: ignore[arg-type]
                max_tokens=max_tokens,
                temperature=temperature,
            )

    text = "".join(
        block.text for block in msg.content if getattr(block, "type", None) == "text"
    )

    usage = msg.usage
    cached = getattr(usage, "cache_read_input_tokens", 0) or 0
    input_tokens = usage.input_tokens
    output_tokens = usage.output_tokens

    pricing = _PRICING_PAISE_PER_MTOK.get(model, _PRICING_PAISE_PER_MTOK["claude-haiku-4-5"])
    fresh_in = max(0, input_tokens - cached)
    cost = (
        fresh_in * pricing["input"]
        + cached * pricing["cached_input"]
        + output_tokens * pricing["output"]
    ) // 1_000_000  # paise per token

    log.debug(
        "claude_call",
        model=model,
        input_tokens=input_tokens,
        cached=cached,
        output_tokens=output_tokens,
        cost_paise=cost,
    )

    return ClaudeResult(
        content=text,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached,
        cost_inr_paise=cost,
    )
