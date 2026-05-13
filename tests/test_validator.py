"""Citation validator tests — the most critical piece."""
import os
import pytest
from unittest.mock import MagicMock, patch

from app.chat.validator import extract_cite_refs, validate_and_clean

DB_URL = os.getenv("DATABASE_URL", "")


@pytest.fixture
def mock_db():
    return MagicMock()


class TestCitationValidator:
    def _mock_db(self):
        return MagicMock()

    def test_fully_cited_answer_passes(self):
        text = 'Revenue was <cite ref="raw_shopify_orders:order:5001">₹799</cite> last week.'
        prov_ids = ["raw_shopify_orders:order:5001"]
        db = self._mock_db()

        cleaned, valid, issues = validate_and_clean(text, prov_ids, db)
        assert valid is True
        assert issues == []
        assert "₹799" in cleaned

    def test_uncited_number_triggers_issue(self):
        text = "Revenue was 50000 last week."
        prov_ids = []
        db = self._mock_db()

        with patch("app.chat.validator._try_resolve", return_value=False):
            cleaned, valid, issues = validate_and_clean(text, prov_ids, db)
        # 50000 >= 100 and has no cite tag — validator should flag it
        assert valid is False
        assert any("Uncited" in i for i in issues)

    def test_unresolvable_cite_ref_marks_unverified(self):
        text = 'Revenue was <cite ref="bad_id">50000</cite>.'
        prov_ids = ["different_id"]
        db = self._mock_db()

        with patch("app.chat.validator._try_resolve", return_value=False):
            cleaned, valid, issues = validate_and_clean(text, prov_ids, db)

        assert valid is False
        assert any("bad_id" in i for i in issues)
        assert "unverified" in cleaned

    def test_multiple_valid_cites(self):
        text = (
            'Spend was <cite ref="meta:insight:1">₹5000</cite> '
            'and revenue was <cite ref="shopify:order:1">₹15000</cite>.'
        )
        prov_ids = ["meta:insight:1", "shopify:order:1"]
        db = self._mock_db()

        cleaned, valid, issues = validate_and_clean(text, prov_ids, db)
        assert valid is True
        assert issues == []

    def test_extract_cite_refs(self):
        text = '<cite ref="abc">100</cite> and <cite ref="def">200</cite>'
        refs = extract_cite_refs(text)
        assert "abc" in refs
        assert "def" in refs
        assert len(refs) == 2


def test_computed_prefix_not_a_free_pass(mock_db):
    # A computed: ID that was NOT returned by any tool should be marked unverified
    text = 'Revenue was <cite ref="computed:invented">9999</cite> today.'
    with patch("app.chat.validator._try_resolve", return_value=False):
        cleaned, all_valid, issues = validate_and_clean(text, provenance_ids=[], db=mock_db, merchant_id="demo")
    assert not all_valid
    assert "unverified" in cleaned


def test_value_not_in_tool_results_flagged(mock_db):
    # Valid provenance ID but value doesn't match anything tools returned
    text = '<cite ref="real_id">999999</cite>'
    with patch("app.chat.validator._try_resolve", return_value=True):
        cleaned, all_valid, issues = validate_and_clean(
            text, provenance_ids=[], db=mock_db, merchant_id="demo",
            tool_value_set={100.0, 200.0},
        )
    assert not all_valid
    assert any("not found in tool results" in i for i in issues)


def test_sub_100_bare_numbers_flagged(mock_db):
    text = "Our RTO rate is 28% and ROAS is 1.5x today."
    cleaned, all_valid, issues = validate_and_clean(text, provenance_ids=[], db=mock_db, merchant_id="demo")
    assert not all_valid
    # 28 and 1.5 should both be flagged (not year-like, not cited)
    assert any("28" in str(i) or "1.5" in str(i) for i in issues)


# ---------------------------------------------------------------------------
# Helper — fetches a real source_record_id from the DB; skips if none exists.
# ---------------------------------------------------------------------------

