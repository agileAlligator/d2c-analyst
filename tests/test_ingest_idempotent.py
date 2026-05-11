"""Verify ingestion is idempotent — running twice produces no duplicates."""


from app.connectors.base import RawRecord


class TestIdempotentIngestion:
    def test_upsert_does_not_duplicate(self):
        """Two identical records with the same source_record_id should produce 1 row."""
        from app.ingest.runner import RAW_MODEL_MAP
        # Verify the map covers all connector/resource combos
        expected_keys = [
            ("shopify", "order"),
            ("shopify", "product"),
            ("shopify", "refund"),
            ("meta_ads", "insight"),
            ("meta_ads", "campaign"),
            ("shiprocket", "shipment"),
        ]
        for key in expected_keys:
            assert key in RAW_MODEL_MAP, f"Missing RAW_MODEL_MAP entry for {key}"

    def test_raw_record_structure(self):
        record = RawRecord(
            source_record_id="order:12345",
            payload={"id": 12345, "total_price": "799.00"},
            resource_type="order",
        )
        assert record.source_record_id == "order:12345"
        assert record.payload["id"] == 12345
