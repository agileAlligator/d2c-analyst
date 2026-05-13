"""Normalize raw Shiprocket records into universal entities + events."""
import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from app.normalize.shopify_to_universal import _parse_dt, _upsert_entity, _upsert_event
from app.provenance.record import record as prov_record
from app.warehouse.models import RawShiprocketShipment

logger = logging.getLogger(__name__)
TRANSFORM_ID = "shiprocket_normalizer_v1"


def normalize_shipments(db: Session, merchant_id: str) -> int:
    rows = db.query(RawShiprocketShipment).filter_by(merchant_id=merchant_id).all()
    count = 0
    for raw in rows:
        _upsert_shipment(db, merchant_id, raw)
        count += 1
    db.commit()
    return count


def _upsert_shipment(db: Session, merchant_id: str, raw: RawShiprocketShipment):
    p = raw.payload
    shipment_id = str(p.get("id") or p.get("shipment_id", "unknown"))
    channel_order_id = str(p.get("channel_order_id") or p.get("order_id", ""))
    awb = str(p.get("awb") or p.get("awb_code", ""))
    status = str(p.get("status") or p.get("shipment_status", ""))

    natural_key = f"shiprocket:shipment:{shipment_id}"
    entity_id = _upsert_entity(db, merchant_id, "shipment", natural_key, "shiprocket", {
        "shiprocket_shipment_id": shipment_id,
        "channel_order_id": channel_order_id,  # Shopify order number
        "awb": awb,
        "courier": p.get("courier_name") or p.get("courier", ""),
        "status": status,
        "is_rto": _is_rto(status),
        "city": p.get("city", ""),
        "state": p.get("state", ""),
        "pincode": str(p.get("pincode", "")),
    })
    prov_record(db, merchant_id, "entities", str(entity_id),
                "raw_shiprocket_shipments", raw.source_record_id, TRANSFORM_ID)

    # Shipping cost event
    freight = Decimal(str(p.get("freight_charges") or p.get("charges", {}).get("freight", "0") or "0"))
    occurred_at = _parse_dt(p.get("created_at") or p.get("order_date"))
    if occurred_at and freight > 0:
        event_id = _upsert_event(db, merchant_id, entity_id, "shipping_cost", occurred_at,
                                  freight, "INR", None, {
                                      "channel_order_id": channel_order_id,
                                      "awb": awb,
                                      "courier": p.get("courier_name", ""),
                                      "weight": p.get("weight"),
                                  })
        prov_record(db, merchant_id, "events", str(event_id),
                    "raw_shiprocket_shipments", raw.source_record_id, TRANSFORM_ID)

    # RTO event (if returned)
    if _is_rto(status):
        rto_charges = Decimal(str(p.get("rto_charges", "0") or "0"))
        rto_dt = _parse_dt(p.get("rto_initiated_date") or p.get("updated_at")) or occurred_at
        if rto_dt:
            event_id = _upsert_event(db, merchant_id, entity_id, "rto", rto_dt,
                                      rto_charges, "INR", None, {
                                          "channel_order_id": channel_order_id,
                                          "awb": awb,
                                          "rto_reason": p.get("rto_reason", ""),
                                      })
            prov_record(db, merchant_id, "events", str(event_id),
                        "raw_shiprocket_shipments", raw.source_record_id, TRANSFORM_ID)


_RTO_STATUSES = {
    "rto", "rto initiated", "rto delivered", "rto in transit",
    "returned", "returned to origin", "return to origin", "rto out for delivery",
    "rto shipment", "return initiated",
}


def _is_rto(status: str) -> bool:
    return status.strip().lower() in _RTO_STATUSES
