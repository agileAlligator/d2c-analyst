"""Cross-source identity resolution — links Shopify orders ↔ Shiprocket shipments, Meta ↔ Shopify."""
import logging
import uuid

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.warehouse.models import Link

logger = logging.getLogger(__name__)


def resolve_all(db: Session, merchant_id: str) -> dict[str, int]:
    counts = {}
    counts["order_shipment"] = _link_shopify_shiprocket(db, merchant_id)
    counts["order_campaign"] = _link_meta_shopify(db, merchant_id)
    db.commit()
    return counts


def _link_shopify_shiprocket(db: Session, merchant_id: str) -> int:
    """High-confidence: match on channel_order_id == Shopify order number."""
    rows = db.execute(text("""
        SELECT
            e_order.entity_id AS order_entity_id,
            e_ship.entity_id  AS ship_entity_id
        FROM entities e_order
        JOIN entities e_ship
            ON e_order.merchant_id = e_ship.merchant_id
           AND e_order.attributes->>'order_number' IS NOT NULL
           AND e_ship.attributes->>'channel_order_id' IS NOT NULL
           AND e_order.attributes->>'order_number' = e_ship.attributes->>'channel_order_id'
        WHERE e_order.merchant_id = :mid
          AND e_order.entity_type = 'order'
          AND e_ship.entity_type  = 'shipment'
    """), {"mid": merchant_id}).fetchall()

    count = 0
    for order_eid, ship_eid in rows:
        _upsert_link(db, merchant_id, order_eid, ship_eid, "order_shipment", 1.0, "order_number_match")
        count += 1
    logger.info("[%s] Linked %d Shopify orders → Shiprocket shipments", merchant_id, count)
    return count


def _link_meta_shopify(db: Session, merchant_id: str) -> int:
    """Match orders to Meta campaigns by discount code prefix (e.g. 'CAMP001' → 'camp_001').

    Only links an order to the specific campaign whose id appears in the discount code,
    not to every campaign. Confidence 0.6 — discount codes are campaign-scoped in the seed.
    """
    rows = db.execute(text("""
        SELECT DISTINCT
            e_order.entity_id AS order_entity_id,
            e_camp.entity_id  AS campaign_entity_id,
            e_order.attributes->>'discount_codes' AS codes,
            e_camp.attributes->>'meta_campaign_id' AS campaign_id
        FROM entities e_order
        JOIN entities e_camp ON e_order.merchant_id = e_camp.merchant_id
        WHERE e_order.merchant_id = :mid
          AND e_order.entity_type = 'order'
          AND e_camp.entity_type  = 'ad_campaign'
          AND e_order.attributes->'discount_codes' != '[]'::jsonb
          AND e_order.attributes->>'discount_codes' IS NOT NULL
    """), {"mid": merchant_id}).fetchall()

    import json as _json
    count = 0
    for order_eid, camp_eid, codes_raw, campaign_id in rows:
        if not codes_raw or not campaign_id:
            continue
        try:
            codes = _json.loads(codes_raw) if isinstance(codes_raw, str) else codes_raw
        except Exception:
            continue
        # Only link if a discount code contains the campaign_id as a substring
        if any(campaign_id.lower() in str(c).lower() for c in codes):
            _upsert_link(db, merchant_id, order_eid, camp_eid, "order_campaign", 0.6, "discount_code_prefix")
            count += 1

    logger.info("[%s] Linked %d orders → Meta campaigns (discount code prefix)", merchant_id, count)
    return count


def _upsert_link(db: Session, merchant_id: str,
                 from_entity, to_entity,
                 link_type: str, confidence: float, method: str):
    stmt = pg_insert(Link.__table__).values(
        id=uuid.uuid4(),
        merchant_id=merchant_id,
        from_entity=from_entity,
        to_entity=to_entity,
        link_type=link_type,
        confidence=confidence,
        method=method,
    ).on_conflict_do_update(
        constraint="uq_link",
        set_={"confidence": confidence},
    )
    db.execute(stmt)
