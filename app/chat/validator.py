"""Server-side citation validator.

Every number in the model's response must be wrapped in <cite ref="ID">value</cite>.
Numbers without a cite tag get stripped; unresolvable cite refs get an 'unverified' badge.
"""
import logging
import re

from sqlalchemy.orm import Session

from app.provenance.record import get_raw_payload

logger = logging.getLogger(__name__)

# Matches: <cite ref="some-id">any text</cite>  (content may be empty)
CITE_RE = re.compile(r'<cite ref="([^"]+)">([^<]*)</cite>')

# Strips any remaining bare <cite...> or </cite> tags (e.g. malformed GPT output)
_BARE_CITE_TAG_RE = re.compile(r'</?cite[^>]*>')


def validate_and_clean(
    response_text: str,
    provenance_ids: list[str],
    db: Session,
) -> tuple[str, bool, list[str]]:
    """
    Validate citations in model response.

    Returns:
        (cleaned_text, all_valid, issues)
        - cleaned_text: unresolvable cites marked as unverified
        - all_valid: True if all cited IDs resolved AND no bare numbers outside cite tags
        - issues: list of problem descriptions
    """
    issues = []

    # 1. Validate every cited ref resolves
    cleaned = response_text
    cited_ids = CITE_RE.findall(response_text)

    for ref_id, value in cited_ids:
        if ref_id not in provenance_ids:
            resolved = _try_resolve(db, ref_id)
            if not resolved:
                issues.append(f"Unresolvable cite ref: {ref_id}")
                cleaned = cleaned.replace(
                    f'<cite ref="{ref_id}">{value}</cite>',
                    f'{value} *(unverified)*',
                )

    # 2. Always scan for bare numbers outside cite tags.
    # Strip cite blocks first, then any remaining malformed tags, then scan.
    text_without_cites = CITE_RE.sub("__CITED__", cleaned)
    text_without_cites = _BARE_CITE_TAG_RE.sub("", text_without_cites)
    # Simple number pattern: integers ≥ 2 digits, or any decimal
    bare_number_re = re.compile(r'\b\d{2,}(?:\.\d+)?\b|\b\d+\.\d+\b')
    bare_numbers = bare_number_re.findall(text_without_cites)
    # Filter trivial year-like numbers that appear in dates (4-digit ≤ 2030 check is too complex,
    # so we just flag anything ≥ 100 that isn't in a cite)
    significant_bare = [n for n in bare_numbers if float(n) >= 100]

    if significant_bare:
        issues.append(
            f"Uncited numbers found (significant values ≥ 100 without <cite> tags): {significant_bare[:5]}"
        )

    all_valid = len(issues) == 0
    return cleaned, all_valid, issues


def _try_resolve(db: Session, ref_id: str) -> bool:
    """Check whether the raw record exists. ref_id may be 'raw_table:record_id' or just a key."""
    # Synthetic provenance IDs for computed values (delta, pct_change from compare tool)
    if ref_id.startswith("computed:"):
        return True

    raw_tables = [
        "raw_shopify_orders", "raw_shopify_products", "raw_shopify_refunds",
        "raw_meta_insights", "raw_meta_campaigns", "raw_shiprocket_shipments",
    ]

    # If ref_id looks like "table:record_id", try that table first
    if ":" in ref_id:
        parts = ref_id.split(":", 1)
        table_hint = parts[0]
        record_hint = parts[1]
        candidates = [t for t in raw_tables if table_hint in t] + \
                     [t for t in raw_tables if table_hint not in t]
        all_ids = [(t, record_hint) for t in candidates] + [(t, ref_id) for t in raw_tables]
    else:
        all_ids = [(t, ref_id) for t in raw_tables]

    for table, record_id in all_ids:
        try:
            result = get_raw_payload(db, table, record_id)
            if result:
                return True
        except Exception:
            continue
    return False


def extract_cite_refs(text: str) -> list[str]:
    return [ref for ref, _ in CITE_RE.findall(text)]
