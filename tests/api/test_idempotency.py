from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.main import app
import uuid

client = TestClient(app)

def test_idempotency_financial_transaction(db: Session):
    idem_key = str(uuid.uuid4())
    headers = {"idempotency-key": idem_key}
    
    # First call creates
    response1 = client.post("/api/v1/idempotency/financial-transaction", headers=headers)
    assert response1.status_code == 200
    data1 = response1.json()
    assert data1["key"] == idem_key
    
    # Second call returns existing
    response2 = client.post("/api/v1/idempotency/financial-transaction", headers=headers)
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2["id"] == data1["id"]
    assert data2["processed_at"] == data1["processed_at"]
