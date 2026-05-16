"""Seed a second merchant (merchant_id='demo2') to prove RLS isolation."""
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from random import Random

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.warehouse.db import SessionLocal
from app.warehouse.models import RawShopifyOrder

MERCHANT_ID = "demo2"
RNG = Random(99)

# Deterministic anchor — re-seeds produce identical timestamps so README figures stay valid.
BASE_DATE = datetime(2026, 5, 13, tzinfo=UTC)


def seed():
    with SessionLocal() as db:
        # 5 orders for demo2
        for i in range(5):
            order_id = 9000 + i
            order_number = 2000 + i
            total = RNG.randint(500, 3000)
            created_at = BASE_DATE - timedelta(days=RNG.randint(1, 30))
            order = {
                "id": order_id,
                "order_number": str(order_number),
                "email": f"demo2_customer{i}@example.com",
                "financial_status": "paid",
                "total_price": str(total),
                "currency": "INR",
                "created_at": created_at.isoformat(),
                "updated_at": created_at.isoformat(),
                "discount_codes": [],
                "line_items": [{"id": 1000 + i, "sku": "SKU-X01", "title": "Product X",
                                 "quantity": 1, "price": str(total), "vendor": "Demo2"}],
                "total_shipping_price_set": {"shop_money": {"amount": "0", "currency_code": "INR"}},
            }
            db.merge(RawShopifyOrder(
                id=uuid.uuid4(),
                merchant_id=MERCHANT_ID,
                source_record_id=f"order:{order_id}",
                payload=order,
                fetched_at=datetime.now(UTC),
                run_id="seed2",
            ))
        db.commit()
    print(f"Merchant '{MERCHANT_ID}' seeded with 5 orders.")


if __name__ == "__main__":
    seed()
