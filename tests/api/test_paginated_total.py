from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from app.api.deps.pagination import PaginationParams
from app.schemas.pagination import PaginatedResponse

app = FastAPI()

@app.get("/test-paginated-list", response_model=PaginatedResponse[str])
def mock_paginated_list(pagination: PaginationParams = Depends()):
    mock_total = 100
    mock_items = [f"item-{i}" for i in range(pagination.offset, min(pagination.offset + pagination.limit, mock_total))]
    return {
        "items": mock_items,
        "total": mock_total,
        "page": pagination.page,
        "page_size": pagination.page_size,
    }

client = TestClient(app)

def test_paginated_response_includes_total():
    response = client.get("/test-paginated-list?page=2&page_size=10")
    assert response.status_code == 200
    
    data = response.json()
    assert "total" in data
    assert data["total"] == 100
    assert data["page"] == 2
    assert data["page_size"] == 10
    assert len(data["items"]) == 10
    assert data["items"][0] == "item-10"