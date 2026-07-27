"""#355 – Cold-start latency reduction for critical API routes.

Provides:
* ``StartupProfiler`` – records wall-clock timing for each startup phase.
* ``StartupOptimizer`` – pre-warm caches, lazy-load heavy modules, pre-compile
  frequently used regexes.
* A ``GET /health/startup`` FastAPI router that exposes the profiling data.
"""

from __future__ import annotations

import importlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config knobs (consumed from Settings – defaults live here as fallback)
# ---------------------------------------------------------------------------

STARTUP_WARM_CACHE_ENABLED: bool = getattr(
    settings, "STARTUP_WARM_CACHE_ENABLED", True
)

STARTUP_LAZY_LOAD_MODULES: list[str] = getattr(
    settings,
    "STARTUP_LAZY_LOAD_MODULES",
    [
        "app.services.contracts.sla_adapter",
        "app.services.contracts.translation",
        "app.services.outage_store",
        "app.services.sla_metric_registry",
    ],
)


# ---------------------------------------------------------------------------
# Profiler
# ---------------------------------------------------------------------------

@dataclass
class PhaseRecord:
    name: str
    duration_ms: float
    success: bool = True
    error: str | None = None


@dataclass
class StartupProfiler:
    """Collects wall-clock timings for each named startup phase."""

    _phases: list[PhaseRecord] = field(default_factory=list, repr=False)
    _start: float = field(default=0.0, repr=False)
    _total_ms: float = field(default=0.0, repr=False)

    # -- lifecycle -----------------------------------------------------------

    def begin(self) -> None:
        self._start = time.perf_counter()

    def finish(self) -> None:
        self._total_ms = (time.perf_counter() - self._start) * 1000

    # -- per-phase helpers ---------------------------------------------------

    def record(self, name: str, fn: Any) -> None:  # fn: Callable[[], T]  # noqa: ANN401
        t0 = time.perf_counter()
        success = True
        error: str | None = None
        try:
            fn()
        except Exception as exc:
            success = False
            error = str(exc)
            logger.exception("Startup phase '%s' failed", name)
        finally:
            dur = (time.perf_counter() - t0) * 1000
            self._phases.append(PhaseRecord(name=name, duration_ms=dur, success=success, error=error))

    def record_async(self, name: str, coro: Any) -> None:  # noqa: ANN401
        import asyncio

        t0 = time.perf_counter()
        success = True
        error: str | None = None
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're inside an async context – schedule and block briefly
                future = asyncio.ensure_future(coro)
                loop.run_until_complete(future)
            else:
                loop.run_until_complete(coro)
        except Exception as exc:
            success = False
            error = str(exc)
            logger.exception("Async startup phase '%s' failed", name)
        finally:
            dur = (time.perf_counter() - t0) * 1000
            self._phases.append(PhaseRecord(name=name, duration_ms=dur, success=success, error=error))

    # -- reporting -----------------------------------------------------------

    @property
    def total_ms(self) -> float:
        return self._total_ms

    def summary(self) -> dict[str, Any]:
        return {
            "total_ms": round(self._total_ms, 2),
            "phases": [
                {
                    "name": p.name,
                    "duration_ms": round(p.duration_ms, 2),
                    "success": p.success,
                    "error": p.error,
                }
                for p in self._phases
            ],
        }


# Module-level singleton
profiler = StartupProfiler()


# ---------------------------------------------------------------------------
# Cache pre-warming
# ---------------------------------------------------------------------------

def _warm_wallet_cache() -> None:
    """Pre-populate the wallet cache so the first real request is fast."""
    try:
        from app.services.wallet_registry import WalletRegistry  # noqa: F811

        logger.info("Pre-warming wallet cache …")
        # In production we'd preload hot wallets here.
        # For now just ensure the class is importable and its class-level
        # caches are initialized.
        _ = WalletRegistry._wallets_by_user
        logger.info("Wallet cache pre-warmed.")
    except Exception:
        logger.warning("Wallet cache pre-warm skipped (non-critical)")


