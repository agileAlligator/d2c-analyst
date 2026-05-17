"""Normalize raw Shopify records into universal entities + events."""
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.provenance.record import record as prov_record
from app.warehouse.db import set_merchant
from app.warehouse.models import Entity, Event, RawShopifyOrder, RawShopifyRefund

logger = logging.getLogger(__name__)
TRANSFORM_ID = "shopify_normalizer_v1"


def normalize_orders(db: Session, merchant_id: str) -> int:
    set_merchant(db, merchant_id)
    raw_orders = db.query(RawShopifyOrder).filter_by(merchant_id=merchant_id).all()
    count = 0
    for raw in raw_orders:
        _upsert_order(db, merchant_id, raw)
        count += 1
    db.commit()
    return count


def _upsert_order(db: Session, merchant_id: str, raw: RawShopifyOrder):
    p = raw.payload
    order_id = str(p["id"])
    natural_key = f"shopify:order:{order_id}"

    entity_id = _upsert_entity(db, merchant_id, "order", natural_key, "shopify", {
        "shopify_order_id": order_id,
        "order_number": p.get("order_number"),
        "email": p.get("email"),
        "financial_status": p.get("financial_status"),
        "fulfillment_status": p.get("fulfillment_status"),
        "tags": p.get("tags", ""),
        "discount_codes": [d.get("code") for d in p.get("discount_codes", [])],
    })

    prov_record(db, merchant_id, "entities", str(entity_id),
                "raw_shopify_orders", raw.source_record_id, TRANSFORM_ID)

    # Revenue event — use subtotal_price (goods revenue only), fall back to total_price.
    # Compare Decimal values so "0.00" (shipping-only / gift-card orders) falls through
    # to total_price rather than being accepted as a truthy non-empty string.
    subtotal = Decimal(str(p.get("subtotal_price") or "0"))
    total = Decimal(str(p.get("total_price") or "0"))
    amount = subtotal if subtotal > 0 else total
    occurred_at = _parse_dt(p.get("created_at"))
    if occurred_at and amount > 0:
        event_id = _upsert_event(db, merchant_id, entity_id, "order_revenue", occurred_at,
                                  amount, p.get("currency", "INR"), None, {
                                      "subtotal": str(p.get("subtotal_price", "0")),
                                      "shipping": str(p.get("total_shipping_price_set", {})
                                                       .get("shop_money", {}).get("amount", "0")),
                                      "line_items": [
                                          {
                                              "sku": li.get("sku"),
                                              "title": li.get("title"),
                                              "quantity": li.get("quantity"),
                                              "price": li.get("price"),
                                              "vendor": li.get("vendor"),
                                          }
                                          for li in p.get("line_items", [])
                                      ],
                                  })
        prov_record(db, merchant_id, "events", str(event_id),
                    "raw_shopify_orders", raw.source_record_id, TRANSFORM_ID)


def normalize_refunds(db: Session, merchant_id: str) -> int:
    set_merchant(db, merchant_id)
    raw_refunds = db.query(RawShopifyRefund).filter_by(merchant_id=merchant_id).all()
    count = 0
    for raw in raw_refunds:
        _upsert_refund(db, merchant_id, raw)
        count += 1
    db.commit()
    return count


