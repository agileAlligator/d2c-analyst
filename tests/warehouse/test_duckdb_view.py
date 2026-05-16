"""Tests for duckdb_view query validation and merchant isolation."""
import os

import pytest

from app.warehouse.duckdb_view import _FORBIDDEN_TOKENS, _validate_query, sandboxed_sql


# ---------------------------------------------------------------------------
# _coerce_id_list  (pure unit tests — no DB required)
# ---------------------------------------------------------------------------


class TestCoerceIdList:
    """Regression for ValueError on multi-element numpy arrays from ARRAY_AGG."""

    def test_none_returns_empty(self):
        from app.warehouse.duckdb_view import _coerce_id_list
        assert _coerce_id_list(None) == []

    def test_empty_list_returns_empty(self):
        from app.warehouse.duckdb_view import _coerce_id_list
        assert _coerce_id_list([]) == []

    def test_python_list_passthrough(self):
        from app.warehouse.duckdb_view import _coerce_id_list
        assert _coerce_id_list(["a", "b"]) == ["a", "b"]

    def test_multi_element_numpy_array_does_not_raise(self):
        import numpy as np
        from app.warehouse.duckdb_view import _coerce_id_list
        arr = np.array(["raw-1", "raw-2", "raw-3"], dtype=object)
        result = _coerce_id_list(arr)
        assert result == ["raw-1", "raw-2", "raw-3"]

    def test_empty_numpy_array_returns_empty(self):
        import numpy as np
        from app.warehouse.duckdb_view import _coerce_id_list
        assert _coerce_id_list(np.array([], dtype=object)) == []

    def test_single_element_numpy_array(self):
        import numpy as np
        from app.warehouse.duckdb_view import _coerce_id_list
        assert _coerce_id_list(np.array(["only"], dtype=object)) == ["only"]

    def test_numpy_array_with_none_filtered(self):
        import numpy as np
        from app.warehouse.duckdb_view import _coerce_id_list
        arr = np.array(["a", None, "b"], dtype=object)
        assert _coerce_id_list(arr) == ["a", "b"]


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


# ---------------------------------------------------------------------------
# Comment-bypass + path-literal blocking  (no DB)
# ---------------------------------------------------------------------------


class TestCommentBypassBlocked:
    def test_sql_comment_bypass_blocked(self):
        """pg/**/.entities must not bypass the pg. check via block comment injection."""
        with pytest.raises(ValueError, match="Direct schema access"):
            _validate_query("SELECT * FROM pg/**/.entities")

    def test_line_comment_bypass_blocked(self):
        with pytest.raises(ValueError, match="Direct schema access"):
            _validate_query("SELECT * FROM pg-- x\n.entities")

    def test_path_literal_absolute_blocked(self):
        with pytest.raises(ValueError, match="path literals"):
            _validate_query("SELECT * FROM '/etc/passwd'")

    def test_path_literal_dot_relative_blocked(self):
        with pytest.raises(ValueError, match="path literals"):
            _validate_query("SELECT * FROM './data.csv'")


# ---------------------------------------------------------------------------
# Merchant isolation  (DB-gated)
# ---------------------------------------------------------------------------


DB_AVAILABLE = bool(os.getenv("DATABASE_URL"))
pytestmark_db = pytest.mark.skipif(not DB_AVAILABLE, reason="DATABASE_URL not set")


@pytestmark_db
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


@pytestmark_db
class TestSandboxedSqlRealQueries:
    """Integration tests that run representative queries against live seed data.

    These tests are the canary for DuckDB connectivity and extension loading.
    If the postgres extension cannot be loaded (e.g. enable_external_access=false
    blocks disk access), every test here fails immediately with a clear error —
    rather than silently degrading at chat time.
    """

    def test_events_table_accessible(self):
        """Proves DuckDB can connect to Postgres and read the events table."""
        rows, _ = sandboxed_sql(
            "SELECT COUNT(*) AS n FROM events WHERE event_type = 'order_revenue'",
            "demo",
        )
        assert rows[0]["n"] > 0, "No order_revenue events found — seed data missing or DuckDB broken"

    def test_events_join_entities(self):
        """JOIN across two tables — the pattern used by top-N and revenue queries."""
        rows, _ = sandboxed_sql(
            """
            SELECT en.attributes->>'order_number' AS order_number, SUM(ev.amount) AS revenue
            FROM events ev
            JOIN entities en ON ev.entity_id = en.entity_id
            WHERE ev.event_type IN ('order_revenue', 'refund')
            GROUP BY en.attributes->>'order_number'
            ORDER BY revenue DESC
            LIMIT 5
            """,
            "demo",
        )
        assert len(rows) > 0, "JOIN across events+entities returned no rows"
        assert rows[0]["revenue"] >= rows[-1]["revenue"], "ORDER BY DESC not respected"

    def test_provenance_join_returns_ids(self):
        """JOIN to provenance table returns raw_record_id values."""
        rows, prov_ids = sandboxed_sql(
            """
            SELECT
                ev.event_id,
                ARRAY_AGG(DISTINCT p.raw_record_id) AS provenance_ids
            FROM events ev
            JOIN provenance p ON p.row_table = 'events' AND p.row_pk = ev.event_id::text
            WHERE ev.event_type = 'order_revenue'
            GROUP BY ev.event_id
            LIMIT 3
            """,
            "demo",
        )
        assert len(rows) > 0, "provenance JOIN returned no rows"
        assert len(prov_ids) > 0, "no provenance_ids extracted from result"
        assert all(isinstance(p, str) for p in prov_ids), "numpy scalars leaked into prov_ids"

    def test_top5_orders_by_revenue_with_provenance(self):
        """The exact query pattern the model uses for 'top 5 orders by revenue'.

        This is the regression test for the enable_external_access=false bug that
        made the sql tool silently return an error for any real analytical query.
        """
        rows, prov_ids = sandboxed_sql(
            """
            SELECT
                en.attributes->>'order_number' AS order_number,
                SUM(ev.amount) AS revenue,
                ARRAY_AGG(DISTINCT p.raw_record_id) AS provenance_ids
            FROM events ev
            JOIN entities en ON ev.entity_id = en.entity_id
            JOIN provenance p ON p.row_table = 'events' AND p.row_pk = ev.event_id::text
            WHERE ev.event_type IN ('order_revenue', 'refund')
              AND ev.occurred_at >= NOW() - INTERVAL '30 days'
            GROUP BY en.attributes->>'order_number'
            ORDER BY revenue DESC
            LIMIT 5
            """,
            "demo",
        )
        assert len(rows) > 0, "top-5-orders query returned no rows"
        assert len(prov_ids) > 0, "no provenance_ids in top-5-orders result"
        assert all("order_number" in r for r in rows), "order_number column missing"
        assert rows[0]["revenue"] >= rows[-1]["revenue"], "rows not sorted by revenue DESC"
        assert all(isinstance(p, str) for p in prov_ids), "numpy scalars leaked into prov_ids"

    def test_merchant_isolation_in_join_query(self):
        """A JOIN query for 'demo' must not surface any 'demo2' rows."""
        rows, _ = sandboxed_sql(
            """
            SELECT COUNT(DISTINCT ev.event_id) AS n
            FROM events ev
            JOIN entities en ON ev.entity_id = en.entity_id
            WHERE ev.merchant_id = 'demo2'
            """,
            "demo",
        )
        assert rows[0]["n"] == 0, "demo query leaked demo2 rows through JOIN"