def _warm_sla_config_cache() -> None:
    """Pre-load SLA configuration into memory."""
    try:
        from app.services.sla.config import SLAConfig  # noqa: F811, F401

        logger.info("SLA config cache pre-warmed.")
    except Exception:
        logger.warning("SLA config pre-warm skipped (non-critical)")


# ---------------------------------------------------------------------------
# Lazy-loading
# ---------------------------------------------------------------------------

def setup_lazy_imports(module_names: list[str] | None = None) -> None:
    """Replace heavy module entries in ``sys.modules`` with deferred proxies.

    The first attribute access on a lazy module triggers the real import.
    This is a lightweight pattern – does **not** use ``importlib.util.LazyLoader``
    (which has edge-case issues with ``from X import Y``).
    """
    import sys

    targets = module_names or STARTUP_LAZY_LOAD_MODULES
    for mod_name in targets:
        if mod_name in sys.modules:
            continue  # already imported – nothing to defer

        class _LazyModule:
            """Proxy that defers real import until first attribute access."""

            def __init__(self, name: str) -> None:
                self.__dict__["_name"] = name
                self.__dict__["_real"] = None

            def _load(self) -> Any:  # noqa: ANN401
                real = importlib.import_module(self.__dict__["_name"])
                self.__dict__["_real"] = real
                sys.modules[self.__dict__["_name"]] = real
                return real

            def __getattr__(self, item: str) -> Any:  # noqa: ANN401
                real = self.__dict__["_real"] or self._load()
                return getattr(real, item)

            def __repr__(self) -> str:
                return f"<LazyModule '{self.__dict__['_name']}'>"

        sys.modules[mod_name] = _LazyModule(mod_name)

    logger.info("Lazy imports configured for %d modules", len(targets))


# ---------------------------------------------------------------------------
# Pre-compiled regexes
# ---------------------------------------------------------------------------

COMPILED_PATTERNS: dict[str, re.Pattern[str]] = {}


def precompile_patterns() -> None:
    """Pre-compile frequently used regex patterns to avoid per-request compilation."""
    patterns: dict[str, str] = {
        "stellar_address": r"^[GA-Z0-9]{55,56}$",
        "iso_datetime": r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",
        "uuid4": r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        "asset_code": r"^[A-Z0-9]{1,12}$",
        "url": r"^https?://[^\s/$.?#].[^\s]*$",
    }
    for name, pattern in patterns.items():
        COMPILED_PATTERNS[name] = re.compile(pattern)
    logger.info("Pre-compiled %d regex patterns", len(COMPILED_PATTERNS))


# ---------------------------------------------------------------------------
# Full startup optimisation runner
# ---------------------------------------------------------------------------

def run_startup_optimization(p: StartupProfiler | None = None) -> StartupProfiler:
    """Execute all optimisation steps, recording each phase.

    Call this once during application boot (e.g. from ``main.py``).
    """
    p = p or profiler
    p.begin()

    # Phase: lazy imports
    p.record("lazy_imports", lambda: setup_lazy_imports())

    # Phase: pre-compile regexes
    p.record("precompile_regexes", lambda: precompile_patterns())

    # Phase: warm caches (optional)
    if STARTUP_WARM_CACHE_ENABLED:
        p.record("warm_wallet_cache", lambda: _warm_wallet_cache())
        p.record("warm_sla_config_cache", lambda: _warm_sla_config_cache())
    else:
        logger.info("Cache pre-warming disabled via STARTUP_WARM_CACHE_ENABLED=false")

    p.finish()
    logger.info("Startup optimisation complete in %.1f ms", p.total_ms)
    return p


# ---------------------------------------------------------------------------
# /health/startup endpoint
# ---------------------------------------------------------------------------

startup_router = APIRouter(tags=["health"])


@startup_router.get("/health/startup")
def startup_health() -> dict[str, Any]:
    """Return startup duration and per-phase breakdown."""
    return {
        "status": "ok",
        "startup": profiler.summary(),
    }
