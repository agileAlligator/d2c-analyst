"""Normalize raw Meta Ads records into universal entities + events."""

import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from app.normalize.shopify_to_universal import (
    _get_or_create_entity,
    _parse_dt,
    _upsert_entity,
    _upsert_event,
)
from app.provenance.record import record as prov_record
from app.warehouse.db import set_merchant
from app.warehouse.models import RawMetaCampaign, RawMetaInsight

logger = logging.getLogger(__name__)
TRANSFORM_ID = "meta_normalizer_v1"


def _safe_int(s: str) -> int:
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return 0


def _safe_float(s: str) -> float:
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def normalize_campaigns(db: Session, merchant_id: str) -> int:
    set_merchant(db, merchant_id)
    rows = db.query(RawMetaCampaign).filter_by(merchant_id=merchant_id).all()
    count = 0
    for raw in rows:
        p = raw.payload
        natural_key = f"meta:campaign:{p['id']}"
        entity_id = _upsert_entity(
            db,
            merchant_id,
            "ad_campaign",
            natural_key,
            "meta",
            {
                "meta_campaign_id": str(p["id"]),
                "name": p.get("name"),
                "status": p.get("status"),
                "objective": p.get("objective"),
            },
        )
        prov_record(
            db, merchant_id, "entities", str(entity_id), "raw_meta_campaigns", raw.source_record_id, TRANSFORM_ID
        )
        count += 1
    db.commit()
    return count


def normalize_insights(db: Session, merchant_id: str) -> int:
    set_merchant(db, merchant_id)
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
    adset_id = str(p.get("adset_id") or "")
    ad_id = str(p.get("ad_id") or "")
    date = p.get("date_start", "")

    # Use the finest grain available so multiple ads in the same campaign/day
    # don't collapse to the same entity (and deduplicate spend via MD5 event_id).
    if ad_id and ad_id != "unknown" and ad_id != "":
        entity_key = f"meta:ad:{ad_id}"
        entity_type = "ad_creative"
        entity_id = _upsert_entity(
            db,
            merchant_id,
            entity_type,
            entity_key,
            "meta",
            {
                "meta_campaign_id": campaign_id,
                "name": p.get("campaign_name"),  # preserved so group_by="campaign" still works
                "adset_id": adset_id,
                "adset_name": p.get("adset_name"),
                "ad_id": ad_id,
                "ad_name": p.get("ad_name"),
            },
        )
    elif adset_id and adset_id != "":
        entity_key = f"meta:adset:{adset_id}"
        entity_type = "ad_set"
        entity_id = _upsert_entity(
            db,
            merchant_id,
            entity_type,
            entity_key,
            "meta",
            {
                "meta_campaign_id": campaign_id,
                "name": p.get("campaign_name"),
                "adset_id": adset_id,
                "adset_name": p.get("adset_name"),
                "ad_id": ad_id,
                "ad_name": p.get("ad_name"),
            },
        )
    else:
        # Fallback: campaign-level insight row.  DO NOT overwrite attributes on
        # the campaign entity — normalize_campaigns() already wrote richer data
        # (status, objective, canonical name).  Use _get_or_create_entity so we
        # get the id for the event foreign-key without clobbering existing attrs.
        entity_key = f"meta:campaign:{campaign_id}"
        entity_type = "ad_campaign"
        entity_id = _get_or_create_entity(
            db,
            merchant_id,
            entity_type,
            entity_key,
            "meta",
            {
                "meta_campaign_id": campaign_id,
                "name": p.get("campaign_name"),
            },
        )

    prov_record(db, merchant_id, "entities", str(entity_id), "raw_meta_insights", raw.source_record_id, TRANSFORM_ID)

    spend = Decimal(str(p.get("spend") or "0"))
    occurred_at = _parse_dt(date + "T00:00:00+00:00") if date else None

    if occurred_at and spend > 0:
        # Purchase COUNT comes from actions[].value; purchase REVENUE in ₹ comes
        # from action_values[].value.  These are two separate arrays in the Meta
        # Insights payload — using actions for revenue is the bug this fixes.
        actions = p.get("actions") or []
        action_values = p.get("action_values") or []
        purchase_count = _safe_int(
            next((a.get("value") or "0" for a in actions if a.get("action_type") == "purchase"), "0")
        )
        purchase_value = Decimal(
            str(
                _safe_float(
                    next((a.get("value") or "0" for a in action_values if a.get("action_type") == "purchase"), "0")
                )
            )
        )

        event_id = _upsert_event(
            db,
            merchant_id,
            entity_id,
            "ad_spend",
            occurred_at,
            spend,
            "INR",
            None,
            {
                "impressions": p.get("impressions"),
                "clicks": p.get("clicks"),
                "cpc": p.get("cpc"),
                "cpm": p.get("cpm"),
                "ctr": p.get("ctr"),
                "purchase_count": purchase_count,
                "purchase_value": str(purchase_value),
                "ad_id": ad_id,
                "adset_id": p.get("adset_id"),
            },
        )
        prov_record(db, merchant_id, "events", str(event_id), "raw_meta_insights", raw.source_record_id, TRANSFORM_ID)
