"""Mock Stellar Horizon client for offline payment service unit tests.

Provides a MockStellarClient that simulates Stellar Horizon API responses
for transaction submission, balance queries, and account lookups without
requiring a live Horizon server connection.

Usage:
    from tests.mocks.stellar import MockStellarClient

    client = MockStellarClient()
    balance = client.get_account_balance("GABC...")
    result = client.submit_transaction(tx_envelope)
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4


@dataclass
class MockAccount:
    """Simulated Stellar account state."""

    address: str
    sequence: int = 100000000
    balances: List[Dict[str, Any]] = field(default_factory=list)
    exists: bool = True
    home_domain: Optional[str] = None
    thresholds: Dict[str, int] = field(default_factory=lambda: {
        "low": 0,
        "medium": 1,
        "high": 2,
    })


@dataclass
class MockTransactionResult:
    """Simulated transaction submission result."""

    hash: str
    successful: bool = True
    ledger: int = 100000
    envelope_xdr: str = ""
    result_xdr: str = "AAAAAgAAAA..."
    fee_charged: int = 100
    created_at: str = ""
    memos: List[Dict[str, Any]] = field(default_factory=list)
    operations: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


@dataclass
class MockHorizonError(Exception):
    """Simulated Horizon API error response."""

    status: int = 400
    title: str = "Error"
    detail: str = ""
    type: str = "https://stellar.org/horizon-errors/"


class MockStellarClient:
    """Mock Stellar Horizon client for offline testing.

    Simulates key Stellar Horizon API endpoints:
    - Account balance queries
    - Transaction submission
    - Account existence checks
    - Trustline status

    Acceptance Criteria:
    - Mock transaction submission and balance query responses.
    - Execute all payment unit tests offline without network dependencies.
    """

    def __init__(self) -> None:
        self._accounts: Dict[str, MockAccount] = {}
        self._submitted_transactions: List[MockTransactionResult] = []
        self._network_passphrase: str = "Test SDF Network ; September 2015"
        self._default_balance_code: str = "USDC"
        self._default_balance_issuer: str = "TEST_ISSUER_ADDRESS"

    def configure_default_account(
        self,
        address: str,
        balances: Optional[List[Dict[str, Any]]] = None,
        sequence: int = 100000000,
    ) -> MockAccount:
        """Configure a mock account with given balances."""
        if balances is None:
            balances = [
                {
                    "asset_type": "native",
                    "balance": "100.0000000",
                },
                {
                    "asset_type": "credit_alphanum4",
                    "asset_code": self._default_balance_code,
                    "asset_issuer": self._default_balance_issuer,
                    "balance": "500.0000000",
                    "limit": "10000000.0000000",
                },
            ]
        account = MockAccount(address=address, sequence=sequence, balances=balances)
        self._accounts[address] = account
        return account

    def get_account_balance(self, address: str) -> Dict[str, Any]:
        """Fetch account balances from the Horizon server.

        Returns:
            Dict with 'balances' list and 'sequence' number.

        Raises:
            MockHorizonError: If account does not exist.
        """
        account = self._accounts.get(address)
        if not account or not account.exists:
            raise MockHorizonError(
                status=404,
                title="Resource Missing",
                detail=f"Account {address} not found",
            )
        return {
            "address": address,
            "balances": account.balances,
            "sequence": str(account.sequence),
            "subentry_count": len(account.balances),
            "thresholds": account.thresholds,
        }

    def submit_transaction(self, envelope_xdr: str) -> MockTransactionResult:
        """Submit a transaction envelope to the Horizon server.

        Returns:
            MockTransactionResult with a generated hash and success status.

        Raises:
            MockHorizonError: On simulated submission failure.
        """
        # Generate deterministic hash from envelope
        tx_hash = hashlib.sha256(envelope_xdr.encode()).hexdigest()[:64].upper()

        result = MockTransactionResult(
            hash=tx_hash,
            successful=True,
            ledger=len(self._submitted_transactions) + 100000,
            envelope_xdr=envelope_xdr,
            fee_charged=100,
        )
        self._submitted_transactions.append(result)
        return result

    def submit_transaction_expect_failure(
        self, envelope_xdr: str, error_title: str = "Transaction Failed"
    ) -> MockHorizonError:
        """Simulate a failed transaction submission."""
        error = MockHorizonError(
            status=400,
            title=error_title,
            detail="simulated transaction failure",
            type="https://stellar.org/horizon-errors/transaction_failed",
        )
        return error

    def account_exists(self, address: str) -> bool:
        """Check if an account exists on the network."""
        account = self._accounts.get(address)
        return account is not None and account.exists

    def get_account_trustlines(
        self, address: str, asset_code: str, asset_issuer: str
    ) -> Dict[str, Any]:
        """Check trustline status for an account.

        Returns:
            Dict with 'status', 'balance', 'limit' keys.
        """
        account = self._accounts.get(address)
        if not account or not account.exists:
            return {"status": "missing", "balance": None, "limit": None}

        for balance in account.balances:
            if (
                balance.get("asset_code") == asset_code
                and balance.get("asset_issuer") == asset_issuer
            ):
                limit = balance.get("limit", "0")
                return {
                    "status": "ready" if float(limit) > 0 else "limit_zero",
                    "balance": balance.get("balance"),
                    "limit": limit,
                }

        return {"status": "missing", "balance": None, "limit": None}

    def get_transaction_history(
        self, address: str, limit: int = 10
    ) -> List[MockTransactionResult]:
        """Get recent transactions for an account (mock)."""
        return self._submitted_transactions[-limit:]

    def get_submitted_transactions(self) -> List[MockTransactionResult]:
        """Return all submitted transactions (test helper)."""
        return list(self._submitted_transactions)

    def reset(self) -> None:
        """Reset all mock state."""
        self._accounts.clear()
        self._submitted_transactions.clear()

    @property
    def network_passphrase(self) -> str:
        return self._network_passphrase


class MockStellarClientFactory:
    """Factory for creating pre-configured MockStellarClient instances."""

    @staticmethod
    def for_testing() -> MockStellarClient:
        """Create a client with common test defaults."""
        client = MockStellarClient()
        client.configure_default_account(
            address="GABC1234567890ABCDEF",
            balances=[
                {"asset_type": "native", "balance": "100.0000000"},
                {
                    "asset_type": "credit_alphanum4",
                    "asset_code": "USDC",
                    "asset_issuer": "TEST_ISSUER",
                    "balance": "500.0000000",
                    "limit": "10000000.0000000",
                },
            ],
        )
        client.configure_default_account(
            address="GDEF9876543210FEDCBA",
            balances=[
                {"asset_type": "native", "balance": "0.0000000"},
            ],
        )
        return client

    @staticmethod
    def for_empty_account() -> MockStellarClient:
        """Create a client with an unfunded account."""
        client = MockStellarClient()
        client.configure_default_account(
            address="GEMPTY00000000000000",
            balances=[
                {"asset_type": "native", "balance": "0.0000000"},
            ],
        )
        return client

    @staticmethod
    def for_missing_account() -> MockStellarClient:
        """Create a client with no accounts configured."""
        return MockStellarClient()
