"""FastAPI endpoint tests — covers app/api/main.py (was 0%).

Uses FastAPI's TestClient with mocked run_chat and DB session so tests
run without a live LLM or database connection. DB dependency is overridden
via app.dependency_overrides (the FastAPI-idiomatic way).
"""
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.config import settings
from app.warehouse.db import get_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

GOOD_RESULT = {
    "answer": "Revenue was ₹31,814.",
    "all_citations_valid": True,
    "issues": [],
    "tool_calls": [{"tool": "query_metric", "input": {}, "result": {}}],
    "provenance_ids": ["order:1001"],
    "routing": {"model": "gpt-4o-mini", "tier": "cheap", "reason": "default", "escalated": False},
}


def _mock_db_session():
    db = MagicMock()
    return db


def _db_override(db=None):
    """FastAPI dependency override that yields a mock DB session."""
    session = db or _mock_db_session()
    yield session


@pytest.fixture()
def dev_client():
    """TestClient with dev_mode=True and a mock DB so no external services needed."""
    with patch.object(settings, "dev_mode", True), \
         patch.object(type(settings), "api_key_map",
                      new_callable=lambda: property(lambda self: {})):
        app.dependency_overrides[get_db] = _db_override
        yield TestClient(app, raise_server_exceptions=False)
        app.dependency_overrides.clear()


@pytest.fixture()
def keyed_client():
    """TestClient with a real API key configured."""
    key_map = {"test-key-123": "demo"}
    with patch.object(settings, "dev_mode", False), \
         patch.object(type(settings), "api_key_map",
                      new_callable=lambda: property(lambda self: key_map)):
        app.dependency_overrides[get_db] = _db_override
        yield TestClient(app, raise_server_exceptions=False)
        app.dependency_overrides.clear()


def _make_run(run_id=None, merchant_id="demo"):
    run = MagicMock()
    run.id = run_id or uuid.uuid4()
    run.agent_name = "MarginWatch"
    run.status = "completed"
    run.started_at = datetime.now(UTC)
    run.finished_at = datetime.now(UTC)
    run.proposals = [{"type": "discount", "amount": 100}]
    run.log_md = "## Run log"
    run.merchant_id = merchant_id
    return run


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_returns_ok(self):
        # /health has no auth or DB — use a plain client
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

class TestAuthentication:
    def test_no_key_and_no_dev_mode_returns_500(self):
        with patch.object(settings, "dev_mode", False), \
             patch.object(type(settings), "api_key_map",
                          new_callable=lambda: property(lambda self: {})):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/chat", json={"question": "hello"})
        assert resp.status_code == 500
        assert "misconfiguration" in resp.json()["detail"].lower()

    def test_invalid_key_returns_401(self, keyed_client):
        resp = keyed_client.post(
            "/chat",
            json={"question": "hello"},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401

    def test_missing_key_returns_401(self, keyed_client):
        resp = keyed_client.post("/chat", json={"question": "hello"})
        assert resp.status_code == 401

    def test_valid_key_accepted(self, keyed_client):
        with patch("app.api.main.run_chat", return_value=GOOD_RESULT):
            resp = keyed_client.post(
                "/chat",
                json={"question": "What was revenue?"},
                headers={"X-API-Key": "test-key-123"},
            )
        assert resp.status_code == 200

    def test_dev_mode_allows_no_key(self, dev_client):
        with patch("app.api.main.run_chat", return_value=GOOD_RESULT):
            resp = dev_client.post("/chat", json={"question": "What was revenue?"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /chat
# ---------------------------------------------------------------------------

class TestChatEndpoint:
    def test_chat_returns_correct_schema(self, dev_client):
        with patch("app.api.main.run_chat", return_value=GOOD_RESULT):
            resp = dev_client.post("/chat", json={"question": "What was revenue?"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"] == GOOD_RESULT["answer"]
        assert data["all_citations_valid"] is True
        assert data["issues"] == []
        assert "provenance_ids" in data
        assert "tool_calls" in data
        assert data["routing"]["model"] == "gpt-4o-mini"

    def test_chat_passes_history_to_run_chat(self, dev_client):
        history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        captured = {}

        def fake_run_chat(question, db, merchant_id, history=None):
            captured["history"] = history
            return GOOD_RESULT

        with patch("app.api.main.run_chat", side_effect=fake_run_chat):
            dev_client.post("/chat", json={"question": "follow-up", "history": history})

        assert captured.get("history") == history

    def test_chat_runtime_error_returns_500_with_detail(self, dev_client):
        with patch("app.api.main.run_chat", side_effect=RuntimeError("LLM key missing")):
            resp = dev_client.post("/chat", json={"question": "hello"})

        assert resp.status_code == 500
        assert "LLM key missing" in resp.json()["detail"]

    def test_chat_unexpected_error_returns_500(self, dev_client):
        with patch("app.api.main.run_chat", side_effect=ValueError("unexpected")):
            resp = dev_client.post("/chat", json={"question": "hello"})

        assert resp.status_code == 500
        # Unexpected errors should not leak internals
        assert resp.json()["detail"] == "Internal error — check server logs"

    def test_chat_null_history_accepted(self, dev_client):
        with patch("app.api.main.run_chat", return_value=GOOD_RESULT):
            resp = dev_client.post("/chat", json={"question": "hi"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /runs and GET /runs/{run_id}
# ---------------------------------------------------------------------------

class TestRunsEndpoint:
    def test_list_runs_returns_list(self, dev_client):
        runs = [_make_run(), _make_run()]
        mock_db = _mock_db_session()
        mock_db.query.return_value \
               .filter.return_value \
               .order_by.return_value \
               .limit.return_value \
               .all.return_value = runs

        def _override():
            yield mock_db

        app.dependency_overrides[get_db] = _override
        resp = dev_client.get("/runs")
        app.dependency_overrides[get_db] = _db_override  # restore

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["agent_name"] == "MarginWatch"
        assert data[0]["proposal_count"] == 1

    def test_list_runs_empty(self, dev_client):
        mock_db = _mock_db_session()
        mock_db.query.return_value \
               .filter.return_value \
               .order_by.return_value \
               .limit.return_value \
               .all.return_value = []

        def _override():
            yield mock_db

        app.dependency_overrides[get_db] = _override
        resp = dev_client.get("/runs")
        app.dependency_overrides[get_db] = _db_override

        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_run_returns_run(self, dev_client):
        run_id = uuid.uuid4()
        run = _make_run(run_id=run_id)
        mock_db = _mock_db_session()
        mock_db.query.return_value.filter.return_value.first.return_value = run

        def _override():
            yield mock_db

        app.dependency_overrides[get_db] = _override
        resp = dev_client.get(f"/runs/{run_id}")
        app.dependency_overrides[get_db] = _db_override

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(run_id)
        assert data["agent_name"] == "MarginWatch"
        assert "log_md" in data
        assert "proposals" in data

    def test_get_run_not_found_returns_404(self, dev_client):
        mock_db = _mock_db_session()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        def _override():
            yield mock_db

        app.dependency_overrides[get_db] = _override
        resp = dev_client.get(f"/runs/{uuid.uuid4()}")
        app.dependency_overrides[get_db] = _db_override

        assert resp.status_code == 404
