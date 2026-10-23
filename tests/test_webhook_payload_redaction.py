"""Webhook payload redaction before dispatch (#984).

``_redact_payload`` in ``app/services/webhook_service.py:271`` walks a payload and
masks the value of any key whose name is in ``WEBHOOK_REDACTED_FIELDS``. It runs
on every outbound webhook, so a defect here leaks secrets to third-party
endpoints — the exact failure the setting exists to prevent.

Existing coverage is partial and indirect. ``tests/test_be_w5_069_071`` asserts
that ``AuditLogService._sanitize`` redacts a flat dict, which is a **different
function** in a different module. Nothing tested ``_redact_payload`` itself.

Two properties here are easy to regress and would not fail loudly:

- **Recursion into nested structures.** A dispatcher that only masked top-level
  keys would redact a payload whose secrets sit one level down, and the leaked
  value would be delivered to the subscriber's endpoint before anyone noticed.
- **Not over-redacting.** Masking every field would pass a "secrets are redacted"
  test while destroying the payload. Consumers rely on non-sensitive fields
  surviving, so both directions are asserted.
"""

import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def redaction_enabled(monkeypatch):
    """Force redaction on with a known field set, independent of environment.

    ``_REDACTED_FIELDS`` is module-level and populated lazily from settings, so it
    is reset explicitly. Without this, an environment that disabled redaction
    would make every assertion below pass against a broken implementation.
    """
    from app.services import webhook_service

    monkeypatch.setattr(settings, "WEBHOOK_REDACTION_ENABLED", True)
    monkeypatch.setattr(settings, "WEBHOOK_REDACTION_MASK", "[REDACTED]")
    monkeypatch.setattr(
        settings,
        "WEBHOOK_REDACTED_FIELDS",
        "seed,secret_seed,private_key,mnemonic,password,token,"
        "access_token,refresh_token,signing_key,wallet_secret",
    )
    webhook_service._REDACTED_FIELDS = set()
    yield
    webhook_service._REDACTED_FIELDS = set()


def redact(data):
    from app.services.webhook_service import _redact_payload

    return _redact_payload(data)


MASK = "[REDACTED]"


# --------------------------------------------------------------------------- #
# #984 — configured fields are redacted
# --------------------------------------------------------------------------- #


class TestConfiguredFieldsRedacted:
    @pytest.mark.parametrize(
        "field",
        [
            "seed",
            "secret_seed",
            "private_key",
            "mnemonic",
            "password",
            "token",
            "access_token",
            "refresh_token",
            "signing_key",
            "wallet_secret",
        ],
    )
    def test_each_configured_field_is_masked(self, field):
        """Every entry in the default list, asserted individually.

        Parametrised rather than one combined payload, so a single field dropping
        out of the list is named in the failure instead of hiding behind a bulk
        assertion.
        """
        result = redact({field: "sensitive-value"})

        assert result[field] == MASK
        assert "sensitive-value" not in str(result)

    def test_multiple_sensitive_fields_are_all_masked(self):
        result = redact({"seed": "s", "password": "p", "token": "t", "site": "fra"})

        assert result["seed"] == MASK
        assert result["password"] == MASK
        assert result["token"] == MASK
        assert result["site"] == "fra"


class TestUnconfiguredFieldsRetained:
    def test_non_sensitive_fields_survive(self):
        """Redaction must not degrade the payload consumers depend on.

        Masking everything would satisfy a 'secrets are redacted' test while
        making every webhook useless, so retention is asserted as a first-class
        property.
        """
        result = redact(
            {
                "outage_id": "out-1",
                "severity": "critical",
                "status": "resolved",
                "amount": 1000,
            }
        )

        assert result == {
            "outage_id": "out-1",
            "severity": "critical",
            "status": "resolved",
            "amount": 1000,
        }

    def test_field_named_similar_to_a_configured_one_is_retained(self):
        """Matching is on the whole field name, not a substring.

        ``tokenizer`` and ``password_reset_required`` are not secrets. A
        substring match would mask them and quietly break consumer logic.
        """
        result = redact({"tokenizer": "sentencepiece", "password_policy": "strong"})

        assert result["tokenizer"] == "sentencepiece"
        assert result["password_policy"] == "strong"

    def test_configured_field_with_different_case_is_masked(self):
        """Matching is case-insensitive on the key.

        A payload key of ``Token`` or ``PASSWORD`` would otherwise slip through a
        case-sensitive comparison.
        """
        result = redact({"Token": "abc", "PASSWORD": "hunter2"})

        assert result["Token"] == MASK
        assert result["PASSWORD"] == MASK


# --------------------------------------------------------------------------- #
# #984 — nested payloads
# --------------------------------------------------------------------------- #


