"""SLA domain error types.

Both errors subclass ``ValueError`` so existing callers that translate SLA
failures into HTTP 400 responses keep working unchanged.
"""


class InvalidSLAConfigError(ValueError):
    """Raised when an SLA severity config violates domain invariants."""


class InvalidMTTRError(ValueError):
    """Raised when an MTTR value falls outside the supported range."""