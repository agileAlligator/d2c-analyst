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
        conn.execute(text("ALTER TABLE ingest_cursors ENABLE ROW LEVEL SECURITY"))
        conn.execute(text("ALTER TABLE ingest_cursors FORCE ROW LEVEL SECURITY"))
        conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_policies
                    WHERE tablename = 'ingest_cursors'
                      AND policyname = 'merchant_isolation'
                ) THEN
                    CREATE POLICY merchant_isolation ON ingest_cursors
                        USING (merchant_id = current_setting('app.current_merchant', true));
                END IF;
            END
            $$
        """))
        conn.commit()
    print("ingest_cursors table ready.")


if __name__ == "__main__":
    run()
