"""Regression tests for system prompt content.

These guard against prompt changes that remove critical routing guidance,
not against model behavior (which requires a live LLM).
"""

from pathlib import Path

PROMPT_PATH = Path(__file__).parent.parent.parent / "app/chat/prompts/system.txt"


def _prompt() -> str:
    return PROMPT_PATH.read_text()


def test_prompt_documents_specific_entity_lookup():
    """Rule 10 must tell the model HOW to look up entities by id via sql."""
    p = _prompt()
    assert "json_extract_string(attributes" in p, (
        "system prompt must show the DuckDB json_extract_string lookup pattern for specific orders"
    )
    assert "list_entities" in p and "20-row sample" in p, "system prompt must warn that list_entities is only a sample"


def test_prompt_no_derived_arithmetic_rule():
    """Rule 11 must prohibit inline average/ratio computation."""
    p = _prompt()
    assert "Do NOT compute averages" in p or "do NOT compute averages" in p, (
        "system prompt must instruct model not to compute averages inline"
    )


def test_prompt_time_range_xd_form():
    """Xd-form pinning rule must be present."""
    p = _prompt()
    assert "Xd form" in p or "Xd-form" in p or "30d" in p, (
        "system prompt must guide model to use Xd form for window references"
    )