def _upsert_refund(db: Session, merchant_id: str, raw: RawShopifyRefund):
    p = raw.payload
    refund_id = str(p["id"])
    order_id = str(p.get("order_id", "unknown"))
    natural_key = f"shopify:refund:{refund_id}"

    # Resolve order_number from the linked order entity so the contribution_margin
    # CTE can group refund events by the same key as order_revenue events.
    order_number = None
    order_entity = (
        db.query(Entity)
        .filter_by(merchant_id=merchant_id, entity_type="order")
        .filter(Entity.attributes["shopify_order_id"].astext == order_id)
        .first()
    )
    if order_entity:
        order_number = order_entity.attributes.get("order_number")

    entity_id = _upsert_entity(db, merchant_id, "refund", natural_key, "shopify", {
        "shopify_refund_id": refund_id,
        "shopify_order_id": order_id,
        "order_number": order_number,
        "note": p.get("note"),
    })
    prov_record(db, merchant_id, "entities", str(entity_id),
                "raw_shopify_refunds", raw.source_record_id, TRANSFORM_ID)

    total_refund = sum(
        Decimal(str(t.get("amount", "0")))
        for t in p.get("transactions", [])
        if t.get("kind") == "refund"
    )
    occurred_at = _parse_dt(p.get("created_at"))
    if occurred_at and total_refund > 0:
        event_id = _upsert_event(db, merchant_id, entity_id, "refund", occurred_at,
                                  -total_refund, p.get("currency", "INR"), None, {
                                      "order_id": order_id,
                                      "refund_line_items": p.get("refund_line_items", []),
                                  })
        prov_record(db, merchant_id, "events", str(event_id),
                    "raw_shopify_refunds", raw.source_record_id, TRANSFORM_ID)


def _upsert_entity(db: Session, merchant_id: str, entity_type: str,
                   natural_key: str, source: str, attributes: dict) -> uuid.UUID:
    now = datetime.now(UTC)
    stmt = pg_insert(Entity.__table__).values(
        entity_id=uuid.uuid4(),
        merchant_id=merchant_id,
        entity_type=entity_type,
        natural_key=natural_key,
        source=source,
        attributes=attributes,
        first_seen=now,
        last_seen=now,
    ).on_conflict_do_update(
        constraint="uq_entity_natural_key",
        set_={"attributes": attributes, "last_seen": now},
    ).returning(Entity.__table__.c.entity_id)
    result = db.execute(stmt)
    return result.scalar()


def _get_or_create_entity(db: Session, merchant_id: str, entity_type: str,
                          natural_key: str, source: str, attributes: dict) -> uuid.UUID:
    """Insert the entity only if it does not already exist.

    Unlike _upsert_entity, this never overwrites attributes on conflict —
    safe to call from insight normalizers that must not clobber the richer
    attributes written by dedicated campaign/adset normalizers.
    """
    now = datetime.now(UTC)
    new_id = uuid.uuid4()
    stmt = pg_insert(Entity.__table__).values(
        entity_id=new_id,
        merchant_id=merchant_id,
        entity_type=entity_type,
        natural_key=natural_key,
        source=source,
        attributes=attributes,
        first_seen=now,
        last_seen=now,
    ).on_conflict_do_nothing(
        constraint="uq_entity_natural_key",
    )
    db.execute(stmt)
    # Whether we inserted or hit the conflict, fetch the authoritative id.
    row = (
        db.query(Entity.entity_id)
        .filter_by(merchant_id=merchant_id, natural_key=natural_key)
        .one()
    )
    return row.entity_id


def _upsert_event(db: Session, merchant_id: str, entity_id: uuid.UUID,
                  event_type: str, occurred_at: datetime, amount: Decimal,
                  currency: str, quantity, attributes: dict) -> uuid.UUID:
    # Deterministic event_id so re-running normalization is idempotent.
    # Key: (merchant_id, entity_id, event_type, occurred_at)
    import hashlib
    key = f"{merchant_id}:{entity_id}:{event_type}:{occurred_at.isoformat()}"
    event_id = uuid.UUID(hashlib.md5(key.encode()).hexdigest())
    stmt = pg_insert(Event.__table__).values(
        event_id=event_id,
        merchant_id=merchant_id,
        entity_id=entity_id,
        event_type=event_type,
        occurred_at=occurred_at,
        amount=amount,
        currency=currency,
        quantity=quantity,
        attributes=attributes,
    ).on_conflict_do_update(
        index_elements=["event_id"],
        set_={"amount": amount, "attributes": attributes},
    )
    db.execute(stmt)
    return event_id


def _parse_dt(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except Exception:
        return None
