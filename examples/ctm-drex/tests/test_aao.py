"""The deterministic parts of the AAO dataset: outcome from the ORDER line, model JSON parsing, prompt cutting."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aao_label import MAX_CHARS, build_prompt, order_outcome, parse  # noqa: E402


def test_order_line_outcomes():
    assert order_outcome("... discussion ...\nORDER: The appeal is dismissed.") == "dismissed"
    assert order_outcome("ORDER: The appeal is sustained.") == "sustained"
    assert order_outcome("ORDER: The decision is withdrawn and the matter remanded for a new decision.") == "remanded"
    assert order_outcome("ORDER: The motion to reconsider is denied.") == "dismissed"
    assert order_outcome("no order here") is None


def test_last_order_line_wins():
    text = "The director's ORDER: approved earlier was revoked.\n...\nORDER: The appeal is dismissed."
    assert order_outcome(text) == "dismissed"


def test_parse_accepts_json_with_criteria_only():
    assert parse('x {"outcome": "dismissed", "criteria": {"awards": "met"}} y')["criteria"]["awards"] == "met"
    assert parse('{"outcome": "dismissed"}') is None
    assert parse("not json") is None


def test_long_decision_keeps_the_end():
    text = "A" * MAX_CHARS + "ORDER: The appeal is dismissed."
    assert "ORDER: The appeal is dismissed." in build_prompt(text)
