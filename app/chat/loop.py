"""LLM tool-use loop with server-side citation validation and heuristic model routing.

Routing: queries go to gpt-4o-mini by default. Eight heuristic signals escalate to
gpt-4o before the first call. If the cheap model fails citation validation, a
FrugalGPT-style cascade retries from scratch with gpt-4o.
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
_SYSTEM_PROMPT_TEMPLATE = (Path(__file__).parent / "prompts" / "system.txt").read_text()
_system_prompt_cache: str | None = None

_client = None

_router = HeuristicRouter()


def _get_system_prompt() -> str:
    global _system_prompt_cache
    if _system_prompt_cache is None:
        from app.warehouse.db import get_schema_description

        # Use replace, not .format(), so literal { } in the template (e.g. JSON examples)
        # don't cause KeyError/IndexError.
        _system_prompt_cache = _SYSTEM_PROMPT_TEMPLATE.replace("{warehouse_schema}", get_schema_description())
    return _system_prompt_cache


def _collect_tool_numbers(obj, acc: set) -> None:
    if isinstance(obj, dict):
        for v in obj.values():
            _collect_tool_numbers(v, acc)
    elif isinstance(obj, list):
        for item in obj:
            _collect_tool_numbers(item, acc)
    elif isinstance(obj, bool):
        pass
    elif isinstance(obj, (int, float)):
        acc.add(float(obj))
    elif isinstance(obj, str):
        try:
            cleaned = obj.replace(",", "").replace("₹", "").replace("$", "").strip()
            acc.add(float(cleaned))
        except ValueError:
            pass
    else:
        # Decimal and other numeric-like objects (Postgres Numeric columns)
        try:
            acc.add(float(obj))
        except (ValueError, TypeError):
            pass


def get_client():
    global _client
    if _client is not None:
        return _client

    if settings.openai_api_key and settings.openai_api_key != "dummy":
        import openai

        _client = openai.OpenAI(api_key=settings.openai_api_key)
    else:
        raise RuntimeError("No LLM API key found. Set OPENAI_API_KEY in .env")

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
    return _run_openai(question, db, merchant_id, history, decision)


# ── OpenAI backend ────────────────────────────────────────────────────────────


def _openai_tools() -> list[dict]:
    """Convert tool definitions to OpenAI function-calling format."""
    out = []
    for t in TOOL_DEFINITIONS:
        schema = t.get("input_schema", {})
        out.append(
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": schema,
                },
            }
        )
    return out


def _run_openai(question, db, merchant_id, history, decision: RoutingDecision):
    oai_tools = _openai_tools()
    client = get_client()

    result = _openai_attempt(question, db, merchant_id, history, decision.model, oai_tools, client)

    # Cascade only on citation failures, not infrastructure failures (max_turns, empty_response,
    # tool_parse_error, finish:length). Infrastructure failures won't be fixed by a smarter model.
    _INFRA_PREFIXES = ("max_turns", "empty_response", "tool_parse_error", "finish:", "max_retries")
    infra_failure = any(i.startswith(_INFRA_PREFIXES) for i in (result["issues"] or []))
    if not result["all_citations_valid"] and not infra_failure and not decision.escalated and decision.tier == "cheap":
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
    messages: list[dict] = [{"role": "system", "content": _get_system_prompt()}]
    messages += list(history or [])
    messages.append({"role": "user", "content": question})
    all_provenance_ids: list[str] = []
    tool_trace: list[dict] = []
    tool_value_set: set[float] = set()

    for attempt in range(MAX_RETRIES + 1):
        for _ in range(MAX_TURNS):
            response = client.chat.completions.create(
                model=model,
                max_tokens=4096,
                tools=oai_tools,
                tool_choice="auto",
                messages=messages,
                timeout=30.0,
            )
            msg = response.choices[0].message
            finish = response.choices[0].finish_reason

            if finish == "tool_calls" and msg.tool_calls:
                messages.append(msg.model_dump())
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
                    _collect_tool_numbers(result, tool_value_set)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(result, default=str),
                        }
                    )

            elif finish == "stop":
                raw_answer = msg.content or ""
                if not raw_answer.strip():
                    return _timeout_result(tool_trace, all_provenance_ids, reason="empty_response")
                cleaned, valid, issues = validate_and_clean(
                    raw_answer,
                    all_provenance_ids,
                    db,
                    merchant_id=merchant_id,
                    tool_value_set=tool_value_set,
                )
                if valid or attempt >= MAX_RETRIES:
                    return _result(cleaned, valid, issues, tool_trace, all_provenance_ids)
                messages.append({"role": "assistant", "content": raw_answer})
                messages.append({"role": "user", "content": _retry_prompt(issues)})
                break  # to outer loop for citation retry
            else:
                # Unexpected finish reason (e.g. length, content_filter) — don't retry
                return _timeout_result(tool_trace, all_provenance_ids, reason=f"finish:{finish or 'none'}")
        else:
            # Inner loop exhausted MAX_TURNS without producing an answer — don't re-attempt
            return _timeout_result(tool_trace, all_provenance_ids, reason="max_turns_exceeded")


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
