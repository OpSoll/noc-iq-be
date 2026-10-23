"""Tests for issue #529 — database connection retry on startup."""
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
