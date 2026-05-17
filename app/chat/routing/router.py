"""Heuristic model router with FrugalGPT-style cascade on citation failure.

Default path:  query → extract_signals → any complexity signal? → gpt-4o : gpt-4o-mini
Cascade path:  cheap model fails citation validation → escalate to gpt-4o, retry once

Reference: Chen, Zaharia, Zou — "FrugalGPT" (Stanford, 2023).
           We apply the cascade pattern using the citation validator as the verifier.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from app.chat.routing.signals import COMPLEXITY_SIGNALS, extract_signals

logger = logging.getLogger(__name__)

ModelTier = Literal["cheap", "smart"]

CHEAP_MODEL = "gpt-4o-mini"
SMART_MODEL = "gpt-4o"


@dataclass
class RoutingDecision:
    model: str
    tier: ModelTier
    # Human-readable reason: "keyword:roas" | "length:240" | "default" | "cascade:citation_fail"
    reason: str
    signals: dict = field(default_factory=dict)
    escalated: bool = False


class HeuristicRouter:
    """Route queries to cheap or smart model based on query signals.

    Fires all 8 heuristics in parallel — escalates to smart model if ANY fires.
    A second-pass cascade is triggered by the caller when citation validation fails.
    """

    def route(self, query: str, history: list, turn: int = 0) -> RoutingDecision:
        signals = extract_signals(query, history, turn)

        fired = [s for s in COMPLEXITY_SIGNALS if signals.get(s)]
        if fired:
            reason = f"signal:{fired[0]}"
            logger.debug("Routing to %s — %s fired", SMART_MODEL, fired)
            return RoutingDecision(
                model=SMART_MODEL,
                tier="smart",
                reason=reason,
                signals=signals,
            )

        logger.debug("Routing to %s — no complexity signals", CHEAP_MODEL)
        return RoutingDecision(
            model=CHEAP_MODEL,
            tier="cheap",
            reason="default",
            signals=signals,
        )

    def escalate(self, prior: RoutingDecision, failure_reason: str) -> RoutingDecision:
        """Upgrade to smart model after the cheap model failed validation."""
        logger.info(
            "Cascade escalation: %s → %s (reason: %s)",
            prior.model,
            SMART_MODEL,
            failure_reason,
        )
        return RoutingDecision(
            model=SMART_MODEL,
            tier="smart",
            reason=f"cascade:{failure_reason}",
            signals=prior.signals,
            escalated=True,
        )
