"""Tests for the utterance normalisation helpers."""

from __future__ import annotations

from salesvoice.text import normalize_utterance, was_normalized


def test_a_split_product_name_is_repaired():
    assert normalize_utterance("how much is task flow crm") == "how much is TaskFlow CRM"


def test_a_glued_product_name_is_repaired():
    assert normalize_utterance("tell me about taskflowcrm") == "tell me about TaskFlow CRM"


def test_the_product_name_alone_is_repaired():
    assert normalize_utterance("task flow") == "TaskFlow"


def test_the_company_name_is_repaired():
    assert normalize_utterance("i spoke to sankar group") == "i spoke to Sankar Group"


def test_extra_whitespace_is_collapsed():
    assert normalize_utterance("  hello    there  ") == "hello there"


def test_normal_text_is_left_alone():
    text = "we already use a crm for our pipeline"
    assert normalize_utterance(text) == text


def test_empty_text_stays_empty():
    assert normalize_utterance("") == ""
    assert normalize_utterance(None) == ""


def test_was_normalized_detects_a_change():
    assert was_normalized("task flow crm", "TaskFlow CRM") is True
    assert was_normalized("TaskFlow CRM", "TaskFlow CRM") is False
