from fastapi import HTTPException, status
from pydantic import BaseModel, Field

class NotFoundDetail(BaseModel):
    error: str = Field("Resource not found", description="Standardized error message")
    resource_type: str = Field(..., description="Type of the missing resource (e.g., outage, payment, wallet)")
    resource_id: str = Field(..., description="Unique identifier of the missing resource")

def raise_not_found(resource_type: str, resource_id: str):
    """
    Raises a standardized 404 HTTPException with consistent detail payload shape. (#929)
    """
    detail_payload = NotFoundDetail(
        resource_type=resource_type,
        resource_id=resource_id
    ).model_dump()

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=detail_payload
    )