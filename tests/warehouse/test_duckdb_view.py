"""Tests for duckdb_view query validation and merchant isolation."""
import os

import pytest

from app.warehouse.duckdb_view import _FORBIDDEN_TOKENS, _validate_query, sandboxed_sql


# ---------------------------------------------------------------------------
# Forbidden-token validation  (no DB)
# ---------------------------------------------------------------------------


class TestForbiddenTokens:
    @pytest.mark.parametrize("token", sorted(_FORBIDDEN_TOKENS))
    def test_each_forbidden_token_rejected(self, token):
        with pytest.raises(ValueError):
            _validate_query(f"SELECT * FROM entities {token} foo")

    def test_lowercase_forbidden_rejected(self):
        with pytest.raises(ValueError):
            _validate_query("drop table entities")

    def test_multiple_statements_rejected(self):
        with pytest.raises(ValueError, match="Multiple"):
            _validate_query("SELECT 1; SELECT 2")

    def test_trailing_semicolon_allowed(self):
        # A single trailing semicolon is stripped before the multi-statement check
        _validate_query("SELECT 1;")  # must not raise

    def test_clean_select_passes(self):
        _validate_query("SELECT entity_id, merchant_id FROM entities WHERE merchant_id = 'demo'")


# ---------------------------------------------------------------------------
# pg. prefix blocking  (no DB)
# ---------------------------------------------------------------------------


class TestPgPrefixBlocked:
    def test_pg_dot_table_rejected(self):
        with pytest.raises(ValueError):
            _validate_query("SELECT * FROM pg.entities")

    def test_pg_case_insensitive(self):
        with pytest.raises(ValueError):
            _validate_query("SELECT * FROM PG.entities")

    def test_pg_in_join_rejected(self):
        with pytest.raises(ValueError):
            _validate_query("SELECT a FROM entities JOIN pg.events ON a=b")

    def test_word_starting_with_pg_allowed(self):
        # "pgname" does not contain the pattern pg\s*\. so it should not be rejected
        _validate_query("SELECT pgname FROM entities WHERE pgname='x'")

    def test_pg_double_quoted_rejected(self):
        with pytest.raises(ValueError):
            _validate_query('SELECT * FROM "pg"."entities"')

    def test_pg_backtick_quoted_rejected(self):
        with pytest.raises(ValueError):
            _validate_query("SELECT * FROM `pg`.`entities`")


class TestNewForbiddenTokens:
    @pytest.mark.parametrize("token", ["DUCKDB_SECRETS", "READ_TEXT", "READ_BLOB", "PARQUET_SCAN"])
    def test_new_token_rejected(self, token):
        with pytest.raises(ValueError, match="Forbidden"):
            _validate_query(f"SELECT {token}() FROM entities")


# ---------------------------------------------------------------------------
# Merchant isolation  (DB-gated)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL not set",
)
class TestMerchantIsolation:
    def test_entities_view_filters_to_demo(self):
        demo_rows, _ = sandboxed_sql("SELECT COUNT(*) AS n FROM entities", "demo")
        demo2_rows, _ = sandboxed_sql("SELECT COUNT(*) AS n FROM entities", "demo2")
        assert demo_rows[0]["n"] > demo2_rows[0]["n"]

    def test_demo_cannot_see_demo2_entities(self):
        rows, _ = sandboxed_sql(
            "SELECT COUNT(*) AS n FROM entities WHERE merchant_id = 'demo2'",
            "demo",
        )
        assert rows[0]["n"] == 0

    def test_placeholder_substituted(self):
        rows, _ = sandboxed_sql(
            "SELECT COUNT(*) AS n FROM entities WHERE merchant_id = :merchant_id",
            "demo2",
        )
        assert rows[0]["n"] >= 1

    def test_pg_prefix_rejected_at_sandboxed_sql_level(self):
        with pytest.raises(ValueError):
            sandboxed_sql("SELECT * FROM pg.entities", "demo")
