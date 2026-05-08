"""Create ingest_cursors table."""
from sqlalchemy import text

from app.warehouse.db import engine


def run():
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ingest_cursors (
                merchant_id TEXT NOT NULL,
                connector TEXT NOT NULL,
                resource TEXT NOT NULL,
                cursor TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (merchant_id, connector, resource)
            )
        """))
        conn.commit()
    print("ingest_cursors table ready.")


if __name__ == "__main__":
    run()
