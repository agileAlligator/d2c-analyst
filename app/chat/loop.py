"""LLM tool-use loop with server-side citation validation and heuristic model routing.

Routing: queries go to gpt-4o-mini by default. Eight heuristic signals escalate to
gpt-4o before the first call. If the cheap model fails citation validation, a
FrugalGPT-style cascade retries from scratch with gpt-4o.

Supports both OpenAI (primary) and  (fallback) backends.
"""
import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.chat.routing import HeuristicRouter, RoutingDecision
from app.chat.tools import TOOL_DEFINITIONS, dispatch_tool
from app.chat.validator import validate_and_clean
from app.config import settings

logger = logging.getLogger(__name__)

MAX_TURNS = 12
MAX_RETRIES = 2
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system.txt").read_text()

_client = None
_backend: str | None = None  # "" or "openai"

_router = HeuristicRouter()


def get_client():
    global _client, _backend
    if _client is not None:
        return _client

    if settings.OPENAI_API_KEY and settings.OPENAI_API_KEY != "dummy":
        
        _client = openai.OpenAI(api_key=settings.openai_api_key)

    elif settings.openai_api_key and settings.openai_api_key != "dummy":
        import openai
        _client = openai.OpenAI(api_key=settings.openai_api_key)
        _backend = "openai"
    else:
        raise RuntimeError(
            "No LLM API key found. Set OPENAI_API_KEY or OPENAI_API_KEY in .env"
        )

    logger.info("Using %s backend", _backend)
    return _client


def run_chat(
    question: str,
    db: Session,
    merchant_id: str,
    history: list[dict] | None = None,
) -> dict[str, Any]:
    """Run one chat turn. Returns answer, citation validity, tool trace, provenance IDs,
    and routing metadata (model used, tier, reason, escalated).
    """
    get_client()
    turn = len(history) // 2 if history else 0  # approximate conversation depth
    decision = _router.route(question, history or [], turn)

    if _backend == "openai":
        return _run_openai(question, db, merchant_id, history, decision)
    return _run_openai_compat(question, db, merchant_id, history)


# ── OpenAI backend ────────────────────────────────────────────────────────────

def _openai_tools() -> list[dict]:
    """Convert -style tool definitions to OpenAI function-calling format."""
    out = []
    for t in TOOL_DEFINITIONS:
        schema = t.get("input_schema", {})
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": schema,
            },
        })
    return out


def _run_openai(question, db, merchant_id, history, decision: RoutingDecision):
    oai_tools = _openai_tools()
    client = get_client()

    result = _openai_attempt(question, db, merchant_id, history, decision.model, oai_tools, client)

    # Cascade: if the cheap model failed citation and we haven't escalated yet
    if not result["all_citations_valid"] and not decision.escalated and decision.tier == "cheap":
        failure_summary = "; ".join(result["issues"][:2])
        decision = _router.escalate(decision, failure_summary)
        logger.info("Cascade: retrying with %s", decision.model)
        result = _openai_attempt(question, db, merchant_id, history, decision.model, oai_tools, client)

    result["routing"] = {
        "model": decision.model,
        "tier": decision.tier,
        "reason": decision.reason,
        "escalated": decision.escalated,
    }
    return result


def _openai_attempt(question, db, merchant_id, history, model, oai_tools, client):
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += list(history or [])
    messages.append({"role": "user", "content": question})
    all_provenance_ids: list[str] = []
    tool_trace: list[dict] = []

    for attempt in range(MAX_RETRIES + 1):
        for _ in range(MAX_TURNS):
            response = client.chat.completions.create(
                model=model,
                max_tokens=4096,
                tools=oai_tools,
                tool_choice="auto",
                messages=messages,
            )
            msg = response.choices[0].message
            finish = response.choices[0].finish_reason

            if finish == "tool_calls" and msg.tool_calls:
                messages.append(msg)
                for tc in msg.tool_calls:
                    try:
                        tool_input = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        # malformed tool args — treat as cascade trigger
                        return _timeout_result(tool_trace, all_provenance_ids, reason="tool_parse_error")
                    result = dispatch_tool(tc.function.name, tool_input, db, merchant_id)
                    tool_trace.append({"tool": tc.function.name, "input": tool_input, "result": result})
                    if isinstance(result, dict):
                        prov = result.get("provenance_ids") or result.get("all_provenance_ids") or []
                        all_provenance_ids.extend(prov)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, default=str),
                    })

            elif finish == "stop":
                raw_answer = msg.content or ""
                cleaned, valid, issues = validate_and_clean(raw_answer, all_provenance_ids, db)
                if valid or attempt >= MAX_RETRIES:
                    return _result(cleaned, valid, issues, tool_trace, all_provenance_ids)
                messages.append({"role": "assistant", "content": raw_answer})
                messages.append({"role": "user", "content": _retry_prompt(issues)})
                break
            else:
                break

    return _timeout_result(tool_trace, all_provenance_ids)


# ──  backend ────────────────────────────────────────────────────────

def _run_openai_compat(question, db, merchant_id, history):
    """ path — no routing (always Sonnet). Kept for fallback compatibility."""
    messages = list(history or []) + [{"role": "user", "content": question}]
    all_provenance_ids: list[str] = []
    tool_trace: list[dict] = []
    client = get_client()

    for attempt in range(MAX_RETRIES + 1):
        for _ in range(MAX_TURNS):
            response = client.messages.create(
                model="gpt-4o",
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOL_DEFINITIONS,
                messages=messages,
            )

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    result = dispatch_tool(block.name, block.input, db, merchant_id)
                    tool_trace.append({"tool": block.name, "input": block.input, "result": result})
                    if isinstance(result, dict):
                        prov = result.get("provenance_ids") or result.get("all_provenance_ids") or []
                        all_provenance_ids.extend(prov)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str),
                    })
                messages = messages + [
                    {"role": "assistant", "content": response.content},
                    {"role": "user", "content": tool_results},
                ]

            elif response.stop_reason == "end_turn":
                raw_answer = "".join(
                    block.text for block in response.content if hasattr(block, "text")
                )
                cleaned, valid, issues = validate_and_clean(raw_answer, all_provenance_ids, db)
                if valid or attempt >= MAX_RETRIES:
                    r = _result(cleaned, valid, issues, tool_trace, all_provenance_ids)
                    r["routing"] = {
                        "model": "gpt-4o",
                        "tier": "smart",
                        "reason": "openai_routing",
                        "escalated": False,
                    }
                    return r
                messages = messages + [
                    {"role": "assistant", "content": response.content},
                    {"role": "user", "content": _retry_prompt(issues)},
                ]
                break
            else:
                break

    return _timeout_result(tool_trace, all_provenance_ids)


# ── Shared helpers ────────────────────────────────────────────────────────────

def _retry_prompt(issues: list[str]) -> str:
    return (
        f"Your answer had citation issues: {issues}. "
        "Please revise — every number must have a <cite ref='...'> tag "
        "using a provenance ID returned by the tools."
    )


def _result(answer, valid, issues, tool_trace, prov_ids) -> dict:
    return {
        "answer": answer,
        "all_citations_valid": valid,
        "issues": issues,
        "tool_calls": tool_trace,
        "provenance_ids": list(set(prov_ids)),
    }


def _timeout_result(tool_trace, prov_ids, reason: str = "max_retries_exceeded") -> dict:
    return {
        "answer": "Unable to generate a fully cited answer. Please try rephrasing.",
        "all_citations_valid": False,
        "issues": [reason],
        "tool_calls": tool_trace,
        "provenance_ids": list(set(prov_ids)),
    }