def _real_record_id(merchant: str, table: str = "raw_shopify_orders") -> str:
    from sqlalchemy import text
    from app.warehouse.db import SessionLocal

    with SessionLocal() as db:
        row = db.execute(
            text(f"SELECT source_record_id FROM {table} WHERE merchant_id = :m LIMIT 1"),
            {"m": merchant},
        ).fetchone()
    if row is None:
        pytest.skip(f"No seeded data in {table} for {merchant}")
    return row[0]


@pytest.mark.skipif(not DB_URL, reason="DATABASE_URL not set")
class TestResolverRealDB:
    """Tests that exercise _try_resolve against a live database.

    A regression making _try_resolve always return True would cause
    test_nonexistent_id_marks_unverified and test_cross_merchant_id_does_not_resolve
    to fail, catching the regression immediately.
    """

    def test_existing_raw_record_resolves(self):
        """A real source_record_id must resolve to all_valid=True."""
        from app.warehouse.db import SessionLocal

        real_id = _real_record_id("demo")
        text = f'Revenue was <cite ref="{real_id}">₹500</cite>.'

        with SessionLocal() as db:
            cleaned, all_valid, issues = validate_and_clean(
                text, provenance_ids=[], db=db, merchant_id="demo"
            )

        assert all_valid is True, (
            f"Expected real record {real_id!r} to resolve, but got issues: {issues}"
        )

    def test_nonexistent_id_marks_unverified(self):
        """A fabricated ID must NOT resolve — all_valid=False, 'unverified' in output."""
        from app.warehouse.db import SessionLocal

        fake_id = "order:does_not_exist_99999"
        text = f'Revenue was <cite ref="{fake_id}">₹500</cite>.'

        with SessionLocal() as db:
            cleaned, all_valid, issues = validate_and_clean(
                text, provenance_ids=[], db=db, merchant_id="demo"
            )

        assert all_valid is False, "Fabricated ID should not resolve"
        assert "unverified" in cleaned, "'unverified' badge missing from cleaned output"
        assert len(issues) > 0, "issues list must be non-empty for unresolvable ID"

    def test_cross_merchant_id_does_not_resolve(self):
        """A record belonging to demo2 must NOT resolve when queried as demo."""
        from app.warehouse.db import SessionLocal
        from sqlalchemy import text

        # Ensure demo2 has seed data; skip if not
        with SessionLocal() as db:
            row = db.execute(
                text(
                    "SELECT source_record_id FROM raw_shopify_orders "
                    "WHERE merchant_id = :m LIMIT 1"
                ),
                {"m": "demo2"},
            ).fetchone()
        if row is None:
            pytest.skip("No seeded data in raw_shopify_orders for demo2")
        demo2_id = row[0]

        answer_text = f'Revenue was <cite ref="{demo2_id}">₹500</cite>.'

        with SessionLocal() as db:
            cleaned, all_valid, issues = validate_and_clean(
                answer_text, provenance_ids=[], db=db, merchant_id="demo"
            )

        assert all_valid is False, (
            f"Record {demo2_id!r} belongs to demo2 but resolved under demo — "
            "get_raw_payload must filter by merchant_id"
        )

    def test_table_prefix_hint_resolves(self):
        """Both 'table:id' and bare 'id' forms must resolve for a real record."""
        from app.warehouse.db import SessionLocal

        real_id = _real_record_id("demo")
        prefixed_id = f"raw_shopify_orders:{real_id}"

        for ref_id in (prefixed_id, real_id):
            text = f'Revenue was <cite ref="{ref_id}">₹500</cite>.'
            with SessionLocal() as db:
                cleaned, all_valid, issues = validate_and_clean(
                    text, provenance_ids=[], db=db, merchant_id="demo"
                )
            assert all_valid is True, (
                f"Expected ref_id {ref_id!r} to resolve, but got issues: {issues}"
            )
