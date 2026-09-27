from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Generator

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.trace import Span, StatusCode

logger = logging.getLogger(__name__)

_provider: TracerProvider | None = None
_tracer: trace.Tracer | None = None


def init_tracing() -> None:
    """Set up the global TracerProvider and OTLP exporter."""
    global _provider, _tracer

    service_name = os.getenv("OTEL_SERVICE_NAME", "nociq-api")
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

    resource = Resource.create({SERVICE_NAME: service_name})
    exporter = OTLPSpanExporter(endpoint=endpoint)

    _provider = TracerProvider(resource=resource)
    _provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(_provider)

    _tracer = trace.get_tracer(service_name)
    logger.info(
        "OpenTelemetry tracing initialised (service=%s, endpoint=%s)",
        service_name,
        endpoint,
    )


def shutdown_tracing() -> None:
    """Flush remaining spans and shut down the provider."""
    if _provider is not None:
        _provider.shutdown()
        logger.info("OpenTelemetry tracing shut down")


def get_tracer() -> trace.Tracer:
    if _tracer is None:
        raise RuntimeError("Tracing has not been initialised – call init_tracing() first")
    return _tracer


def instrument_fastapi(app) -> None:  # type: ignore[no-untyped-def]
    """Auto-instrument a FastAPI application."""
    if os.environ.get("TESTING") or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        logger.info("FastAPI auto-instrumentation enabled")
    except Exception:
        logger.exception("Failed to instrument FastAPI with OpenTelemetry")


@contextmanager
def span(name: str, **attributes: str | int | float) -> Generator[Span, None, None]:
    """Create a child span with optional attributes."""
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as s:
        for k, v in attributes.items():
            s.set_attribute(k, v)
        try:
            yield s
        except Exception as exc:
            s.set_status(StatusCode.ERROR, str(exc))
            s.record_exception(exc)
            raise
        else:
            s.set_status(StatusCode.OK)
