"""Citation validator tests — the most critical piece."""
from unittest.mock import MagicMock, patch

from app.chat.validator import extract_cite_refs, validate_and_clean


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
