"""Normalize raw Meta Ads records into universal entities + events."""
import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from app.normalize.shopify_to_universal import _parse_dt, _upsert_entity, _upsert_event
from app.provenance.record import record as prov_record
from app.warehouse.models import RawMetaCampaign, RawMetaInsight

logger = logging.getLogger(__name__)
TRANSFORM_ID = "meta_normalizer_v1"


def normalize_campaigns(db: Session, merchant_id: str) -> int:
    rows = db.query(RawMetaCampaign).filter_by(merchant_id=merchant_id).all()
    count = 0
    for raw in rows:
        p = raw.payload
        natural_key = f"meta:campaign:{p['id']}"
        entity_id = _upsert_entity(db, merchant_id, "ad_campaign", natural_key, "meta", {
            "meta_campaign_id": str(p["id"]),
            "name": p.get("name"),
            "status": p.get("status"),
            "objective": p.get("objective"),
        })
        prov_record(db, merchant_id, "entities", str(entity_id),
                    "raw_meta_campaigns", raw.source_record_id, TRANSFORM_ID)
        count += 1
    db.commit()
    return count


def normalize_insights(db: Session, merchant_id: str) -> int:
    rows = db.query(RawMetaInsight).filter_by(merchant_id=merchant_id).all()
    count = 0
    for raw in rows:
        _upsert_insight(db, merchant_id, raw)
        count += 1
    db.commit()
    return count


def _upsert_insight(db: Session, merchant_id: str, raw: RawMetaInsight):
    p = raw.payload
    campaign_id = str(p.get("campaign_id", "unknown"))
    adset_id = str(p.get("adset_id", ""))
    ad_id = str(p.get("ad_id", ""))
    date = p.get("date_start", "")

    # Use the finest grain available so multiple ads in the same campaign/day
    # don't collapse to the same entity (and deduplicate spend via MD5 event_id).
    if ad_id and ad_id != "unknown" and ad_id != "":
        entity_key = f"meta:ad:{ad_id}"
        entity_type = "ad_creative"
    elif adset_id and adset_id != "":
        entity_key = f"meta:adset:{adset_id}"
        entity_type = "ad_set"
    else:
        entity_key = f"meta:campaign:{campaign_id}"
        entity_type = "ad_campaign"

    entity_id = _upsert_entity(db, merchant_id, entity_type, entity_key, "meta", {
        "meta_campaign_id": campaign_id,
        "name": p.get("campaign_name"),  # preserved so group_by="campaign" still works
        "adset_id": adset_id,
        "adset_name": p.get("adset_name"),
        "ad_id": ad_id,
        "ad_name": p.get("ad_name"),
    })
    prov_record(db, merchant_id, "entities", str(entity_id),
                "raw_meta_insights", raw.source_record_id, TRANSFORM_ID)

    spend = Decimal(str(p.get("spend", "0")))
    occurred_at = _parse_dt(date + "T00:00:00+00:00") if date else None

    if occurred_at and spend > 0:
        # Extract purchase actions for UTM attribution hints
        actions = p.get("actions", [])
        purchases = next((a for a in actions if a.get("action_type") == "purchase"), {})
        purchase_value = Decimal(str(purchases.get("value", "0")))

        event_id = _upsert_event(db, merchant_id, entity_id, "ad_spend", occurred_at,
                                  spend, "INR", None, {
                                      "impressions": p.get("impressions"),
                                      "clicks": p.get("clicks"),
                                      "cpc": p.get("cpc"),
                                      "cpm": p.get("cpm"),
                                      "ctr": p.get("ctr"),
                                      "purchase_value": str(purchase_value),
                                      "ad_id": ad_id,
                                      "adset_id": p.get("adset_id"),
                                  })
        prov_record(db, merchant_id, "events", str(event_id),
                    "raw_meta_insights", raw.source_record_id, TRANSFORM_ID)
