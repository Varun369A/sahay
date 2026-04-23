"""Replicate client for image generation (Flux Schnell by default).

Returns raw bytes of the first generated image. Caller persists to R2.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import httpx
import replicate

from sahay.config import get_settings
from sahay.util.logging import get_logger

settings = get_settings()
log = get_logger(__name__)

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="replicate")


def _set_token() -> None:
    # The replicate SDK reads from env var.
    import os
    os.environ["REPLICATE_API_TOKEN"] = settings.replicate_api_token.get_secret_value()


async def generate_image(
    prompt: str,
    *,
    aspect_ratio: str = "1:1",
    seed: int | None = None,
) -> tuple[bytes, str]:
    """Generate an image. Returns (image_bytes, mime_type)."""
    _set_token()

    def _run() -> str:
        out = replicate.run(
            settings.replicate_image_model,
            input={
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "output_format": "png",
                "num_outputs": 1,
                **({"seed": seed} if seed is not None else {}),
            },
        )
        # Replicate returns a list of FileOutput / urls
        if isinstance(out, list):
            first = out[0]
        else:
            first = out
        return str(first)

    url = await asyncio.get_running_loop().run_in_executor(_executor, _run)

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        log.info("image_generated", prompt_chars=len(prompt))
        return resp.content, "image/png"


async def edit_image(
    base_url: str,
    edit_prompt: str,
) -> tuple[bytes, str]:
    """Edit an existing image with a prompt. Uses Flux dev img2img variant.

    Stub: in v1 we just regenerate with the prompt + 'reference' description.
    Replace with a real img2img model in production.
    """
    composite = f"{edit_prompt}\n\nReference: {base_url}"
    return await generate_image(composite)
