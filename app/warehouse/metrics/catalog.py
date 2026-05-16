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
    # Bug 9 fix: aggregate first in CTE, then fetch provenance via scalar subquery to avoid
    # LEFT JOIN fan-out that multiplies amounts by the number of provenance rows per event.
    # Bug 12 fix: include 'refund' events (stored with negative amounts) so refunds reduce revenue.
    "revenue": """
        WITH agg AS (
            SELECT
                {group_by_select}
                SUM(ev.amount) AS revenue,
                COUNT(DISTINCT CASE WHEN ev.event_type = 'order_revenue' THEN ev.entity_id END) AS order_count,
                ARRAY_AGG(DISTINCT ev.event_id::text) AS event_ids
            FROM events ev
            JOIN entities en ON ev.entity_id = en.entity_id
            WHERE ev.event_type IN ('order_revenue', 'refund')
              AND ev.merchant_id = :merchant_id
              {time_filter}
              {extra_filters}
            {group_by_clause}
        )
        SELECT
            agg.*,
            (
                SELECT ARRAY_AGG(DISTINCT raw_record_id)
                FROM provenance
                WHERE row_table = 'events'
                  AND row_pk = ANY(agg.event_ids)
            ) AS provenance_ids
        FROM agg
    """,

    "orders": """
        WITH agg AS (
            SELECT
                {group_by_select}
                COUNT(DISTINCT ev.entity_id) AS order_count,
                ARRAY_AGG(DISTINCT ev.event_id::text) AS event_ids
            FROM events ev
            JOIN entities en ON ev.entity_id = en.entity_id
            WHERE ev.event_type = 'order_revenue'
              AND ev.merchant_id = :merchant_id
              {time_filter}
              {extra_filters}
            {group_by_clause}
        )
        SELECT
            agg.*,
            (
                SELECT ARRAY_AGG(DISTINCT raw_record_id)
                FROM provenance
                WHERE row_table = 'events'
                  AND row_pk = ANY(agg.event_ids)
            ) AS provenance_ids
        FROM agg
    """,

    "refunds": """
        WITH agg AS (
            SELECT
                {group_by_select}
                SUM(-ev.amount) AS refunds,
                COUNT(DISTINCT ev.event_id) AS refund_count,
                COUNT(DISTINCT ev.entity_id) AS refunded_order_count,
                ARRAY_AGG(DISTINCT ev.event_id::text) AS event_ids
            FROM events ev
            JOIN entities en ON ev.entity_id = en.entity_id
            WHERE ev.event_type = 'refund'
              AND ev.merchant_id = :merchant_id
              {time_filter}
              {extra_filters}
            {group_by_clause}
        )
        SELECT
            agg.*,
            (
                SELECT ARRAY_AGG(DISTINCT raw_record_id)
                FROM provenance
                WHERE row_table = 'events'
                  AND row_pk = ANY(agg.event_ids)
            ) AS provenance_ids
        FROM agg
    """,

    # Bug 9 fix: same aggregate-first CTE pattern.
    "ad_spend": """
        WITH agg AS (
            SELECT
                {group_by_select}
                SUM(ev.amount) AS ad_spend,
                SUM((ev.attributes->>'impressions')::numeric) AS impressions,
                SUM((ev.attributes->>'clicks')::numeric) AS clicks,
                ARRAY_AGG(DISTINCT ev.event_id::text) AS event_ids
            FROM events ev
            JOIN entities en ON ev.entity_id = en.entity_id
            WHERE ev.event_type = 'ad_spend'
              AND ev.merchant_id = :merchant_id
              {time_filter}
              {extra_filters}
            {group_by_clause}
        )
        SELECT
            agg.*,
            (
                SELECT ARRAY_AGG(DISTINCT raw_record_id)
                FROM provenance
                WHERE row_table = 'events'
                  AND row_pk = ANY(agg.event_ids)
            ) AS provenance_ids
        FROM agg
    """,

    # Bugs 15+16 fix: count denominator from entities (shipment type) so free-shipping orders
    # are included and the rate cannot exceed 100%.  Provenance is fetched via entity_id scalar
    # subquery (entities table, not events).
    # Bug (time-window): filter RTO count by rto event's occurred_at (the actual shipment date
    # set during normalization) rather than en.first_seen (which is always normalize-run time).
    # The denominator (total_shipments) is intentionally unfiltered — we count all shipments
    # as the base, and only restrict which RTOs fall in the window.
    "rto_rate": """
        WITH rto_events AS (
            SELECT DISTINCT ev.entity_id
            FROM events ev
            WHERE ev.event_type = 'rto'
              AND ev.merchant_id = :merchant_id
              {time_filter}
        ),
        agg AS (
            SELECT
                {group_by_select}
                CAST(
                    COUNT(DISTINCT CASE WHEN rto_ev.entity_id IS NOT NULL THEN en.entity_id END) AS float
                ) / NULLIF(COUNT(DISTINCT en.entity_id), 0) AS rto_rate,
                ROUND(
                    100.0 * COUNT(DISTINCT CASE WHEN rto_ev.entity_id IS NOT NULL THEN en.entity_id END)
                        / NULLIF(COUNT(DISTINCT en.entity_id), 0),
                    1
                ) AS rto_rate_pct,
                COUNT(DISTINCT CASE WHEN rto_ev.entity_id IS NOT NULL THEN en.entity_id END) AS rto_count,
                COUNT(DISTINCT en.entity_id) AS total_shipments,
                ARRAY_AGG(DISTINCT en.entity_id::text) AS event_ids
            FROM entities en
            LEFT JOIN rto_events rto_ev ON rto_ev.entity_id = en.entity_id
            WHERE en.entity_type = 'shipment'
              AND en.merchant_id = :merchant_id
              {extra_filters}
            {group_by_clause}
        )
        SELECT
            agg.*,
            (
                SELECT ARRAY_AGG(DISTINCT raw_record_id)
                FROM provenance
                WHERE row_table = 'entities'
                  AND row_pk = ANY(agg.event_ids)
            ) AS provenance_ids
        FROM agg
    """,

    # Bug 9 fix: aggregate revenue and shipping CTEs first, then assemble provenance
    # via scalar subqueries on collected event_ids.
    # Bug 14 fix: {time_filter} added to shipping CTE so it respects the same window
    # as the revenue CTE (was previously aggregating ALL TIME for shipping/rto).
    # Bug 12 fix: revenue CTE includes 'refund' events.
    "contribution_margin": """
        WITH revenue AS (
            SELECT
                en.attributes->>'order_number' AS order_number,
                SUM(ev.amount) AS revenue,
                ARRAY_AGG(DISTINCT ev.event_id::text) AS rev_event_ids
            FROM events ev
            JOIN entities en ON ev.entity_id = en.entity_id
            WHERE ev.event_type IN ('order_revenue', 'refund')
              AND ev.merchant_id = :merchant_id
              {time_filter}
            GROUP BY en.attributes->>'order_number'
        ),
        shipping AS (
            SELECT
                en.attributes->>'channel_order_id' AS order_number,
                SUM(CASE WHEN ev.event_type = 'shipping_cost' THEN ev.amount ELSE 0 END) AS shipping_cost,
                SUM(CASE WHEN ev.event_type = 'rto' THEN ev.amount ELSE 0 END) AS rto_cost,
                ARRAY_AGG(DISTINCT ev.event_id::text) AS ship_event_ids
            FROM events ev
            JOIN entities en ON ev.entity_id = en.entity_id
            WHERE ev.event_type IN ('shipping_cost', 'rto')
              AND ev.merchant_id = :merchant_id
              {time_filter}
            GROUP BY en.attributes->>'channel_order_id'
        )
        SELECT
            r.order_number,
            r.revenue,
            COALESCE(s.shipping_cost, 0) AS shipping_cost,
            COALESCE(s.rto_cost, 0) AS rto_cost,
            r.revenue - COALESCE(s.shipping_cost, 0) - COALESCE(s.rto_cost, 0) AS contribution_margin,
            (
                SELECT ARRAY_AGG(DISTINCT raw_record_id)
                FROM provenance
                WHERE row_table = 'events'
                  AND row_pk = ANY(
                      r.rev_event_ids || COALESCE(s.ship_event_ids, ARRAY[]::text[])
                  )
            ) AS provenance_ids
        FROM revenue r
        LEFT JOIN shipping s ON r.order_number = s.order_number
        ORDER BY contribution_margin ASC
        LIMIT 100
    """,

    "average_order_value": """
        WITH agg AS (
            SELECT
                {group_by_select}
                SUM(ev.amount) / NULLIF(COUNT(DISTINCT CASE WHEN ev.event_type = 'order_revenue' THEN ev.entity_id END), 0) AS average_order_value,
                COUNT(DISTINCT CASE WHEN ev.event_type = 'order_revenue' THEN ev.entity_id END) AS order_count,
                SUM(ev.amount) AS total_revenue,
                ARRAY_AGG(DISTINCT ev.event_id::text) AS event_ids
            FROM events ev
            JOIN entities en ON ev.entity_id = en.entity_id
            WHERE ev.event_type IN ('order_revenue', 'refund')
              AND ev.merchant_id = :merchant_id
              {time_filter}
              {extra_filters}
            {group_by_clause}
        )
        SELECT
            agg.*,
            (
                SELECT ARRAY_AGG(DISTINCT raw_record_id)
                FROM provenance
                WHERE row_table = 'events'
                  AND row_pk = ANY(agg.event_ids)
            ) AS provenance_ids
        FROM agg
    """,

    "cac": """
        WITH spend AS (
            SELECT SUM(ev.amount) AS total_spend,
                   ARRAY_AGG(DISTINCT ev.event_id::text) AS event_ids
            FROM events ev
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
            (
                SELECT ARRAY_AGG(DISTINCT raw_record_id)
                FROM provenance
                WHERE row_table = 'events'
                  AND row_pk = ANY(spend.event_ids)
            ) AS provenance_ids
        FROM spend, orders
    """,

    "roas": """
        WITH revenue AS (
            SELECT
                {group_by_select}
                SUM(ev.amount) AS revenue,
                COUNT(DISTINCT CASE WHEN ev.event_type = 'order_revenue' THEN ev.entity_id END) AS order_count,
                ARRAY_AGG(DISTINCT ev.event_id::text) AS event_ids
            FROM events ev
            JOIN entities en ON ev.entity_id = en.entity_id
            WHERE ev.event_type IN ('order_revenue', 'refund')
              AND ev.merchant_id = :merchant_id
              {time_filter}
              {extra_filters}
            {group_by_clause}
        ),
        spend AS (
            SELECT
                SUM(ev.amount) AS ad_spend,
                ARRAY_AGG(DISTINCT ev.event_id::text) AS event_ids
            FROM events ev
            WHERE ev.event_type = 'ad_spend'
              AND ev.merchant_id = :merchant_id
              {time_filter}
        )
        SELECT
            revenue.revenue / NULLIF(spend.ad_spend, 0) AS roas,
            revenue.revenue,
            spend.ad_spend,
            revenue.order_count,
            (
                SELECT ARRAY_AGG(DISTINCT raw_record_id)
                FROM provenance
                WHERE row_table = 'events'
                  AND row_pk = ANY(COALESCE(revenue.event_ids, ARRAY[]::text[]) || COALESCE(spend.event_ids, ARRAY[]::text[]))
            ) AS provenance_ids
        FROM revenue, spend
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

# Which group_by dimensions are semantically valid per metric.
# A non-date group_by on a metric not listed here will produce rows with NULL group
# keys because the metric's entity (e.g. order) doesn't carry the requested attribute
# (e.g. campaign name). We detect this at query time and raise an explicit error.
GROUP_BY_VALID_FOR_METRIC: dict[str, set[str]] = {
    "campaign": {"ad_spend"},     # only ad_spend entities carry campaign names
    "courier": {"rto_rate"},  # courier lives on shipment entities; contribution_margin SQL has no group_by placeholders
    # "date", "week", "month" are valid for any event-based metric — no restriction
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
    "refunds": "refunds",
    "ad_spend": "ad_spend",
    "rto_rate": "rto_rate",
    "contribution_margin": "contribution_margin",
    "cac": "cac",
    "average_order_value": "average_order_value",
    "roas": "roas",
    "orders": "order_count",
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

    template = METRIC_SQL[metric_name]

    # rto_rate now filters on rto event occurred_at, so it uses TIME_FILTERS like other metrics.
    time_filter = f"AND {TIME_FILTERS[time_range]}" if time_range in TIME_FILTERS else ""

    if group_by and group_by in GROUP_BY_EXPRESSIONS:
        if metric_name == "roas":
            raise ValueError(
                "Metric 'roas' does not support group_by — the spend CTE is ungrouped, "
                "so per-group ROAS would divide per-group revenue by total spend (wrong). "
                "Query revenue and ad_spend separately with group_by, then compute ROAS per group."
            )
        if "{group_by_select}" not in template or "{group_by_clause}" not in template:
            raise ValueError(
                f"Metric '{metric_name}' does not support group_by='{group_by}'. "
                "Use a different metric or omit group_by."
            )
        gb_expr = GROUP_BY_EXPRESSIONS[group_by]
        group_by_select = f"{gb_expr} AS {group_by},"
        group_by_clause = "GROUP BY 1"
    else:
        group_by_select = ""
        group_by_clause = ""  # No GROUP BY for ungrouped aggregates

    fmt_kwargs: dict[str, str] = {
        "group_by_select": group_by_select,
        "group_by_clause": group_by_clause,
        "time_filter": time_filter,
        "extra_filters": "",
    }

    sql = template.format(**fmt_kwargs)

    result = db.execute(text(sql), {"merchant_id": merchant_id})
    rows = [dict(zip(result.keys(), row)) for row in result.fetchall()]

    # Detect schema-mismatch group_by: if the requested dimension doesn't exist on
    # this metric's entities, SQL returns NULL group keys. Returning that silently
    # would cause the model to fabricate dimension names. Raise instead.
    if group_by and group_by in GROUP_BY_VALID_FOR_METRIC:
        valid_metrics = GROUP_BY_VALID_FOR_METRIC[group_by]
        if metric_name not in valid_metrics and rows:
            null_group_rows = [r for r in rows if r.get(group_by) is None]
            if null_group_rows:
                supported = ", ".join(sorted(valid_metrics))
                raise ValueError(
                    f"Metric '{metric_name}' does not support group_by='{group_by}' — "
                    f"the warehouse has no {group_by} attribution on {metric_name} events. "
                    f"group_by='{group_by}' is only valid for: {supported}. "
                    f"Omit group_by or query one of those metrics instead."
                )

    # Flatten provenance IDs, filtering out None.
    # Use .get() (not .pop()) so each row retains its own provenance list;
    # the agent uses per-row IDs to cite only sources relevant to that order.
    prov_ids: list[str] = []
    for row in rows:
        ids = row.get("provenance_ids") or []
        ids = [v for v in ids if v is not None]
        row["provenance_ids"] = sorted({str(v) for v in ids})
        prov_ids.extend(ids)

    return MetricResult(rows=rows, provenance_ids=list(set(prov_ids)), sql_used=sql.strip())
