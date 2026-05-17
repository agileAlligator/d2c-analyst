"""Ingestion runner — pulls from connectors and writes to raw_* tables.

Idempotent: re-running is a no-op (upsert keyed on merchant_id + source_record_id).
"""

import argparse
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.connectors.base import new_run_id
from app.connectors.meta_ads.connector import MetaAdsConnector
from app.connectors.shiprocket.connector import ShiprocketConnector
from app.connectors.shopify.connector import ShopifyConnector
from app.ingest.cursors import get_cursor, save_cursor
from app.warehouse.db import SessionLocal, set_merchant
from app.warehouse.models import (
    RawMetaCampaign,
    RawMetaInsight,
    RawShiprocketShipment,
    RawShopifyCustomer,
    RawShopifyOrder,
    RawShopifyProduct,
    RawShopifyRefund,
)

logger = logging.getLogger(__name__)

# Map (connector_name, resource_type) -> (ORM model, unique constraint name)
RAW_MODEL_MAP: dict[tuple[str, str], tuple[type, str]] = {
    ("shopify", "order"): (RawShopifyOrder, "uq_raw_shopify_orders"),
    ("shopify", "product"): (RawShopifyProduct, "uq_raw_shopify_products"),
    ("shopify", "refund"): (RawShopifyRefund, "uq_raw_shopify_refunds"),
    ("shopify", "customer"): (RawShopifyCustomer, "uq_raw_shopify_customers"),
    ("meta_ads", "insight"): (RawMetaInsight, "uq_raw_meta_insights"),
    ("meta_ads", "campaign"): (RawMetaCampaign, "uq_raw_meta_campaigns"),
    ("shiprocket", "shipment"): (RawShiprocketShipment, "uq_raw_shiprocket_shipments"),
}


def run_connector(merchant_id: str, connector_name: str, db: Session) -> int:
    connector_cls = {
        "shopify": ShopifyConnector,
        "meta_ads": MetaAdsConnector,
        "shiprocket": ShiprocketConnector,
    }[connector_name]

    set_merchant(db, merchant_id)
    connector = connector_cls()
    run_id = new_run_id()
    total = 0

    try:
        for resource in connector.meta().resources:
            try:
                cursor = get_cursor(db, merchant_id, connector_name, resource)
                logger.info("[%s] pulling %s/%s since %s", merchant_id, connector_name, resource, cursor)

                latest_cursor = cursor
                count = 0

                for record in connector.pull(resource, since=cursor):
                    entry = RAW_MODEL_MAP.get((connector_name, record.resource_type))
                    if entry is None:
                        continue
                    model_cls, constraint_name = entry

                    stmt = (
                        pg_insert(model_cls.__table__)
                        .values(
                            id=uuid.uuid4(),
                            merchant_id=merchant_id,
                            source_record_id=record.source_record_id,
                            payload=record.payload,
                            fetched_at=datetime.now(UTC),
                            run_id=run_id,
                        )
                        .on_conflict_do_update(
                            constraint=constraint_name,
                            # Preserve the original payload for provenance round-trip integrity.
                            # APIs sometimes return partial representations on re-fetch (Shopify
                            # lightweight events vs full order fetch); overwriting could corrupt the
                            # source record that citations resolve to. Only update run metadata.
                            set_={"fetched_at": datetime.now(UTC), "run_id": run_id},
                        )
                    )
                    db.execute(stmt)
                    count += 1
                    total += 1

                    # Update cursor to latest seen timestamp if available
                    ts = (
                        record.payload.get("updated_at")
                        or record.payload.get("date_stop")
                        or record.payload.get("created_at")
                    )
                    if ts and (latest_cursor is None or ts > latest_cursor):
                        latest_cursor = ts

                db.commit()
                if latest_cursor and latest_cursor != cursor:
                    save_cursor(db, merchant_id, connector_name, resource, latest_cursor)
                logger.info("[%s] %s/%s: ingested %d records", merchant_id, connector_name, resource, count)
            except Exception:
                db.rollback()
                logger.exception("[%s] %s/%s: failed, skipping resource", merchant_id, connector_name, resource)
    finally:
        connector.close()
    return total


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--merchant", default="demo")
    parser.add_argument("--connector", choices=["shopify", "meta_ads", "shiprocket"])
    parser.add_argument("--all", action="store_true", dest="all_connectors")
    args = parser.parse_args()

    if not args.all_connectors and not args.connector:
        parser.error("Specify --connector <name> or --all")

    connectors = ["shopify", "meta_ads", "shiprocket"] if args.all_connectors else [args.connector]

    with SessionLocal() as db:
        for c in connectors:
            if c:
                n = run_connector(args.merchant, c, db)
                logger.info("Total ingested from %s: %d", c, n)


if __name__ == "__main__":
    main()
