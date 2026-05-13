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
        captured_revenue = [None]

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

            # Second call: extract real provenance ID and revenue from the tool result
            if captured_prov_id[0] is None:
                for msg in reversed(messages):
                    if msg.get("role") == "user":
                        for block in (msg.get("content") or []):
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                data = _json.loads(block.get("content", "{}"))
                                pids = data.get("provenance_ids", [])
                                rows = data.get("rows", [])
                                if pids:
                                    captured_prov_id[0] = pids[0]
                                if rows and captured_revenue[0] is None:
                                    rev = rows[0].get("revenue", "0")
                                    # revenue comes back as string after json.dumps(default=str)
                                    try:
                                        captured_revenue[0] = str(int(float(str(rev))))
                                    except (ValueError, TypeError):
                                        captured_revenue[0] = str(rev)
                                if captured_prov_id[0]:
                                    break
                        if captured_prov_id[0]:
                            break

            ref = captured_prov_id[0] or "order:5000"
            rev_str = captured_revenue[0] or "37053"
            return _make_response([
                _make_text_message(
                    f'Total revenue in the last 30 days was '
                    f'<cite ref="{ref}">₹{rev_str}</cite>.'
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
        # Revenue should appear as whatever the DB returned (dynamic, not hard-coded)
        assert captured_revenue[0] is not None and captured_revenue[0] in result["answer"]

    def test_uncited_number_triggers_retry(self):
        """LLM returns uncited number → validator rejects → retry with correction → passes."""
        import json as _json

        from app.chat.loop import run_chat
        from app.warehouse.db import SessionLocal

        call_count = [0]
        captured_messages = []
        captured_prov_id = [None]
        captured_revenue = [None]

        def fake_create(**kwargs):
            call_count[0] += 1
            # Capture a snapshot of messages on every call
            captured_messages.append(list(kwargs.get("messages", [])))

            # First call: return tool_use
            if call_count[0] == 1:
                return _make_response(
                    [_make_tool_use_message("query_metric", "tu_001", {
                        "metric_name": "revenue",
                        "time_range": "30d",
                    })],
                    stop_reason="tool_use",
                )

            # On the second call, extract the real provenance ID and revenue from the tool_result
            if captured_prov_id[0] is None:
                msgs = captured_messages[-1]
                for msg in reversed(msgs):
                    if msg.get("role") == "user":
                        for block in (msg.get("content") or []):
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                data = _json.loads(block.get("content", "{}"))
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
                    if captured_prov_id[0]:
                        break

            # Second call: uncited number (should fail validation) — use real revenue so
            # the bare-number heuristic fires but the value is actually in tool_value_set
            bare_rev = captured_revenue[0] or "99999"
            if call_count[0] == 2:
                return _make_response([
                    _make_text_message(f"Total revenue was {bare_rev} last month."),
                ])

            # Third call: cited answer using real provenance ID and real revenue value
            ref = captured_prov_id[0] or "fallback-prov-id"
            rev_str = captured_revenue[0] or "99999"
            return _make_response([
                _make_text_message(
                    f'Total revenue was <cite ref="{ref}">₹{rev_str}</cite> last month.'
                ),
            ])

        with patch("app.chat.loop.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = fake_create
            mock_get_client.return_value = mock_client

            with SessionLocal() as db:
                result = run_chat("What was revenue last month?", db, "demo")

        assert call_count[0] >= 3, "Expected at least 3 LLM calls (tool use + bad answer + retry)"

        # The retry call's last user message should reference citation issues
        retry_user_msg = captured_messages[2][-1]  # last msg on 3rd call
        retry_content = str(retry_user_msg.get("content", ""))
        assert "citation" in retry_content.lower() or "cite" in retry_content.lower(), (
            f"Retry prompt did not mention citations: {retry_content!r}"
        )

        assert result["all_citations_valid"] is True, (
            f"Expected retry to succeed. Issues: {result['issues']}\nAnswer: {result['answer']}"
        )
        # The bare number from the bad answer should not appear uncited in the final answer
        bare_rev_final = (captured_revenue[0] or "99999").replace(",", "")
        assert bare_rev_final not in result["answer"].replace(",", "") or "<cite" in result["answer"], (
            "Bad answer's bare number leaked into final answer uncited"
        )

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

    def test_max_turns_returns_invalid_citation(self):
        """LLM that never stops looping hits MAX_TURNS and returns all_citations_valid=False."""
        from app.chat.loop import MAX_TURNS, run_chat
        from app.warehouse.db import SessionLocal

        call_count = [0]

        def fake_create(**kwargs):
            call_count[0] += 1
            # Always return tool_use with no tool_use blocks — loop spins without making progress
            return _make_response([], stop_reason="tool_use")

        with patch("app.chat.loop.get_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = fake_create
            mock_get_client.return_value = mock_client

            with SessionLocal() as db:
                result = run_chat("What is my revenue?", db, "demo")

        assert result["all_citations_valid"] is False
        assert "max_turns" in result["issues"][0]
        assert call_count[0] == MAX_TURNS, (
            f"Expected exactly MAX_TURNS={MAX_TURNS} calls, got {call_count[0]}"
        )
