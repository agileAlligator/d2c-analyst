"""Normalize layer tests — covers app/normalize/* (was 0–33%).

Pure-function tests run without a DB. DB-gated tests use the live seed data
and verify that each normalizer produces entities/events and is idempotent.
"""
import os

import pytest

DB_URL = os.getenv("DATABASE_URL", "")
pytestmark_db = pytest.mark.skipif(not DB_URL, reason="DATABASE_URL not set")

MERCHANT = "demo"


# ---------------------------------------------------------------------------
# Pure helpers (no DB)
# ---------------------------------------------------------------------------

class TestShopifyHelpers:
    def test_parse_dt_valid_iso(self):
        from app.normalize.shopify_to_universal import _parse_dt
        dt = _parse_dt("2024-01-15T10:30:00+05:30")
        assert dt is not None
        assert dt.year == 2024 and dt.month == 1 and dt.day == 15

    def test_parse_dt_z_suffix(self):
        from app.normalize.shopify_to_universal import _parse_dt
        dt = _parse_dt("2024-03-01T00:00:00Z")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_parse_dt_none(self):
        from app.normalize.shopify_to_universal import _parse_dt
        assert _parse_dt(None) is None

    def test_parse_dt_empty_string(self):
        from app.normalize.shopify_to_universal import _parse_dt
        assert _parse_dt("") is None


class TestShiprocketHelpers:
    def test_is_rto_positive(self):
        from app.normalize.shiprocket_to_universal import _is_rto
        for status in ["RTO", "rto initiated", "returned", "Return To Origin", "RTO Delivered"]:
            assert _is_rto(status), f"Expected {status!r} to be RTO"

    def test_is_rto_negative(self):
        from app.normalize.shiprocket_to_universal import _is_rto
        for status in ["delivered", "in transit", "out for delivery", "pending", ""]:
            assert not _is_rto(status), f"Expected {status!r} to NOT be RTO"

    def test_is_rto_case_insensitive(self):
        from app.normalize.shiprocket_to_universal import _is_rto
        assert _is_rto("RTO INITIATED")
        assert _is_rto("Rto In Transit")


# ---------------------------------------------------------------------------
# DB-gated: Shopify normalizer
# ---------------------------------------------------------------------------

@pytestmark_db
class TestShopifyNormalizer:
    def test_normalize_orders_returns_positive_count(self):
        from app.normalize.shopify_to_universal import normalize_orders
        from app.warehouse.db import SessionLocal

        with SessionLocal() as db:
            count = normalize_orders(db, MERCHANT)
        assert count > 0, "normalize_orders returned 0 — no seed data?"

    def test_normalize_orders_creates_order_entities(self):
        from app.normalize.shopify_to_universal import normalize_orders
        from app.warehouse.db import SessionLocal
        from sqlalchemy import text

        with SessionLocal() as db:
            normalize_orders(db, MERCHANT)
            n = db.execute(
                text("SELECT COUNT(*) FROM entities WHERE merchant_id=:m AND entity_type='order'"),
                {"m": MERCHANT},
            ).scalar()
        assert n > 0, "No order entities after normalize_orders"

    def test_normalize_orders_creates_revenue_events(self):
        from app.normalize.shopify_to_universal import normalize_orders
        from app.warehouse.db import SessionLocal
        from sqlalchemy import text

        with SessionLocal() as db:
            normalize_orders(db, MERCHANT)
            n = db.execute(
                text("SELECT COUNT(*) FROM events WHERE merchant_id=:m AND event_type='order_revenue'"),
                {"m": MERCHANT},
            ).scalar()
        assert n > 0, "No order_revenue events after normalize_orders"

    def test_normalize_orders_idempotent(self):
        """Running twice must not change entity or event counts."""
        from app.normalize.shopify_to_universal import normalize_orders
        from app.warehouse.db import SessionLocal
        from sqlalchemy import text

        with SessionLocal() as db:
            normalize_orders(db, MERCHANT)
            n_entities_1 = db.execute(
                text("SELECT COUNT(*) FROM entities WHERE merchant_id=:m AND entity_type='order'"),
                {"m": MERCHANT},
            ).scalar()
            n_events_1 = db.execute(
                text("SELECT COUNT(*) FROM events WHERE merchant_id=:m AND event_type='order_revenue'"),
                {"m": MERCHANT},
            ).scalar()

        with SessionLocal() as db:
            normalize_orders(db, MERCHANT)
            n_entities_2 = db.execute(
                text("SELECT COUNT(*) FROM entities WHERE merchant_id=:m AND entity_type='order'"),
                {"m": MERCHANT},
            ).scalar()
            n_events_2 = db.execute(
                text("SELECT COUNT(*) FROM events WHERE merchant_id=:m AND event_type='order_revenue'"),
                {"m": MERCHANT},
            ).scalar()

        assert n_entities_1 == n_entities_2, "normalize_orders is not idempotent for entities"
        assert n_events_1 == n_events_2, "normalize_orders is not idempotent for events"

    def test_normalize_refunds_creates_negative_events(self):
        from app.normalize.shopify_to_universal import normalize_refunds
        from app.warehouse.db import SessionLocal
        from sqlalchemy import text

        with SessionLocal() as db:
            normalize_refunds(db, MERCHANT)
            n = db.execute(
                text("SELECT COUNT(*) FROM events WHERE merchant_id=:m AND event_type='refund'"),
                {"m": MERCHANT},
            ).scalar()
        assert n > 0, "No refund events — seed data may have no refunds"


