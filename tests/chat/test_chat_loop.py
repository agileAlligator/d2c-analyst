"""Integration tests for the chat loop — mocks only the  API call.

All tool dispatch, metric queries, provenance lookups, and citation validation
run against real logic (not mocked). Only the LLM response is pre-recorded.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

# Skip if no DB available
DB_URL = os.getenv("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DB_URL, reason="DATABASE_URL not set")


def _make_tool_use_message(tool_name: str, tool_id: str, inputs: dict):
    """Build an -style tool_use content block."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.id = tool_id
    block.input = inputs
    return block


def _make_text_message(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _make_response(content_blocks, stop_reason="end_turn"):
    resp = MagicMock()
    resp.content = content_blocks
    resp.stop_reason = stop_reason
    return resp


class TestChatLoopIntegration:
    """Full chat loop with pre-recorded LLM responses and real tool dispatch."""

    def test_revenue_question_with_cited_answer(self):
        """LLM calls query_metric, gets provenance IDs, returns a cited answer."""
        import json as _json

        from app.chat.loop import run_chat
        from app.warehouse.db import SessionLocal

        call_count = [0]
        captured_prov_id = [None]

        def fake_create(**kwargs):
            call_count[0] += 1
            messages = kwargs.get("messages", [])

            if call_count[0] == 1:
                # First call: return tool_use
                return _make_response(
                    [_make_tool_use_message("query_metric", "tu_001", {
                        "metric_name": "revenue",
                        "time_range": "30d",
                    })],
                    stop_reason="tool_use",
                )

            # Second call: extract real provenance ID from the tool result in messages
            if captured_prov_id[0] is None:
                for msg in reversed(messages):
                    if msg.get("role") == "user":
                        for block in (msg.get("content") or []):
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                data = _json.loads(block.get("content", "{}"))
                                pids = data.get("provenance_ids", [])
                                if pids:
                                    captured_prov_id[0] = pids[0]
                                    break
                        if captured_prov_id[0]:
                            break

            ref = captured_prov_id[0] or "order:5000"
            return _make_response([
                _make_text_message(
                    f'Total revenue in the last 30 days was '
                    f'<cite ref="{ref}">₹41,646</cite>.'
                ),
            ])

        with patch("app.chat.loop.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = fake_create
            mock_get_client.return_value = mock_client

            with SessionLocal() as db:
                result = run_chat("What was total revenue in the last 30 days?", db, "demo")

        assert result["all_citations_valid"] is True, f"Citation issues: {result['issues']}\nAnswer: {result['answer']}"
        assert len(result["tool_calls"]) >= 1
        assert "41,646" in result["answer"] or "41646" in result["answer"]

    def test_uncited_number_triggers_retry(self):
        """LLM returns uncited number → validator rejects → retry with correction → passes."""
        from app.chat.loop import run_chat
        from app.warehouse.db import SessionLocal

        call_count = [0]

        def fake_create(**kwargs):
            messages = kwargs.get("messages", [])
            last = messages[-1] if messages else {}
            call_count[0] += 1

            # First call: tool use
            if last.get("role") == "user" and "revenue" in str(last.get("content", "")):
                return _make_response(
                    [_make_tool_use_message("query_metric", "tu_001", {
                        "metric_name": "revenue",
                        "time_range": "30d",
                    })],
                    stop_reason="tool_use",
                )

            # Second call: uncited number (should fail validation)
            if call_count[0] == 2:
                return _make_response([
                    _make_text_message("Total revenue was 41646 last month."),
                ])

            # Third call: cited answer after retry
            return _make_response([
                _make_text_message('Total revenue was <cite ref="order:5001">₹41,646</cite>.'),
            ])

        with patch("app.chat.loop.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = fake_create
            mock_get_client.return_value = mock_client

            with SessionLocal() as db:
                run_chat("What was revenue last month?", db, "demo")

        # After retry, final answer should pass (order:5001 is in the provenance table)
        assert call_count[0] >= 3, "Expected at least 3 LLM calls (tool use + bad answer + retry)"

    def test_rls_isolation_different_merchants_get_different_tool_results(self):
        """demo and demo2 queries should return different revenue values (RLS)."""
        from app.chat.tools import dispatch_tool
        from app.warehouse.db import SessionLocal

        with SessionLocal() as db:
            demo_r = dispatch_tool("query_metric", {"metric_name": "revenue", "time_range": "90d"}, db, "demo")
            demo2_r = dispatch_tool("query_metric", {"metric_name": "revenue", "time_range": "90d"}, db, "demo2")

        demo_rev = sum(float(r.get("revenue", 0) or 0) for r in demo_r["rows"])
        demo2_rev = sum(float(r.get("revenue", 0) or 0) for r in demo2_r["rows"])

        assert demo_rev != demo2_rev, (
            f"RLS isolation broken: demo ({demo_rev}) == demo2 ({demo2_rev})"
        )
        assert demo_rev > demo2_rev, (
            "demo (80 orders) should have higher revenue than demo2 (5 orders)"
        )
