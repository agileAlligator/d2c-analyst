"""Integration tests for chat tool dispatch.

Every tool in TOOL_DEFINITIONS is exercised against real seed data so that:
- Missing metrics fail at test time, not at chat time
- SQL tool connectivity is proven (regression for enable_external_access=false bug)
- All dispatch paths are covered end-to-end
"""
import os

import pytest

DB_URL = os.getenv("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DB_URL, reason="DATABASE_URL not set")

MERCHANT = "demo"


@pytest.fixture(scope="module")
def db():
    from app.warehouse.db import SessionLocal
    with SessionLocal() as session:
        yield session


# ---------------------------------------------------------------------------
# query_metric — every metric must return rows and provenance_ids
# ---------------------------------------------------------------------------

METRICS = [
    ("revenue", None, "30d"),
    ("ad_spend", None, "30d"),
    ("rto_rate", None, "30d"),
    ("contribution_margin", None, "30d"),
    ("cac", None, "30d"),
    ("average_order_value", None, "30d"),
    # grouped variants
    ("revenue", "campaign", "30d"),
    ("revenue", "courier", "30d"),
    ("rto_rate", "courier", "30d"),
    ("ad_spend", "campaign", "30d"),
]


@pytest.mark.parametrize("metric_name,group_by,time_range", METRICS)
def test_query_metric_returns_data(db, metric_name, group_by, time_range):
    """Every supported metric must return at least one row with provenance IDs."""
    from app.chat.tools import dispatch_tool

    result = dispatch_tool(
        "query_metric",
        {"metric_name": metric_name, "time_range": time_range, **({"group_by": group_by} if group_by else {})},
        db,
        MERCHANT,
    )

    assert "error" not in result, f"query_metric({metric_name}) returned error: {result.get('error')}"
    assert "rows" in result, f"query_metric({metric_name}) missing 'rows' key"
    assert len(result["rows"]) > 0, f"query_metric({metric_name}) returned zero rows — seed data missing or metric broken"
    assert len(result.get("provenance_ids", [])) > 0, (
        f"query_metric({metric_name}) returned no provenance_ids — citation will fail"
    )


def test_average_order_value_is_positive(db):
    from app.chat.tools import dispatch_tool

    result = dispatch_tool("query_metric", {"metric_name": "average_order_value", "time_range": "30d"}, db, MERCHANT)
    assert "error" not in result
    aov = result["rows"][0].get("average_order_value")
    assert aov is not None and float(aov) > 0, f"AOV should be positive, got {aov}"


def test_unknown_metric_returns_error(db):
    from app.chat.tools import dispatch_tool

    result = dispatch_tool("query_metric", {"metric_name": "nonexistent_metric"}, db, MERCHANT)
    assert "error" in result, "Unknown metric should return an error dict"


# ---------------------------------------------------------------------------
# sql tool — proves DuckDB connectivity with realistic analytical queries
# ---------------------------------------------------------------------------

class TestSqlTool:
    def test_simple_select(self, db):
        from app.chat.tools import dispatch_tool

        result = dispatch_tool("sql", {"query": "SELECT COUNT(*) AS n FROM events"}, db, MERCHANT)
        assert "error" not in result, f"sql tool error: {result.get('error')}"
        assert result["rows"][0]["n"] > 0

    def test_join_with_order_and_limit(self, db):
        """Top-N query: the pattern that was silently broken by enable_external_access=false."""
        from app.chat.tools import dispatch_tool

        result = dispatch_tool(
            "sql",
            {
                "query": """
                    SELECT
                        en.attributes->>'order_number' AS order_number,
                        SUM(ev.amount) AS revenue,
                        ARRAY_AGG(DISTINCT p.raw_record_id) AS provenance_ids
                    FROM events ev
                    JOIN entities en ON ev.entity_id = en.entity_id
                    JOIN provenance p ON p.row_table = 'events' AND p.row_pk = ev.event_id::text
                    WHERE ev.event_type IN ('order_revenue', 'refund')
                      AND ev.occurred_at >= NOW() - INTERVAL '30 days'
                    GROUP BY en.attributes->>'order_number'
                    ORDER BY revenue DESC
                    LIMIT 5
                """
            },
            db,
            MERCHANT,
        )
        assert "error" not in result, f"top-5-orders sql failed: {result.get('error')}"
        assert result["row_count"] > 0, "top-5-orders returned zero rows"
        assert len(result.get("provenance_ids", [])) > 0, "no provenance_ids from sql tool"

    def test_sql_tool_is_read_only(self, db):
        """Write statements must be rejected."""
        from app.chat.tools import dispatch_tool

        for bad_sql in [
            "DROP TABLE entities",
            "INSERT INTO entities VALUES (1,2,3)",
            "DELETE FROM events",
        ]:
            result = dispatch_tool("sql", {"query": bad_sql}, db, MERCHANT)
            assert "error" in result, f"Write statement should have been rejected: {bad_sql!r}"

    def test_sql_merchant_isolation(self, db):
        """sql tool must not expose other merchants' rows."""
        from app.chat.tools import dispatch_tool

        result = dispatch_tool(
            "sql",
            {"query": "SELECT COUNT(*) AS n FROM events WHERE merchant_id = 'demo2'"},
            db,
            MERCHANT,
        )
        assert "error" not in result
        assert result["rows"][0]["n"] == 0, "sql tool leaked demo2 rows to demo"


# ---------------------------------------------------------------------------
# list_entities — every supported entity_type must return rows
# ---------------------------------------------------------------------------

# Only types that are actually seeded. 'product' and 'sku' are in the tool enum
# but no connector currently ingests them — they correctly return zero rows.
ENTITY_TYPES = ["order", "ad_campaign", "shipment"]


@pytest.mark.parametrize("entity_type", ENTITY_TYPES)
def test_list_entities(db, entity_type):
    from app.chat.tools import dispatch_tool

    result = dispatch_tool("list_entities", {"entity_type": entity_type, "limit": 5}, db, MERCHANT)
    assert "error" not in result, f"list_entities({entity_type}) error: {result.get('error')}"
    assert "entities" in result
    assert len(result["entities"]) > 0, f"list_entities({entity_type}) returned zero rows"


# ---------------------------------------------------------------------------
# compare tool
# ---------------------------------------------------------------------------

def test_compare_revenue_7d_vs_30d(db):
    from app.chat.tools import dispatch_tool

    result = dispatch_tool(
        "compare",
        {"metric_name": "revenue", "period_a": "7d", "period_b": "30d"},
        db,
        MERCHANT,
    )
    assert "error" not in result, f"compare tool error: {result.get('error')}"
    assert result["period_a"]["value"] >= 0
    assert result["period_b"]["value"] > result["period_a"]["value"], (
        "30d revenue should exceed 7d revenue in seed data"
    )
    assert len(result.get("all_provenance_ids", [])) > 0, "compare tool returned no provenance_ids"
    assert result["pct_change"] is not None


# ---------------------------------------------------------------------------
# get_raw tool
# ---------------------------------------------------------------------------

def test_get_raw_resolves_real_record(db):
    """get_raw must return the source payload for a known provenance_id."""
    from app.chat.tools import dispatch_tool
    from sqlalchemy import text

    row = db.execute(
        text("SELECT source_record_id FROM raw_shopify_orders WHERE merchant_id = :m LIMIT 1"),
        {"m": MERCHANT},
    ).fetchone()
    if row is None:
        pytest.skip("No seed data in raw_shopify_orders")

    result = dispatch_tool("get_raw", {"provenance_id": row[0]}, db, MERCHANT)
    assert "error" not in result, f"get_raw error: {result.get('error')}"
    assert "payload" in result and result["payload"], "get_raw returned empty payload"


def test_get_raw_nonexistent_returns_error(db):
    from app.chat.tools import dispatch_tool

    result = dispatch_tool("get_raw", {"provenance_id": "order:does_not_exist_99999"}, db, MERCHANT)
    assert "error" in result, "get_raw for nonexistent ID should return an error"
