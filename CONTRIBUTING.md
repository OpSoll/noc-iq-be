# Contributing to NOCIQ

First off, thank you for considering contributing to NOCIQ! It's people like you that make NOCIQ such a great tool for network operations teams.

## 🌊 Participating in Stellar Wave

NOCIQ is part of the [Stellar Wave Program](https://www.drips.network/wave/stellar)! If you're here from the Wave:

1. **Browse Issues**: Look for issues tagged with `Stellar Wave`
2. **Apply to Work**: Comment on the issue you want to work on
3. **Get Assigned**: Wait for a maintainer to assign you
4. **Submit PR**: Create a pull request when ready

**Important**: Only one contributor per issue. First to apply and get assigned gets the work.

## 🤝 Ways to Contribute

There are many ways to contribute to NOCIQ:

- **Report bugs** and issues
- **Suggest new features** or enhancements
- **Fix bugs** and implement features
- **Improve documentation**
- **Write tests** to increase coverage
- **Review pull requests**
- **Help answer questions** in discussions

## 🚀 Getting Started

### Prerequisites

**For Frontend (noc-iq-fe):**
- Node.js 18.x or higher
- npm or yarn
- Git
- Freighter wallet (for Stellar features)

**For Backend (noc-iq-be):**
- Python 3.9 or higher
- pip and virtualenv
- Git

**For Smart Contracts (noc-iq-contracts):**
- Rust and Cargo
- Soroban CLI
- Stellar CLI

### Fork and Clone

1. **Fork the repository** on GitHub
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/noc-iq-fe.git
   # or
   git clone https://github.com/YOUR_USERNAME/noc-iq-be.git
   # or
   git clone https://github.com/YOUR_USERNAME/noc-iq-contracts.git
   ```
3. **Add upstream remote**:
   ```bash
   git remote add upstream https://github.com/OpSoll/noc-iq-fe.git
   ```

### Setup Development Environment

**Frontend:**
```bash
cd noc-iq-fe
npm install
cp .env.example .env.local
# Edit .env.local with your config
npm run dev
```

**Backend:**
```bash
cd noc-iq-be
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your config
uvicorn main:app --reload
```

**Smart Contracts:**
```bash
cd noc-iq-contracts
# Install Soroban CLI if you haven't
cargo install --locked soroban-cli
# Build contracts
make build
# Run tests
make test
```

## 📝 Development Workflow

### 1. Create a Branch

Always create a new branch for your work:

```bash
git checkout -b feature/wallet-integration
# or
git checkout -b fix/payment-bug
# or
git checkout -b docs/stellar-guide
```

**Branch naming convention:**
- `feature/description` - New features
- `fix/description` - Bug fixes
- `docs/description` - Documentation
- `test/description` - Adding tests
- `refactor/description` - Code refactoring

### 2. Make Your Changes

- Write clean, readable code
- Follow the project's code style (see below)
- Add tests for new functionality
- Update documentation as needed
- Keep commits focused and atomic

### 3. Test Your Changes

**Frontend:**
```bash
npm run test
npm run lint
npm run type-check
```

**Backend:**
```bash
pytest
pytest --cov=app --cov-report=html
black app/
flake8 app/
mypy app/
```

**Smart Contracts:**
```bash
cargo test
cargo clippy -- -D warnings
```

### 4. Commit Your Changes

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```bash
git commit -m "feat: add wallet balance display"
git commit -m "fix: resolve payment timeout issue"
git commit -m "docs: update stellar integration guide"
git commit -m "test: add unit tests for SLA calculator"
```

**Commit message format:**
```
<type>: <description>

[optional body]

[optional footer]
```

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, semicolons, etc.)
- `refactor`: Code refactoring
- `test`: Adding or updating tests
- `chore`: Maintenance tasks
- `perf`: Performance improvements

### 5. Push and Create Pull Request

```bash
git push origin feature/wallet-integration
```

Then open a pull request on GitHub with:
- **Clear title** following conventional commit format
- **Description** of what you changed and why
- **Screenshots** (for UI changes)
- **Testing notes** (how you tested the changes)
- **Related issue**: `Closes #123` or `Fixes #456`

## 🎨 Code Style Guidelines

### Frontend (TypeScript/React)

- Use **TypeScript** for all new files
- Follow **React hooks** best practices
- Use **functional components** over class components
- Use **Tailwind CSS** for styling (no inline styles)
- Use **shadcn/ui** components when available
- **Extract reusable logic** into custom hooks
- **PropTypes or TypeScript interfaces** for all components

**Example:**
```typescript
import { useState } from 'react';
import { Button } from '@/components/ui/button';

interface WalletConnectProps {
  onConnect: (publicKey: string) => void;
}

export function WalletConnect({ onConnect }: WalletConnectProps) {
  const [connected, setConnected] = useState(false);
  
  // Component logic here
  
  return (
    <Button onClick={handleConnect}>
      {connected ? 'Disconnect' : 'Connect Wallet'}
    </Button>
  );
}
```

### Backend (Python/FastAPI)

- Follow **PEP 8** style guide
- Use **type hints** for all functions
- Write **docstrings** for all public functions
- Use **async/await** for I/O operations
- **Pydantic models** for request/response validation
- **Dependency injection** for services
- **Environment variables** for configuration

**Example:**
```python
from fastapi import APIRouter, Depends, HTTPException
from app.models.payment import PaymentCreate, PaymentResponse
from app.services.stellar.payment_service import PaymentService
from app.api.deps import get_current_user

router = APIRouter()

@router.post("/payments", response_model=PaymentResponse)
async def create_payment(
    payment: PaymentCreate,
    current_user = Depends(get_current_user)
) -> PaymentResponse:
    """
    Create a new payment transaction on Stellar network.
    
    Args:
        payment: Payment details including amount and destination
        current_user: Currently authenticated user
        
    Returns:
        PaymentResponse with transaction hash and status
        
    Raises:
        HTTPException: If payment creation fails
    """
    service = PaymentService()
    result = await service.create_payment(payment)
    return result
```

### Smart Contracts (Rust/Soroban)

- Follow **Rust best practices**
- **Document all public functions**
- Use **proper error handling**
- **Test all functions** thoroughly
- Keep **gas costs** in mind
- Use **clippy** for linting

**Example:**
```rust
#[contractimpl]
impl SLAContract {
    /// Calculate SLA result for an outage
    /// 
    /// # Arguments
    /// * `outage_id` - Unique identifier for the outage
    /// * `severity` - Severity level (Critical, High, Medium, Low)
    /// * `mttr_minutes` - Mean time to repair in minutes
    /// 
    /// # Returns
    /// SLAResult containing status and payment information
    pub fn calculate_sla(
        env: Env,
        outage_id: Symbol,
        severity: Severity,
        mttr_minutes: u32,
    ) -> SLAResult {
        // Implementation here
    }
}
```

## ✅ Pull Request Guidelines

### Before Submitting

- [ ] Code follows the style guidelines
- [ ] Self-review completed
- [ ] Tests added/updated and passing
- [ ] Documentation updated
- [ ] No console.log or print statements
- [ ] Environment variables in .env.example
- [ ] Breaking changes clearly documented

### PR Description Template

```markdown
## Description
Brief description of the changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Related Issue
Closes #123

## Testing
- [ ] Unit tests added/updated
- [ ] Integration tests added/updated
- [ ] Manual testing completed

## Checklist
- [ ] Code follows style guidelines
- [ ] Self-review completed
- [ ] Documentation updated
- [ ] Tests pass locally

## Screenshots (if applicable)
[Add screenshots here]

## Additional Notes
Any additional information for reviewers
```

### For Stellar Wave Contributors

Include in your PR description:
- **Testnet transaction hashes** (for blockchain features)
- **Video/GIF** of feature working (for UI changes)
- **Performance metrics** (if relevant)
- **Time spent** on the issue (optional)

## 🧪 Testing Guidelines

### Frontend Tests

```bash
# Run all tests
npm run test

# Run tests in watch mode
npm run test:watch

# Run with coverage
npm run test:coverage
```

**Test structure:**
```typescript
import { render, screen, fireEvent } from '@testing-library/react';
import { WalletConnect } from './WalletConnect';

describe('WalletConnect', () => {
  it('should connect to Freighter wallet', async () => {
    render(<WalletConnect onConnect={jest.fn()} />);
    
    const button = screen.getByText('Connect Wallet');
    fireEvent.click(button);
    
    // Assertions here
  });
});
```

### Backend Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_payment_service.py

# Run with coverage
pytest --cov=app --cov-report=html
```

**Test structure:**
```python
import pytest
from app.services.stellar.payment_service import PaymentService

@pytest.mark.asyncio
async def test_create_payment():
    """Test payment creation on Stellar network"""
    service = PaymentService(network="testnet")
    
    result = await service.create_payment(
        source_secret="S...",
        destination="G...",
        amount="10.00"
    )
    
    assert result["status"] == "success"
    assert "tx_hash" in result
```

### Smart Contract Tests

```bash
# Run tests
cargo test

# Run with output
cargo test -- --nocapture
```

## 📚 Documentation Guidelines

- Use **clear, concise language**
- Include **code examples**
- Add **screenshots** for UI features
- Keep **up-to-date** with code changes
- **Link to related docs** where helpful
- Use **Markdown** for formatting

## 🔒 Security Guidelines

**CRITICAL: Security is everyone's responsibility. Follow these guidelines strictly.**

### Secret Management
- **Never commit secrets** (API keys, private keys, passwords, tokens) to version control
- Use **environment variables** or a secrets manager (AWS Secrets Manager, HashiCorp Vault) for sensitive data
- **Never log secrets** or include them in error messages, stack traces, or documentation
- Use **separate secrets** for each environment (dev/staging/prod)
- **Rotate secrets** regularly and immediately after any suspected compromise

### Authentication & Authorization
- Always use **bcrypt** for password hashing (never plaintext or weak hashes)
- Implement **rate limiting** on auth endpoints to prevent brute force attacks
- Use **token rotation** for refresh tokens to detect replay attacks
- Follow the **principle of least privilege** for all API endpoints
- Validate **role-based access** on every protected endpoint

### Input Validation & Sanitization
- **Validate all inputs** using Pydantic models with strict type checking
- Enforce **payload size limits** to prevent abuse (see MAX_REQUEST_BODY_SIZE_BYTES)
- **Sanitize user inputs** before storage or processing
- Use **parameterized queries** for database operations (SQLAlchemy handles this)
- Implement **CORS policies** that restrict allowed origins

### Blockchain & Wallet Security
- **NEVER expose Stellar secret keys** (starting with 'S') in code, logs, or docs
- Only use **public keys** (starting with 'G') for wallet linking and balance queries
- **Separate testnet and mainnet keys** - never reuse across environments
- Use **hardware security modules (HSM)** or secure enclaves for production key storage
- Implement **transaction validation** before submission (amount, destination, asset type)

### Webhook Security
- Always **verify webhook signatures** using HMAC-SHA256 before processing
- Implement **idempotency** to prevent duplicate webhook processing
- Use **HTTPS** for all webhook endpoints
- Validate **webhook payload structure** before acting on events

### Audit Logging (BE-010)
- **Never log sensitive data** (passwords, tokens, secret keys)
- Include **actor attribution** (user ID/email) in all audit events
- Add **correlation IDs** to track requests across services
- Audit logs are **immutable** - never modify or delete audit entries
- Redact sensitive fields automatically using the audit service's sanitization logic

### Code Review Security Checklist
Before approving any PR, verify:
- [ ] No secrets or credentials in code or comments
- [ ] All user inputs are validated and sanitized
- [ ] Auth checks are present on protected endpoints
- [ ] Error messages don't leak sensitive information
- [ ] Dependencies are up-to-date and free of known vulnerabilities
- [ ] Audit logging captures security-relevant events


## 🐛 Reporting Bugs

Use the GitHub issue template and include:

- **Clear title** describing the bug
- **Steps to reproduce** the issue
- **Expected behavior**
- **Actual behavior**
- **Screenshots** (if applicable)
- **Environment details** (OS, browser, versions)
- **Error messages** (full stack trace if possible)
- **For Stellar issues**: Include network (testnet/mainnet) and transaction hash

## 💡 Suggesting Features

Use the GitHub issue template and include:

- **Clear title** describing the feature
- **Problem statement** (what problem does this solve?)
- **Proposed solution**
- **Alternative solutions** considered
- **Additional context** (mockups, examples, etc.)


---

## 🔁 CI Pipeline

NOCIQ uses a **multi-stage GitHub Actions CI pipeline** designed for fast, actionable feedback.  All stages cache pip dependencies using `actions/cache` and upload stage-specific artifacts.

### Stage Dependency Graph

```
lint ──┬──► tests ─────────────┐
       └──► contract-checks ───┴──► integration-checks

release-drift-check  (push to main / release/**)
benchmarks           (push to main / release/**)
```

### Stage Summary

| Stage | Job name | What it checks | Artifact |
|-------|----------|---------------|----------|
| 1 | `lint` | flake8 + mypy type checks | — |
| 2a | `tests` | Unit & integration tests (excludes contract/stellar) | `unit-test-results/` (14 days) |
| 2b | `contract-checks` | Contract parity (`test_contract_parity.py`) | `contract-check-results/` (14 days) |
| 3 | `integration-checks` | Stellar Wave integration (`test_stellar_wave_issues.py`) | `integration-check-results/` (14 days) |
| — | `benchmarks` | Analytics export latency benchmarks | `benchmark-results-<sha>/` (30 days) |
| — | `release-drift-check` | docs/router/config synchronisation drift | `release-drift-report-<sha>.json` (30 days) |

### Fail-fast behaviour

Each stage gate **fails fast** and blocks dependent stages:
- **`lint` fails** → `tests` and `contract-checks` are skipped immediately.
- **`tests` or `contract-checks` fails** → `integration-checks` is skipped.
- **`release-drift-check` exits 1** → workflow fails; the JSON drift report is still uploaded.

### Reproducing locally

```bash
# Stage 1 — Lint & type check
flake8 app/ --max-line-length=120 --extend-ignore=E203,W503
mypy app/ --ignore-missing-imports --no-strict-optional

# Stage 2a — Unit & integration tests
pytest tests/ \
  --ignore=tests/test_contract_parity.py \
  --ignore=tests/test_stellar_wave_issues.py \
  --ignore=tests/test_analytics_benchmarks.py \
  -v

# Stage 2b — Contract checks
pytest tests/test_contract_parity.py -v

# Stage 3 — Integration checks
pytest tests/test_stellar_wave_issues.py -v

# Benchmarks
pytest tests/test_analytics_benchmarks.py -v

# Release drift check
python scripts/check_release_drift.py
```

### Analytics Benchmark Thresholds

The benchmark suite (`tests/test_analytics_benchmarks.py`) asserts:
- **Aggregation operations** complete in < `AGGREGATION_LATENCY_THRESHOLD_MS` (default 200 ms).
- **Export operations** complete in < `EXPORT_LATENCY_THRESHOLD_MS` (default 500 ms).

CI relaxes thresholds via env vars:
```bash
AGGREGATION_LATENCY_THRESHOLD_MS=500 EXPORT_LATENCY_THRESHOLD_MS=1000 \
  pytest tests/test_analytics_benchmarks.py
```

Benchmark results are written to `tests/benchmark-results.json` and uploaded as a named artifact per commit SHA, enabling trend comparison across releases.

### Release Drift Check

`scripts/check_release_drift.py` cross-checks three sources for synchronisation:

1. **API docs vs router**: Every path documented in `docs/API.md` must be reachable via a registered prefix in `app/api/v1/router.py`.
2. **Config vs `.env.example`**: Every `Settings` field in `app/core/config.py` should have an entry in `.env.example`.
3. **Router vs filesystem**: Every module imported in `router.py` must exist on disk.

Drift findings are categorised as `critical`, `warning`, or `info`.  **Critical findings cause a CI failure** (exit code 1).

---

## 🙏 Thank You!

Your contributions make NOCIQ better for everyone. We appreciate your time and effort!

---

**Happy coding! 🚀**
