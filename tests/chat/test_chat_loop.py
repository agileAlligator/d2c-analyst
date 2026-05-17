"""Integration tests for the chat loop — mocks only the OpenAI API call.

All tool dispatch, metric queries, provenance lookups, and citation validation
run against real logic (not mocked). Only the LLM response is pre-recorded.
"""

import json as _json
import os
from unittest.mock import MagicMock, patch

import pytest

# Skip if no DB available
DB_URL = os.getenv("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DB_URL, reason="DATABASE_URL not set")


def _make_tool_call(tool_name: str, tool_id: str, inputs: dict):
    """Build an OpenAI-style tool_call object."""
    tc = MagicMock()
    tc.id = tool_id
    tc.function.name = tool_name
    tc.function.arguments = _json.dumps(inputs)
    return tc


def _make_response(content=None, tool_calls=None, finish_reason="stop"):
    """Build an OpenAI-style chat completion response."""
    choice = MagicMock()
    choice.finish_reason = finish_reason
    choice.message.content = content
    choice.message.tool_calls = tool_calls if tool_calls is not None else []
    resp = MagicMock()
    resp.choices = [choice]
    return resp


class TestChatLoopIntegration:
    """Full chat loop with pre-recorded LLM responses and real tool dispatch."""

    def test_revenue_question_with_cited_answer(self):
        """LLM calls query_metric, gets provenance IDs, returns a cited answer."""
        from app.chat.loop import run_chat
        from app.warehouse.db import SessionLocal

        call_count = [0]
        captured_prov_id = [None]
        captured_revenue = [None]

        def fake_create(**kwargs):
            call_count[0] += 1
            messages = kwargs.get("messages", [])

            if call_count[0] == 1:
                return _make_response(
                    tool_calls=[
                        _make_tool_call(
                            "query_metric",
                            "tu_001",
                            {
                                "metric_name": "revenue",
                                "time_range": "30d",
                            },
                        )
                    ],
                    finish_reason="tool_calls",
                )

            # Extract real provenance ID and revenue from the tool result message
            if captured_prov_id[0] is None:
                for msg in reversed(messages):
                    if isinstance(msg, dict) and msg.get("role") == "tool":
                        data = _json.loads(msg.get("content", "{}"))
                        pids = data.get("provenance_ids", [])
                        rows = data.get("rows", [])
                        if pids:
                            captured_prov_id[0] = pids[0]
                        if rows and captured_revenue[0] is None:
                            rev = rows[0].get("revenue", "0")
                            try:
                                captured_revenue[0] = str(int(float(str(rev))))
                            except (ValueError, TypeError):
                                captured_revenue[0] = str(rev)
                        if captured_prov_id[0]:
                            break

            ref = captured_prov_id[0] or "order:5000"
            rev_str = captured_revenue[0] or "31814"
            return _make_response(
                content=f'Total revenue in the last 30 days was <cite ref="{ref}">₹{rev_str}</cite>.',
                finish_reason="stop",
            )

        with patch("app.chat.loop.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = fake_create
            mock_get_client.return_value = mock_client

            with SessionLocal() as db:
                result = run_chat("What was total revenue in the last 30 days?", db, "demo")

        assert result["all_citations_valid"] is True, f"Citation issues: {result['issues']}\nAnswer: {result['answer']}"
        assert len(result["tool_calls"]) >= 1
        assert captured_revenue[0] is not None and captured_revenue[0] in result["answer"]

    def test_uncited_number_triggers_retry(self):
        """LLM returns uncited number → validator rejects → retry with correction → passes."""
        from app.chat.loop import run_chat
        from app.warehouse.db import SessionLocal

        call_count = [0]
        captured_messages = []
        captured_prov_id = [None]
        captured_revenue = [None]

        def fake_create(**kwargs):
            call_count[0] += 1
            captured_messages.append(list(kwargs.get("messages", [])))

            if call_count[0] == 1:
                return _make_response(
                    tool_calls=[
                        _make_tool_call(
                            "query_metric",
                            "tu_001",
                            {
                                "metric_name": "revenue",
                                "time_range": "30d",
                            },
                        )
                    ],
                    finish_reason="tool_calls",
                )

            if captured_prov_id[0] is None:
                for msg in reversed(captured_messages[-1]):
                    if isinstance(msg, dict) and msg.get("role") == "tool":
                        data = _json.loads(msg.get("content", "{}"))
                        pids = data.get("provenance_ids", [])
                        rows = data.get("rows", [])
                        if pids:
                            captured_prov_id[0] = pids[0]
                        if rows and captured_revenue[0] is None:
                            rev = rows[0].get("revenue", "0")
                            try:
                                captured_revenue[0] = str(int(float(str(rev))))
                            except (ValueError, TypeError):
                                captured_revenue[0] = str(rev)
                        if captured_prov_id[0]:
                            break

            bare_rev = captured_revenue[0] or "99999"
            if call_count[0] == 2:
                return _make_response(
                    content=f"Total revenue was {bare_rev} last month.",
                    finish_reason="stop",
                )

            ref = captured_prov_id[0] or "fallback-prov-id"
            rev_str = captured_revenue[0] or "99999"
            return _make_response(
                content=f'Total revenue was <cite ref="{ref}">₹{rev_str}</cite> last month.',
                finish_reason="stop",
            )

        with patch("app.chat.loop.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = fake_create
            mock_get_client.return_value = mock_client

            with SessionLocal() as db:
                result = run_chat("What was revenue last month?", db, "demo")

        assert call_count[0] >= 3, "Expected at least 3 LLM calls (tool use + bad answer + retry)"

        retry_user_msg = captured_messages[2][-1]
        retry_content = str(retry_user_msg.get("content", ""))
        assert "citation" in retry_content.lower() or "cite" in retry_content.lower(), (
            f"Retry prompt did not mention citations: {retry_content!r}"
        )

        assert result["all_citations_valid"] is True, (
            f"Expected retry to succeed. Issues: {result['issues']}\nAnswer: {result['answer']}"
        )
        # The validator strips cite tags to produce clean prose — numbers from valid citations
        # appear bare in result["answer"] by design. all_citations_valid=True is the correct
        # assertion; checking for absence of numbers in the cleaned text would always fail.

    def test_rls_isolation_different_merchants_get_different_tool_results(self):
        """demo and demo2 queries should return different revenue values (RLS)."""
        from app.chat.tools import dispatch_tool
        from app.warehouse.db import SessionLocal

        with SessionLocal() as db:
            demo_r = dispatch_tool("query_metric", {"metric_name": "revenue", "time_range": "90d"}, db, "demo")
            demo2_r = dispatch_tool("query_metric", {"metric_name": "revenue", "time_range": "90d"}, db, "demo2")

        demo_rev = sum(float(r.get("revenue", 0) or 0) for r in demo_r["rows"])
        demo2_rev = sum(float(r.get("revenue", 0) or 0) for r in demo2_r["rows"])

        assert demo_rev != demo2_rev, f"RLS isolation broken: demo ({demo_rev}) == demo2 ({demo2_rev})"
        assert demo_rev > 0 and demo2_rev > 0, "Both merchants should have non-zero revenue"

    def test_cascade_fires_when_cheap_model_fails_citation(self):
        """Cheap model exhausts retries with uncited answer → cascade retries with gpt-4o."""
        from app.chat.loop import run_chat
        from app.chat.routing import RoutingDecision
        from app.warehouse.db import SessionLocal

        call_count = [0]
        models_used = []
        captured_prov_id = [None]

        def fake_create(**kwargs):
            call_count[0] += 1
            models_used.append(kwargs.get("model", "unknown"))
            messages = kwargs.get("messages", [])

            # First call: tool use to get a real provenance ID
            if call_count[0] == 1:
                return _make_response(
                    tool_calls=[
                        _make_tool_call(
                            "query_metric",
                            "tu_001",
                            {
                                "metric_name": "revenue",
                                "time_range": "30d",
                            },
                        )
                    ],
                    finish_reason="tool_calls",
                )

            # Extract provenance on second call
            if captured_prov_id[0] is None:
                for msg in reversed(messages):
                    if isinstance(msg, dict) and msg.get("role") == "tool":
                        data = _json.loads(msg.get("content", "{}"))
                        pids = data.get("provenance_ids", [])
                        if pids:
                            captured_prov_id[0] = pids[0]
                        break

            # Cheap model (calls 2-4): always returns bare uncited number — fails validation
            # gpt-4o (call 5+): returns properly cited answer
            if kwargs.get("model") == "gpt-4o-mini":
                return _make_response(content="Revenue was 99999 last month.", finish_reason="stop")

            # gpt-4o cascade response
            ref = captured_prov_id[0] or "order:5000"
            return _make_response(
                content=f'Revenue was <cite ref="{ref}">₹31,814</cite> last month.',
                finish_reason="stop",
            )

        cheap = RoutingDecision(model="gpt-4o-mini", tier="cheap", reason="default")
        smart = RoutingDecision(model="gpt-4o", tier="smart", reason="cascade:uncited", escalated=True)

        with patch("app.chat.loop.get_client") as mock_get_client, patch("app.chat.loop._router") as mock_router:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = fake_create
            mock_get_client.return_value = mock_client
            mock_router.route.return_value = cheap
            mock_router.escalate.return_value = smart

            with SessionLocal() as db:
                result = run_chat("What was revenue last month?", db, "demo")

        assert mock_router.escalate.called, "Cascade (escalate) was never triggered"
        assert "gpt-4o" in models_used, f"gpt-4o was never called; models used: {models_used}"
        assert models_used[0] == "gpt-4o-mini", "First attempt should use cheap model"
        assert result["all_citations_valid"] is True, (
            f"Cascade should have fixed citation failures. Issues: {result['issues']}\nAnswer: {result['answer']}"
        )
        assert result.get("routing", {}).get("model") == "gpt-4o", (
            f"Result should reflect escalated gpt-4o model. Routing: {result.get('routing')}"
        )

    def test_cascade_not_triggered_on_infrastructure_failure(self):
        """max_turns infra failure should NOT cascade to gpt-4o — only citation failures cascade."""
        from app.chat.loop import MAX_TURNS, run_chat
        from app.chat.routing import RoutingDecision
        from app.warehouse.db import SessionLocal

        call_count = [0]
        cheap = RoutingDecision(model="gpt-4o-mini", tier="cheap", reason="default")

        def fake_create(**kwargs):
            call_count[0] += 1
            return _make_response(
                tool_calls=[
                    _make_tool_call(
                        "list_entities",
                        f"tu_{call_count[0]:03d}",
                        {
                            "entity_type": "order",
                            "limit": 1,
                        },
                    )
                ],
                finish_reason="tool_calls",
            )

        with patch("app.chat.loop.get_client") as mock_get_client, patch("app.chat.loop._router") as mock_router:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = fake_create
            mock_get_client.return_value = mock_client
            mock_router.route.return_value = cheap
            mock_router.escalate.return_value = cheap  # should never be called

            with SessionLocal() as db:
                result = run_chat("What is my revenue?", db, "demo")

        mock_router.escalate.assert_not_called()
        assert call_count[0] == MAX_TURNS, f"Infra failure cascaded: expected {MAX_TURNS} calls, got {call_count[0]}"
        assert "max_turns" in result["issues"][0]

    def test_max_turns_returns_invalid_citation(self):
        """LLM that never stops looping hits MAX_TURNS and returns all_citations_valid=False."""
        from app.chat.loop import MAX_TURNS, run_chat
        from app.warehouse.db import SessionLocal

        call_count = [0]

        def fake_create(**kwargs):
            call_count[0] += 1
            # Always return a tool call — loop spins until MAX_TURNS
            return _make_response(
                tool_calls=[
                    _make_tool_call(
                        "list_entities",
                        f"tu_{call_count[0]:03d}",
                        {
                            "entity_type": "order",
                            "limit": 1,
                        },
                    )
                ],
                finish_reason="tool_calls",
            )

        with patch("app.chat.loop.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = fake_create
            mock_get_client.return_value = mock_client

            with SessionLocal() as db:
                result = run_chat("What is my revenue?", db, "demo")

        assert result["all_citations_valid"] is False
        assert "max_turns" in result["issues"][0]
        assert call_count[0] == MAX_TURNS, f"Expected exactly MAX_TURNS={MAX_TURNS} calls, got {call_count[0]}"
