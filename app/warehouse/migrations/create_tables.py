"""Create all tables and enable RLS."""
from sqlalchemy import text

from app.warehouse.db import engine
from app.warehouse.models import Base


def run():
    Base.metadata.create_all(engine)

    with engine.connect() as conn:
        # Enable RLS on multi-tenant tables
        tables = [
            "entities", "events", "links", "provenance",
            "raw_shopify_orders", "raw_shopify_products", "raw_shopify_refunds",
            "raw_shopify_customers",
            "raw_meta_insights", "raw_meta_campaigns",
            "raw_shiprocket_shipments",
            "agent_runs", "ingest_jobs",
        ]
        for tbl in tables:
            conn.execute(text(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY"))
            conn.execute(text(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY"))
            conn.execute(text(f"DROP POLICY IF EXISTS merchant_isolation ON {tbl}"))
            conn.execute(text(
                f"CREATE POLICY merchant_isolation ON {tbl}"
                f" USING (merchant_id = current_setting('app.current_merchant', true))"
            ))
        conn.commit()
    print("Tables created and RLS policies applied.")


if __name__ == "__main__":
    run()
