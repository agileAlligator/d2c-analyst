"""Helpers for writing provenance rows."""
import uuid
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.warehouse.models import Provenance


def record(
    db: Session,
    merchant_id: str,
    row_table: str,
    row_pk: str,
    raw_table: str,
    raw_record_id: str,
    transform_id: str,
) -> None:
    stmt = pg_insert(Provenance.__table__).values(
        id=uuid.uuid4(),
        merchant_id=merchant_id,
        row_table=row_table,
        row_pk=row_pk,
        raw_table=raw_table,
        raw_record_id=raw_record_id,
        transform_id=transform_id,
        ingested_at=datetime.now(UTC),
    ).on_conflict_do_nothing(constraint="uq_provenance_dedup")
    db.execute(stmt)



_ALLOWED_RAW_TABLES = {
    "raw_shopify_orders", "raw_shopify_products", "raw_shopify_refunds",
    "raw_meta_insights", "raw_meta_campaigns", "raw_shiprocket_shipments",
    "raw_shopify_customers",
}


def get_raw_payload(db: Session, raw_table: str, raw_record_id: str, merchant_id: str) -> dict | None:
    from sqlalchemy import text
    if raw_table not in _ALLOWED_RAW_TABLES:
        raise ValueError(f"raw_table not allowed: {raw_table!r}")
    row = db.execute(
        text(f"SELECT payload FROM {raw_table} WHERE merchant_id = :mid AND source_record_id = :rid LIMIT 1"),
        {"mid": merchant_id, "rid": raw_record_id},
    ).fetchone()
    return row[0] if row else None
