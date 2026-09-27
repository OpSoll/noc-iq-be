from enum import Enum

class ErrorCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    INTERNAL_ERROR = "internal_error"
    VALIDATION_ERROR = "validation_error"
    # BE-W5-056: Unified error code catalog across routed modules
