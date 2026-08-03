"""
model_router.py - Model tier resolution for main chat vs background workers.

Tiers: none | nano | small | main | heavy

Env overrides (optional):
  AVA_MODEL_NANO, AVA_MODEL_SMALL, AVA_MODEL_MAIN, AVA_MODEL_HEAVY
  MODEL_NAME is the default main-tier model (server settings).
"""

from __future__ import annotations

import os
from typing import Optional

VALID_TIERS = ("none", "nano", "small", "main", "heavy")

# Default tier by task_type (plan.md model routing)
TIER_BY_TASK_TYPE = {
    "timer": "none",
    "demo": "none",
    "scrape": "none",
    "opencode": "none",  # uses OpenCode's own default model
    "coding": "none",
    "smarthome": "small",
    "delegate": "small",
    "mission": "main",  # plan/synth; children use small
    "research": "heavy",
    "general": "small",
}

# difficulty can bump tier
_DIFFICULTY_BUMP = {
    "easy": 0,
    "normal": 0,
    "hard": 1,
}

_TIER_ORDER = ["none", "nano", "small", "main", "heavy"]


def infer_model_tier(
    task_type: str = "general",
    difficulty: Optional[str] = None,
    explicit: Optional[str] = None,
) -> str:
    if explicit in VALID_TIERS:
        return explicit
    base = TIER_BY_TASK_TYPE.get(task_type or "general", "small")
    if base == "none":
        return "none"
    bump = _DIFFICULTY_BUMP.get((difficulty or "normal").lower(), 0)
    if bump:
        try:
            idx = min(len(_TIER_ORDER) - 1, _TIER_ORDER.index(base) + bump)
            return _TIER_ORDER[idx]
        except ValueError:
            pass
    return base


def resolve_model_name(tier: str, default_main: Optional[str] = None) -> Optional[str]:
    """
    Map tier → concrete model id for the OpenAI-compatible API.

    Returns None for tier ``none`` (caller should not call the LLM).
    Falls back to MODEL_NAME / AVA_MODEL_MAIN for unknown tiers.
    """
    tier = (tier or "main").lower()
    if tier == "none":
        return None

    main = (
        default_main
        or os.getenv("AVA_MODEL_MAIN")
        or os.getenv("MODEL_NAME")
        or ""
    ).strip() or None

    mapping = {
        "nano": os.getenv("AVA_MODEL_NANO") or os.getenv("AVA_MODEL_SMALL") or main,
        "small": os.getenv("AVA_MODEL_SMALL") or main,
        "main": main,
        "heavy": os.getenv("AVA_MODEL_HEAVY") or main,
    }
    name = mapping.get(tier, main)
    return (name or None) if name else None


def workers_skip_server_context(tier: str = "") -> bool:
    """Background workers should not pull recent_hour_context (token waste)."""
    return True
