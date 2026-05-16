"""Base class for autonomous agents."""
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.warehouse.db import set_merchant
from app.warehouse.models import AgentRun

logger = logging.getLogger(__name__)


@dataclass
class Proposal:
    action_type: str       # "pause_adset", "raise_price", "switch_courier", "bundle"
    entity_key: str        # the thing to act on
    expected_inr_impact: float
    reasoning: str
    provenance_ids: list[str]
    would_do_api_call: dict[str, Any]  # serialized, NOT sent


class BaseAgent:
    name: str = "base_agent"

    def __init__(self, db: Session, merchant_id: str):
        self.db = db
        self.merchant_id = merchant_id
        self._run_id = str(uuid.uuid4())
        self._log_lines: list[str] = []
        self._proposals: list[Proposal] = []

    def log(self, msg: str):
        ts = datetime.now(UTC).isoformat()
        self._log_lines.append(f"[{ts}] {msg}")
        logger.info("[%s] %s", self.merchant_id, msg)

    def run(self) -> AgentRun:
        set_merchant(self.db, self.merchant_id)
        started = datetime.now(UTC)
        agent_run = AgentRun(
            id=uuid.uuid4(),
            merchant_id=self.merchant_id,
            agent_name=self.name,
            started_at=started,
            status="running",
        )
        self.db.add(agent_run)
        self.db.commit()

        try:
            self._execute()
            agent_run.status = "completed"
        except Exception as e:
            logger.exception("Agent %s failed", self.name)
            agent_run.status = "failed"
            self.log(f"ERROR: {e}")
            self.db.rollback()

        agent_run.finished_at = datetime.now(UTC)
        agent_run.log_md = self._render_log()
        agent_run.proposals = [_proposal_to_dict(p) for p in self._proposals]
        self.db.commit()
        return agent_run

    def emit_proposal(self, proposal: "Proposal") -> None:
        """Record a proposal. Raises if would_do_api_call is missing NOT_SENT=True.

        Every code path that adds to self._proposals MUST go through here so that
        the NOT_SENT guard is never bypassed.
        """
        api_call = proposal.would_do_api_call
        if not isinstance(api_call, dict) or api_call.get("NOT_SENT") is not True:
            raise RuntimeError(
                f"Proposal '{proposal.action_type}' has no NOT_SENT=True "
                "in would_do_api_call — refusing to record to prevent accidental execution."
            )
        self._proposals.append(proposal)

    def _execute(self):
        raise NotImplementedError

    def _render_log(self) -> str:
        lines = [f"# {self.name} run — {datetime.now(UTC).date()}", ""]
        lines += self._log_lines
        lines += ["", "## Proposals", ""]
        for i, p in enumerate(self._proposals, 1):
            lines += [
                f"### {i}. {p.action_type} — {p.entity_key}",
                f"**Expected impact:** ₹{p.expected_inr_impact:,.0f}",
                f"**Reasoning:** {p.reasoning}",
                f"**Provenance:** {', '.join(p.provenance_ids[:5])}",
                f"**Would-do API call:** `{p.would_do_api_call}`",
                "",
            ]
        return "\n".join(lines)


def _proposal_to_dict(p: Proposal) -> dict:
    return {
        "action_type": p.action_type,
        "entity_key": p.entity_key,
        "expected_inr_impact": p.expected_inr_impact,
        "reasoning": p.reasoning,
        "provenance_ids": p.provenance_ids,
        "would_do_api_call": p.would_do_api_call,
    }
