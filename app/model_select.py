"""
OpenAI client + model selection.

We use OpenAI exclusively. Two tiers, because the deterministic core means the
chat model never has to reason about numbers — it only converses and routes to
tools — so a fast model is the right default there, while the one-shot W-2 vision
extraction wants a more capable model.

Exact model ids are *verified against the account at startup* (GET /v1/models)
rather than hard-coded, since the GPT-5 family ids vary by account and GPT-4o/4.1
were retired in Feb 2026. ``OPENAI_MODEL`` / ``OPENAI_VISION_MODEL`` override.
"""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from openai import OpenAI

from .observability import logger

load_dotenv()  # pick up a local .env (gitignored) if present

# Preference order, best-effort. First available (exact or prefix) wins.
# Chat tier favors fast, capable conversational models; vision favors flagships.
CHAT_PREFERENCES = [
    "gpt-5.4", "gpt-5.5", "gpt-5.3", "gpt-5-mini", "gpt-5",
    "gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini",
]
VISION_PREFERENCES = [
    "gpt-5.5", "gpt-5.4", "gpt-5", "gpt-4.1", "gpt-4o",
]


class ModelConfig:
    def __init__(self, client: OpenAI, chat_model: str, vision_model: str):
        self.client = client
        self.chat_model = chat_model
        self.vision_model = vision_model


def _pick(available: list[str], preferences: list[str], env_value: str | None) -> str | None:
    if env_value:
        # Trust an explicit override even if listing failed; validate if we can.
        if not available or any(env_value == a or a.startswith(env_value) for a in available):
            return env_value
        logger.warning("Configured model %r not found on account; falling back.", env_value)
    for pref in preferences:
        for a in available:
            if a == pref or a.startswith(pref):
                return a
    # last resort: any gpt-5 / gpt-4 chat-capable model
    for a in available:
        if a.startswith(("gpt-5", "gpt-4")):
            return a
    return None


@lru_cache(maxsize=1)
def get_models() -> ModelConfig:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Add it to a local .env (gitignored) or the "
            "environment before starting the server."
        )
    client = OpenAI()
    available: list[str] = []
    try:
        available = sorted(m.id for m in client.models.list().data)
    except Exception as e:  # network/auth issues shouldn't crash import
        logger.warning("Could not list OpenAI models (%s); relying on env/defaults.", e)

    chat = _pick(available, CHAT_PREFERENCES, os.getenv("OPENAI_MODEL")) or "gpt-5"
    vision = _pick(available, VISION_PREFERENCES, os.getenv("OPENAI_VISION_MODEL")) or chat
    logger.info("OpenAI models selected -> chat=%s, vision=%s (from %d available)",
                chat, vision, len(available))
    return ModelConfig(client=client, chat_model=chat, vision_model=vision)
