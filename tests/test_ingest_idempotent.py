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
    """Verify that a second ingest of the same source_record_id:
    1. does not create a duplicate row (idempotency), and
    2. preserves the *original* payload — matching the runner's on_conflict_do_update
       strategy which only updates fetched_at/run_id so that citation round-trips
       always resolve to the first full fetch, not a potentially partial re-fetch.
    """
    import datetime

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.warehouse.db import SessionLocal, engine, set_merchant
    from app.warehouse.models import Base, RawShopifyOrder

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        set_merchant(db, "test_idempotent")

        original_payload = {"id": 99999, "total_price": "100.00"}
        second_payload = {"id": 99999, "total_price": "999.00"}

        # First insert — establishes the canonical payload.
        stmt_first = (
            pg_insert(RawShopifyOrder.__table__)
            .values(
                merchant_id="test_idempotent",
                source_record_id="order:99999",
                payload=original_payload,
                fetched_at=datetime.datetime.now(datetime.UTC),
                run_id="run_test_idempotent_1",
            )
            .on_conflict_do_update(
                constraint="uq_raw_shopify_orders",
                set_={
                    "fetched_at": datetime.datetime.now(datetime.UTC),
                    "run_id": "run_test_idempotent_1",
                },
            )
        )
        db.execute(stmt_first)
        db.commit()

        # Second insert with a *different* payload — runner must NOT overwrite.
        stmt_second = (
            pg_insert(RawShopifyOrder.__table__)
            .values(
                merchant_id="test_idempotent",
                source_record_id="order:99999",
                payload=second_payload,
                fetched_at=datetime.datetime.now(datetime.UTC),
                run_id="run_test_idempotent_2",
            )
            .on_conflict_do_update(
                constraint="uq_raw_shopify_orders",
                set_={
                    "fetched_at": datetime.datetime.now(datetime.UTC),
                    "run_id": "run_test_idempotent_2",
                },
            )
        )
        db.execute(stmt_second)
        db.commit()

        rows = (
            db.query(RawShopifyOrder)
            .filter_by(
                merchant_id="test_idempotent",
                source_record_id="order:99999",
            )
            .all()
        )

        # Idempotency: exactly one row regardless of how many times we ingest.
        assert len(rows) == 1, f"Expected 1 row after double ingest, got {len(rows)}"

        # Payload preservation: original payload must survive the second ingest.
        # The citation layer resolves <cite> tags to the raw payload in provenance;
        # if the payload were overwritten by a partial re-fetch the cited number
        # could no longer be verified against the source record.
        row = rows[0]
        assert row.payload.get("total_price") == "100.00", (
            f"Original payload was overwritten: got total_price={row.payload.get('total_price')!r}, "
            "expected '100.00'. Runner must preserve the first-fetched payload."
        )
        assert row.run_id == "run_test_idempotent_2", "run_id should be updated to the latest run on conflict"

        # Cleanup
        db.query(RawShopifyOrder).filter_by(
            merchant_id="test_idempotent",
            source_record_id="order:99999",
        ).delete()
        db.commit()
