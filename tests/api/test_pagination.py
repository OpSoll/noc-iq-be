from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from app.api.deps.pagination import PaginationParams

app = FastAPI()

@app.get("/test-pagination")
def test_pagination_endpoint(pagination: PaginationParams = Depends()):
    return {
        "page": pagination.page,
        "page_size": pagination.page_size,
        "offset": pagination.offset,
        "limit": pagination.limit,
    }

client = TestClient(app)

def test_pagination_default_values():
    response = client.get("/test-pagination")
    assert response.status_code == 200
    assert response.json() == {
        "page": 1,
        "page_size": 20,
        "offset": 0,
        "limit": 20,
    }

def test_pagination_custom_valid_values():
    response = client.get("/test-pagination?page=3&page_size=50")
    assert response.status_code == 200
    assert response.json() == {
        "page": 3,
        "page_size": 50,
        "offset": 100,
        "limit": 50,
    }

def test_pagination_bounds_validation():
    # Page cannot be less than 1
    response_low_page = client.get("/test-pagination?page=0")
    assert response_low_page.status_code == 422

    # Page size cannot exceed max bound of 100
    response_high_size = client.get("/test-pagination?page_size=101")
    assert response_high_size.status_code == 422

    # Page size cannot be less than 1
    response_low_size = client.get("/test-pagination?page_size=0")
    assert response_low_size.status_code == 422