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


def _is_yearlike(n: str) -> bool:
    try:
        v = float(n)
        return v == int(v) and 1900 <= int(v) <= 2099
    except (ValueError, OverflowError):
        return False


def _extract_number(s: str) -> float | None:
    cleaned = re.sub(r"[₹$€£,\s]", "", s)
    m = re.search(r"-?\d+\.?\d*", cleaned)
    try:
        return float(m.group()) if m else None
    except (ValueError, AttributeError):
        return None


def validate_and_clean(
    response_text: str,
    provenance_ids: list[str],
    db: Session,
    merchant_id: str = "",
    tool_value_set: set[float] | None = None,
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
            resolved = _try_resolve(db, ref_id, merchant_id)
            if not resolved:
                issues.append(f"Unresolvable cite ref: {ref_id}")
                cleaned = cleaned.replace(
                    f'<cite ref="{ref_id}">{value}</cite>',
                    f'{value} *(unverified)*',
                )

        # Also verify the cited numeric value is plausible given what tools returned
        if tool_value_set and value.strip():
            num = _extract_number(value)
            if num is not None and num != 0:
                tolerance = max(0.01, abs(num) * 0.01)
                value_ok = any(abs(num - v) <= tolerance for v in tool_value_set)
                if not value_ok:
                    issues.append(f"Cited value {value!r} for ref {ref_id!r} not found in tool results")
                    # Rewrite to unverified if not already done by ID check
                    cite_tag = f'<cite ref="{ref_id}">{value}</cite>'
                    if cite_tag in cleaned:
                        cleaned = cleaned.replace(cite_tag, f'{value} *(unverified)*')

    # 2. Strip bare numbers outside cite tags.
    # NOTE: do NOT strip bare cite tags here — _BARE_CITE_TAG_RE matches valid cite tags
    # too, exposing their inner numbers as bare. Malformed <cite> tags (no ref=) are not
    # matched by CITE_RE so their inner numbers are correctly NOT in excluded_spans and
    # will be caught and stripped by the scan below. Bare tags are cleaned at the end.

    # Build exclusion spans: valid cite tags + time-period references (e.g. "30 days", "14d")
    bare_number_re = re.compile(r'\b\d{2,}(?:\.\d+)?\b|\b\d+\.\d+\b')
    _timeref_re = re.compile(
        r'\b\d+\s*(?:days?|weeks?|months?|hours?|minutes?|years?|[dwmh])\b',
        re.IGNORECASE,
    )
    excluded_spans = (
        [(m.start(), m.end()) for m in CITE_RE.finditer(cleaned)]
        + [(m.start(), m.end()) for m in _timeref_re.finditer(cleaned)]
    )

    stripped: list[str] = []

    def _replace_bare(m: re.Match) -> str:
        n = m.group(0)
        pos = m.start()
        if _is_yearlike(n):
            return n
        if any(s <= pos < e for s, e in excluded_spans):
            return n
        # Already flagged unverified by step 1 — don't double-annotate
        after = cleaned[m.end(): m.end() + 20]
        if re.match(r'\s*\*\(unverified\)\*', after):
            return n
        stripped.append(n)
        return "*(uncited)*"

    cleaned = bare_number_re.sub(_replace_bare, cleaned)
    # Now safe to remove residual malformed <cite> tags — numbers inside them were
    # already processed by the scan above (they weren't in excluded_spans).
    cleaned = _BARE_CITE_TAG_RE.sub("", cleaned)

    if stripped:
        issues.append(f"Uncited numbers stripped from output: {stripped[:5]}")

    all_valid = len(issues) == 0
    return cleaned, all_valid, issues


def _try_resolve(db: Session, ref_id: str, merchant_id: str) -> bool:
    """Check whether the raw record exists. ref_id may be 'raw_table:record_id' or just a key."""
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
            result = get_raw_payload(db, table, record_id, merchant_id)
            if result:
                return True
        except Exception:
            continue
    return False


def extract_cite_refs(text: str) -> list[str]:
    return [ref for ref, _ in CITE_RE.findall(text)]
