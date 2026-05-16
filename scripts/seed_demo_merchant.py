"""Seed a demo merchant with realistic synthetic data for testing."""
import hashlib
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from random import Random

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.warehouse.db import SessionLocal
from app.warehouse.migrations.create_app_role import run as create_app_role
from app.warehouse.migrations.create_cursors import run as create_cursors
from app.warehouse.migrations.create_tables import run as create_tables
from app.warehouse.models import (
    RawMetaCampaign,
    RawMetaInsight,
    RawShiprocketShipment,
    RawShopifyOrder,
    RawShopifyProduct,
    RawShopifyRefund,
)

MERCHANT_ID = "demo"
RNG = Random(42)

# Deterministic anchor — re-seeds produce identical timestamps so README figures stay valid.
BASE_DATE = datetime(2026, 5, 13, tzinfo=UTC)

SKUS = [
    ("SKU-001", "Organic Cotton Tee", 799),
    ("SKU-002", "Bamboo Yoga Pants", 1499),
    ("SKU-003", "Hemp Face Wash", 399),
    ("SKU-004", "Natural Lip Balm", 199),
    ("SKU-005", "Eco Water Bottle", 649),
]
COURIERS = ["Delhivery", "BlueDart", "Xpressbees", "Shadowfax"]
CAMPAIGNS = [
    ("camp_001", "Diwali Sale 2024"),
    ("camp_002", "New Year Push"),
    ("camp_003", "Brand Awareness"),
]


def rand_date(days_ago_max: int = 60) -> datetime:
    return BASE_DATE - timedelta(days=RNG.randint(0, days_ago_max))


def seed():
    from sqlalchemy import create_engine
    import os
    bootstrap_url = os.environ.get("DATABASE_URL", "postgresql://d2c:d2c@localhost:5434/d2c")
    create_app_role(create_engine(bootstrap_url))
    create_tables()
    create_cursors()

    with SessionLocal() as db:
        _seed_products(db)
        order_ids = _seed_orders(db)
        _seed_refunds(db, order_ids)
        _seed_meta(db)
        _seed_shiprocket(db, order_ids)
        db.commit()

    print(f"Demo merchant '{MERCHANT_ID}' seeded successfully.")


def _upsert_raw(db, model_class, merchant_id: str, source_record_id: str, **kwargs):
    """Insert a raw record only if (merchant_id, source_record_id) does not already exist."""
    existing = db.query(model_class).filter_by(
        merchant_id=merchant_id, source_record_id=source_record_id
    ).first()
    if existing is None:
        db.add(model_class(
            id=uuid.uuid4(),
            merchant_id=merchant_id,
            source_record_id=source_record_id,
            **kwargs,
        ))


def _seed_products(db):
    for sku, name, price in SKUS:
        product = {
            "id": int(hashlib.md5(sku.encode()).hexdigest()[:8], 16) % 10**9,
            "title": name,
            "variants": [{"id": int(hashlib.md5((sku + "v").encode()).hexdigest()[:8], 16) % 10**9, "sku": sku, "price": str(price)}],
            "updated_at": datetime.now(UTC).isoformat(),
        }
        _upsert_raw(db, RawShopifyProduct, MERCHANT_ID, f"product:{product['id']}",
                    payload=product, fetched_at=datetime.now(UTC), run_id="seed")


def _seed_orders(db) -> list[int]:
    order_ids = []
    for i in range(80):
        sku, name, price = RNG.choice(SKUS)
        qty = RNG.randint(1, 3)
        total = price * qty
        order_number = 1000 + i
        order_id = 5000 + i
        discount_code = RNG.choice(["", "camp_001", "camp_002", "camp_003", ""])
        created_at = rand_date(60)
        order = {
            "id": order_id,
            "order_number": str(order_number),
            "email": f"customer{i}@example.com",
            "financial_status": "paid",
            "fulfillment_status": "fulfilled",
            "total_price": str(total),
            "subtotal_price": str(total),
            "currency": "INR",
            "created_at": created_at.isoformat(),
            "updated_at": created_at.isoformat(),
            "discount_codes": [{"code": discount_code, "amount": "0"}] if discount_code else [],
            "line_items": [{
                "id": 9000 + i,
                "sku": sku,
                "title": name,
                "quantity": qty,
                "price": str(price),
                "vendor": "Demo Brand",
            }],
            "total_shipping_price_set": {"shop_money": {"amount": "0", "currency_code": "INR"}},
        }
        _upsert_raw(db, RawShopifyOrder, MERCHANT_ID, f"order:{order_id}",
                    payload=order, fetched_at=datetime.now(UTC), run_id="seed")
        order_ids.append((order_id, order_number, total, created_at, sku))
    return order_ids


