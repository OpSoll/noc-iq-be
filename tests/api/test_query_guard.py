from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.deps.query_guard import verify_query_param_length_middleware

app = FastAPI()
app.middleware("http")(verify_query_param_length_middleware)

@app.get("/test-query")
def test_query_endpoint(search: str = None, filter: str = None):
    return {"status": "ok", "search": search}

client = TestClient(app)

def test_valid_query_parameters_unaffected():
    response = client.get("/test-query?search=datacenter-01&filter=active")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "search": "datacenter-01"}

def test_rejects_long_individual_query_parameter():
    long_value = "a" * 300 # Exceeds MAX_QUERY_PARAM_LENGTH of 255
    response = client.get(f"/test-query?search={long_value}")
    assert response.status_code == 422
    assert "exceeds maximum length" in response.json()["detail"]

def test_rejects_excessive_total_query_string():
    # Construct query string exceeding MAX_TOTAL_QUERY_STRING_LENGTH (1024)
    params = "&".join([f"param{i}={'x'*50}" for i in range(25)])
    response = client.get(f"/test-query?{params}")
    assert response.status_code == 422
    assert "exceeds maximum allowed length" in response.json()["detail"]