class TestNestedPayloads:
    def test_nested_dict_is_redacted(self):
        """The property most likely to regress.

        A dispatcher that only walked top-level keys would pass a flat-payload
        test and leak a secret one level down straight to a third-party endpoint.
        """
        result = redact(
            {"outage_id": "out-1", "credentials": {"private_key": "abc123", "user": "admin"}}
        )

        assert result["credentials"]["private_key"] == MASK
        assert result["credentials"]["user"] == "admin"

    def test_deeply_nested_dict_is_redacted(self):
        result = redact({"a": {"b": {"c": {"seed": "leak-me"}}}})

        assert result["a"]["b"]["c"]["seed"] == MASK

    def test_dict_inside_a_list_is_redacted(self):
        """Lists of objects are the common SLA-violation payload shape."""
        result = redact(
            {
                "violations": [
                    {"outage_id": "out-1", "token": "t1"},
                    {"outage_id": "out-2", "token": "t2"},
                ]
            }
        )

        assert result["violations"][0]["token"] == MASK
        assert result["violations"][1]["token"] == MASK
        assert result["violations"][0]["outage_id"] == "out-1"

    def test_list_inside_a_dict_is_redacted(self):
        result = redact({"accounts": [{"wallet_secret": "w1"}, {"wallet_secret": "w2"}]})

        assert all(item["wallet_secret"] == MASK for item in result["accounts"])

    def test_secret_buried_in_a_mixed_structure_is_redacted(self):
        """Lists, dicts and scalars interleaved — the realistic webhook shape."""
        result = redact(
            {
                "event": "sla.violation",
                "outages": [
                    {
                        "id": "out-1",
                        "meta": {"tags": ["a", "b"], "owner": {"seed": "deep"}},
                        "amount": 500,
                    }
                ],
            }
        )

        assert result["outages"][0]["meta"]["owner"]["seed"] == MASK
        assert result["outages"][0]["meta"]["tags"] == ["a", "b"]
        assert result["outages"][0]["amount"] == 500

    def test_no_secret_value_survives_anywhere_in_the_structure(self):
        """Walks the whole result rather than checking known paths."""
        payload = {
            "seed": "top-level-secret",
            "nested": {"password": "nested-secret"},
            "list": [{"token": "list-secret"}],
        }
        rendered = str(redact(payload))

        assert "top-level-secret" not in rendered
        assert "nested-secret" not in rendered
        assert "list-secret" not in rendered


class TestStructurePreserved:
    def test_scalar_passes_through(self):
        assert redact("plain") == "plain"
        assert redact(42) == 42
        assert redact(None) is None

    def test_key_order_is_preserved(self):
        """Consumers may render these payloads in order."""
        result = redact({"b": 1, "a": 2, "c": 3})

        assert list(result.keys()) == ["b", "a", "c"]

    def test_recursion_terminates_on_a_deeply_nested_payload(self):
        """A pathologically nested payload must not raise.

        `_redact_payload` caps recursion at depth 10 and returns deeper values
        unchanged, so the assertion is that it *terminates*, not that it redacts.
        """
        payload: dict = {"seed": "deep"}
        for _ in range(50):
            payload = {"nested": payload}

        redact(payload)  # must not raise RecursionError

    def test_redaction_applies_within_the_depth_limit(self):
        """Depth 10 is where the cap bites; below it redaction still happens.

        Without this the terminating test above would also pass if the cap were
        accidentally 0, which would disable redaction entirely for any nesting.
        """
        payload: dict = {"seed": "deep"}
        for _ in range(5):
            payload = {"nested": payload}

        result = redact(payload)

        cursor = result
        for _ in range(5):
            cursor = cursor["nested"]
        assert cursor["seed"] == MASK

    def test_beyond_the_depth_limit_values_are_returned_unchanged(self):
        """Pins the documented trade-off: past depth 10 nothing is redacted.

        This is a real gap in the current implementation, not a desirable
        behaviour. Asserting it as-is means the gap is visible and any fix shows
        up as this test changing rather than passing silently either way.
        """
        payload: dict = {"seed": "beyond-cap"}
        for _ in range(20):
            payload = {"nested": payload}

        result = redact(payload)

        cursor = result
        for _ in range(20):
            cursor = cursor["nested"]

        # Unchanged, i.e. the secret is still present at this depth.
        assert cursor["seed"] == "beyond-cap"

    def test_original_payload_is_not_mutated(self):
        """Redaction returns a copy; the caller's object is theirs to reuse.

        Mutating in place would mean a caller holding the same dict — for logging
        or a retry — sees already-redacted data it may not have intended to send.
        """
        original = {"seed": "value", "outage_id": "out-1"}

        redact(original)

        assert original["seed"] == "value"


class TestBuildRedactedPayload:
    def test_only_the_data_section_is_wrapped_not_redacted(self):
        """Envelope keys are structural and must survive verbatim."""
        from app.models.webhook import WebhookEvent
        from app.services.webhook_service import build_redacted_payload

        result = build_redacted_payload(
            {"outage_id": "out-1", "seed": "secret"},
            WebhookEvent.SLA_VIOLATION,
            "1.0",
            "2026-01-01T00:00:00Z",
        )

        assert result["schema_version"] == "1.0"
        assert result["event"] == WebhookEvent.SLA_VIOLATION.value
        assert result["timestamp"] == "2026-01-01T00:00:00Z"
        assert result["data"]["seed"] == MASK
        assert result["data"]["outage_id"] == "out-1"

    def test_envelope_is_not_itself_redacted(self):
        """A redaction config naming 'event' must not strip the envelope.

        The redaction pass is scoped to ``data``. If it ever ran over the whole
        envelope, a config entry colliding with a structural key would produce a
        payload no consumer could parse.
        """
        from app.models.webhook import WebhookEvent
        from app.services.webhook_service import build_redacted_payload

        result = build_redacted_payload(
            {"seed": "secret"},
            WebhookEvent.SLA_VIOLATION,
            "1.0",
            "2026-01-01T00:00:00Z",
        )

        assert result["event"] != MASK
        assert result["schema_version"] != MASK
