"""Unit tests for correlation id extraction and fallback (#895)."""

from contextvars import copy_context

from app.utils.correlation import (
    generate_correlation_id,
    get_correlation_id,
    get_or_generate_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)


def test_get_returns_none_when_unset():
    assert get_correlation_id() is None


def test_set_and_reset_restores_previous_value():
    token = set_correlation_id("corr-123")
    try:
        assert get_correlation_id() == "corr-123"
    finally:
        reset_correlation_id(token)
    assert get_correlation_id() is None


def test_get_or_generate_creates_an_id_when_missing():
    correlation_id = get_or_generate_correlation_id()

    assert correlation_id
    assert get_correlation_id() == correlation_id


def test_get_or_generate_reuses_an_existing_id():
    token = set_correlation_id("corr-existing")
    try:
        assert get_or_generate_correlation_id() == "corr-existing"
    finally:
        reset_correlation_id(token)


def test_generated_ids_are_unique():
    assert generate_correlation_id() != generate_correlation_id()


def test_context_is_isolated_between_contexts():
    """A value set in one context must not leak into another."""
    set_correlation_id("corr-parent")

    seen = copy_context().run(get_correlation_id)

    assert seen is None
    assert get_correlation_id() == "corr-parent"
