#!/usr/bin/env python3
"""Run all normalizers and identity resolution for a merchant."""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.normalize.identity import resolve_all
from app.normalize.meta_to_universal import normalize_campaigns, normalize_insights
from app.normalize.shiprocket_to_universal import normalize_shipments
from app.normalize.shopify_to_universal import normalize_orders, normalize_refunds
from app.warehouse.db import SessionLocal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--merchant", default="demo")
    args = parser.parse_args()

    with SessionLocal() as db:
        mid = args.merchant
        print(f"Normalizing {mid}...")
        print(f"  Shopify orders: {normalize_orders(db, mid)}")
        print(f"  Shopify refunds: {normalize_refunds(db, mid)}")
        print(f"  Meta campaigns: {normalize_campaigns(db, mid)}")
        print(f"  Meta insights: {normalize_insights(db, mid)}")
        print(f"  Shiprocket shipments: {normalize_shipments(db, mid)}")
        links = resolve_all(db, mid)
        print(f"  Links resolved: {links}")


if __name__ == "__main__":
    main()
