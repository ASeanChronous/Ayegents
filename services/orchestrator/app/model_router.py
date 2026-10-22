"""Cost-tiered model routing (architecture doc §9a).

An agent's `model_tier` ('haiku' | 'sonnet' | 'opus') is a property of the agent
config, not a platform-wide default — this maps that tier to an actual Claude
model id. Centralized here so a model-id bump is a one-line change, not a
grep-and-replace across every call site.
"""

MODEL_IDS = {
    "haiku": "claude-haiku-5-5",
    "sonnet": "claude-sonnet-5-5",
    "opus": "claude-opus-5-5",
}


def resolve_model_id(tier: str) -> str:
    try:
        return MODEL_IDS[tier]
    except KeyError:
        raise ValueError(f"unknown model tier: {tier!r}")
