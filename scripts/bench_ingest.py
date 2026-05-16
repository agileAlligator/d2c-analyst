"""Measure JSONB upsert throughput on the raw layer.

Usage:  python scripts/bench_ingest.py [--merchants N] [--orders-per-merchant M]
Default: 10 merchants × 20 orders = 200 rows.
Output:  JSON line: {"rows": N, "elapsed_seconds": X, "rows_per_second": Y}
"""
import argparse
import json
import time
import uuid
from datetime import UTC, datetime
from itertools import groupby

from sqlalchemy import text

from app.warehouse.db import SessionLocal, engine, set_merchant
from app.warehouse.models import RawShopifyOrder, Base


def make_payload(i: int) -> dict:
    return {
        "id": 10_000_000 + i,
        "order_number": str(20_000_000 + i),
        "email": f"bench{i}@example.com",
        "financial_status": "paid",
        "total_price": "499.00",
        "currency": "INR",
        "created_at": datetime.now(UTC).isoformat(),
        "line_items": [{"id": i, "sku": "BENCH-001", "quantity": 1, "price": "499"}],
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--merchants", type=int, default=10)
    p.add_argument("--orders-per-merchant", type=int, default=20)
    args = p.parse_args()

    Base.metadata.create_all(engine)

    payloads = [
        (f"bench_m{m}", m * 1000 + i, make_payload(m * 1000 + i))
        for m in range(args.merchants)
        for i in range(args.orders_per_merchant)
    ]
    n_rows = len(payloads)

    payloads_sorted = sorted(payloads, key=lambda x: x[0])  # sort by merchant_id

    with SessionLocal() as db:
        t0 = time.perf_counter()
        for merchant_id, group in groupby(payloads_sorted, key=lambda x: x[0]):
            set_merchant(db, merchant_id)
            for _, idx, payload in group:
                obj = RawShopifyOrder(
                    id=uuid.uuid4(),
                    merchant_id=merchant_id,
                    source_record_id=f"bench:{merchant_id}:{idx}",
                    payload=payload,
                    fetched_at=datetime.now(UTC),
                    run_id="bench",
                )
                db.merge(obj)
            db.flush()  # write this merchant's rows while the GUC is still set
        db.commit()
        elapsed = time.perf_counter() - t0

    # Cleanup so re-runs are independent — delete under each merchant's GUC
    with SessionLocal() as db:
        for merchant_id in {p[0] for p in payloads}:
            set_merchant(db, merchant_id)
            db.execute(
                text("DELETE FROM raw_shopify_orders WHERE run_id = 'bench' AND merchant_id = :mid"),
                {"mid": merchant_id},
            )
        db.commit()

    result = {
        "rows": n_rows,
        "elapsed_seconds": round(elapsed, 3),
        "rows_per_second": round(n_rows / elapsed, 1),
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
