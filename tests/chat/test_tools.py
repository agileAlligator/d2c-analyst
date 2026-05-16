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
    ("roas", None, "30d"),
    ("orders", None, "30d"),
    ("orders", "date", "30d"),
    # grouped variants (only combinations where the entity carries the dimension)
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


def test_refunds_metric_returns_positive_value_with_provenance(db):
    """query_metric('refunds') must return a positive gross refund total and at least one provenance ID."""
    from app.chat.tools import dispatch_tool

    result = dispatch_tool(
        "query_metric",
        {"metric_name": "refunds", "time_range": "30d"},
        db,
        MERCHANT,
    )
    assert "error" not in result, f"query_metric(refunds) returned error: {result.get('error')}"
    assert "rows" in result and len(result["rows"]) > 0, "query_metric(refunds) returned zero rows — no refund seed data"
    refund_total = result["rows"][0].get("refunds")
    assert refund_total is not None, "refunds column missing from result rows"
    assert float(refund_total) > 0, f"refunds metric should be a positive gross total, got {refund_total}"
    assert len(result.get("provenance_ids", [])) > 0, "query_metric(refunds) returned no provenance_ids"


def test_orders_metric_is_time_windowed_and_cited(db):
    """orders metric must return a positive integer count with provenance IDs, and the 90d window must be >= 30d window."""
    from app.chat.tools import dispatch_tool

    result_30 = dispatch_tool("query_metric", {"metric_name": "orders", "time_range": "30d"}, db, MERCHANT)
    result_90 = dispatch_tool("query_metric", {"metric_name": "orders", "time_range": "90d"}, db, MERCHANT)

    assert "error" not in result_30, f"orders 30d error: {result_30.get('error')}"
    assert "error" not in result_90, f"orders 90d error: {result_90.get('error')}"

    count_30 = result_30["rows"][0].get("order_count")
    count_90 = result_90["rows"][0].get("order_count")

    assert count_30 is not None and int(count_30) > 0, f"orders 30d should be positive, got {count_30}"
    assert int(count_90) > int(count_30), f"Expected count_90 ({count_90}) > count_30 ({count_30}); time filter may not be applying correctly"
    assert len(result_30.get("provenance_ids", [])) > 0, "orders 30d must have provenance IDs"


def test_roas_metric_returns_positive_ratio_with_combined_provenance(db):
    from app.chat.tools import dispatch_tool

    result = dispatch_tool(
        "query_metric",
        {"metric_name": "roas", "time_range": "30d"},
        db,
        MERCHANT,
    )
    assert "error" not in result, f"query_metric(roas) returned error: {result.get('error')}"
    rows = result.get("rows", [])
    assert len(rows) == 1, "roas should return exactly one row"

    row = rows[0]
    roas = row.get("roas")
    revenue = row.get("revenue")
    ad_spend = row.get("ad_spend")

    assert roas is not None and float(roas) > 0
    assert revenue is not None and float(revenue) > 0
    assert ad_spend is not None and float(ad_spend) > 0
    assert abs(float(roas) - float(revenue) / float(ad_spend)) < 1e-6

    prov_ids = result.get("provenance_ids", [])
    assert len(prov_ids) > 0
    assert all(isinstance(p, str) for p in prov_ids)


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


def test_compare_returns_distinct_provenance_ids(db):
    """delta and pct_change must get distinct synthetic provenance IDs that both appear in all_provenance_ids."""
    from app.chat.tools import dispatch_tool

    result = dispatch_tool(
        "compare",
        {"metric_name": "revenue", "period_a": "7d", "period_b": "30d"},
        db,
        MERCHANT,
    )
    assert "error" not in result, f"compare tool error: {result.get('error')}"

    delta_id = result.get("delta_provenance_id")
    pct_id = result.get("pct_change_provenance_id")
    all_ids = result.get("all_provenance_ids", [])

    assert delta_id is not None, "delta_provenance_id missing from compare result"
    assert pct_id is not None, "pct_change_provenance_id missing from compare result"
    assert delta_id != pct_id, "delta_provenance_id and pct_change_provenance_id must be distinct"
    assert delta_id in all_ids, f"delta_provenance_id {delta_id!r} not in all_provenance_ids"
    assert pct_id in all_ids, f"pct_change_provenance_id {pct_id!r} not in all_provenance_ids"

    # Shape checks: each period sub-dict must carry its own provenance_ids
    assert "provenance_ids" in result["period_a"], "period_a missing provenance_ids"
    assert "provenance_ids" in result["period_b"], "period_b missing provenance_ids"


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


def test_query_metric_revenue_by_campaign_raises_clear_error(db):
    """revenue grouped by campaign must raise — orders have no campaign attribution."""
    from app.chat.tools import dispatch_tool
    result = dispatch_tool(
        "query_metric",
        {"metric_name": "revenue", "group_by": "campaign", "time_range": "30d"},
        db,
        MERCHANT,
    )
    assert "error" in result, f"expected an error for revenue+campaign, got rows: {result}"
    err = result["error"].lower()
    assert "campaign" in err and "revenue" in err, (
        f"error message missing key terms: {result['error']!r}"
    )


def test_query_metric_revenue_by_courier_raises_clear_error(db):
    """revenue grouped by courier must raise — order entities have no courier attribute."""
    from app.chat.tools import dispatch_tool
    result = dispatch_tool(
        "query_metric",
        {"metric_name": "revenue", "group_by": "courier", "time_range": "30d"},
        db,
        MERCHANT,
    )
    assert "error" in result, f"expected an error for revenue+courier, got rows: {result}"
    err = result["error"].lower()
    assert "courier" in err and "revenue" in err, (
        f"error message missing key terms: {result['error']!r}"
    )


def test_write_note_persists_and_appends(db):
    """write_note must succeed and the note must appear in entities.attributes.notes."""
    from app.chat.tools import dispatch_tool
    from sqlalchemy import text as sql_text
    import json

    row = db.execute(
        sql_text(
            "SELECT natural_key FROM entities "
            "WHERE merchant_id = :m AND entity_type = 'order' LIMIT 1"
        ),
        {"m": MERCHANT},
    ).fetchone()
    if row is None:
        pytest.skip("no seeded order entities")
    natural_key = row[0]

    result = dispatch_tool(
        "write_note",
        {"entity_natural_key": natural_key, "note": "follow up with carrier"},
        db,
        MERCHANT,
    )
    assert "error" not in result, f"write_note failed: {result.get('error')}"
    assert result.get("status") == "saved"

    notes_raw = db.execute(
        sql_text(
            "SELECT attributes->'notes' FROM entities "
            "WHERE merchant_id = :m AND natural_key = :k"
        ),
        {"m": MERCHANT, "k": natural_key},
    ).scalar()
    assert notes_raw is not None
    notes = notes_raw if isinstance(notes_raw, list) else json.loads(notes_raw)
    assert any("follow up with carrier" == n.get("text") for n in notes), (
        f"note not persisted; got: {notes}"
    )
