"""Server-side citation validator.

Every number in the model's response must be wrapped in <cite ref="ID">value</cite>.
Numbers without a cite tag get stripped; unresolvable cite refs get an 'unverified' badge.
"""

import logging
import re

from sqlalchemy.orm import Session

from app.provenance.record import _ALLOWED_RAW_TABLES, get_raw_payload

logger = logging.getLogger(__name__)

# Matches: <cite ref="some-id">any text</cite>  (content may be empty)
CITE_RE = re.compile(r'<cite ref="([^"]+)">([^<]*)</cite>')

# Strips any remaining bare <cite...> or </cite> tags (e.g. malformed GPT output)
_BARE_CITE_TAG_RE = re.compile(r"</?cite[^>]*>")

# Time-period references that should never require a citation.
_timeref_re = re.compile(
    # "30 days", "14d", "30 minutes"
    r"\b\d+\s*(?:days?|weeks?|months?|hours?|minutes?|years?|[dwmh])\b"
    # "30-day", "14-week", "3-month" (hyphenated adjective form)
    r"|\b\d+-(?:day|week|month|hour|minute|year)s?\b"
    # "last 30", "past 14", "previous 90", "trailing 7", "rolling 30", "next 30"
    # (include the leading word so the bare digit falls inside the excluded span)
    r"|\b(?:last|past|previous|trailing|rolling|next)\s+\d+\b",
    re.IGNORECASE,
)

# Calendar-date references that should never be stripped. These protect the day-of-month
# and year digits inside date labels (ISO, written, week-of). Distinct from _timeref_re
# which covers rolling-window sizes ("30 days", "14d").
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

_WRITTEN_DATE_RE = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:,\s*\d{4})?\b",
    re.IGNORECASE,
)

_WEEK_LABEL_RE = re.compile(
    r"\bweek\s+of\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:,\s*\d{4})?\b"
    r"|\bweek\s+(?:starting|ending|of)\s+\d{4}-\d{2}-\d{2}\b",
    re.IGNORECASE,
)


_MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?"
)
_YEAR_CONTEXT_BEFORE = re.compile(
    r"(?:year|fy|fiscal|q[1-4]|" + _MONTHS + r")[\s,\-/]*$",
    re.IGNORECASE,
)
_YEAR_CONTEXT_AFTER = re.compile(
    r"^[\s,\-/]*(?:year|fy|fiscal|q[1-4]|" + _MONTHS + r"|to\s+\d{4}|-\s*\d{4}|/\d{2,4})",
    re.IGNORECASE,
)
_ADJACENT_YEAR_RANGE = re.compile(r"^\s*[-–—]\s*\d{4}\b|\b\d{4}\s*[-–—]\s*$")
# Proper-noun-before-year: protects campaign/event names like "Diwali Sale 2024",
# "Spring Launch 2023", "New Year Push 2024". Requires TWO consecutive capitalized
# words immediately before the year so that sentence-initial articles ("Our 2023",
# "The 2024", "Last 2023") are NOT exempted — those have only one capital word.
_PROPER_NOUN_BEFORE = re.compile(r"[A-Z][a-zA-Z]+\s+[A-Z][a-zA-Z]+[\s,\-]*$")

# Module-level export so the eval suite and other callers can reuse this
# regex without duplicating it.  Matches numbers that require a citation:
# comma-formatted integers, plain integers ≥ 2 digits, and decimals.
bare_number_re = re.compile(
    r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b"  # comma-formatted: 30,412
    r"|\b\d{2,}(?:\.\d+)?\b"  # plain integers ≥ 2 digits
    r"|\b\d+\.\d+\b"  # decimals
)


def _is_yearlike(n: str, ctx_before: str = "", ctx_after: str = "") -> bool:
    try:
        v = float(n)
        if not (v == int(v) and 1900 <= int(v) <= 2099):
            return False
    except (ValueError, OverflowError):
        return False
    if _YEAR_CONTEXT_BEFORE.search(ctx_before):
        return True
    if _YEAR_CONTEXT_AFTER.match(ctx_after):
        return True
    if _ADJACENT_YEAR_RANGE.search(ctx_before) or _ADJACENT_YEAR_RANGE.match(ctx_after):
        return True
    if _PROPER_NOUN_BEFORE.search(ctx_before):
        return True
    return False


