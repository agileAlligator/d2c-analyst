import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# ── Raw tables (immutable, append-only) ──────────────────────────────────────


class RawShopifyOrder(Base):
    __tablename__ = "raw_shopify_orders"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id = Column(String, nullable=False, index=True)
    source_record_id = Column(String, nullable=False)
    payload = Column(JSONB, nullable=False)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    run_id = Column(String, nullable=False)
    __table_args__ = (UniqueConstraint("merchant_id", "source_record_id", name="uq_raw_shopify_orders"),)


class RawShopifyProduct(Base):
    __tablename__ = "raw_shopify_products"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id = Column(String, nullable=False, index=True)
    source_record_id = Column(String, nullable=False)
    payload = Column(JSONB, nullable=False)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    run_id = Column(String, nullable=False)
    __table_args__ = (UniqueConstraint("merchant_id", "source_record_id", name="uq_raw_shopify_products"),)


class RawShopifyRefund(Base):
    __tablename__ = "raw_shopify_refunds"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id = Column(String, nullable=False, index=True)
    source_record_id = Column(String, nullable=False)
    payload = Column(JSONB, nullable=False)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    run_id = Column(String, nullable=False)
    __table_args__ = (UniqueConstraint("merchant_id", "source_record_id", name="uq_raw_shopify_refunds"),)


class RawShopifyCustomer(Base):
    __tablename__ = "raw_shopify_customers"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id = Column(String, nullable=False, index=True)
    source_record_id = Column(String, nullable=False)
    payload = Column(JSONB, nullable=False)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    run_id = Column(String, nullable=False)
    __table_args__ = (UniqueConstraint("merchant_id", "source_record_id", name="uq_raw_shopify_customers"),)


class RawMetaInsight(Base):
    __tablename__ = "raw_meta_insights"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id = Column(String, nullable=False, index=True)
    source_record_id = Column(String, nullable=False)
    payload = Column(JSONB, nullable=False)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    run_id = Column(String, nullable=False)
    __table_args__ = (UniqueConstraint("merchant_id", "source_record_id", name="uq_raw_meta_insights"),)


class RawMetaCampaign(Base):
    __tablename__ = "raw_meta_campaigns"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id = Column(String, nullable=False, index=True)
    source_record_id = Column(String, nullable=False)
    payload = Column(JSONB, nullable=False)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    run_id = Column(String, nullable=False)
    __table_args__ = (UniqueConstraint("merchant_id", "source_record_id", name="uq_raw_meta_campaigns"),)


class RawShiprocketShipment(Base):
    __tablename__ = "raw_shiprocket_shipments"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id = Column(String, nullable=False, index=True)
    source_record_id = Column(String, nullable=False)
    payload = Column(JSONB, nullable=False)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    run_id = Column(String, nullable=False)
    __table_args__ = (UniqueConstraint("merchant_id", "source_record_id", name="uq_raw_shiprocket_shipments"),)


# ── Universal entity model ────────────────────────────────────────────────────


class EntityType(StrEnum):
    order = "order"
    customer = "customer"
    product = "product"
    sku = "sku"
    ad_campaign = "ad_campaign"
    ad_set = "ad_set"
    ad_creative = "ad_creative"
    shipment = "shipment"
    refund = "refund"


class Entity(Base):
    __tablename__ = "entities"
    entity_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id = Column(String, nullable=False, index=True)
    entity_type = Column(String, nullable=False)
    natural_key = Column(String, nullable=False)  # e.g. "shopify:order:5821"
    source = Column(String, nullable=False)
    attributes = Column(JSONB, nullable=False, default=dict)
    first_seen = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (
        UniqueConstraint("merchant_id", "natural_key", name="uq_entity_natural_key"),
        Index("ix_entities_merchant_type", "merchant_id", "entity_type"),
    )


class Event(Base):
    __tablename__ = "events"
    event_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id = Column(String, nullable=False, index=True)
    entity_id = Column(UUID(as_uuid=True), ForeignKey("entities.entity_id"), nullable=False)
    event_type = Column(String, nullable=False)
    occurred_at = Column(DateTime, nullable=False)
    amount = Column(Numeric(18, 4), nullable=True)
    currency = Column(String(3), nullable=True, default="INR")
    quantity = Column(Numeric(18, 4), nullable=True)
    attributes = Column(JSONB, nullable=False, default=dict)
    __table_args__ = (
        Index("ix_events_merchant_entity", "merchant_id", "entity_id"),
        Index("ix_events_occurred_at", "merchant_id", "occurred_at"),
    )


class Link(Base):
    __tablename__ = "links"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id = Column(String, nullable=False, index=True)
    from_entity = Column(UUID(as_uuid=True), ForeignKey("entities.entity_id"), nullable=False)
    to_entity = Column(UUID(as_uuid=True), ForeignKey("entities.entity_id"), nullable=False)
    link_type = Column(String, nullable=False)  # e.g. "order_shipment", "order_campaign"
    confidence = Column(Float, nullable=False, default=1.0)
    method = Column(String, nullable=False)  # "order_id_match", "utm_match", etc.
    __table_args__ = (UniqueConstraint("merchant_id", "from_entity", "to_entity", "link_type", name="uq_link"),)


class Provenance(Base):
    """Maps every normalized row back to its source raw row(s)."""

    __tablename__ = "provenance"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id = Column(String, nullable=False, index=True)
    # what normalized row this covers
    row_table = Column(String, nullable=False)  # "events" or "entities"
    row_pk = Column(String, nullable=False)  # the UUID of the event/entity
    # where it came from
    raw_table = Column(String, nullable=False)  # "raw_shopify_orders"
    raw_record_id = Column(String, nullable=False)  # source_record_id from raw
    transform_id = Column(String, nullable=False)  # name of the normalizer function
    ingested_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (
        UniqueConstraint(
            "merchant_id",
            "row_table",
            "row_pk",
            "raw_table",
            "raw_record_id",
            "transform_id",
            name="uq_provenance_dedup",
        ),
        Index("ix_provenance_row", "row_table", "row_pk"),
        Index("ix_provenance_raw", "raw_table", "raw_record_id"),
    )


# ── Agent run logs ────────────────────────────────────────────────────────────


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id = Column(String, nullable=False, index=True)
    agent_name = Column(String, nullable=False)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String, nullable=False, default="running")  # running|completed|failed
    log_md = Column(Text, nullable=True)
    proposals = Column(JSONB, nullable=False, default=list)


# ── Ingest job queue ──────────────────────────────────────────────────────────


class IngestJob(Base):
    __tablename__ = "ingest_jobs"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id = Column(String, nullable=False)
    connector = Column(String, nullable=False)
    cursor = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending")
    run_id = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)
    __table_args__ = (Index("ix_ingest_jobs_pending", "status", "created_at"),)