# ---------------------------------------------------------------------------
# DB-gated: Meta normalizer
# ---------------------------------------------------------------------------

@pytestmark_db
class TestMetaNormalizer:
    def test_normalize_campaigns_returns_positive_count(self):
        from app.normalize.meta_to_universal import normalize_campaigns
        from app.warehouse.db import SessionLocal

        with SessionLocal() as db:
            count = normalize_campaigns(db, MERCHANT)
        assert count > 0

    def test_normalize_campaigns_creates_ad_campaign_entities(self):
        from app.normalize.meta_to_universal import normalize_campaigns
        from app.warehouse.db import SessionLocal
        from sqlalchemy import text

        with SessionLocal() as db:
            normalize_campaigns(db, MERCHANT)
            n = db.execute(
                text("SELECT COUNT(*) FROM entities WHERE merchant_id=:m AND entity_type='ad_campaign'"),
                {"m": MERCHANT},
            ).scalar()
        assert n > 0

    def test_normalize_insights_creates_ad_spend_events(self):
        from app.normalize.meta_to_universal import normalize_insights
        from app.warehouse.db import SessionLocal
        from sqlalchemy import text

        with SessionLocal() as db:
            normalize_insights(db, MERCHANT)
            n = db.execute(
                text("SELECT COUNT(*) FROM events WHERE merchant_id=:m AND event_type='ad_spend'"),
                {"m": MERCHANT},
            ).scalar()
        assert n > 0

    def test_normalize_insights_idempotent(self):
        from app.normalize.meta_to_universal import normalize_insights
        from app.warehouse.db import SessionLocal
        from sqlalchemy import text

        with SessionLocal() as db:
            normalize_insights(db, MERCHANT)
            n1 = db.execute(
                text("SELECT COUNT(*) FROM events WHERE merchant_id=:m AND event_type='ad_spend'"),
                {"m": MERCHANT},
            ).scalar()

        with SessionLocal() as db:
            normalize_insights(db, MERCHANT)
            n2 = db.execute(
                text("SELECT COUNT(*) FROM events WHERE merchant_id=:m AND event_type='ad_spend'"),
                {"m": MERCHANT},
            ).scalar()

        assert n1 == n2, "normalize_insights is not idempotent"


# ---------------------------------------------------------------------------
# DB-gated: Shiprocket normalizer
# ---------------------------------------------------------------------------

