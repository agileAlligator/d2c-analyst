"""Typed metric definitions — safe, pre-validated queries returning rows + provenance IDs."""
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass
class MetricResult:
    rows: list[dict[str, Any]]
    provenance_ids: list[str]
    sql_used: str


# {group_by_select} — empty or "expr AS dim,"
# {group_by_clause} — empty or "GROUP BY 1"
# {time_filter}     — empty or "AND ev.occurred_at >= ..."
# {extra_filters}   — reserved, always ""

METRIC_SQL: dict[str, str] = {
    "revenue": """
        SELECT
            {group_by_select}
            SUM(ev.amount) AS revenue,
            COUNT(DISTINCT ev.entity_id) AS order_count,
            ARRAY_AGG(DISTINCT p.raw_record_id) FILTER (WHERE p.raw_record_id IS NOT NULL) AS provenance_ids
        FROM events ev
        JOIN entities en ON ev.entity_id = en.entity_id
        LEFT JOIN provenance p ON p.row_table = 'events' AND p.row_pk = ev.event_id::text
        WHERE ev.event_type = 'order_revenue'
          AND ev.merchant_id = :merchant_id
          {time_filter}
          {extra_filters}
        {group_by_clause}
    """,

    "ad_spend": """
        SELECT
            {group_by_select}
            SUM(ev.amount) AS ad_spend,
            SUM((ev.attributes->>'impressions')::numeric) AS impressions,
            SUM((ev.attributes->>'clicks')::numeric) AS clicks,
            ARRAY_AGG(DISTINCT p.raw_record_id) FILTER (WHERE p.raw_record_id IS NOT NULL) AS provenance_ids
        FROM events ev
        JOIN entities en ON ev.entity_id = en.entity_id
        LEFT JOIN provenance p ON p.row_table = 'events' AND p.row_pk = ev.event_id::text
        WHERE ev.event_type = 'ad_spend'
          AND ev.merchant_id = :merchant_id
          {time_filter}
          {extra_filters}
        {group_by_clause}
    """,

    "rto_rate": """
        SELECT
            {group_by_select}
            COUNT(DISTINCT CASE WHEN ev.event_type = 'rto' THEN ev.entity_id END)::float /
                NULLIF(COUNT(DISTINCT CASE WHEN ev.event_type = 'shipping_cost' THEN ev.entity_id END), 0) AS rto_rate,
            COUNT(DISTINCT CASE WHEN ev.event_type = 'rto' THEN ev.entity_id END) AS rto_count,
            COUNT(DISTINCT CASE WHEN ev.event_type = 'shipping_cost' THEN ev.entity_id END) AS total_shipments,
            ARRAY_AGG(DISTINCT p.raw_record_id) FILTER (WHERE p.raw_record_id IS NOT NULL) AS provenance_ids
        FROM events ev
        JOIN entities en ON ev.entity_id = en.entity_id
        LEFT JOIN provenance p ON p.row_table = 'events' AND p.row_pk = ev.event_id::text
        WHERE ev.event_type IN ('rto', 'shipping_cost')
          AND ev.merchant_id = :merchant_id
          {time_filter}
          {extra_filters}
        {group_by_clause}
    """,

    # Join revenue ↔ shipping via order_number (Shopify order_number == Shiprocket channel_order_id)
    "contribution_margin": """
        WITH revenue AS (
            SELECT
                en.attributes->>'order_number' AS order_number,
                SUM(ev.amount) AS revenue,
                ARRAY_AGG(DISTINCT p.raw_record_id) FILTER (WHERE p.raw_record_id IS NOT NULL) AS rev_prov
            FROM events ev
            JOIN entities en ON ev.entity_id = en.entity_id
            LEFT JOIN provenance p ON p.row_table = 'events' AND p.row_pk = ev.event_id::text
            WHERE ev.event_type = 'order_revenue'
              AND ev.merchant_id = :merchant_id
              {time_filter}
            GROUP BY en.attributes->>'order_number'
        ),
        shipping AS (
            SELECT
                en.attributes->>'channel_order_id' AS order_number,
                SUM(CASE WHEN ev.event_type = 'shipping_cost' THEN ev.amount ELSE 0 END) AS shipping_cost,
                SUM(CASE WHEN ev.event_type = 'rto' THEN ev.amount ELSE 0 END) AS rto_cost,
                ARRAY_AGG(DISTINCT p.raw_record_id) FILTER (WHERE p.raw_record_id IS NOT NULL) AS ship_prov
            FROM events ev
            JOIN entities en ON ev.entity_id = en.entity_id
            LEFT JOIN provenance p ON p.row_table = 'events' AND p.row_pk = ev.event_id::text
            WHERE ev.event_type IN ('shipping_cost', 'rto')
              AND ev.merchant_id = :merchant_id
            GROUP BY en.attributes->>'channel_order_id'
        )
        SELECT
            r.order_number,
            r.revenue,
            COALESCE(s.shipping_cost, 0) AS shipping_cost,
            COALESCE(s.rto_cost, 0) AS rto_cost,
            r.revenue - COALESCE(s.shipping_cost, 0) - COALESCE(s.rto_cost, 0) AS contribution_margin,
            r.rev_prov || COALESCE(s.ship_prov, ARRAY[]::text[]) AS provenance_ids
        FROM revenue r
        LEFT JOIN shipping s ON r.order_number = s.order_number
        ORDER BY contribution_margin ASC
        LIMIT 100
    """,

    "cac": """
        WITH spend AS (
            SELECT SUM(ev.amount) AS total_spend,
                   ARRAY_AGG(DISTINCT p.raw_record_id) FILTER (WHERE p.raw_record_id IS NOT NULL) AS prov_ids
            FROM events ev
            LEFT JOIN provenance p ON p.row_table = 'events' AND p.row_pk = ev.event_id::text
            WHERE ev.event_type = 'ad_spend'
              AND ev.merchant_id = :merchant_id
              {time_filter}
        ),
        orders AS (
            SELECT COUNT(DISTINCT ev.entity_id) AS total_orders
            FROM events ev
            WHERE ev.event_type = 'order_revenue'
              AND ev.merchant_id = :merchant_id
              {time_filter}
        )
        SELECT
            spend.total_spend,
            orders.total_orders,
            spend.total_spend / NULLIF(orders.total_orders, 0) AS cac,
            spend.prov_ids AS provenance_ids
        FROM spend, orders
    """,
}