def _extract_all_numbers(text: str) -> list[float]:
    """Return all numeric values from text, stripping currency symbols and commas."""
    cleaned_text = re.sub(r"[₹$,]", "", text)
    return [float(m) for m in re.findall(r"(?<!\d)-?\d+\.?\d*", cleaned_text) if m]


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
    # Cache DB resolution per ref_id to avoid duplicate round-trips.
    # Keyed on ref_id; value is True (resolved) or False (unresolvable).
    _resolve_cache: dict[str, bool] = {}
    # Deduplicate issue messages so the same ref_id doesn't produce two identical lines.
    _seen_issues: set[str] = set()
    # Deduplicate cite_tags so str.replace is called at most once per (ref_id, value) pair.
    # str.replace replaces all occurrences in one call, so a second call for the same
    # tag string is a no-op on already-replaced text but wastes the value-check.
    _seen_tags: set[str] = set()

    for ref_id, value in cited_ids:
        cite_tag = f'<cite ref="{ref_id}">{value}</cite>'
        if cite_tag in _seen_tags:
            continue  # already replaced all occurrences of this exact tag form
        _seen_tags.add(cite_tag)

        ref_unresolvable = False
        if ref_id not in provenance_ids:
            if ref_id not in _resolve_cache:
                _resolve_cache[ref_id] = _try_resolve(db, ref_id, merchant_id)
            if not _resolve_cache[ref_id]:
                ref_unresolvable = True
                issue_msg = f"Unresolvable cite ref: {ref_id}"
                if issue_msg not in _seen_issues:
                    issues.append(issue_msg)
                    _seen_issues.add(issue_msg)
                cleaned = cleaned.replace(cite_tag, f"{value} *(unverified)*")

        # Also verify the cited numeric value is plausible given what tools returned.
        # Skip this check when the ref is already flagged unresolvable — only one issue
        # per cite tag.
        if not ref_unresolvable and tool_value_set and value.strip():
            all_nums = _extract_all_numbers(value)
            if all_nums:
                # Every number in the cite value must match a tool result.
                # Catches fabricated context like <cite ref="id">₹31,814 (was ₹99,999)</cite>
                # where 99,999 is hallucinated but 31,814 resolves.
                value_ok = all(
                    any(
                        abs(num - v) <= max(0.01, abs(num) * 0.01)
                        or (v < 0 < num and abs(abs(num) - abs(v)) <= max(0.01, abs(num) * 0.01))
                        for v in tool_value_set
                    )
                    for num in all_nums
                )
                if not value_ok:
                    issues.append(f"Cited value {value!r} for ref {ref_id!r} not found in tool results")
                    if cite_tag in cleaned:
                        cleaned = cleaned.replace(cite_tag, f"{value} *(unverified)*")

    # 2. Strip bare numbers outside cite tags.
    # NOTE: do NOT strip bare cite tags here — _BARE_CITE_TAG_RE matches valid cite tags
    # too, exposing their inner numbers as bare. Malformed <cite> tags (no ref=) are not
    # matched by CITE_RE so their inner numbers are correctly NOT in excluded_spans and
    # will be caught and stripped by the scan below. Bare tags are cleaned at the end.

    # Build exclusion spans: valid cite tags + time-period references (e.g. "30 days", "14d")
    excluded_spans = (
        [(m.start(), m.end()) for m in CITE_RE.finditer(cleaned)]
        + [(m.start(), m.end()) for m in _timeref_re.finditer(cleaned)]
        + [(m.start(), m.end()) for m in _ISO_DATE_RE.finditer(cleaned)]
        + [(m.start(), m.end()) for m in _WRITTEN_DATE_RE.finditer(cleaned)]
        + [(m.start(), m.end()) for m in _WEEK_LABEL_RE.finditer(cleaned)]
    )

    stripped: list[str] = []

    def _replace_bare(m: re.Match) -> str:
        n = m.group(0)
        pos = m.start()
        ctx_before = cleaned[max(0, pos - 16) : pos]
        ctx_after = cleaned[m.end() : m.end() + 16]
        if _is_yearlike(n, ctx_before, ctx_after):
            return n
        if any(s <= pos < e for s, e in excluded_spans):
            return n
        # Already flagged unverified by step 1 — don't double-annotate
        after = cleaned[m.end() : m.end() + 20]
        if re.match(r"\s*\*\(unverified\)\*", after):
            return n
        stripped.append(n)
        return "*(uncited)*"

    cleaned = bare_number_re.sub(_replace_bare, cleaned)
    # Strip all <cite> markup from the final output. Design intent: the answer text
    # is returned as clean prose; citation validity is communicated via the
    # `all_citations_valid` flag and `provenance_ids` list in the API response —
    # not as inline markup. Numbers inside valid cite tags were protected above
    # (in excluded_spans) so they survive as bare text. Numbers inside malformed
    # cite tags were already replaced with *(uncited)* by the scan above.
    cleaned = _BARE_CITE_TAG_RE.sub("", cleaned)

    if stripped:
        issues.append(f"Uncited numbers stripped from output: {stripped[:5]}")

    all_valid = len(issues) == 0
    return cleaned, all_valid, issues


def _try_resolve(db: Session, ref_id: str, merchant_id: str) -> bool:
    """Check whether the raw record exists. ref_id may be 'raw_table:record_id' or just a key."""
    raw_tables = sorted(_ALLOWED_RAW_TABLES)

    # If ref_id looks like "table:record_id", try that table first
    if ":" in ref_id:
        parts = ref_id.split(":", 1)
        table_hint = parts[0]
        record_hint = parts[1]
        candidates = [t for t in raw_tables if table_hint in t] + [t for t in raw_tables if table_hint not in t]
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
