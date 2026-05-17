"""Feature extraction for routing decisions."""

import re

# Keywords that predict multi-step, derived-metric, or causal queries
_COMPARISON_RE = re.compile(r"\b(vs|versus|compare|against|between|difference|delta|change|trend)\b", re.I)
_DERIVED_METRIC_RE = re.compile(r"\b(roas|cac|ltv|margin|contribution|payback|blended|breakeven|roi)\b", re.I)
_CAUSAL_RE = re.compile(r"\b(why|explain|driver|cause|breakdown|attribute|negative|worse|better|impact)\b", re.I)
_SQL_SIGNAL_RE = re.compile(r"\b(top\s+\d|rank|order by|highest|lowest|worst|best|list|show me all)\b", re.I)
_DATE_EXPR_RE = re.compile(
    r"\b(\d+\s*days?|last\s+week|last\s+month|this\s+week|yesterday|ytd|mtd|30d|7d|14d|90d)\b", re.I
)
_NEGATIVE_RE = re.compile(
    r"\bnegative\s+(?:margin|contribution|cm|roas|revenue|returns?)\b"
    r"|\b(?:margin|contribution)\s+(?:is\s+)?negative\b",
    re.I,
)


def extract_signals(query: str, history: list, turn: int) -> dict:
    """Return a dict of named boolean/numeric signals for routing."""
    q = query.strip()
    token_count = len(q.split())
    date_matches = len(_DATE_EXPR_RE.findall(q))

    return {
        "length": len(q) > 180 or token_count > 30,
        "comparison": bool(_COMPARISON_RE.search(q)),
        "derived_metric": bool(_DERIVED_METRIC_RE.search(q)),
        "causal": bool(_CAUSAL_RE.search(q)),
        "sql_escape": bool(_SQL_SIGNAL_RE.search(q)),
        "multi_timerange": date_matches >= 2,
        "deep_turn": turn >= 3,
        "negative_margin": bool(_NEGATIVE_RE.search(q)),
        # raw values for logging
        "char_count": len(q),
        "token_count": token_count,
        "date_expr_count": date_matches,
        "history_turns": len(history),
    }


# Which boolean signals trigger escalation to the smart model
COMPLEXITY_SIGNALS = [
    "length",
    "comparison",
    "derived_metric",
    "causal",
    "sql_escape",
    "multi_timerange",
    "deep_turn",
    "negative_margin",
]
