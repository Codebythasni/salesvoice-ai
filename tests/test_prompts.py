"""Tests for the product knowledge base and prompt construction."""

from __future__ import annotations

from salesvoice.prompts import build_conversation_contents, build_greeting, build_system_prompt
from salesvoice.product import get_product


def test_product_is_taskflow_crm():
    product = get_product()
    assert product.name == "TaskFlow CRM"
    assert product.company == "Sankar Group"
    assert product.features
    assert product.benefits
    assert product.plans


def test_product_covers_objections_and_faqs():
    product = get_product()
    assert len(product.objections) >= 5
    assert len(product.faqs) >= 4
    for objection, response in product.objections:
        assert objection and response


def test_system_prompt_contains_sales_instructions():
    prompt = build_system_prompt(get_product())
    assert "TaskFlow CRM" in prompt
    assert "objection" in prompt.lower()
    assert "next step" in prompt.lower()
    assert "Never invent" in prompt


def test_system_prompt_asks_for_short_spoken_replies():
    prompt = build_system_prompt(get_product())
    assert "spoken" in prompt.lower()


def test_greeting_introduces_the_assistant_and_product():
    greeting = build_greeting(get_product())
    assert "Ava" in greeting
    assert "TaskFlow CRM" in greeting


def test_conversation_contents_maps_roles():
    history = [
        {"role": "assistant", "text": "Hello"},
        {"role": "customer", "text": "Hi there"},
        {"role": "assistant", "text": "How can I help?"},
    ]
    contents = build_conversation_contents(history)
    assert contents[0]["role"] == "model"
    assert contents[1]["role"] == "user"
    assert contents[1]["parts"][0]["text"] == "Hi there"


def test_conversation_contents_skips_empty_entries():
    contents = build_conversation_contents([{"role": "customer", "text": "   "}])
    assert contents == []