@pytestmark_db
class TestShiprocketNormalizer:
    def test_normalize_shipments_returns_positive_count(self):
        from app.normalize.shiprocket_to_universal import normalize_shipments
        from app.warehouse.db import SessionLocal

        with SessionLocal() as db:
            count = normalize_shipments(db, MERCHANT)
        assert count > 0

    def test_normalize_shipments_creates_shipment_entities(self):
        from app.normalize.shiprocket_to_universal import normalize_shipments
        from app.warehouse.db import SessionLocal
        from sqlalchemy import text

        with SessionLocal() as db:
            normalize_shipments(db, MERCHANT)
            n = db.execute(
                text("SELECT COUNT(*) FROM entities WHERE merchant_id=:m AND entity_type='shipment'"),
                {"m": MERCHANT},
            ).scalar()
        assert n > 0

    def test_normalize_shipments_creates_shipping_cost_events(self):
        from app.normalize.shiprocket_to_universal import normalize_shipments
        from app.warehouse.db import SessionLocal
        from sqlalchemy import text

        with SessionLocal() as db:
            normalize_shipments(db, MERCHANT)
            n = db.execute(
                text("SELECT COUNT(*) FROM events WHERE merchant_id=:m AND event_type='shipping_cost'"),
                {"m": MERCHANT},
            ).scalar()
        assert n > 0

    def test_normalize_shipments_rto_entities_have_flag(self):
        from app.normalize.shiprocket_to_universal import normalize_shipments
        from app.warehouse.db import SessionLocal
        from sqlalchemy import text

        with SessionLocal() as db:
            normalize_shipments(db, MERCHANT)
            n = db.execute(
                text("""
                    SELECT COUNT(*) FROM entities
                    WHERE merchant_id=:m
                      AND entity_type='shipment'
                      AND attributes->>'is_rto' = 'true'
                """),
                {"m": MERCHANT},
            ).scalar()
        assert n > 0, "No RTO shipments found in seed data"

    def test_normalize_shipments_idempotent(self):
        from app.normalize.shiprocket_to_universal import normalize_shipments
        from app.warehouse.db import SessionLocal
        from sqlalchemy import text

        with SessionLocal() as db:
            normalize_shipments(db, MERCHANT)
            n1 = db.execute(
                text("SELECT COUNT(*) FROM entities WHERE merchant_id=:m AND entity_type='shipment'"),
                {"m": MERCHANT},
            ).scalar()

        with SessionLocal() as db:
            normalize_shipments(db, MERCHANT)
            n2 = db.execute(
                text("SELECT COUNT(*) FROM entities WHERE merchant_id=:m AND entity_type='shipment'"),
                {"m": MERCHANT},
            ).scalar()

        assert n1 == n2, "normalize_shipments is not idempotent"


# ---------------------------------------------------------------------------
# DB-gated: Identity resolver
# ---------------------------------------------------------------------------

@pytestmark_db
class TestIdentityResolver:
    def test_resolve_all_returns_counts(self):
        from app.normalize.identity import resolve_all
        from app.warehouse.db import SessionLocal

        with SessionLocal() as db:
            counts = resolve_all(db, MERCHANT)
        assert "order_shipment" in counts
        assert "order_campaign" in counts

    def test_resolve_all_links_orders_to_shipments(self):
        from app.normalize.identity import resolve_all
        from app.warehouse.db import SessionLocal
        from sqlalchemy import text

        with SessionLocal() as db:
            resolve_all(db, MERCHANT)
            n = db.execute(
                text("""
                    SELECT COUNT(*) FROM links
                    WHERE merchant_id=:m AND link_type='order_shipment'
                """),
                {"m": MERCHANT},
            ).scalar()
        assert n > 0, "No order→shipment links created by identity resolver"

    def test_resolve_all_idempotent(self):
        from app.normalize.identity import resolve_all
        from app.warehouse.db import SessionLocal
        from sqlalchemy import text

        with SessionLocal() as db:
            resolve_all(db, MERCHANT)
            n1 = db.execute(
                text("SELECT COUNT(*) FROM links WHERE merchant_id=:m AND link_type='order_shipment'"),
                {"m": MERCHANT},
            ).scalar()

        with SessionLocal() as db:
            resolve_all(db, MERCHANT)
            n2 = db.execute(
                text("SELECT COUNT(*) FROM links WHERE merchant_id=:m AND link_type='order_shipment'"),
                {"m": MERCHANT},
            ).scalar()

        assert n1 == n2, "resolve_all is not idempotent for order_shipment links"
