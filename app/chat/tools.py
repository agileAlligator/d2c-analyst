"""Tool definitions and handlers for the chat agent."""
import json
import logging
from datetime import UTC
from typing import Any

from sqlalchemy.orm import Session

from app.provenance.record import get_raw_payload
from app.warehouse.db import set_merchant
from app.warehouse.duckdb_view import sandboxed_sql
from app.warehouse.metrics.catalog import query_metric

logger = logging.getLogger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "list_entities",
        "description": (
            "List a SAMPLE of entities in the warehouse (orders, products, campaigns, shipments, etc.). "
            "Returns up to `limit` rows (default 20) ordered by last_seen DESC. "
            "This is a SAMPLE, NOT a count — `returned` reports how many rows were returned in this page, "
            "which is capped by `limit` and is NOT the total number of entities of that type. "
            "For 'how many X?' or any total-count question, use the `sql` tool with "
            "SELECT COUNT(*) FROM entities WHERE entity_type = '...' AND merchant_id = :merchant_id instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_type": {
                    "type": "string",
                    "enum": ["order", "product", "sku", "ad_campaign", "shipment", "refund"],
                },
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["entity_type"],
        },
    },
    {
        "name": "query_metric",
        "description": (
            "Query a pre-defined business metric. Returns rows with provenance IDs. "
            "Use this for revenue, ad_spend, rto_rate, contribution_margin, cac, average_order_value, refunds, roas. "
            "Note: `revenue` already nets refunds out (refunds are stored as negative amounts). "
            "To answer 'how much was refunded?' use the `refunds` metric instead — it returns gross refund total as a positive number. "
            "Grain: `contribution_margin` is per ORDER (one row per order_number). "
            "The warehouse has NO SKU-level events — do not relabel order numbers as SKUs. "
            "Use `roas` for return-on-ad-spend questions — it returns revenue/ad_spend in a single cited row; do NOT divide revenue by ad_spend in prose, that produces an uncited number. "
            "Use the 'orders' metric for 'how many orders?' questions — do NOT use sql COUNT(*) or list_entities for order counts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "metric_name": {
                    "type": "string",
                    "enum": ["revenue", "ad_spend", "rto_rate", "contribution_margin", "cac", "average_order_value", "refunds", "roas", "orders"],
                },
                "group_by": {
                    "type": "string",
                    "enum": ["campaign", "courier", "date", "week", "month"],
                    "description": "Optional dimension to group results by.",
                },
                "time_range": {
                    "type": "string",
                    "enum": ["7d", "14d", "30d", "90d"],
                    "default": "30d",
                },
            },
            "required": ["metric_name"],
        },
    },
    {
        "name": "sql",
        "description": (
            "Run a read-only SELECT query against the warehouse. "
            "Use ONLY for questions query_metric cannot answer (e.g. ranking, ORDER BY, specific filters). "
            "Available tables: entities, events, links, provenance. "
            "Full column schema and a worked example are in the system prompt."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "A SELECT-only SQL query."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_raw",
        "description": (
            "Retrieve the original source payload for a provenance ID returned by a tool. "
            "Pass the provenance_id exactly as returned (e.g. 'order:5001', 'insight:camp_001:2026-04-01'). "
            "Use to verify a number before citing it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "provenance_id": {
                    "type": "string",
                    "description": "A provenance ID from query_metric or sql results.",
                },
            },
            "required": ["provenance_id"],
        },
    },
    {
        "name": "compare",
        "description": "Compare two metric values explicitly, returning delta and % change with both cited.",
        "input_schema": {
            "type": "object",
            "properties": {
                "metric_name": {"type": "string"},
                "period_a": {"type": "string", "enum": ["7d", "14d", "30d", "90d"]},
                "period_b": {"type": "string", "enum": ["7d", "14d", "30d", "90d"]},
                "group_by": {"type": "string"},
            },
            "required": ["metric_name", "period_a", "period_b"],
        },
    },
    {
        "name": "write_note",
        "description": "Save an annotation to an entity. The ONLY write operation permitted.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_natural_key": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["entity_natural_key", "note"],
        },
    },
]


def dispatch_tool(
    tool_name: str,
    tool_input: dict,
    db: Session,
    merchant_id: str,
) -> dict[str, Any]:
    set_merchant(db, merchant_id)
    try:
        if tool_name == "list_entities":
            return _list_entities(db, merchant_id, **tool_input)
        elif tool_name == "query_metric":
            return _query_metric(db, merchant_id, **tool_input)
        elif tool_name == "sql":
            return _sql(db, merchant_id, **tool_input)
        elif tool_name == "get_raw":
            return _get_raw(db, merchant_id, **tool_input)
        elif tool_name == "compare":
            return _compare(db, merchant_id, **tool_input)
        elif tool_name == "write_note":
            return _write_note(db, merchant_id, **tool_input)
        else:
            return {"error": f"Unknown tool: {tool_name}"}
    except Exception as e:
        logger.exception("Tool %s failed", tool_name)
        return {"error": str(e)}


