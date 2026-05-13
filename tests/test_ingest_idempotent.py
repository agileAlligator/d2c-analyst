"""Verify ingestion is idempotent — running twice produces no duplicates."""
import os

import pytest

from app.connectors.base import RawRecord


def test_raw_record_structure():
    record = RawRecord(
        source_record_id="order:12345",
        payload={"id": 12345, "total_price": "799.00"},
        resource_type="order",
    )
    assert record.source_record_id == "order:12345"
    assert record.payload["id"] == 12345


def test_raw_model_map_covers_all_resources():
    from app.ingest.runner import RAW_MODEL_MAP

    expected_keys = [
        ("shopify", "order"),
        ("shopify", "product"),
        ("shopify", "refund"),
        ("shopify", "customer"),
        ("meta_ads", "insight"),
        ("meta_ads", "campaign"),
        ("shiprocket", "shipment"),
    ]
    for key in expected_keys:
        assert key in RAW_MODEL_MAP, f"RAW_MODEL_MAP missing {key}"


@pytest.mark.skipif(os.getenv("DATABASE_URL") is None, reason="Requires live DB")
def test_double_ingest_yields_single_row():
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.warehouse.db import SessionLocal, engine, set_merchant
    from app.warehouse.models import Base, RawShopifyOrder

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        set_merchant(db, "test_idempotent")
        payload = {"id": 99999, "total_price": "100.00"}
        for _ in range(2):
            stmt = (
                pg_insert(RawShopifyOrder.__table__)
                .values(
                    merchant_id="test_idempotent",
                    source_record_id="order:99999",
                    payload=payload,
                    run_id="run_test_idempotent",
                )
                .on_conflict_do_update(
                    constraint="uq_raw_shopify_orders",
                    set_={"payload": payload},
                )
            )
            db.execute(stmt)
            db.commit()

        count = (
            db.query(RawShopifyOrder)
            .filter_by(
                merchant_id="test_idempotent",
                source_record_id="order:99999",
            )
            .count()
        )
        assert count == 1, f"Expected 1 row after double ingest, got {count}"

        # Cleanup
        db.query(RawShopifyOrder).filter_by(
            merchant_id="test_idempotent",
            source_record_id="order:99999",
        ).delete()
        db.commit()