# SKU grouping uses a lateral join subquery (templated as a special case in query_metric)
GROUP_BY_EXPRESSIONS = {
    "campaign": "en.attributes->>'name'",
    "courier": "en.attributes->>'courier'",
    "date": "DATE(ev.occurred_at)",
    "week": "DATE_TRUNC('week', ev.occurred_at)",
    "month": "DATE_TRUNC('month', ev.occurred_at)",
}

TIME_FILTERS = {
    "7d": "ev.occurred_at >= NOW() - INTERVAL '7 days'",
    "14d": "ev.occurred_at >= NOW() - INTERVAL '14 days'",
    "30d": "ev.occurred_at >= NOW() - INTERVAL '30 days'",
    "90d": "ev.occurred_at >= NOW() - INTERVAL '90 days'",
}

# Maps metric name to the primary value column (for compare tool)
METRIC_VALUE_COL = {
    "revenue": "revenue",
    "ad_spend": "ad_spend",
    "rto_rate": "rto_rate",
    "contribution_margin": "contribution_margin",
    "cac": "cac",
}


def query_metric(
    db: Session,
    merchant_id: str,
    metric_name: str,
    group_by: str | None = None,
    time_range: str | None = "30d",
    filters: dict | None = None,
) -> MetricResult:
    if metric_name not in METRIC_SQL:
        raise ValueError(f"Unknown metric: {metric_name}. Available: {list(METRIC_SQL)}")

    time_filter = f"AND {TIME_FILTERS[time_range]}" if time_range in TIME_FILTERS else ""

    if group_by and group_by in GROUP_BY_EXPRESSIONS:
        gb_expr = GROUP_BY_EXPRESSIONS[group_by]
        group_by_select = f"{gb_expr} AS {group_by},"
        group_by_clause = "GROUP BY 1"
    else:
        group_by_select = ""
        group_by_clause = ""  # No GROUP BY for ungrouped aggregates

    sql = METRIC_SQL[metric_name].format(
        group_by_select=group_by_select,
        group_by_clause=group_by_clause,
        time_filter=time_filter,
        extra_filters="",
    )

    result = db.execute(text(sql), {"merchant_id": merchant_id})
    rows = [dict(zip(result.keys(), row)) for row in result.fetchall()]

    # Flatten provenance IDs, filtering out None
    prov_ids: list[str] = []
    for row in rows:
        ids = row.pop("provenance_ids", None) or []
        prov_ids.extend(v for v in ids if v is not None)

    return MetricResult(rows=rows, provenance_ids=list(set(prov_ids)), sql_used=sql.strip())