def _list_entities(db: Session, merchant_id: str, entity_type: str, limit: int = 20) -> dict:
    from sqlalchemy import text
    rows = db.execute(
        text("""
            SELECT entity_id, natural_key, source, attributes, last_seen
            FROM entities
            WHERE merchant_id = :mid AND entity_type = :etype
            ORDER BY last_seen DESC
            LIMIT :lim
        """),
        {"mid": merchant_id, "etype": entity_type, "lim": limit},
    ).fetchall()
    return {
        "entities": [
            {"entity_id": str(r[0]), "natural_key": r[1], "source": r[2],
             "attributes": r[3], "last_seen": str(r[4])}
            for r in rows
        ],
        "returned": len(rows),
    }


def _query_metric(db: Session, merchant_id: str, metric_name: str,
                  group_by: str | None = None, time_range: str = "30d") -> dict:
    result = query_metric(db, merchant_id, metric_name, group_by, time_range)
    return {
        "rows": result.rows,
        "provenance_ids": result.provenance_ids,
        "metric": metric_name,
        "time_range": time_range,
        "group_by": group_by,
    }


def _sql(db: Session, merchant_id: str, query: str) -> dict:
    rows, prov_ids = sandboxed_sql(query, merchant_id)
    return {"rows": rows, "provenance_ids": prov_ids, "row_count": len(rows)}


_RAW_TABLES = [
    "raw_shopify_orders", "raw_shopify_products", "raw_shopify_refunds",
    "raw_meta_insights", "raw_meta_campaigns", "raw_shiprocket_shipments",
]


def _get_raw(db: Session, merchant_id: str, provenance_id: str) -> dict:
    """Resolve a provenance_id to its source payload by scanning all raw tables."""
    parts = provenance_id.split(":", 1)
    table_hint = parts[0] if len(parts) > 1 else ""

    # Try tables with matching hint first
    ordered = sorted(_RAW_TABLES, key=lambda t: (0 if table_hint in t else 1))
    for table in ordered:
        payload = get_raw_payload(db, table, provenance_id, merchant_id)
        if payload is not None:
            return {"raw_table": table, "raw_record_id": provenance_id, "payload": payload}

    return {"error": f"No raw record found for provenance_id '{provenance_id}'"}


def _compare(db: Session, merchant_id: str, metric_name: str,
             period_a: str, period_b: str, group_by: str | None = None) -> dict:
    from app.warehouse.metrics.catalog import METRIC_VALUE_COL
    a = query_metric(db, merchant_id, metric_name, group_by, period_a)
    b = query_metric(db, merchant_id, metric_name, group_by, period_b)
    value_col = METRIC_VALUE_COL.get(metric_name, metric_name)

    def _total(rows: list[dict]) -> float:
        if not rows:
            return 0.0
        return sum(float(r.get(value_col, 0) or 0) for r in rows)

    val_a = _total(a.rows)
    val_b = _total(b.rows)
    delta = val_b - val_a
    pct = (delta / val_a * 100) if val_a else None

    # Give computed values distinct synthetic provenance IDs so the model can cite each independently
    delta_id = f"computed:{metric_name}:delta:{period_a}vs{period_b}"
    pct_id   = f"computed:{metric_name}:pct_change:{period_a}vs{period_b}"
    all_ids = list(set(a.provenance_ids + b.provenance_ids + [delta_id, pct_id]))

    return {
        "period_a": {
            "range": period_a, "value": val_a, "rows": a.rows,
            "provenance_ids": a.provenance_ids,
        },
        "period_b": {
            "range": period_b, "value": val_b, "rows": b.rows,
            "provenance_ids": b.provenance_ids,
        },
        "delta": delta,
        "delta_provenance_id": delta_id,
        "pct_change": pct,
        "pct_change_provenance_id": pct_id,
        "citation_hint": (
            f"Cite period_a.value with a period_a.provenance_ids id; "
            f"period_b.value with a period_b.provenance_ids id; "
            f"delta ({delta}) with {delta_id!r}; "
            f"pct_change ({pct}) with {pct_id!r}."
        ),
        "all_provenance_ids": all_ids,
    }


def _write_note(db: Session, merchant_id: str, entity_natural_key: str, note: str) -> dict:
    from datetime import datetime

    from sqlalchemy import text
    result = db.execute(
        text("""
            UPDATE entities
            SET attributes = jsonb_set(
                attributes,
                '{notes}',
                COALESCE(attributes->'notes', '[]'::jsonb) || CAST(:note AS jsonb)
            )
            WHERE merchant_id = :mid AND natural_key = :key
        """),
        {
            "mid": merchant_id,
            "key": entity_natural_key,
            "note": json.dumps([{"text": note, "at": datetime.now(UTC).isoformat()}]),
        },
    )
    if result.rowcount == 0:
        return {"error": "Entity not found — no row matched natural_key and merchant_id"}
    db.commit()
    return {"status": "saved", "entity": entity_natural_key}
