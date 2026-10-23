"""Shadow cost: what a run would cost at paid list prices (the real bill is $0 on the free tier).

The prices below are ASSUMED list prices in USD per 1M tokens, kept in one table so they are easy
to correct; they are not read from the provider. Unknown models fall back to the most expensive
entry so the shadow cost never under-reports.
"""

from app.agent.types import AgentResult
from app.llm.types import Usage

# model -> (input, output) USD per 1M tokens. Thinking tokens are billed as output.
PRICES_PER_M: dict[str, tuple[float, float]] = {
    "gemini-3.5-flash": (0.30, 2.50),
    "gemini-3.5-flash-lite": (0.10, 0.40),
    "gemini-3.1-flash-lite": (0.10, 0.40),
}
FALLBACK = max(PRICES_PER_M.values())


def usage_cost(usage: Usage, model: str) -> float:
    inp, out = PRICES_PER_M.get(model, FALLBACK)
    return (usage.prompt_tokens * inp + (usage.output_tokens + usage.thinking_tokens) * out) / 1e6


def run_cost(result: AgentResult, sql_model: str = "gemini-3.5-flash") -> float:
    """Agent steps priced per the model that answered, SQL sub-calls at the main model price."""
    total = sum(usage_cost(s.usage, s.model) for s in result.steps)
    return total + usage_cost(result.sql_usage, sql_model)
