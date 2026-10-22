"""Tests for issue #529 / #1002 — database connection retry on startup.

Covers:
- Transient failure then success (retry then succeed)
- Permanent failure (all retries exhausted, raises)
- Backoff bounded (exponential growth capped by retry count)
- First-attempt success (no retry needed)
- Warmup delegation
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from app.db import session as db_session


class TestConnectDbWithRetry:
    def test_succeeds_on_first_attempt(self, caplog):
        with caplog.at_level(logging.INFO, logger=db_session.logger.name):
            with patch.object(db_session, "_attempt_db_connection") as attempt:
                db_session.connect_db_with_retry(max_retries=3, base_backoff=2.0)

        attempt.assert_called_once()
        assert "Database connection attempt 1/3" in caplog.text
        assert "Database connection established on attempt 1/3" in caplog.text

    def test_retries_until_success(self, caplog):
        """Transient failure then success: first attempt fails, second succeeds."""
        attempt = MagicMock(
            side_effect=[ConnectionError("connection refused"), None],
        )
        with caplog.at_level(logging.INFO, logger=db_session.logger.name):
            with (
                patch.object(db_session, "_attempt_db_connection", attempt),
                patch.object(db_session.time, "sleep") as sleep,
            ):
                db_session.connect_db_with_retry(max_retries=3, base_backoff=2.0)

        assert attempt.call_count == 2
        sleep.assert_called_once_with(2.0)
        assert "Database connection attempt 1/3 failed" in caplog.text
        assert "Database connection established on attempt 2/3" in caplog.text

    def test_uses_exponential_backoff(self):
        """Backoff doubles between retries: 2s, 4s, 8s, ..."""
        attempt = MagicMock(
            side_effect=[
                ConnectionError("fail 1"),
                ConnectionError("fail 2"),
                None,
            ],
        )
        with (
            patch.object(db_session, "_attempt_db_connection", attempt),
            patch.object(db_session.time, "sleep") as sleep,
        ):
            db_session.connect_db_with_retry(max_retries=5, base_backoff=2.0)

        assert sleep.call_args_list[0].args == (2.0,)
        assert sleep.call_args_list[1].args == (4.0,)

    def test_raises_after_max_retries(self, caplog):
        """Permanent failure: all retries exhausted, last exception raised."""
        attempt = MagicMock(side_effect=ConnectionError("still down"))
        with (
            patch.object(db_session, "_attempt_db_connection", attempt),
            patch.object(db_session.time, "sleep"),
            pytest.raises(ConnectionError, match="still down"),
        ):
            db_session.connect_db_with_retry(max_retries=3, base_backoff=2.0)

        assert attempt.call_count == 3
        assert "Database connection failed after 3 attempts" in caplog.text

    def test_warmup_db_pool_delegates_to_retry(self):
        with patch.object(db_session, "connect_db_with_retry") as retry:
            db_session.warmup_db_pool()

        retry.assert_called_once()

    # -----------------------------------------------------------------------
    # Issue #1002: additional coverage for backoff bounded and multi-retry
    # -----------------------------------------------------------------------

    def test_backoff_bounded_by_max_retries(self):
        """Backoff is bounded: with 3 retries the max delay is base * 2^(n-1).

        With base_backoff=2.0 and 3 retries, delays are:
          attempt 1 fail -> sleep 2.0 (2^0 * 2)
          attempt 2 fail -> sleep 4.0 (2^1 * 2)
          attempt 3 fail -> no sleep (max reached, raise)
        """
        attempt = MagicMock(side_effect=ConnectionError("down"))
        with (
            patch.object(db_session, "_attempt_db_connection", attempt),
            patch.object(db_session.time, "sleep") as sleep,
            pytest.raises(ConnectionError),
        ):
            db_session.connect_db_with_retry(max_retries=3, base_backoff=2.0)

        # Only 2 sleeps (between 3 attempts), not 3
        assert sleep.call_count == 2
        # Delays: 2.0, 4.0
        assert sleep.call_args_list[0].args == (2.0,)
        assert sleep.call_args_list[1].args == (4.0,)

    def test_transient_failure_then_success_multiple_retries(self):
        """Two transient failures, then success on third attempt."""
        attempt = MagicMock(
            side_effect=[
                ConnectionError("fail 1"),
                ConnectionError("fail 2"),
                ConnectionError("fail 3"),
                None,
            ],
        )
        with (
            patch.object(db_session, "_attempt_db_connection", attempt),
            patch.object(db_session.time, "sleep") as sleep,
        ):
            db_session.connect_db_with_retry(max_retries=5, base_backoff=1.0)

        assert attempt.call_count == 4
        assert sleep.call_count == 3
        # Delays: 1.0, 2.0, 4.0
        assert sleep.call_args_list[0].args == (1.0,)
        assert sleep.call_args_list[1].args == (2.0,)
        assert sleep.call_args_list[2].args == (4.0,)

    def test_permanent_failure_raises_last_exception(self):
        """The exception raised on permanent failure is the last one."""
        exceptions = [
            ConnectionError("fail 1"),
            ConnectionError("fail 2"),
            ConnectionError("fail 3"),
        ]
        attempt = MagicMock(side_effect=exceptions)
        with (
            patch.object(db_session, "_attempt_db_connection", attempt),
            patch.object(db_session.time, "sleep"),
            pytest.raises(ConnectionError, match="fail 3"),
        ):
            db_session.connect_db_with_retry(max_retries=3, base_backoff=2.0)

    def test_no_sleep_on_first_attempt_success(self):
        """When the first attempt succeeds, sleep is never called."""
        with (
            patch.object(db_session, "_attempt_db_connection") as attempt,
            patch.object(db_session.time, "sleep") as sleep,
        ):
            db_session.connect_db_with_retry(max_retries=5, base_backoff=2.0)

        attempt.assert_called_once()
        sleep.assert_not_called()

    def test_does_not_sleep_after_final_attempt(self):
        """No sleep after the last failed attempt — raise immediately."""
        attempt = MagicMock(side_effect=ConnectionError("always fails"))
        with (
            patch.object(db_session, "_attempt_db_connection", attempt),
            patch.object(db_session.time, "sleep") as sleep,
            pytest.raises(ConnectionError),
        ):
            db_session.connect_db_with_retry(max_retries=2, base_backoff=1.0)

        # 2 attempts, 1 sleep (between attempt 1 and 2)
        assert attempt.call_count == 2
        assert sleep.call_count == 1

    def test_logs_each_attempt(self, caplog):
        """Each attempt is logged with its number."""
        attempt = MagicMock(
            side_effect=[ConnectionError("fail"), None],
        )
        with caplog.at_level(logging.INFO, logger=db_session.logger.name):
            with (
                patch.object(db_session, "_attempt_db_connection", attempt),
                patch.object(db_session.time, "sleep"),
            ):
                db_session.connect_db_with_retry(max_retries=3, base_backoff=1.0)

        assert "Database connection attempt 1/3" in caplog.text
        assert "Database connection attempt 2/3" in caplog.text

    def test_logs_warning_on_retry(self, caplog):
        """A warning is logged when an attempt fails and will retry."""
        attempt = MagicMock(
            side_effect=[ConnectionError("transient"), None],
        )
        with caplog.at_level(logging.WARNING, logger=db_session.logger.name):
            with (
                patch.object(db_session, "_attempt_db_connection", attempt),
                patch.object(db_session.time, "sleep"),
            ):
                db_session.connect_db_with_retry(max_retries=3, base_backoff=1.0)

        assert "Database connection attempt 1/3 failed" in caplog.text
        assert "Retrying in 1.0s" in caplog.text

    def test_logs_error_on_final_failure(self, caplog):
        """An error is logged when all retries are exhausted."""
        attempt = MagicMock(side_effect=ConnectionError("permanent"))
        with caplog.at_level(logging.ERROR, logger=db_session.logger.name):
            with (
                patch.object(db_session, "_attempt_db_connection", attempt),
                patch.object(db_session.time, "sleep"),
                pytest.raises(ConnectionError),
            ):
                db_session.connect_db_with_retry(max_retries=2, base_backoff=1.0)

        assert "Database connection failed after 2 attempts" in caplog.text

    def test_default_retries_value(self):
        """Default max_retries is DB_CONNECT_MAX_RETRIES (10)."""
        assert db_session.DB_CONNECT_MAX_RETRIES == 10

    def test_default_backoff_value(self):
        """Default base_backoff is DB_CONNECT_BACKOFF_SECONDS (2.0)."""
        assert db_session.DB_CONNECT_BACKOFF_SECONDS == 2.0
