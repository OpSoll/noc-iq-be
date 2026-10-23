"""Shared formatting for request validation failures."""

from typing import Any


def format_validation_errors(errors: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the stable API error envelope used for HTTP 422 responses."""
    return {
        "code": "VALIDATION_ERROR",
        "message": "Request validation failed",
        "details": [
            {
                "loc": list(error.get("loc", [])),
                "msg": error.get("msg", ""),
                "type": error.get("type", ""),
            }
            for error in errors
        ],
    }
