from typing import Generic, TypeVar, List
from pydantic import BaseModel, Field

T = TypeVar("T")

class PaginatedResponse(BaseModel, Generic[T]):
    """
    Standardized paginated response wrapper ensuring a consistent 'total' count field.
    """
    items: List[T] = Field(..., description="List of items on the current page")
    total: int = Field(..., description="Total count of matching records across all pages")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Number of items per page")

    class Config:
        from_attributes = True