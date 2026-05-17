"""Incremental ingestion cursor management."""

from sqlalchemy import text
from sqlalchemy.orm import Session


def get_cursor(db: Session, merchant_id: str, connector: str, resource: str) -> str | None:
    row = db.execute(
        text("""
            SELECT cursor FROM ingest_cursors
            WHERE merchant_id = :mid AND connector = :conn AND resource = :res
        """),
        {"mid": merchant_id, "conn": connector, "res": resource},
    ).fetchone()
    return row[0] if row else None


def save_cursor(db: Session, merchant_id: str, connector: str, resource: str, cursor: str):
    db.execute(
        text("""
            INSERT INTO ingest_cursors (merchant_id, connector, resource, cursor, updated_at)
            VALUES (:mid, :conn, :res, :cursor, NOW())
            ON CONFLICT (merchant_id, connector, resource)
            DO UPDATE SET cursor = :cursor, updated_at = NOW()
        """),
        {"mid": merchant_id, "conn": connector, "res": resource, "cursor": cursor},
    )
    db.commit()
