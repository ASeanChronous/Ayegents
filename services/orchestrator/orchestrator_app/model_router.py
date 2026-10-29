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

# USD per token, by tier -- this is what closes docs/03-frontend.md's
# "total_cost_usd/cost_usd are always 0, no pricing table exists" gap.
# Published per-million-token list prices, divided down to a per-token rate
# so callers (executor.py) can multiply directly against
# response.usage.input_tokens/output_tokens with no further conversion.
# A list-price table, not the account's actual negotiated/cached rate --
# prompt-caching discounts (architecture doc §9a) aren't reflected here, so
# this is an upper-bound estimate, not a billing-accurate figure.
MODEL_PRICING_PER_TOKEN = {
    "haiku": {"input": 0.80 / 1_000_000, "output": 4.00 / 1_000_000},
    "sonnet": {"input": 3.00 / 1_000_000, "output": 15.00 / 1_000_000},
    "opus": {"input": 15.00 / 1_000_000, "output": 75.00 / 1_000_000},
}


def resolve_model_id(tier: str) -> str:
    try:
        return MODEL_IDS[tier]
    except KeyError:
        raise ValueError(f"unknown model tier: {tier!r}")


def compute_cost_usd(tier: str, tokens_input: int, tokens_output: int) -> float:
    try:
        rates = MODEL_PRICING_PER_TOKEN[tier]
    except KeyError:
        raise ValueError(f"unknown model tier: {tier!r}")
    return tokens_input * rates["input"] + tokens_output * rates["output"]
