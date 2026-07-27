from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.main import app
from app.schemas.site_hierarchy import SiteHierarchyCreate
from app.services.site_hierarchy import SiteHierarchyService

client = TestClient(app)

def test_create_site_hierarchy(db: Session):
    service = SiteHierarchyService()
    
    # Create parent
    parent_data = {"name": "US-East", "region": "North America"}
    response_parent = client.post("/api/v1/site-hierarchy/", json=parent_data)
    assert response_parent.status_code == 200
    parent_id = response_parent.json()["id"]
    
    # Create child
    child_data = {"name": "NY-DC1", "region": "North America", "parent_id": parent_id}
    response_child = client.post("/api/v1/site-hierarchy/", json=child_data)
    assert response_child.status_code == 200
    assert response_child.json()["parent_id"] == parent_id