def _seed_refunds(db, order_ids):
    # ~10% of orders get refunded
    for order_id, order_number, total, created_at, sku in RNG.sample(order_ids, k=8):
        refund_id = 7000 + order_id
        refund = {
            "id": refund_id,
            "order_id": order_id,
            "created_at": (created_at + timedelta(days=RNG.randint(3, 10))).isoformat(),
            "note": "Customer requested refund",
            "currency": "INR",
            "transactions": [{"kind": "refund", "amount": str(total)}],
            "refund_line_items": [{"line_item": {"sku": sku, "quantity": 1}}],
        }
        _upsert_raw(db, RawShopifyRefund, MERCHANT_ID, f"refund:{refund_id}",
                    payload=refund, fetched_at=datetime.now(UTC), run_id="seed")


def _seed_meta(db):
    for camp_id, camp_name in CAMPAIGNS:
        campaign = {"id": camp_id, "name": camp_name, "status": "ACTIVE", "objective": "CONVERSIONS"}
        _upsert_raw(db, RawMetaCampaign, MERCHANT_ID, f"campaign:{camp_id}",
                    payload=campaign, fetched_at=datetime.now(UTC), run_id="seed")

    # Daily insights for each campaign for last 30 days
    for days_ago in range(30):
        date = (BASE_DATE - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        for camp_id, camp_name in CAMPAIGNS:
            spend = RNG.uniform(180, 480)
            impressions = int(spend * RNG.uniform(100, 300))
            clicks = int(impressions * RNG.uniform(0.01, 0.05))
            purchases = int(clicks * RNG.uniform(0.02, 0.08))
            purchase_value = purchases * RNG.uniform(400, 1500)
            insight = {
                "campaign_id": camp_id,
                "campaign_name": camp_name,
                "adset_id": f"adset_{camp_id}",
                "adset_name": f"{camp_name} - Adset 1",
                "ad_id": f"ad_{camp_id}",
                "ad_name": f"{camp_name} - Ad 1",
                "spend": f"{spend:.2f}",
                "impressions": str(impressions),
                "clicks": str(clicks),
                "cpc": f"{spend/max(clicks,1):.2f}",
                "cpm": f"{spend/max(impressions,1)*1000:.2f}",
                "ctr": f"{clicks/max(impressions,1):.4f}",
                "actions": [{"action_type": "purchase", "value": str(purchases)}],
                "action_values": [{"action_type": "purchase", "value": f"{purchase_value:.2f}"}],
                "date_start": date,
                "date_stop": date,
            }
            _upsert_raw(db, RawMetaInsight, MERCHANT_ID, f"insight:{camp_id}:{date}",
                        payload=insight, fetched_at=datetime.now(UTC), run_id="seed")


def _seed_shiprocket(db, order_ids):
    statuses = ["DELIVERED", "DELIVERED", "DELIVERED", "RTO Initiated", "DELIVERED", "RTO Delivered"]
    for i, (order_id, order_number, total, created_at, sku) in enumerate(order_ids):
        status = RNG.choice(statuses)
        courier = RNG.choice(COURIERS)
        freight = RNG.uniform(50, 200)
        rto_charges = RNG.uniform(80, 150) if "RTO" in status else 0
        shipment = {
            "id": 8000 + i,
            "channel_order_id": str(order_number),  # matches Shopify order_number
            "order_id": order_id,
            "awb": f"AWB{100000 + i}",
            "courier_name": courier,
            "status": status,
            "city": RNG.choice(["Mumbai", "Delhi", "Bengaluru", "Hyderabad", "Pune"]),
            "state": RNG.choice(["Maharashtra", "Delhi", "Karnataka", "Telangana"]),
            "pincode": str(RNG.randint(100000, 999999)),
            "freight_charges": f"{freight:.2f}",
            "rto_charges": f"{rto_charges:.2f}",
            "weight": f"{RNG.uniform(0.2, 1.5):.2f}",
            "created_at": created_at.isoformat(),
            "updated_at": (created_at + timedelta(days=RNG.randint(1, 7))).isoformat(),
            "rto_initiated_date": (created_at + timedelta(days=5)).isoformat() if "RTO" in status else None,
        }
        _upsert_raw(db, RawShiprocketShipment, MERCHANT_ID, f"shipment:{shipment['id']}",
                    payload=shipment, fetched_at=datetime.now(UTC), run_id="seed")


if __name__ == "__main__":
    seed()
